"""
FastAPI server for KidJukebox
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    SearchRequest,
    SearchResponse,
    SearchResult,
    DownloadRequest,
    DownloadResponse,
    Playlist,
    PlaylistItem,
    PlaylistReorderRequest,
    StorageInfo,
    HealthResponse,
    LyricsLine,
    LyricsResponse,
    LyricsManualRequest,
    TimingOffsetRequest,
    BackfillStatus,
)
from components.youtube_search import get_searcher
from components.youtube_download import get_downloader
from components import lyrics as lyrics_lib
from components.lyrics import get_lyrics_service, get_timing_offsets

# App paths
APP_DIR = Path(__file__).parent.parent
DATA_DIR = APP_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
THUMB_DIR = DATA_DIR / "thumbnails"
LYRICS_DIR = DATA_DIR / "lyrics"
PLAYLIST_FILE = DATA_DIR / "playlist.json"
STATIC_DIR = APP_DIR / "static"

# Initialize FastAPI app
app = FastAPI(
    title="KidJukebox",
    description="Kid-friendly YouTube music player",
    version="1.0.0"
)


# ============================================================================
# Playlist Storage Helpers
# ============================================================================

def load_playlist() -> Playlist:
    """Load playlist from JSON file"""
    try:
        if PLAYLIST_FILE.exists():
            data = json.loads(PLAYLIST_FILE.read_text())
            items = []
            for item in data.get("items", []):
                # Parse datetime string
                added_at = item.get("added_at", datetime.now().isoformat())
                if isinstance(added_at, str):
                    added_at = datetime.fromisoformat(added_at.replace("Z", "+00:00"))
                items.append(PlaylistItem(
                    id=item["id"],
                    video_id=item["video_id"],
                    title=item["title"],
                    audio_file=item["audio_file"],
                    thumbnail_file=item["thumbnail_file"],
                    duration=item.get("duration", ""),
                    added_at=added_at,
                    # .get() so entries written before the karaoke feature load
                    lyrics_status=item.get("lyrics_status"),
                    lyrics_checked_at=item.get("lyrics_checked_at"),
                ))
            return Playlist(items=items)
    except Exception as e:
        print(f"Error loading playlist: {e}")

    return Playlist(items=[])


def save_playlist(playlist: Playlist) -> None:
    """Save playlist to JSON file"""
    data = {
        "items": [
            {
                "id": item.id,
                "video_id": item.video_id,
                "title": item.title,
                "audio_file": item.audio_file,
                "thumbnail_file": item.thumbnail_file,
                "duration": item.duration,
                "added_at": item.added_at.isoformat(),
                "lyrics_status": item.lyrics_status,
                "lyrics_checked_at": item.lyrics_checked_at,
            }
            for item in playlist.items
        ]
    }
    PLAYLIST_FILE.write_text(json.dumps(data, indent=2))


# ============================================================================
# Lyrics Helpers
# ============================================================================

def _find_item(playlist: Playlist, video_id: str):
    """Find a playlist item by video id."""
    return next((i for i in playlist.items if i.video_id == video_id), None)


def _set_lyrics_status(video_id: str, status: str) -> None:
    """Persist a lyrics lookup outcome onto the playlist entry."""
    playlist = load_playlist()
    item = _find_item(playlist, video_id)
    if not item:
        return
    item.lyrics_status = status
    item.lyrics_checked_at = datetime.now().isoformat()
    save_playlist(playlist)


async def _resolve_lyrics(item: PlaylistItem) -> LyricsResponse:
    """
    Return lyrics for an item, fetching from LRCLIB on first request.

    Songs already checked are served from disk. A previous miss stays a miss
    until an explicit refetch, so we do not re-query LRCLIB on every tap.
    """
    service = get_lyrics_service(str(LYRICS_DIR))
    offsets = get_timing_offsets(str(DATA_DIR))
    status = item.lyrics_status

    # Never looked up before - do it now
    if status is None:
        result = await service.fetch(item.title, lyrics_lib.duration_to_seconds(item.duration))
        if result.text:
            service.save(item.video_id, result.text)
        _set_lyrics_status(item.video_id, result.status)
        status = result.status

    if status in (lyrics_lib.STATUS_NONE, lyrics_lib.STATUS_INSTRUMENTAL):
        return LyricsResponse(
            video_id=item.video_id, status=status, synced=False, lines=[],
            timing_offset=offsets.get(item.video_id),
        )

    text = service.load(item.video_id)
    if text is None:
        # File vanished - report as missing rather than pretending
        return LyricsResponse(
            video_id=item.video_id, status=lyrics_lib.STATUS_NONE, synced=False, lines=[],
            timing_offset=offsets.get(item.video_id),
        )

    parsed = lyrics_lib.parse_lrc(text)
    return LyricsResponse(
        video_id=item.video_id,
        status=status,
        synced=parsed.synced,
        lines=[LyricsLine(**line) for line in parsed.lines],
        timing_offset=offsets.get(item.video_id),
    )


# Progress of the bulk backfill job
_backfill_state = {
    "running": False,
    "processed": 0,
    "total": 0,
    "found": 0,
    "missing": 0,
}


async def _run_backfill() -> None:
    """Look up lyrics for every song that has never been checked."""
    service = get_lyrics_service(str(LYRICS_DIR))
    try:
        playlist = load_playlist()
        pending = [i for i in playlist.items if i.lyrics_status is None]
        _backfill_state.update(
            running=True, processed=0, total=len(pending), found=0, missing=0
        )

        for item in pending:
            try:
                result = await service.fetch(
                    item.title, lyrics_lib.duration_to_seconds(item.duration)
                )
                if result.text:
                    service.save(item.video_id, result.text)
                _set_lyrics_status(item.video_id, result.status)

                if result.status in (lyrics_lib.STATUS_SYNCED, lyrics_lib.STATUS_PLAIN):
                    _backfill_state["found"] += 1
                else:
                    _backfill_state["missing"] += 1
            except Exception as e:
                print(f"Lyrics backfill failed for {item.video_id}: {e}")
                _backfill_state["missing"] += 1
            finally:
                _backfill_state["processed"] += 1
    finally:
        _backfill_state["running"] = False


# ============================================================================
# Static File Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the main HTML page"""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=index_file.read_text())


