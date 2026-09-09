"""Offline tests for the private Google Drive record synchronizer."""

from collections import deque
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import openpyxl

import sync_records as sync


ENVIRONMENT = {
    "FCSA_DRIVE_FILE_ID": "valid_drive_file_id_123",
    "FCSA_GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(
        {
            "type": "service_account",
            "token_uri": sync.TOKEN_URI,
            "universe_domain": "googleapis.com",
        }
    ),
}


def workbook_fixture(*, player_name="테스트 선수", wins=1, cached_formula=True):
    workbook = openpyxl.Workbook()
    roster = workbook.active
    roster.title = "26년 선수 기록"
    team = workbook.create_sheet("26년 팀 기록")
    attendance = workbook.create_sheet("26년 출장 기록")
    goals = workbook.create_sheet("26년 득점 기록")
    assists = workbook.create_sheet("26년 도움 기록")

    roster.append(["포지션", "등번호", "이름", "득점", "도움", "공격포인트", "출장횟수", "참석율"])
    roster.append(["FW", 9, player_name, 1 if cached_formula else "=1", 0, 1, 1, 1])

    team["A2"], team["B2"] = "전적", 1
    team["A3"], team["B3"] = "승리", wins
    team["A4"], team["B4"] = "무승부", 0
    team["A5"], team["B5"] = "패배", 1 - wins
    team["A6"], team["B6"] = "승률", wins
    team["A7"], team["B7"] = "득점", 1
    team["A8"], team["B8"] = "실점", 0
    for address, value in {"D1": "vs", "E1": "승", "F1": "무", "G1": "패", "H1": "승률"}.items():
        team[address] = value
    team["D2"], team["E2"], team["F2"], team["G2"], team["H2"] = "상대 FC", wins, 0, 1 - wins, wins

    match_date = datetime(2026, 1, 3)
    for sheet, event_value in ((attendance, "O"), (goals, 1), (assists, None)):
        sheet["A1"], sheet["B1"], sheet["C1"] = "포지션", "이름", match_date
        sheet["A2"], sheet["B2"], sheet["C2"] = "FW", player_name, event_value
        sheet["A3"], sheet["C3"] = "합계", 1 if sheet is not assists else 0

    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def metadata_for(content, *, version="1", sha256=True, md5=True):
    return sync.DriveMetadata(
        sync.DRIVE_MIME_TYPE,
        "2026-09-09T01:02:03.000Z",
        version,
        len(content),
        hashlib.sha256(content).hexdigest() if sha256 else None,
        hashlib.md5(content).hexdigest() if md5 else None,
    )


def write_records(path, sha256, *, title="승인된 제목.xlsx", imported_at="unchanged-time"):
    records = {
        "source": {"title": title, "sha256": sha256, "importedAt": imported_at},
        "players": [{"name": "기존 선수"}],
        "dates": [{}],
        "qualityNotes": [],
    }
    path.write_text(f"window.FCSA_RECORDS = {json.dumps(records, ensure_ascii=False)};\n", encoding="utf-8")
    return records


