#!/usr/bin/env python3
"""
KidJukebox - YouTube Music Player for KidApps
Entry point with CLI argument parsing
"""

import argparse
import uvicorn
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="KidJukebox - Kid-friendly YouTube music player"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7869,
        help="Port to bind to (default: 7869)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with auto-reload"
    )

    args = parser.parse_args()

    # Ensure data directories exist
    data_dir = Path(__file__).parent / "data"
    (data_dir / "audio").mkdir(parents=True, exist_ok=True)
    (data_dir / "thumbnails").mkdir(parents=True, exist_ok=True)

    # Initialize playlist.json if it doesn't exist
    playlist_file = data_dir / "playlist.json"
    if not playlist_file.exists():
        playlist_file.write_text('{"items": []}')

    print(f"🎵 KidJukebox starting on http://{args.host}:{args.port}")

    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=args.debug,
        log_level="debug" if args.debug else "info"
    )


if __name__ == "__main__":
    main()