@app.get("/api/audio/{filename}")
async def serve_audio(filename: str):
    """Serve audio files"""
    # Sanitize filename
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    audio_path = AUDIO_DIR / filename
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(
        path=audio_path,
        media_type="audio/mpeg",
        filename=filename
    )


@app.get("/api/thumbnail/{filename}")
async def serve_thumbnail(filename: str):
    """Serve thumbnail images"""
    # Sanitize filename
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    thumb_path = THUMB_DIR / filename
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(
        path=thumb_path,
        media_type="image/jpeg",
        filename=filename
    )


# ============================================================================
# Health Check
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        app="KidJukebox",
        version="1.0.0"
    )


# ============================================================================
# Search API
# ============================================================================

@app.post("/api/search", response_model=SearchResponse)
async def search_youtube(request: SearchRequest):
    """Search YouTube for videos"""
    searcher = get_searcher()
    results = await searcher.search(request.query, request.limit)

    return SearchResponse(
        results=[
            SearchResult(
                video_id=r["video_id"],
                title=r["title"],
                thumbnail_url=r["thumbnail_url"],
                duration=r["duration"],
                channel=r["channel"]
            )
            for r in results
        ],
        query=request.query
    )


# ============================================================================
# Download API
# ============================================================================

@app.post("/api/download", response_model=DownloadResponse)
async def download_song(request: DownloadRequest):
    """Download a song from YouTube and add to playlist"""
    downloader = get_downloader(str(AUDIO_DIR), str(THUMB_DIR))

    # Check if already in playlist
    playlist = load_playlist()
    for item in playlist.items:
        if item.video_id == request.video_id:
            return DownloadResponse(
                success=True,
                message="Song already in playlist",
                playlist_item=item
            )

    # Download the song
    result = await downloader.download_song(
        video_id=request.video_id,
        thumbnail_url=request.thumbnail_url
    )

    if not result["success"]:
        return DownloadResponse(
            success=False,
            message=result.get("error", "Download failed")
        )

    # Create playlist item
    playlist_item = PlaylistItem(
        id=str(uuid.uuid4()),
        video_id=request.video_id,
        title=request.title or result.get("title", "Unknown"),
        audio_file=f"{request.video_id}.mp3",
        thumbnail_file=f"{request.video_id}.jpg",
        duration=request.duration or result.get("duration", ""),
        added_at=datetime.now()
    )

    # Add to playlist
    playlist.items.append(playlist_item)
    save_playlist(playlist)

    # Look up lyrics now so the karaoke button is ready on first play.
    # Never let a lyrics failure affect the download result.
    try:
        service = get_lyrics_service(str(LYRICS_DIR))
        lyrics_result = await service.fetch(
            playlist_item.title, lyrics_lib.duration_to_seconds(playlist_item.duration)
        )
        if lyrics_result.text:
            service.save(playlist_item.video_id, lyrics_result.text)
        _set_lyrics_status(playlist_item.video_id, lyrics_result.status)
        playlist_item.lyrics_status = lyrics_result.status
    except Exception as e:
        print(f"Lyrics lookup failed for {playlist_item.video_id}: {e}")

    return DownloadResponse(
        success=True,
        message="Song downloaded and added to playlist",
        playlist_item=playlist_item
    )


