"""Validate the public FC SSA YouTube Atom feed and atomically update videos.json."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


CHANNEL_ID = "UCRAyNAfsPxvbI3Kd1xRntfQ"
CHANNEL_NAME = "FC쏘아"
CHANNEL_URL = "https://www.youtube.com/@FC%EC%8F%98%EC%95%84"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
PUBLISHED_URL = "https://kyuuu410.github.io/fcsa/data/videos.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "videos.json"
MAX_FEED_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 20
SNAPSHOT_FIELDS = {"version", "channelId", "channelName", "channelUrl", "fetchedAt", "videos"}
VIDEO_FIELDS = {"id", "title", "publishedAt", "updatedAt", "url", "thumbnail"}
NAMESPACES = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}
VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}\Z")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z")


class VideoUpdateError(ValueError):
    """A rejected download or feed leaves the previous output untouched."""


def required_text(element, path):
    matches = element.findall(path, NAMESPACES)
    if len(matches) != 1 or not matches[0].text or not matches[0].text.strip():
        raise VideoUpdateError(f"Missing or duplicate feed field: {path}")
    return matches[0].text.strip()


def parse_timestamp(value, field):
    if not DATE_PATTERN.fullmatch(value):
        raise VideoUpdateError(f"Invalid {field} timestamp: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise VideoUpdateError(f"Invalid {field} timestamp: {value!r}") from error


def utc_string(value):
    return value.isoformat().replace("+00:00", "Z")


def safe_url(value, hosts):
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
            raise VideoUpdateError("Unapproved URL in YouTube feed")
        return parsed
    except ValueError as error:
        raise VideoUpdateError("Invalid URL in YouTube feed") from error


def validate_channel_link(root):
    links = root.findall("atom:link[@rel='alternate']", NAMESPACES)
    if len(links) != 1:
        raise VideoUpdateError("Missing or duplicate channel link")
    parsed = safe_url(links[0].get("href", ""), {"www.youtube.com"})
    if parsed.path != f"/channel/{CHANNEL_ID}" or parsed.query:
        raise VideoUpdateError("Feed channel link does not match the configured channel")


def parse_feed(content):
    if len(content) > MAX_FEED_BYTES:
        raise VideoUpdateError("YouTube feed exceeds the 1 MiB size limit")
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise VideoUpdateError("DTD and entity declarations are not supported")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise VideoUpdateError(f"Malformed YouTube XML feed: {error}") from error
    if root.tag != f"{{{NAMESPACES['atom']}}}feed":
        raise VideoUpdateError("Expected an Atom feed")
    # The public feed uses a prefix-free channel ID at its root, but full IDs on entries.
    if required_text(root, "yt:channelId") not in {CHANNEL_ID, CHANNEL_ID[2:]}:
        raise VideoUpdateError("Feed belongs to a different YouTube channel")
    validate_channel_link(root)
    entries = root.findall("atom:entry", NAMESPACES)
    if not entries:
        raise VideoUpdateError("YouTube feed contains no videos")
    videos = []
    seen_ids = set()
    for entry in entries:
        if required_text(entry, "yt:channelId") != CHANNEL_ID:
            raise VideoUpdateError("Video belongs to a different YouTube channel")
        video_id = required_text(entry, "yt:videoId")
        if not VIDEO_ID_PATTERN.fullmatch(video_id):
            raise VideoUpdateError(f"Invalid YouTube video ID: {video_id!r}")
        if video_id in seen_ids:
            raise VideoUpdateError(f"Duplicate YouTube video ID: {video_id}")
        seen_ids.add(video_id)
        if required_text(entry, "atom:id") != f"yt:video:{video_id}":
            raise VideoUpdateError("Atom video ID does not match the YouTube video ID")
        title = required_text(entry, "atom:title")
        published = parse_timestamp(required_text(entry, "atom:published"), "published")
        updated = parse_timestamp(required_text(entry, "atom:updated"), "updated")
        links = entry.findall("atom:link[@rel='alternate']", NAMESPACES)
        if len(links) != 1:
            raise VideoUpdateError("Missing or duplicate video link")
        link = safe_url(links[0].get("href", ""), {"www.youtube.com"})
        if link.path != "/watch" or parse_qs(link.query, keep_blank_values=True) != {"v": [video_id]}:
            raise VideoUpdateError("Video watch URL does not match the video ID")
        thumbnails = entry.findall("media:group/media:thumbnail", NAMESPACES)
        if len(thumbnails) != 1:
            raise VideoUpdateError("Missing or duplicate video thumbnail")
        thumbnail = safe_url(thumbnails[0].get("url", ""), {"i.ytimg.com", "i1.ytimg.com", "i2.ytimg.com", "i3.ytimg.com", "i4.ytimg.com"})
        if thumbnail.path != f"/vi/{video_id}/hqdefault.jpg" or thumbnail.query:
            raise VideoUpdateError("Thumbnail URL does not match the video ID")
        videos.append({"id": video_id, "title": title, "publishedAt": utc_string(published), "updatedAt": utc_string(updated), "url": f"https://www.youtube.com/watch?v={video_id}", "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"})
    videos.sort(key=lambda video: (parse_timestamp(video["publishedAt"], "published"), video["id"]), reverse=True)
    return {"version": 1, "channelId": CHANNEL_ID, "channelName": CHANNEL_NAME, "channelUrl": CHANNEL_URL, "fetchedAt": utc_string(datetime.now(timezone.utc)), "videos": videos[:4]}


def read_feed(feed_file=None):
    if feed_file is not None:
        try:
            with Path(feed_file).open("rb") as source:
                return source.read(MAX_FEED_BYTES + 1)
        except OSError as error:
            raise VideoUpdateError(f"Cannot read feed file: {error}") from error
    try:
        request = Request(FEED_URL, headers={"User-Agent": "FCSSA-VideoUpdater/1.0", "Accept": "application/atom+xml, application/xml"})
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            resolved = safe_url(response.geturl(), {"www.youtube.com"})
            if resolved.path != "/feeds/videos.xml" or parse_qs(resolved.query) != {"channel_id": [CHANNEL_ID]}:
                raise VideoUpdateError("YouTube feed redirected to an unexpected URL")
            return response.read(MAX_FEED_BYTES + 1)
    except HTTPError as error:
        raise VideoUpdateError(f"YouTube feed download failed: HTTP {error.code}") from error
    except (TimeoutError, socket.timeout) as error:
        raise VideoUpdateError(f"YouTube feed download timed out after {TIMEOUT_SECONDS} seconds") from error
    except URLError as error:
        raise VideoUpdateError("YouTube feed connection failed") from error
    except OSError as error:
        raise VideoUpdateError("YouTube feed download failed") from error


def validate_snapshot(content):
    if len(content) > MAX_FEED_BYTES:
        raise VideoUpdateError("Published video snapshot exceeds the 1 MiB size limit")
    try:
        snapshot = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VideoUpdateError("Published video snapshot is not valid JSON") from error
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
        raise VideoUpdateError("Published video snapshot has an invalid schema")
    if type(snapshot["version"]) is not int or snapshot["version"] != 1:
        raise VideoUpdateError("Published video snapshot has an unsupported version")
    expected_channel = {"channelId": CHANNEL_ID, "channelName": CHANNEL_NAME, "channelUrl": CHANNEL_URL}
    if any(snapshot[field] != value for field, value in expected_channel.items()):
        raise VideoUpdateError("Published video snapshot belongs to a different channel")
    if not isinstance(snapshot["fetchedAt"], str):
        raise VideoUpdateError("Published video snapshot has an invalid fetchedAt timestamp")
    parse_timestamp(snapshot["fetchedAt"], "fetchedAt")
    if not isinstance(snapshot["videos"], list) or not 1 <= len(snapshot["videos"]) <= 4:
        raise VideoUpdateError("Published video snapshot must contain 1 to 4 videos")

    seen_ids = set()
    for video in snapshot["videos"]:
        if not isinstance(video, dict) or set(video) != VIDEO_FIELDS:
            raise VideoUpdateError("Published video snapshot contains an invalid video schema")
        video_id = video["id"]
        if not isinstance(video_id, str) or not VIDEO_ID_PATTERN.fullmatch(video_id):
            raise VideoUpdateError("Published video snapshot contains an invalid video ID")
        if video_id in seen_ids:
            raise VideoUpdateError(f"Duplicate YouTube video ID: {video_id}")
        seen_ids.add(video_id)
        if not isinstance(video["title"], str) or not video["title"].strip():
            raise VideoUpdateError("Published video snapshot contains an invalid title")
        for field in ("publishedAt", "updatedAt"):
            if not isinstance(video[field], str):
                raise VideoUpdateError(f"Published video snapshot has an invalid {field} timestamp")
            parse_timestamp(video[field], field)
        if video["url"] != f"https://www.youtube.com/watch?v={video_id}":
            raise VideoUpdateError("Published video snapshot contains an invalid watch URL")
        if video["thumbnail"] != f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg":
            raise VideoUpdateError("Published video snapshot contains an invalid thumbnail URL")
    return snapshot


def read_published_snapshot():
    query = urlencode({"cacheBust": datetime.now(timezone.utc).timestamp()})
    requested_url = f"{PUBLISHED_URL}?{query}"
    try:
        request = Request(requested_url, headers={"User-Agent": "FCSSA-VideoUpdater/1.0", "Accept": "application/json", "Cache-Control": "no-cache"})
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if response.geturl() != requested_url:
                raise VideoUpdateError("Published video snapshot redirected to an unexpected URL")
            return response.read(MAX_FEED_BYTES + 1)
    except HTTPError as error:
        raise VideoUpdateError(f"Published video snapshot download failed: HTTP {error.code}") from error
    except (TimeoutError, socket.timeout) as error:
        raise VideoUpdateError(f"Published video snapshot download timed out after {TIMEOUT_SECONDS} seconds") from error
    except URLError as error:
        raise VideoUpdateError("Published video snapshot connection failed") from error
    except OSError as error:
        raise VideoUpdateError("Published video snapshot download failed") from error


def read_local_snapshot(output):
    try:
        return validate_snapshot(output.read_bytes())
    except (OSError, VideoUpdateError):
        return None


def update_videos(output: Path = DEFAULT_OUTPUT, *, feed_file=None, restore_published=False):
    output = Path(output).resolve()
    if output.suffix.lower() != ".json":
        raise VideoUpdateError("Video output must be a .json file")
    if feed_file is not None and output == Path(feed_file).resolve():
        raise VideoUpdateError("Video output must not overwrite the feed file")
    if feed_file is not None and restore_published:
        raise VideoUpdateError("A feed file and published snapshot restore cannot be used together")
    if restore_published:
        records = validate_snapshot(read_published_snapshot())
        current = read_local_snapshot(output)
        if current is not None and parse_timestamp(current["fetchedAt"], "fetchedAt") > parse_timestamp(records["fetchedAt"], "fetchedAt"):
            raise VideoUpdateError("Published video snapshot is older than the current local snapshot")
    else:
        records = parse_feed(read_feed(feed_file))
    payload = json.dumps(records, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary_path = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output.parent, suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output)
        temporary_path = None
    except OSError as error:
        raise VideoUpdateError(f"Cannot publish video data; previous output was preserved: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--feed-file", type=Path, help="Read an existing Atom feed instead of downloading")
    source.add_argument("--restore-published", action="store_true", help="Restore the currently published video snapshot")
    arguments = parser.parse_args()
    try:
        records = update_videos(arguments.output, feed_file=arguments.feed_file, restore_published=arguments.restore_published)
    except VideoUpdateError as error:
        parser.exit(1, f"Video update failed: {error}\n")
    print(f"Updated {len(records['videos'])} videos: {arguments.output}")


if __name__ == "__main__":
    main()
