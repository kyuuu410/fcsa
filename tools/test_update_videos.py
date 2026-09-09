"""Offline tests for feed validation and source-preserving publication."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import update_videos as updater


def fixture(count=5):
    entries = []
    for index in range(count):
        video_id = f"video{index:06d}"
        entries.append(f"""<entry><id>yt:video:{video_id}</id><yt:videoId>{video_id}</yt:videoId>
<yt:channelId>{updater.CHANNEL_ID}</yt:channelId><title>영상 {index}</title>
<published>2026-09-0{index + 1}T10:00:00Z</published><updated>2026-09-06T11:00:00+00:00</updated>
<link rel="alternate" href="https://www.youtube.com/watch?v={video_id}"/>
<media:group><media:thumbnail url="https://i1.ytimg.com/vi/{video_id}/hqdefault.jpg"/></media:group></entry>""")
    return (f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
<yt:channelId>{updater.CHANNEL_ID[2:]}</yt:channelId><title>FC쏘아</title>
<link rel="alternate" href="https://www.youtube.com/channel/{updater.CHANNEL_ID}"/>
{''.join(entries)}</feed>""").encode("utf-8")


def snapshot(fetched_at="2026-09-09T12:34:56.789+00:00"):
    result = updater.parse_feed(fixture(2))
    result["fetchedAt"] = fetched_at
    return result


class FakeResponse:
    def __init__(self, content, url=None):
        self.content = content
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.content[:limit]


