"""
FastAPI server for KidJukebox
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
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
)
from components.youtube_search import get_searcher
from components.youtube_download import get_downloader

# App paths
APP_DIR = Path(__file__).parent.parent
DATA_DIR = APP_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
THUMB_DIR = DATA_DIR / "thumbnails"
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
                    added_at=added_at
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
                "added_at": item.added_at.isoformat()
            }
            for item in playlist.items
        ]
    }
    PLAYLIST_FILE.write_text(json.dumps(data, indent=2))


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
        thumbnail_files=info["thumbnail_files"]
    )


@app.post("/api/storage/cleanup")
async def cleanup_storage():
    """Remove orphaned files not in playlist"""
    playlist = load_playlist()
    valid_ids = [item.video_id for item in playlist.items]

    downloader = get_downloader(str(AUDIO_DIR), str(THUMB_DIR))
    result = downloader.cleanup_orphaned_files(valid_ids)

    return {
        "success": True,
        "removed_audio": result["removed_audio"],
        "removed_thumbnails": result["removed_thumbnails"],
        "freed_mb": result["freed_mb"]
    }