# ============================================================================
# Playlist API
# ============================================================================

@app.get("/api/playlist", response_model=Playlist)
async def get_playlist():
    """Get the current playlist"""
    return load_playlist()


@app.delete("/api/playlist/{item_id}")
async def remove_from_playlist(item_id: str):
    """Remove a song from the playlist"""
    playlist = load_playlist()

    # Find and remove the item
    item_to_remove = None
    for item in playlist.items:
        if item.id == item_id:
            item_to_remove = item
            break

    if not item_to_remove:
        raise HTTPException(status_code=404, detail="Playlist item not found")

    # Remove from playlist
    playlist.items = [i for i in playlist.items if i.id != item_id]
    save_playlist(playlist)

    # Delete associated files
    downloader = get_downloader(str(AUDIO_DIR), str(THUMB_DIR))
    downloader.delete_song(item_to_remove.video_id)
    get_lyrics_service(str(LYRICS_DIR)).delete(item_to_remove.video_id)

    return {"success": True, "message": "Song removed from playlist"}


@app.post("/api/playlist/reorder")
async def reorder_playlist(request: PlaylistReorderRequest):
    """Reorder playlist items"""
    playlist = load_playlist()

    # Create a map of id to item
    item_map = {item.id: item for item in playlist.items}

    # Reorder based on provided IDs
    new_items = []
    for item_id in request.item_ids:
        if item_id in item_map:
            new_items.append(item_map[item_id])

    # Keep any items that weren't in the reorder list (shouldn't happen)
    for item in playlist.items:
        if item.id not in request.item_ids:
            new_items.append(item)

    playlist.items = new_items
    save_playlist(playlist)

    return {"success": True, "message": "Playlist reordered"}


# ============================================================================
# Lyrics API
# ============================================================================

