"""Local development server for the FCSA static site."""

from __future__ import annotations

import argparse
import logging
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from update_videos import update_videos


ROOT = Path(__file__).resolve().parents[1]
VIDEOS_OUTPUT = ROOT / "data" / "videos.json"
LOGGER = logging.getLogger("fcsa.serve")


class StaticRequestHandler(SimpleHTTPRequestHandler):
    """Serve files below ROOT without exposing directory listings."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def translate_path(self, path: str) -> str:
        translated = Path(super().translate_path(path)).resolve()
        try:
            translated.relative_to(ROOT)
        except ValueError:
            return str(ROOT / "__missing__")
        return str(translated)

    def list_directory(self, path: str):
        self.send_error(HTTPStatus.NOT_FOUND, "Directory listing disabled")
        return None

    def end_headers(self) -> None:
        if urlsplit(self.path).path == "/data/videos.json":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


def refresh_videos(stop_event: threading.Event, refresh_seconds: int) -> None:
    while not stop_event.is_set():
        try:
            update_videos(VIDEOS_OUTPUT)
        except Exception:
            LOGGER.exception("Video update failed; keeping the last good JSON.")
        if stop_event.wait(refresh_seconds):
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the FCSA site locally.")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--refresh-seconds", type=int, default=3600)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.refresh_seconds < 60:
        parser.error("--refresh-seconds must be at least 60")
    return args


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    stop_event = threading.Event()
    refresh_thread = threading.Thread(
        target=refresh_videos,
        args=(stop_event, args.refresh_seconds),
        name="fcsa-video-refresh",
        daemon=True,
    )
    refresh_thread.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), StaticRequestHandler)
    LOGGER.info("Serving %s at http://127.0.0.1:%s", ROOT, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping local server.")
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
