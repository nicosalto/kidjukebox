"""
Lyrics Component for KidJukebox

Fetches time-synced lyrics from LRCLIB (https://lrclib.net) and stores them
as .lrc files, one per video id. Used by the fullscreen karaoke view.

LRCLIB is keyless and free. Every failure path degrades to "no lyrics" so
that playback keeps working exactly as before when offline.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "KidJukebox/1.0 (local kid-friendly music player)"

# A search result is only accepted if its duration is within this many seconds
# of the real track. Without this guard LRCLIB happily returns a same-titled
# song of a completely different length, and the lyrics scroll out of sync.
DURATION_TOLERANCE_S = 5

# Status values stored on PlaylistItem.lyrics_status
STATUS_SYNCED = "synced"          # timestamped lyrics on disk
STATUS_PLAIN = "plain"            # untimed lyrics on disk
STATUS_INSTRUMENTAL = "instrumental"  # track has no words
STATUS_NONE = "none"              # looked, found nothing
STATUS_MANUAL = "manual"          # entered by a parent

# Noise commonly appended to YouTube music titles. Stripping this is what makes
# LRCLIB lookups work at all - raw titles match nothing.
_NOISE_WORDS = (
    r"official|video|audio|lyric|lyrics|hd|hq|4k|remaster|remastered|"
    r"mv|m/v|live|visualizer|explicit|clean|full|version|hq audio"
)
_NOISE_RE = re.compile(
    rf"""\s*(?:
        \((?:[^()]*(?:{_NOISE_WORDS})[^()]*)\)
      | \[(?:[^\[\]]*(?:{_NOISE_WORDS})[^\[\]]*)\]
      | \b(?:official\s+(?:music\s+)?video|lyric\s+video|official\s+audio)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_TRAILING_CHANNEL_RE = re.compile(r"\s*[|｜]\s*.*$")
_LRC_TIME_RE = re.compile(r"\[(\d+):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_ARTIST_SEPARATORS = (" - ", " – ", " — ")


@dataclass
class LyricsResult:
    """Outcome of a lyrics lookup."""
    status: str
    text: str = ""                       # raw LRC or plain text, "" when none
    track_name: Optional[str] = None
    artist_name: Optional[str] = None


@dataclass
class ParsedLyrics:
    """Lyrics ready for the frontend."""
    status: str
    synced: bool = False
    lines: list = field(default_factory=list)  # [{"time": float|None, "text": str}]


def clean_title(title: str) -> str:
    """Strip YouTube title noise so the result can be matched against LRCLIB."""
    cleaned = _NOISE_RE.sub("", title or "")
    cleaned = _TRAILING_CHANNEL_RE.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -–—").strip()


def split_artist_track(cleaned: str) -> tuple:
    """
    Split "Artist - Track" into its two halves.

    Returns (None, cleaned) when there is no recognisable separator, which
    routes the lookup through free-text search instead of exact match.
    """
    for separator in _ARTIST_SEPARATORS:
        if separator in cleaned:
            artist, track = cleaned.split(separator, 1)
            artist, track = artist.strip(), track.strip()
            if artist and track:
                return artist, track
    return None, cleaned


def duration_to_seconds(duration: str) -> int:
    """
    Parse a stored display duration ("4:01", "1:02:33") back to seconds.

    PlaylistItem.duration is a display string; the integer from yt-dlp is not
    kept for older entries, so this reverses it. Returns 0 if unparseable.
    """
    if not duration:
        return 0
    try:
        parts = [int(p) for p in duration.strip().split(":")]
    except ValueError:
        return 0

    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 1:
        return parts[0]
    return 0


def parse_lrc(text: str) -> ParsedLyrics:
    """
    Parse LRC (or plain) text into ordered lines for the karaoke view.

    Handles multiple timestamps on one line ("[00:12.00][01:30.00]words"),
    ignores metadata tags such as [ar:] / [ti:], and treats text with no
    timestamps at all as plain lyrics.
    """
    lines = []
    any_timed = False

    for raw_line in (text or "").splitlines():
        stamps = _LRC_TIME_RE.findall(raw_line)
        content = _LRC_TIME_RE.sub("", raw_line).strip()

        # Metadata tags ([ar:...], [ti:...]) carry no timestamps and are not lyrics
        if not stamps:
            if content.startswith("[") and content.endswith("]"):
                continue
            lines.append({"time": None, "text": content})
            continue

        any_timed = True
        for minutes, seconds in stamps:
            seconds = seconds.replace(":", ".")
            lines.append({
                "time": int(minutes) * 60 + float(seconds),
                "text": content,
            })

    if any_timed:
        # Untimed leftovers cannot be positioned; drop them and sort by time
        lines = sorted(
            (line for line in lines if line["time"] is not None),
            key=lambda line: line["time"],
        )
        return ParsedLyrics(status=STATUS_SYNCED, synced=True, lines=lines)

    # Plain text: trim leading/trailing blank lines but keep interior spacing
    while lines and not lines[0]["text"]:
        lines.pop(0)
    while lines and not lines[-1]["text"]:
        lines.pop()

    return ParsedLyrics(status=STATUS_PLAIN, synced=False, lines=lines)


class LyricsService:
    """Fetches and stores lyrics files, one per video id."""

    def __init__(self, lyrics_dir: str):
        self.lyrics_dir = Path(lyrics_dir)
        self.lyrics_dir.mkdir(parents=True, exist_ok=True)

        # LRCLIB is a free community service - stay polite
        self._rate_limit_seconds = 1.0
        self._last_request = 0.0
        self._request_lock = asyncio.Lock()

    def path_for(self, video_id: str) -> Path:
        """On-disk path for a video's lyrics."""
        return self.lyrics_dir / f"{video_id}.lrc"

    def has_lyrics(self, video_id: str) -> bool:
        return self.path_for(video_id).exists()

    def load(self, video_id: str) -> Optional[str]:
        """Read stored lyrics text, or None if absent/unreadable."""
        path = self.path_for(video_id)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    def save(self, video_id: str, text: str) -> None:
        self.path_for(video_id).write_text(text, encoding="utf-8")

    def delete(self, video_id: str) -> bool:
        path = self.path_for(video_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def cleanup_orphaned(self, valid_video_ids) -> int:
        """Remove lyrics files whose song is no longer in the playlist."""
        valid = set(valid_video_ids)
        removed = 0
        for lyrics_file in self.lyrics_dir.glob("*.lrc"):
            if lyrics_file.stem not in valid:
                try:
                    lyrics_file.unlink()
                    removed += 1
                except Exception:
                    continue
        return removed

    def count(self) -> int:
        return len(list(self.lyrics_dir.glob("*.lrc")))

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._rate_limit_seconds:
            await asyncio.sleep(self._rate_limit_seconds - elapsed)
        self._last_request = time.monotonic()

    async def fetch(self, title: str, duration_s: int) -> LyricsResult:
        """
        Look up lyrics for a song. Never raises - returns STATUS_NONE instead.

        Strategy: clean the title, try LRCLIB's exact endpoint when an artist
        can be parsed out, then fall back to free-text search filtered by
        duration. A search hit outside DURATION_TOLERANCE_S is rejected: wrong
        lyrics that scroll out of time are worse than showing none.

        Timestamps beat a first hit. The exact endpoint sometimes holds only an
        untimed version of a track that search has synced copies of, so a
        plain-only exact hit is kept as a fallback and the search still runs -
        otherwise the song shows words that never highlight.
        """
        cleaned = clean_title(title)
        if not cleaned:
            return LyricsResult(status=STATUS_NONE)

        artist, track = split_artist_track(cleaned)

        headers = {"User-Agent": USER_AGENT}
        fallback = None
        try:
            async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
                if artist:
                    hit = await self._try_exact(client, artist, track, duration_s)
                    if hit is not None:
                        result = self._to_result(hit)
                        # Synced or instrumental are authoritative - stop here
                        if result.status in (STATUS_SYNCED, STATUS_INSTRUMENTAL):
                            return result
                        if result.status == STATUS_PLAIN:
                            fallback = result

                hit = await self._try_search(client, cleaned, duration_s)
                if hit is not None:
                    result = self._to_result(hit)
                    if result.status == STATUS_SYNCED:
                        return result
                    if fallback is None and result.status != STATUS_NONE:
                        fallback = result
        except Exception:
            return fallback or LyricsResult(status=STATUS_NONE)

        return fallback or LyricsResult(status=STATUS_NONE)

    async def _try_exact(self, client, artist: str, track: str, duration_s: int):
        """LRCLIB /api/get - exact artist + track match, 404 when unknown."""
        params = {"artist_name": artist, "track_name": track}
        if duration_s:
            params["duration"] = duration_s

        await self._throttle()
        try:
            response = await client.get(f"{LRCLIB_BASE}/get", params=params)
        except Exception:
            return None

        if response.status_code == 200:
            return response.json()
        return None

    async def _try_search(self, client, query: str, duration_s: int):
        """
        LRCLIB /api/search - free text, then filter hard on duration.

        Without the duration filter this endpoint returns same-titled songs of
        entirely different lengths.
        """
        await self._throttle()
        try:
            response = await client.get(f"{LRCLIB_BASE}/search", params={"q": query})
        except Exception:
            return None

        if response.status_code != 200:
            return None

        try:
            results = response.json()
        except Exception:
            return None

        if not isinstance(results, list) or not results:
            return None

        # Without a reliable duration we cannot verify a match at all
        if not duration_s:
            return None

        def within_tolerance(entry):
            entry_duration = entry.get("duration") or 0
            return abs(entry_duration - duration_s) <= DURATION_TOLERANCE_S

        candidates = [entry for entry in results if within_tolerance(entry)]
        if not candidates:
            return None

        def closest(entries):
            return min(entries, key=lambda e: abs((e.get("duration") or 0) - duration_s))

        # Prefer a timestamped match; among equals take the closest duration
        synced = [e for e in candidates if e.get("syncedLyrics")]
        if synced:
            return closest(synced)
        return closest(candidates)

    @staticmethod
    def _to_result(entry: dict) -> LyricsResult:
        """Turn an LRCLIB payload into a LyricsResult."""
        track_name = entry.get("trackName")
        artist_name = entry.get("artistName")

        if entry.get("instrumental"):
            return LyricsResult(
                status=STATUS_INSTRUMENTAL,
                track_name=track_name,
                artist_name=artist_name,
            )

        synced = entry.get("syncedLyrics")
        if synced and synced.strip():
            return LyricsResult(
                status=STATUS_SYNCED,
                text=synced,
                track_name=track_name,
                artist_name=artist_name,
            )

        plain = entry.get("plainLyrics")
        if plain and plain.strip():
            return LyricsResult(
                status=STATUS_PLAIN,
                text=plain,
                track_name=track_name,
                artist_name=artist_name,
            )

        return LyricsResult(status=STATUS_NONE)


# Module-level singleton
_lyrics_service: Optional[LyricsService] = None


def get_lyrics_service(lyrics_dir: str) -> LyricsService:
    """Get the shared LyricsService instance."""
    global _lyrics_service
    if _lyrics_service is None:
        _lyrics_service = LyricsService(lyrics_dir)
    return _lyrics_service