@app.get("/api/lyrics/{video_id}", response_model=LyricsResponse)
async def get_lyrics(video_id: str):
    """Get lyrics for a song, fetching them on first request."""
    playlist = load_playlist()
    item = _find_item(playlist, video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Song not in playlist")

    return await _resolve_lyrics(item)


@app.post("/api/lyrics/{video_id}/refetch", response_model=LyricsResponse)
async def refetch_lyrics(video_id: str):
    """Force a fresh lookup, e.g. to retry a song that previously missed."""
    playlist = load_playlist()
    item = _find_item(playlist, video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Song not in playlist")

    service = get_lyrics_service(str(LYRICS_DIR))
    service.delete(video_id)

    result = await service.fetch(item.title, lyrics_lib.duration_to_seconds(item.duration))
    if result.text:
        service.save(video_id, result.text)
    _set_lyrics_status(video_id, result.status)

    item.lyrics_status = result.status
    return await _resolve_lyrics(item)


@app.put("/api/lyrics/{video_id}", response_model=LyricsResponse)
async def set_lyrics_manually(video_id: str, request: LyricsManualRequest):
    """Store parent-supplied lyrics (raw LRC or plain text)."""
    playlist = load_playlist()
    item = _find_item(playlist, video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Song not in playlist")

    service = get_lyrics_service(str(LYRICS_DIR))
    service.save(video_id, request.text)
    _set_lyrics_status(video_id, lyrics_lib.STATUS_MANUAL)

    parsed = lyrics_lib.parse_lrc(request.text)
    return LyricsResponse(
        video_id=video_id,
        status=lyrics_lib.STATUS_MANUAL,
        synced=parsed.synced,
        lines=[LyricsLine(**line) for line in parsed.lines],
    )


@app.delete("/api/lyrics/{video_id}")
async def delete_lyrics(video_id: str):
    """Remove stored lyrics and reset the song to unchecked."""
    service = get_lyrics_service(str(LYRICS_DIR))
    deleted = service.delete(video_id)

    playlist = load_playlist()
    item = _find_item(playlist, video_id)
    if item:
        item.lyrics_status = None
        item.lyrics_checked_at = None
        save_playlist(playlist)

    return {"success": True, "deleted": deleted}


@app.post("/api/lyrics/backfill", response_model=BackfillStatus)
async def backfill_lyrics(background_tasks: BackgroundTasks):
    """
    Look up lyrics for every song not yet checked.

    Runs in the background: the lookups are rate limited to one per second, so
    a full playlist takes a while and would otherwise time out the request.
    """
    if _backfill_state["running"]:
        return BackfillStatus(**_backfill_state)

    playlist = load_playlist()
    pending = [i for i in playlist.items if i.lyrics_status is None]

    _backfill_state.update(
        running=True, processed=0, total=len(pending), found=0, missing=0
    )
    background_tasks.add_task(_run_backfill)

    return BackfillStatus(**_backfill_state)


@app.get("/api/lyrics/backfill/status", response_model=BackfillStatus)
async def backfill_status():
    """Poll the progress of a running backfill."""
    return BackfillStatus(**_backfill_state)


# ============================================================================
# Storage API
# ============================================================================

@app.get("/api/storage", response_model=StorageInfo)
async def get_storage_info():
    """Get storage usage information"""
    downloader = get_downloader(str(AUDIO_DIR), str(THUMB_DIR))
    info = downloader.get_storage_info()

    return StorageInfo(
        song_count=info["song_count"],
        total_mb=info["total_mb"],
        audio_files=info["audio_files"],
        thumbnail_files=info["thumbnail_files"],
        lyrics_files=get_lyrics_service(str(LYRICS_DIR)).count()
    )


@app.post("/api/storage/cleanup")
async def cleanup_storage():
    """Remove orphaned files not in playlist"""
    playlist = load_playlist()
    valid_ids = [item.video_id for item in playlist.items]

    downloader = get_downloader(str(AUDIO_DIR), str(THUMB_DIR))
    result = downloader.cleanup_orphaned_files(valid_ids)
    removed_lyrics = get_lyrics_service(str(LYRICS_DIR)).cleanup_orphaned(valid_ids)

    return {
        "success": True,
        "removed_audio": result["removed_audio"],
        "removed_thumbnails": result["removed_thumbnails"],
        "removed_lyrics": removed_lyrics,
        "freed_mb": result["freed_mb"]
    }


# ============================================================================
# Timing Offset API  —  per-song manual timing adjustment
# ============================================================================


@app.get("/api/lyrics/{video_id}/offset")
async def get_timing_offset(video_id: str):
    """Get the saved timing offset for a song (seconds)."""
    offsets = get_timing_offsets(str(DATA_DIR))
    return {"video_id": video_id, "offset": offsets.get(video_id)}


@app.put("/api/lyrics/{video_id}/offset")
async def set_timing_offset(video_id: str, request: TimingOffsetRequest):
    """Save a timing offset for a song so it persists across sessions."""
    offsets = get_timing_offsets(str(DATA_DIR))
    offsets.set(video_id, request.offset)
    return {"video_id": video_id, "offset": request.offset, "success": True}


@app.delete("/api/lyrics/{video_id}/offset")
async def reset_timing_offset(video_id: str):
    """Reset a song's timing offset to zero."""
    offsets = get_timing_offsets(str(DATA_DIR))
    offsets.delete(video_id)
    return {"video_id": video_id, "offset": 0.0, "success": True}
