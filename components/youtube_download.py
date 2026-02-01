"""
YouTube Download Component for KidJukebox
Uses yt-dlp for downloading audio and thumbnails
"""

import asyncio
import os
from pathlib import Path
from typing import Optional, Callable
import httpx
import yt_dlp


class YouTubeDownloader:
    """Handles downloading audio and thumbnails from YouTube"""

    def __init__(self, audio_dir: str, thumb_dir: str):
        """
        Initialize downloader with output directories.

        Args:
            audio_dir: Directory to save audio files
            thumb_dir: Directory to save thumbnail images
        """
        self.audio_dir = Path(audio_dir)
        self.thumb_dir = Path(thumb_dir)

        # Ensure directories exist
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.thumb_dir.mkdir(parents=True, exist_ok=True)

        # Track if a download is in progress
        self._downloading = False
        self._download_lock = asyncio.Lock()

    @property
    def is_downloading(self) -> bool:
        """Check if a download is currently in progress"""
        return self._downloading

    async def download_song(
        self,
        video_id: str,
        thumbnail_url: str = "",
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> dict:
        """
        Download audio as MP3 and thumbnail for a YouTube video.

        Args:
            video_id: YouTube video ID
            thumbnail_url: Optional thumbnail URL (will use YouTube CDN if not provided)
            progress_callback: Optional callback for progress updates

        Returns:
            Dictionary with:
            - success: bool
            - audio_path: Path to downloaded audio file
            - thumbnail_path: Path to downloaded thumbnail
            - title: Video title
            - duration: Duration string
            - error: Error message if failed
        """
        async with self._download_lock:
            if self._downloading:
                return {
                    "success": False,
                    "error": "Another download is already in progress"
                }

            self._downloading = True

        try:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            audio_path = self.audio_dir / f"{video_id}.mp3"
            thumb_path = self.thumb_dir / f"{video_id}.jpg"

            # Check if already downloaded
            if audio_path.exists():
                # Get video info for title/duration
                info = await self._get_video_info(video_url)
                return {
                    "success": True,
                    "audio_path": str(audio_path),
                    "thumbnail_path": str(thumb_path) if thumb_path.exists() else "",
                    "title": info.get("title", "Unknown"),
                    "duration": self._format_duration(info.get("duration", 0)),
                    "already_exists": True
                }

            if progress_callback:
                progress_callback("Starting download...")

            # Download audio using yt-dlp
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._download_audio,
                video_url,
                str(audio_path),
                progress_callback
            )

            if not result["success"]:
                return result

            # Download thumbnail
            if progress_callback:
                progress_callback("Downloading thumbnail...")

            await self._download_thumbnail(video_id, thumbnail_url, thumb_path)

            return {
                "success": True,
                "audio_path": str(audio_path),
                "thumbnail_path": str(thumb_path) if thumb_path.exists() else "",
                "title": result.get("title", "Unknown"),
                "duration": result.get("duration", ""),
                "already_exists": False
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

        finally:
            self._downloading = False

    def _download_audio(
        self,
        video_url: str,
        output_path: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> dict:
        """Synchronous audio download using yt-dlp"""

        # Remove .mp3 extension for yt-dlp (it adds its own)
        output_template = output_path.replace(".mp3", "")

        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "extractaudio": True,
            "noplaylist": True,
            # Bypass YouTube 403 errors
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        }

        # Add progress hook if callback provided
        if progress_callback:
            def progress_hook(d):
                if d["status"] == "downloading":
                    percent = d.get("_percent_str", "0%").strip()
                    progress_callback(f"Downloading: {percent}")
                elif d["status"] == "finished":
                    progress_callback("Converting to MP3...")

            ydl_opts["progress_hooks"] = [progress_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)

                return {
                    "success": True,
                    "title": info.get("title", "Unknown"),
                    "duration": self._format_duration(info.get("duration", 0))
                }

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            if "age" in error_msg.lower():
                return {"success": False, "error": "This video is age-restricted"}
            elif "unavailable" in error_msg.lower():
                return {"success": False, "error": "This video is unavailable"}
            elif "private" in error_msg.lower():
                return {"success": False, "error": "This video is private"}
            else:
                return {"success": False, "error": f"Download failed: {error_msg}"}

        except Exception as e:
            return {"success": False, "error": f"Download error: {str(e)}"}

    async def _download_thumbnail(
        self,
        video_id: str,
        thumbnail_url: str,
        output_path: Path
    ) -> bool:
        """Download video thumbnail"""
        # Try provided URL first, then fall back to YouTube CDN
        urls_to_try = []

        if thumbnail_url:
            urls_to_try.append(thumbnail_url)

        # YouTube CDN thumbnail URLs (in order of quality)
        urls_to_try.extend([
            f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
        ])

        async with httpx.AsyncClient() as client:
            for url in urls_to_try:
                try:
                    response = await client.get(url, timeout=10.0)
                    if response.status_code == 200:
                        # Check if it's actually an image (not a placeholder)
                        content_type = response.headers.get("content-type", "")
                        if "image" in content_type and len(response.content) > 1000:
                            output_path.write_bytes(response.content)
                            return True
                except Exception:
                    continue

        return False

    async def _get_video_info(self, video_url: str) -> dict:
        """Get video info without downloading"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._get_info_sync,
            video_url
        )

    def _get_info_sync(self, video_url: str) -> dict:
        """Synchronous video info extraction"""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extractaudio": False,
            "skip_download": True,
            # Bypass YouTube 403 errors
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(video_url, download=False)
        except Exception:
            return {}

    def _format_duration(self, seconds: int) -> str:
        """Convert seconds to MM:SS or HH:MM:SS format"""
        if not seconds:
            return "0:00"

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    def delete_song(self, video_id: str) -> bool:
        """Delete audio and thumbnail files for a video"""
        audio_path = self.audio_dir / f"{video_id}.mp3"
        thumb_path = self.thumb_dir / f"{video_id}.jpg"

        deleted = False
        if audio_path.exists():
            audio_path.unlink()
            deleted = True
        if thumb_path.exists():
            thumb_path.unlink()
            deleted = True

        return deleted

    def get_storage_info(self) -> dict:
        """Get storage usage information"""
        audio_files = list(self.audio_dir.glob("*.mp3"))
        thumb_files = list(self.thumb_dir.glob("*.jpg"))

        total_bytes = sum(f.stat().st_size for f in audio_files)
        total_bytes += sum(f.stat().st_size for f in thumb_files)

        return {
            "song_count": len(audio_files),
            "total_mb": round(total_bytes / (1024 * 1024), 2),
            "audio_files": len(audio_files),
            "thumbnail_files": len(thumb_files)
        }

    def cleanup_orphaned_files(self, valid_video_ids: list) -> dict:
        """Remove files not associated with playlist items"""
        removed_audio = 0
        removed_thumbs = 0
        freed_bytes = 0

        # Clean up audio files
        for audio_file in self.audio_dir.glob("*.mp3"):
            video_id = audio_file.stem
            if video_id not in valid_video_ids:
                freed_bytes += audio_file.stat().st_size
                audio_file.unlink()
                removed_audio += 1

        # Clean up thumbnail files
        for thumb_file in self.thumb_dir.glob("*.jpg"):
            video_id = thumb_file.stem
            if video_id not in valid_video_ids:
                freed_bytes += thumb_file.stat().st_size
                thumb_file.unlink()
                removed_thumbs += 1

        return {
            "removed_audio": removed_audio,
            "removed_thumbnails": removed_thumbs,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2)
        }


# Singleton instance
_downloader: Optional[YouTubeDownloader] = None


def get_downloader(audio_dir: str, thumb_dir: str) -> YouTubeDownloader:
    """Get or create the singleton YouTubeDownloader instance"""
    global _downloader
    if _downloader is None:
        _downloader = YouTubeDownloader(audio_dir, thumb_dir)
    return _downloader
