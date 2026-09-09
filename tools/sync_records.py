"""Securely sync the private FC SSA XLSX file from Google Drive."""

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import build_opener, HTTPRedirectHandler, Request
import zipfile

from import_records import import_records


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "records.js"
DEFAULT_SOURCE_TITLE = "2026_FC쏘아 스탯.xlsx"
MAX_CREDENTIAL_BYTES = 64 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_XLSX_BYTES = 20 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_XLSX_ENTRIES = 10_000
MAX_RECORDS_BYTES = 20 * 1024 * 1024
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3
FILE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")
HEX_32_PATTERN = re.compile(r"[0-9a-fA-F]{32}\Z")
HEX_64_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
RECORDS_PREFIX = b"window.FCSA_RECORDS = "
RECORDS_SUFFIX = b";"
METADATA_FIELDS = "mimeType,modifiedTime,version,size,sha256Checksum,md5Checksum,trashed,capabilities(canDownload)"


class RecordSyncError(ValueError):
    """A rejected remote file leaves the previous records output untouched."""


@dataclass(frozen=True)
class DriveMetadata:
    mime_type: str
    modified_time: str
    version: str
    size: int
    sha256: str | None
    md5: str | None

    def fingerprint(self):
        return (self.version, self.modified_time, self.size, self.sha256, self.md5)


@dataclass(frozen=True)
class SyncResult:
    changed: bool
    player_count: int
    date_count: int
    quality_note_count: int


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def validate_file_id(value):
    if not isinstance(value, str) or not FILE_ID_PATTERN.fullmatch(value):
        raise RecordSyncError("FCSA_DRIVE_FILE_ID is missing or invalid")
    return value


def _service_account_info(serialized):
    if not isinstance(serialized, str) or not serialized or len(serialized.encode("utf-8")) > MAX_CREDENTIAL_BYTES:
        raise RecordSyncError("FCSA_GOOGLE_SERVICE_ACCOUNT_JSON is missing or invalid")
    try:
        info = json.loads(serialized)
    except (json.JSONDecodeError, UnicodeError):
        raise RecordSyncError("FCSA_GOOGLE_SERVICE_ACCOUNT_JSON is missing or invalid") from None
    if not isinstance(info, dict):
        raise RecordSyncError("FCSA_GOOGLE_SERVICE_ACCOUNT_JSON is missing or invalid")
    if info.get("type") != "service_account" or info.get("token_uri") != TOKEN_URI or info.get("universe_domain", "googleapis.com") != "googleapis.com":
        raise RecordSyncError("Google service account configuration is not approved")
    return info


def get_access_token(serialized, *, credentials_factory=None, request_factory=None):
    info = _service_account_info(serialized)
    try:
        if credentials_factory is None or request_factory is None:
            from google.auth.transport.requests import Request as GoogleAuthRequest
            from google.oauth2.service_account import Credentials

            credentials_factory = Credentials.from_service_account_info
            request_factory = GoogleAuthRequest
        credentials = credentials_factory(info, scopes=[DRIVE_SCOPE])
        transport = request_factory()

        def bounded_request(*args, **kwargs):
            kwargs["timeout"] = TIMEOUT_SECONDS
            return transport(*args, **kwargs)

        credentials.refresh(bounded_request)
        token = credentials.token
    except Exception:
        raise RecordSyncError("Google service account authentication failed") from None
    if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
        raise RecordSyncError("Google service account authentication failed")
    return token


def _drive_url(file_id, *, media):
    validated_id = validate_file_id(file_id)
    base = f"https://www.googleapis.com/drive/v3/files/{quote(validated_id, safe='')}"
    if media:
        return f"{base}?alt=media"
    return f"{base}?fields={quote(METADATA_FIELDS, safe=',()')}"


def _response_bytes(response, limit):
    content_length = response.headers.get("Content-Length") if response.headers is not None else None
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            raise RecordSyncError("Google Drive returned an invalid response") from None
        if declared_length < 0 or declared_length > limit:
            raise RecordSyncError("Google Drive response exceeds the size limit")
    content = response.read(limit + 1)
    if len(content) > limit:
        raise RecordSyncError("Google Drive response exceeds the size limit")
    return content


