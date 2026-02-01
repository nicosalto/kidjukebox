"""
Pydantic models for KidJukebox API
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Request model for YouTube search"""
    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class SearchResult(BaseModel):
    """Single search result from YouTube"""
    video_id: str
    title: str
    thumbnail_url: str
    duration: str
    channel: str


class SearchResponse(BaseModel):
    """Response model for search results"""
    results: List[SearchResult]
    query: str


class DownloadRequest(BaseModel):
    """Request model for downloading a song"""
    video_id: str
    title: str
    thumbnail_url: str
    duration: str = ""


class DownloadResponse(BaseModel):
    """Response model after download completes"""
    success: bool
    message: str
    playlist_item: Optional["PlaylistItem"] = None


class PlaylistItem(BaseModel):
    """Single item in the playlist"""
    id: str  # UUID
    video_id: str
    title: str
    audio_file: str
    thumbnail_file: str
    duration: str
    added_at: datetime


class Playlist(BaseModel):
    """Full playlist model"""
    items: List[PlaylistItem] = []


class PlaylistAddRequest(BaseModel):
    """Request to add a song to playlist (after download)"""
    video_id: str
    title: str
    audio_file: str
    thumbnail_file: str
    duration: str


class PlaylistReorderRequest(BaseModel):
    """Request to reorder playlist items"""
    item_ids: List[str]  # List of item IDs in new order


class StorageInfo(BaseModel):
    """Storage usage information"""
    song_count: int
    total_mb: float
    audio_files: int
    thumbnail_files: int


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    app: str
    version: str


# Update forward references
DownloadResponse.model_rebuild()