class VideoUpdateTests(unittest.TestCase):
    def test_latest_four_sorted_by_publication(self):
        result = updater.parse_feed(fixture())
        self.assertEqual([video["id"] for video in result["videos"]], ["video000004", "video000003", "video000002", "video000001"])
        self.assertTrue(all(video["thumbnail"].startswith("https://i.ytimg.com/") for video in result["videos"]))
        self.assertTrue(result["fetchedAt"].endswith("Z"))

    def test_rejects_invalid_feeds(self):
        cases = {
            "empty": fixture(0),
            "malformed": b"<feed>",
            "oversized": b" " * (updater.MAX_FEED_BYTES + 1),
            "wrong root channel": fixture().replace(updater.CHANNEL_ID[2:].encode(), b"wrongchannel"),
            "wrong entry channel": fixture().replace(f"<yt:channelId>{updater.CHANNEL_ID}</yt:channelId>".encode(), b"<yt:channelId>wrong</yt:channelId>"),
            "duplicate ID": fixture().replace(b"video000004", b"video000003"),
            "invalid ID": fixture().replace(b"video000004", b"bad!id00004"),
            "bad date": fixture().replace(b"2026-09-01T10:00:00Z", b"2026-02-30T10:00:00Z"),
            "missing timezone": fixture().replace(b"2026-09-01T10:00:00Z", b"2026-09-01T10:00:00"),
            "external watch": fixture().replace(b"www.youtube.com/watch", b"evil.example/watch"),
            "external thumbnail": fixture().replace(b"i1.ytimg.com", b"i1.ytimg.com.evil.example"),
            "wrong thumbnail ID": fixture().replace(b"/vi/video000004/", b"/vi/video000003/"),
            "insecure thumbnail": fixture().replace(b"https://i1.ytimg.com", b"http://i1.ytimg.com"),
            "entity declaration": b'<!DOCTYPE feed [<!ENTITY x "x">]>' + fixture(),
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.assertRaises(updater.VideoUpdateError):
                updater.parse_feed(content)

    def test_invalid_feed_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "videos.json"
            output.write_bytes(b"existing output")
            with patch.object(updater, "read_feed", return_value=b"bad XML"):
                with self.assertRaises(updater.VideoUpdateError):
                    updater.update_videos(output)
            self.assertEqual(output.read_bytes(), b"existing output")

    def test_atomic_replace_failure_preserves_output_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "videos.json"
            output.write_bytes(b"existing output")
            with patch.object(updater, "read_feed", return_value=fixture()), patch.object(updater.os, "replace", side_effect=PermissionError("locked")):
                with self.assertRaisesRegex(updater.VideoUpdateError, "previous output was preserved"):
                    updater.update_videos(output)
            self.assertEqual(output.read_bytes(), b"existing output")
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_successful_publication_and_fixture_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            feed = Path(directory) / "feed.xml"
            original = fixture()
            feed.write_bytes(original)
            output = Path(directory) / "data" / "videos.json"
            result = updater.update_videos(output, feed_file=feed)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), result)
            self.assertEqual(feed.read_bytes(), original)

    def test_network_failures_are_clear_and_preserve_output(self):
        errors = [(TimeoutError(), "timed out"), (URLError("DNS failure"), "connection failed"), (HTTPError(updater.FEED_URL, 503, "Unavailable", {}, None), "HTTP 503")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "videos.json"
            output.write_bytes(b"existing output")
            for error, message in errors:
                with self.subTest(message=message), patch.object(updater, "urlopen", side_effect=error) as mocked:
                    with self.assertRaisesRegex(updater.VideoUpdateError, message):
                        updater.update_videos(output)
                    self.assertEqual(mocked.call_args.kwargs["timeout"], 20)
                    self.assertEqual(output.read_bytes(), b"existing output")

    def test_restore_published_preserves_original_fetched_at(self):
        published = snapshot()
        content = json.dumps(published, ensure_ascii=False).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "videos.json"

            def respond(request, timeout):
                self.assertEqual(timeout, updater.TIMEOUT_SECONDS)
                self.assertEqual(request.headers["Cache-control"], "no-cache")
                self.assertTrue(request.full_url.startswith(f"{updater.PUBLISHED_URL}?cacheBust="))
                return FakeResponse(content, request.full_url)

            with patch.object(updater, "urlopen", side_effect=respond):
                result = updater.update_videos(output, restore_published=True)
            self.assertEqual(result["fetchedAt"], "2026-09-09T12:34:56.789+00:00")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["fetchedAt"], result["fetchedAt"])

    def test_invalid_published_snapshots_preserve_existing_file(self):
        valid = snapshot()
        cases = {}
        wrong_channel = json.loads(json.dumps(valid))
        wrong_channel["channelId"] = "UCwrong000000000000000000"
        cases["wrong channel"] = wrong_channel
        wrong_url = json.loads(json.dumps(valid))
        wrong_url["videos"][0]["url"] = "https://www.youtube.com/watch?v=video000000&private=1"
        cases["wrong URL"] = wrong_url
        duplicate = json.loads(json.dumps(valid))
        duplicate["videos"][1] = json.loads(json.dumps(duplicate["videos"][0]))
        cases["duplicate ID"] = duplicate
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "videos.json"
            for name, candidate in {**cases, "malformed": None}.items():
                with self.subTest(name=name):
                    output.write_bytes(b"existing output")
                    content = b"{" if candidate is None else json.dumps(candidate).encode("utf-8")
                    with patch.object(updater, "read_published_snapshot", return_value=content):
                        with self.assertRaises(updater.VideoUpdateError):
                            updater.update_videos(output, restore_published=True)
                    self.assertEqual(output.read_bytes(), b"existing output")

    def test_restore_rejects_older_published_snapshot(self):
        current = snapshot("2026-09-10T00:00:00Z")
        published = snapshot("2026-09-09T23:59:59Z")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "videos.json"
            original = (json.dumps(current, ensure_ascii=False) + "\n").encode("utf-8")
            output.write_bytes(original)
            with patch.object(updater, "read_published_snapshot", return_value=json.dumps(published).encode("utf-8")):
                with self.assertRaisesRegex(updater.VideoUpdateError, "older than"):
                    updater.update_videos(output, restore_published=True)
            self.assertEqual(output.read_bytes(), original)

    def test_published_redirect_is_rejected(self):
        with patch.object(updater, "urlopen") as mocked:
            mocked.return_value = FakeResponse(json.dumps(snapshot()).encode("utf-8"), "https://example.com/videos.json")
            with self.assertRaisesRegex(updater.VideoUpdateError, "redirected"):
                updater.read_published_snapshot()

    def test_actual_saved_feed_when_available(self):
        source = Path(__file__).resolve().parents[3] / "work" / "fcsa" / "youtube-feed.xml"
        if not source.is_file():
            self.skipTest("Actual feed fixture is not present")
        result = updater.parse_feed(source.read_bytes())
        self.assertEqual(len(result["videos"]), 4)
        self.assertEqual(result["videos"][0]["id"], "Dy5-AI1nZ08")


if __name__ == "__main__":
    unittest.main()