def drive_get(file_id, access_token, *, media=False, opener=None, sleep=time.sleep):
    if not isinstance(access_token, str) or not access_token or "\r" in access_token or "\n" in access_token:
        raise RecordSyncError("Google Drive authorization is invalid")
    url = _drive_url(file_id, media=media)
    request = Request(
        url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": DRIVE_MIME_TYPE if media else "application/json"},
        method="GET",
    )
    client = opener if opener is not None else build_opener(NoRedirectHandler())
    limit = MAX_XLSX_BYTES if media else MAX_METADATA_BYTES
    for attempt in range(MAX_ATTEMPTS):
        try:
            with client.open(request, timeout=TIMEOUT_SECONDS) as response:
                if response.geturl() != url:
                    raise RecordSyncError("Google Drive redirected the request")
                return _response_bytes(response, limit)
        except HTTPError as error:
            if (error.code == 429 or 500 <= error.code <= 599) and attempt + 1 < MAX_ATTEMPTS:
                sleep(2**attempt)
                continue
            if 300 <= error.code <= 399:
                raise RecordSyncError("Google Drive redirected the request") from None
            raise RecordSyncError(f"Google Drive request failed with HTTP {error.code}") from None
        except (TimeoutError, socket.timeout, URLError, ConnectionError):
            if attempt + 1 < MAX_ATTEMPTS:
                sleep(2**attempt)
                continue
            raise RecordSyncError("Google Drive request failed after retries") from None
        except OSError:
            if attempt + 1 < MAX_ATTEMPTS:
                sleep(2**attempt)
                continue
            raise RecordSyncError("Google Drive request failed after retries") from None
    raise RecordSyncError("Google Drive request failed after retries")


