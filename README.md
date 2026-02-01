# KidJukebox

A kid-friendly music player app for KidApps that allows searching YouTube, downloading audio, and playing songs with a playlist interface.

## Features

- **YouTube Search**: Search for songs directly from YouTube
- **Audio Download**: Downloads audio as MP3 files with thumbnails
- **Playlist Management**: Add, remove, and organize your favorite songs
- **Audio Player**: Full-featured player with play/pause, next/previous, progress bar
- **Shuffle Mode**: Randomize playback order
- **Storage Management**: Track disk usage and clean up unused files
- **Keyboard Shortcuts**: Quick controls for power users
- **Kid-Friendly UI**: Large buttons, bright colors, easy navigation

## Requirements

### System Dependencies

- Python 3.10+
- ffmpeg (required for audio extraction)

To install ffmpeg on Ubuntu/Debian:
```bash
sudo apt install ffmpeg
```

### Python Dependencies

- fastapi
- uvicorn
- yt-dlp
- aiofiles
- httpx

## Installation

1. Navigate to the app directory:
   ```bash
   cd /home/ella/kidapps/apps/kidjukebox
   ```

2. Create a virtual environment (if not already created):
   ```bash
   python3 -m venv venv
   ```

3. Install dependencies:
   ```bash
   ./venv/bin/pip install -r requirements.txt
   ```

## Running the App

### Manual Start

```bash
cd /home/ella/kidapps/apps/kidjukebox
./venv/bin/python main.py --port 7869
```

Then open http://localhost:7869 in your browser.

### Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | 0.0.0.0 | Host address to bind to |
| `--port` | 7869 | Port number to listen on |
| `--debug` | false | Enable debug mode with auto-reload |

### Systemd Service

To run as a background service:

```bash
# Enable the service
systemctl --user enable kidapps-kidjukebox.service

# Start the service
systemctl --user start kidapps-kidjukebox.service

# Check status
systemctl --user status kidapps-kidjukebox.service

# View logs
journalctl --user -u kidapps-kidjukebox.service -f
```

## Usage Guide

### Searching for Songs

1. Click the **Search** button in the header
2. Type your search query (song name, artist, etc.)
3. Press Enter or click the search icon
4. Browse the results

### Adding Songs to Playlist

1. Click on any search result to preview it
2. Review the song title, channel, and duration
3. Click **Add to Playlist** to download and add
4. Wait for the download to complete

### Playing Music

1. Click the **Playlist** button to view your songs
2. Click any song to start playing
3. Use the player controls at the bottom:
   - ▶️/⏸️ Play/Pause
   - ⏮️ Previous song (or restart if >3 seconds in)
   - ⏭️ Next song
   - 🔀 Toggle shuffle mode
   - Progress bar: Click to seek

### Managing Your Playlist

- **Delete a song**: Click the 🗑️ button on any playlist item
- **View storage**: Check the song count and disk usage at the top

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Space | Play/Pause |
| N | Next song |
| P | Previous song |
| S | Toggle shuffle |
| ← | Seek back 10 seconds |
| → | Seek forward 10 seconds |

*Note: Shortcuts are disabled when typing in the search box*

## API Reference

### Health Check

```
GET /health
```

Returns server health status.

### Search

```
POST /api/search
Content-Type: application/json

{
  "query": "search terms",
  "limit": 10
}
```

Returns YouTube search results.

### Download

```
POST /api/download
Content-Type: application/json

{
  "video_id": "dQw4w9WgXcQ",
  "title": "Song Title",
  "thumbnail_url": "https://...",
  "duration": "3:32"
}
```

Downloads audio and adds to playlist.

### Playlist

```
GET /api/playlist          # Get all playlist items
DELETE /api/playlist/{id}  # Remove item from playlist
POST /api/playlist/reorder # Reorder playlist items
```

### Storage

```
GET /api/storage           # Get storage usage info
POST /api/storage/cleanup  # Remove orphaned files
```

### Static Files

```
GET /api/audio/{filename}      # Stream audio file
GET /api/thumbnail/{filename}  # Serve thumbnail image
```

## File Structure

```
kidjukebox/
├── main.py                 # Application entry point
├── requirements.txt        # Python dependencies
├── README.md              # This documentation
├── api/
│   ├── __init__.py
│   ├── server.py          # FastAPI routes and endpoints
│   └── models.py          # Pydantic data models
├── components/
│   ├── __init__.py
│   ├── youtube_search.py  # YouTube search functionality
│   └── youtube_download.py # Audio/thumbnail downloader
├── static/
│   └── index.html         # Frontend single-page app
└── data/
    ├── playlist.json      # Saved playlist data
    ├── audio/             # Downloaded MP3 files
    └── thumbnails/        # Downloaded thumbnail images
```

## Data Storage

### Playlist Format

The playlist is stored in `data/playlist.json`:

```json
{
  "items": [
    {
      "id": "uuid-string",
      "video_id": "youtube-video-id",
      "title": "Song Title",
      "audio_file": "video-id.mp3",
      "thumbnail_file": "video-id.jpg",
      "duration": "3:32",
      "added_at": "2026-01-22T10:00:00"
    }
  ]
}
```

### Audio Files

- Location: `data/audio/`
- Format: MP3 (192kbps)
- Naming: `{video_id}.mp3`

### Thumbnails

- Location: `data/thumbnails/`
- Format: JPEG
- Naming: `{video_id}.jpg`

## Troubleshooting

### "Address already in use" error

Another process is using the port. Find and kill it:
```bash
fuser -k 7869/tcp
```

### Downloads fail with "ffmpeg not found"

Install ffmpeg:
```bash
sudo apt install ffmpeg
```

### Search returns no results

- Check your internet connection
- YouTube may be rate-limiting requests; wait a few seconds and try again
- Try a different search query

### Audio won't play

- Ensure the audio file exists in `data/audio/`
- Check browser console for errors
- Try refreshing the page

## Configuration

### KidApps Integration

The app is registered in `/home/ella/kidapps/config/apps.json`:

```json
{
  "id": "kidjukebox",
  "name": "Jukebox",
  "emoji": "🎵",
  "port": 7869,
  "url": "http://localhost:7869",
  "color": "#EC4899",
  "description": "Play music from YouTube",
  "enabled": true,
  "pinned": true,
  "order": 4,
  "path": "/home/ella/kidapps/apps/kidjukebox",
  "service": "kidapps-kidjukebox.service"
}
```

## Technical Notes

- **Rate Limiting**: 2-second delay between YouTube searches to avoid blocks
- **Download Queue**: One download at a time to prevent resource exhaustion
- **Audio Format**: Uses yt-dlp with FFmpegExtractAudio for reliable MP3 conversion
- **Thumbnail Source**: Downloads from YouTube CDN (img.youtube.com)

## License

Part of the KidApps project.
