"""
YouTube Search Component for KidJukebox
Uses yt-dlp for searching videos (more reliable than youtube-search-python)
"""

import asyncio
import logging
import shutil
from typing import List, Optional
import yt_dlp

logger = logging.getLogger("kidjukebox.search")

# Detect available JS runtime for yt-dlp YouTube extraction
def _get_js_runtime() -> dict | None:
    """Return the js_runtimes config dict if node is available."""
    node_path = shutil.which("node")
    if node_path:
        return {"node": {"path": node_path}}
    logger.warning("No Node.js runtime found - YouTube searches may fail")
    return None

_JS_RUNTIMES = _get_js_runtime()


class YouTubeSearcher:
    """Wrapper for YouTube video search functionality using yt-dlp"""

    def __init__(self):
        self._last_search_time: float = 0
        self._rate_limit_seconds: float = 2.0

    async def search(self, query: str, limit: int = 10) -> List[dict]:
        """
        Search YouTube for videos matching the query.

        Args:
            query: Search query string
            limit: Maximum number of results (default 10, max 20)

        Returns:
            List of video metadata dictionaries with keys:
            - video_id: YouTube video ID
            - title: Video title
            - thumbnail_url: URL to video thumbnail
            - duration: Duration string (e.g., "3:45")
            - channel: Channel name
        """
        # Rate limiting - ensure at least 2 seconds between searches
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self._last_search_time
        if time_since_last < self._rate_limit_seconds:
            await asyncio.sleep(self._rate_limit_seconds - time_since_last)

        # Run search in thread pool since yt-dlp is synchronous
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            self._do_search,
            query,
            limit
        )

        self._last_search_time = asyncio.get_event_loop().time()
        return results

    def _do_search(self, query: str, limit: int) -> List[dict]:
        """Synchronous search implementation using yt-dlp"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'skip_download': True,
            'default_search': 'ytsearch',
            'no_color': True,
            # JS runtime for extraction (flat search may not need it, but be safe)
            'js_runtimes': _JS_RUNTIMES,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Use ytsearch prefix for YouTube search
                search_query = f"ytsearch{limit}:{query}"
                logger.info("Searching YouTube for: '%s'", query)
                result = ydl.extract_info(search_query, download=False)

                videos = []
                entries = result.get('entries', []) if result else []

                logger.info("Search returned %d results for '%s'", len(entries), query)

                for item in entries:
                    if not item:
                        continue

                    video_id = item.get('id', '')
                    if not video_id:
                        continue

                    # Get thumbnail URL
                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"

                    # Extract duration
                    duration_secs = item.get('duration')
                    if duration_secs:
                        duration = self._format_duration(int(duration_secs))
                    else:
                        duration = "Live" if item.get('is_live') else "?"

                    # Extract channel name
                    channel = item.get('channel', '') or item.get('uploader', 'Unknown')

                    videos.append({
                        "video_id": video_id,
                        "title": item.get('title', 'Unknown Title'),
                        "thumbnail_url": thumbnail_url,
                        "duration": duration,
                        "channel": channel
                    })

                return videos

        except Exception as e:
            logger.error("Search error for '%s': %s", query, e)
            return []

    def _format_duration(self, seconds: int) -> str:
        """Convert seconds to MM:SS or HH:MM:SS format"""
        if not seconds or seconds <= 0:
            return "0:00"

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    async def get_video_info(self, video_id: str) -> Optional[dict]:
        """
        Get information about a specific video by ID.

        Args:
            video_id: YouTube video ID

        Returns:
            Video metadata dictionary or None if not found
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._get_video_info_sync,
            video_id
        )

    def _get_video_info_sync(self, video_id: str) -> Optional[dict]:
        """Synchronous video info extraction"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'no_color': True,
            # JS runtime required by YouTube extraction since mid-2025
            'js_runtimes': _JS_RUNTIMES,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                url = f"https://www.youtube.com/watch?v={video_id}"
                logger.info("Getting video info for %s", video_id)
                info = ydl.extract_info(url, download=False)

                if not info:
                    logger.warning("No info returned for %s", video_id)
                    return None

                thumbnail_url = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
                duration_secs = info.get('duration', 0)

                video_info = {
                    "video_id": video_id,
                    "title": info.get('title', 'Unknown Title'),
                    "thumbnail_url": thumbnail_url,
                    "duration": self._format_duration(int(duration_secs)) if duration_secs else "?",
                    "channel": info.get('channel', '') or info.get('uploader', 'Unknown')
                }
                logger.info("Got info for %s: '%s'", video_id, video_info["title"])
                return video_info

        except Exception as e:
            logger.error("Error getting video info for %s: %s", video_id, e)
            return None


# Singleton instance
_searcher: Optional[YouTubeSearcher] = None


def get_searcher() -> YouTubeSearcher:
    """Get or create the singleton YouTubeSearcher instance"""
    global _searcher
    if _searcher is None:
        _searcher = YouTubeSearcher()
    return _searcher