def parse_metadata(content):
    if len(content) > MAX_METADATA_BYTES:
        raise RecordSyncError("Google Drive metadata exceeds the size limit")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise RecordSyncError("Google Drive returned invalid metadata") from None
    if not isinstance(value, dict):
        raise RecordSyncError("Google Drive returned invalid metadata")
    capabilities = value.get("capabilities")
    if value.get("mimeType") != DRIVE_MIME_TYPE:
        raise RecordSyncError("Google Drive source is not an XLSX file")
    if value.get("trashed") is not False or not isinstance(capabilities, dict) or capabilities.get("canDownload") is not True:
        raise RecordSyncError("Google Drive source is unavailable for download")
    version = value.get("version")
    modified_time = value.get("modifiedTime")
    raw_size = value.get("size")
    if not isinstance(version, str) or not version.isdecimal() or not isinstance(modified_time, str):
        raise RecordSyncError("Google Drive returned incomplete metadata")
    try:
        parsed_time = datetime.fromisoformat(modified_time.replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            raise ValueError
        size = int(raw_size)
    except (TypeError, ValueError, OverflowError):
        raise RecordSyncError("Google Drive returned invalid metadata") from None
    if size <= 0 or size > MAX_XLSX_BYTES or isinstance(raw_size, bool):
        raise RecordSyncError("Google Drive XLSX size is invalid")
    sha256 = value.get("sha256Checksum")
    md5 = value.get("md5Checksum")
    if sha256 is not None and (not isinstance(sha256, str) or not HEX_64_PATTERN.fullmatch(sha256)):
        raise RecordSyncError("Google Drive returned an invalid SHA-256 checksum")
    if md5 is not None and (not isinstance(md5, str) or not HEX_32_PATTERN.fullmatch(md5)):
        raise RecordSyncError("Google Drive returned an invalid MD5 checksum")
    return DriveMetadata(DRIVE_MIME_TYPE, modified_time, version, size, sha256.lower() if sha256 else None, md5.lower() if md5 else None)


def fetch_metadata(file_id, access_token, *, opener=None, sleep=time.sleep):
    return parse_metadata(drive_get(file_id, access_token, opener=opener, sleep=sleep))


def download_xlsx(file_id, access_token, *, opener=None, sleep=time.sleep):
    return drive_get(file_id, access_token, media=True, opener=opener, sleep=sleep)


def validate_download(content, metadata):
    if len(content) != metadata.size:
        raise RecordSyncError("Downloaded XLSX size does not match Google Drive metadata")
    sha256 = hashlib.sha256(content).hexdigest()
    if metadata.sha256 is not None and sha256 != metadata.sha256:
        raise RecordSyncError("Downloaded XLSX failed SHA-256 verification")
    if metadata.md5 is not None and hashlib.md5(content, usedforsecurity=False).hexdigest() != metadata.md5:
        raise RecordSyncError("Downloaded XLSX failed MD5 verification")
    return sha256


def validate_xlsx_structure(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_XLSX_ENTRIES:
                raise RecordSyncError("XLSX archive structure is invalid")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise RecordSyncError("XLSX archive structure is invalid")
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise RecordSyncError("XLSX archive structure is invalid")
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise RecordSyncError("Encrypted XLSX files are not supported")
            if sum(entry.file_size for entry in entries) > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise RecordSyncError("XLSX uncompressed data exceeds the size limit")
            if archive.testzip() is not None:
                raise RecordSyncError("XLSX archive checksum verification failed")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError):
        raise RecordSyncError("XLSX archive structure is invalid") from None


def read_existing_records(output):
    path = Path(output)
    if not path.exists():
        return None
    try:
        with path.open("rb") as source:
            content = source.read(MAX_RECORDS_BYTES + 1)
    except OSError:
        raise RecordSyncError("Existing records data cannot be read") from None
    if len(content) > MAX_RECORDS_BYTES or not content.startswith(RECORDS_PREFIX):
        raise RecordSyncError("Existing records data is invalid")
    payload = content[len(RECORDS_PREFIX):].strip()
    if not payload.endswith(RECORDS_SUFFIX):
        raise RecordSyncError("Existing records data is invalid")
    try:
        records = json.loads(payload[:-1].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise RecordSyncError("Existing records data is invalid") from None
    if not isinstance(records, dict) or not isinstance(records.get("source"), dict):
        raise RecordSyncError("Existing records data is invalid")
    return records


def _existing_sha256(records):
    value = records.get("source", {}).get("sha256") if records else None
    return value.lower() if isinstance(value, str) and HEX_64_PATTERN.fullmatch(value) else None


def _source_title(records):
    value = records.get("source", {}).get("title") if records else None
    return value if isinstance(value, str) and value.strip() else DEFAULT_SOURCE_TITLE


def _result(changed, records):
    return SyncResult(
        changed,
        len(records.get("players", [])) if records else 0,
        len(records.get("dates", [])) if records else 0,
        len(records.get("qualityNotes", [])) if records else 0,
    )


def _publish(candidate, output):
    output = Path(output).resolve()
    temporary_path = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", dir=output.parent, suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            with Path(candidate).open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output)
        temporary_path = None
    except OSError:
        raise RecordSyncError("Records publication failed; previous output was preserved") from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def sync_records(output=DEFAULT_OUTPUT, *, environment=None, opener=None, sleep=time.sleep):
    environment = os.environ if environment is None else environment
    file_id = validate_file_id(environment.get("FCSA_DRIVE_FILE_ID"))
    credentials_json = environment.get("FCSA_GOOGLE_SERVICE_ACCOUNT_JSON")
    access_token = get_access_token(credentials_json)
    existing = read_existing_records(output)
    existing_sha256 = _existing_sha256(existing)
    before = fetch_metadata(file_id, access_token, opener=opener, sleep=sleep)
    if before.sha256 is not None and before.sha256 == existing_sha256:
        return _result(False, existing)

    content = download_xlsx(file_id, access_token, opener=opener, sleep=sleep)
    downloaded_sha256 = validate_download(content, before)
    validate_xlsx_structure(content)
    after = fetch_metadata(file_id, access_token, opener=opener, sleep=sleep)
    if before.fingerprint() != after.fingerprint():
        raise RecordSyncError("Google Drive source changed during synchronization")
    if downloaded_sha256 == existing_sha256:
        return _result(False, existing)

    with tempfile.TemporaryDirectory(prefix="fcsa-record-sync-") as directory:
        temporary_directory = Path(directory)
        source_path = temporary_directory / "source.xlsx"
        candidate_path = temporary_directory / "records.js"
        try:
            source_path.write_bytes(content)
            records = import_records(source_path, candidate_path, None, before.modified_time, _source_title(existing))
        except Exception:
            raise RecordSyncError("XLSX validation or import failed; previous output was preserved") from None
        if records.get("source", {}).get("sha256") != downloaded_sha256:
            raise RecordSyncError("Imported records failed source verification")
        _publish(candidate_path, output)
    return _result(True, records)


def write_github_output(changed, environment=None):
    environment = os.environ if environment is None else environment
    destination = environment.get("GITHUB_OUTPUT")
    if not destination:
        return
    try:
        with Path(destination).open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")
    except OSError:
        raise RecordSyncError("GitHub Actions output could not be written") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    try:
        result = sync_records(arguments.output)
        write_github_output(result.changed)
    except RecordSyncError as error:
        parser.exit(1, f"Record sync failed: {error}\n")
    except Exception:
        parser.exit(1, "Record sync failed: unexpected internal error\n")
    action = "Updated" if result.changed else "Unchanged"
    print(f"{action}: {result.player_count} players, {result.date_count} dates, {result.quality_note_count} quality notes.")


if __name__ == "__main__":
    main()