class FakeResponse:
    def __init__(self, url, content):
        self.url = url
        self.content = content
        self.headers = {"Content-Length": str(len(content))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.content[:limit]


class FakeOpener:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(request.full_url, self.responses.popleft())


class RecordSyncTests(unittest.TestCase):
    def run_sync(self, output, content, metadata=None):
        metadata = metadata or metadata_for(content)
        with (
            patch.object(sync, "get_access_token", return_value="access-token"),
            patch.object(sync, "fetch_metadata", side_effect=[metadata, metadata]),
            patch.object(sync, "download_xlsx", return_value=content),
        ):
            return sync.sync_records(output, environment=ENVIRONMENT, sleep=lambda _: None)

    def test_changed_workbook_updates_member_and_team_records(self):
        content = workbook_fixture(player_name="새 선수", wins=0)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "records.js"
            write_records(output, "0" * 64)
            result = self.run_sync(output, content)
            records = sync.read_existing_records(output)

        self.assertTrue(result.changed)
        self.assertEqual(records["players"][0]["name"], "새 선수")
        self.assertEqual(records["summary"]["losses"], 1)
        self.assertEqual(records["opponents"][0]["losses"], 1)
        self.assertEqual(records["source"]["title"], "승인된 제목.xlsx")
        self.assertEqual(records["source"]["sha256"], hashlib.sha256(content).hexdigest())

    def test_full_drive_transport_path_updates_then_short_circuits(self):
        content = workbook_fixture(player_name="전송 경로 선수")
        metadata = {
            "mimeType": sync.DRIVE_MIME_TYPE,
            "modifiedTime": "2026-09-09T01:02:03.000Z",
            "version": "42",
            "size": str(len(content)),
            "sha256Checksum": hashlib.sha256(content).hexdigest(),
            "md5Checksum": hashlib.md5(content).hexdigest(),
            "trashed": False,
            "capabilities": {"canDownload": True},
        }
        metadata_json = json.dumps(metadata).encode("utf-8")
        opener = FakeOpener([metadata_json, content, metadata_json, metadata_json])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "records.js"
            with patch.object(sync, "get_access_token", return_value="access-token"):
                changed = sync.sync_records(output, environment=ENVIRONMENT, opener=opener, sleep=lambda _: None)
                original = output.read_bytes()
                unchanged = sync.sync_records(output, environment=ENVIRONMENT, opener=opener, sleep=lambda _: None)
            records = sync.read_existing_records(output)
            self.assertEqual(output.read_bytes(), original)

        self.assertTrue(changed.changed)
        self.assertFalse(unchanged.changed)
        self.assertEqual(records["players"][0]["name"], "전송 경로 선수")
        self.assertEqual(len(opener.requests), 4)
        self.assertTrue(all(request.get_method() == "GET" for request, _timeout in opener.requests))
        self.assertTrue(all(request.get_header("Authorization") == "Bearer access-token" for request, _timeout in opener.requests))
        self.assertTrue(all(timeout == sync.TIMEOUT_SECONDS for _request, timeout in opener.requests))

    def test_metadata_sha_short_circuits_without_download_or_import(self):
        content = workbook_fixture()
        source_hash = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "records.js"
            records = write_records(output, source_hash)
            original = output.read_bytes()
            with (
                patch.object(sync, "get_access_token", return_value="access-token"),
                patch.object(sync, "fetch_metadata", return_value=metadata_for(content)) as metadata_mock,
                patch.object(sync, "download_xlsx") as download_mock,
                patch.object(sync, "import_records") as import_mock,
            ):
                result = sync.sync_records(output, environment=ENVIRONMENT, sleep=lambda _: None)
            self.assertEqual(output.read_bytes(), original)
        self.assertFalse(result.changed)
        self.assertEqual(result.player_count, len(records["players"]))
        metadata_mock.assert_called_once()
        download_mock.assert_not_called()
        import_mock.assert_not_called()

    def test_md5_only_download_with_same_sha_does_not_reimport(self):
        content = workbook_fixture()
        source_hash = hashlib.sha256(content).hexdigest()
        metadata = metadata_for(content, sha256=False)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "records.js"
            write_records(output, source_hash)
            original = output.read_bytes()
            with (
                patch.object(sync, "get_access_token", return_value="access-token"),
                patch.object(sync, "fetch_metadata", side_effect=[metadata, metadata]),
                patch.object(sync, "download_xlsx", return_value=content),
                patch.object(sync, "import_records") as import_mock,
            ):
                result = sync.sync_records(output, environment=ENVIRONMENT, sleep=lambda _: None)
            self.assertEqual(output.read_bytes(), original)
        self.assertFalse(result.changed)
        import_mock.assert_not_called()

    def test_version_change_during_download_preserves_existing_output(self):
        content = workbook_fixture()
        before = metadata_for(content, version="7")
        after = metadata_for(content, version="8")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "records.js"
            write_records(output, "0" * 64)
            original = output.read_bytes()
            with (
                patch.object(sync, "get_access_token", return_value="access-token"),
                patch.object(sync, "fetch_metadata", side_effect=[before, after]),
                patch.object(sync, "download_xlsx", return_value=content),
            ):
                with self.assertRaisesRegex(sync.RecordSyncError, "changed during"):
                    sync.sync_records(output, environment=ENVIRONMENT, sleep=lambda _: None)
            self.assertEqual(output.read_bytes(), original)

    def test_bad_checksum_and_bad_archive_preserve_existing_output(self):
        valid_content = workbook_fixture()
        cases = [
            (valid_content, sync.DriveMetadata(sync.DRIVE_MIME_TYPE, "2026-09-09T01:02:03Z", "1", len(valid_content), "0" * 64, None), "SHA-256"),
            (b"not an xlsx archive", None, "archive structure"),
        ]
        for content, metadata, message in cases:
            metadata = metadata or metadata_for(content)
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "records.js"
                write_records(output, "1" * 64)
                original = output.read_bytes()
                with (
                    patch.object(sync, "get_access_token", return_value="access-token"),
                    patch.object(sync, "fetch_metadata", return_value=metadata),
                    patch.object(sync, "download_xlsx", return_value=content),
                ):
                    with self.assertRaisesRegex(sync.RecordSyncError, message):
                        sync.sync_records(output, environment=ENVIRONMENT, sleep=lambda _: None)
                self.assertEqual(output.read_bytes(), original)

    def test_formula_without_cached_value_is_rejected_and_preserves_output(self):
        content = workbook_fixture(cached_formula=False)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "records.js"
            write_records(output, "2" * 64)
            original = output.read_bytes()
            with self.assertRaisesRegex(sync.RecordSyncError, "validation or import failed"):
                self.run_sync(output, content)
            self.assertEqual(output.read_bytes(), original)

    def test_authentication_errors_are_sanitized(self):
        class FailingCredentials:
            def refresh(self, request):
                raise RuntimeError("secret@example.com private-key-material")

        def credentials_factory(info, scopes):
            self.assertEqual(scopes, [sync.DRIVE_SCOPE])
            return FailingCredentials()

        with self.assertRaises(sync.RecordSyncError) as caught:
            sync.get_access_token(
                ENVIRONMENT["FCSA_GOOGLE_SERVICE_ACCOUNT_JSON"],
                credentials_factory=credentials_factory,
                request_factory=object,
            )
        self.assertEqual(str(caught.exception), "Google service account authentication failed")

    def test_authentication_refresh_uses_bounded_timeout(self):
        transport = Mock(return_value=object())

        class Credentials:
            token = "access-token"

            def refresh(self, request):
                request("https://oauth2.googleapis.com/token", method="POST", timeout=999)

        token = sync.get_access_token(
            ENVIRONMENT["FCSA_GOOGLE_SERVICE_ACCOUNT_JSON"],
            credentials_factory=lambda _info, scopes: Credentials(),
            request_factory=lambda: transport,
        )
        self.assertEqual(token, "access-token")
        self.assertEqual(transport.call_args.kwargs["timeout"], sync.TIMEOUT_SECONDS)

    def test_redirects_and_network_details_are_not_exposed(self):
        private_id = ENVIRONMENT["FCSA_DRIVE_FILE_ID"]
        token = "private-access-token"
        secret_detail = "secret@example.com/path"
        cases = [
            (HTTPError("https://private.example/secret", 302, secret_detail, {}, None), "redirected", 1),
            (URLError(secret_detail), "failed after retries", sync.MAX_ATTEMPTS),
            (HTTPError("https://private.example/secret", 403, secret_detail, {}, None), "HTTP 403", 1),
        ]
        for failure, expected, attempts in cases:
            opener = Mock()
            opener.open.side_effect = failure
            sleeps = []
            try:
                with self.subTest(expected=expected), self.assertRaises(sync.RecordSyncError) as caught:
                    sync.drive_get(private_id, token, opener=opener, sleep=sleeps.append)
                message = str(caught.exception)
                self.assertIn(expected, message)
                self.assertNotIn(private_id, message)
                self.assertNotIn(token, message)
                self.assertNotIn(secret_detail, message)
                self.assertEqual(opener.open.call_count, attempts)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(request.get_header("Authorization"), f"Bearer {token}")
                self.assertEqual(opener.open.call_args.kwargs["timeout"], sync.TIMEOUT_SECONDS)
            finally:
                if isinstance(failure, HTTPError):
                    failure.close()

    def test_rejects_unapproved_credentials_and_writes_github_output(self):
        approved = {"type": "service_account", "token_uri": sync.TOKEN_URI, "universe_domain": "googleapis.com"}
        for field, value in (("type", "authorized_user"), ("token_uri", "https://example.invalid/token"), ("universe_domain", "example.invalid")):
            bad_info = json.dumps({**approved, field: value})
            with self.subTest(field=field), self.assertRaisesRegex(sync.RecordSyncError, "not approved"):
                sync.get_access_token(bad_info, credentials_factory=lambda *_args, **_kwargs: None, request_factory=object)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "github-output"
            sync.write_github_output(True, {"GITHUB_OUTPUT": str(destination)})
            sync.write_github_output(False, {"GITHUB_OUTPUT": str(destination)})
            self.assertEqual(destination.read_text(encoding="utf-8"), "changed=true\nchanged=false\n")

    def test_metadata_rejects_wrong_mime_type_and_disabled_download(self):
        base = {
            "mimeType": sync.DRIVE_MIME_TYPE,
            "modifiedTime": "2026-09-09T01:02:03Z",
            "version": "1",
            "size": "1024",
            "trashed": False,
            "capabilities": {"canDownload": True},
        }
        cases = [
            ({**base, "mimeType": "application/vnd.google-apps.spreadsheet"}, "not an XLSX"),
            ({**base, "capabilities": {"canDownload": False}}, "unavailable for download"),
        ]
        for metadata, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(sync.RecordSyncError, message):
                sync.parse_metadata(json.dumps(metadata).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
