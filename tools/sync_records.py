"""Sync the publicly shared FC SSA XLSX file from Google Drive."""

import argparse
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
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
from urllib.parse import quote, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
import zipfile

from import_records import import_records


DRIVE_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ALLOWED_CONTENT_TYPES = {DRIVE_MIME_TYPE, "application/octet-stream"}
ALLOWED_DOWNLOAD_HOSTS = {"drive.google.com", "drive.usercontent.google.com"}
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "records.js"
DEFAULT_SOURCE_TITLE = "2026_FC쏘아 스탯.xlsx"
MAX_XLSX_BYTES = 20 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_XLSX_ENTRIES = 10_000
MAX_RECORDS_BYTES = 20 * 1024 * 1024
TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 3
FILE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")
HEX_64_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
RECORDS_PREFIX = b"window.FCSA_RECORDS = "
RECORDS_SUFFIX = b";"


class RecordSyncError(ValueError):
    """A rejected remote file leaves the previous records output untouched."""


@dataclass(frozen=True)
class DownloadedFile:
    content: bytes
    modified_at: str | None


@dataclass(frozen=True)
class SyncResult:
    changed: bool
    player_count: int
    date_count: int
    quality_note_count: int


def _validate_download_url(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise RecordSyncError("Google Drive redirected to an unapproved location") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RecordSyncError("Google Drive redirected to an unapproved location")
    return url


class SafeRedirectHandler(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        _validate_download_url(new_url)
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def validate_file_id(value):
    if not isinstance(value, str) or not FILE_ID_PATTERN.fullmatch(value):
        raise RecordSyncError("FCSA_DRIVE_FILE_ID is missing or invalid")
    return value


def _download_url(file_id):
    validated_id = validate_file_id(file_id)
    return f"https://drive.google.com/uc?export=download&id={quote(validated_id, safe='')}"


def _response_bytes(response):
    content_length = response.headers.get("Content-Length") if response.headers is not None else None
    declared_length = None
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError, OverflowError):
            raise RecordSyncError("Google Drive returned an invalid response") from None
        if declared_length <= 0 or declared_length > MAX_XLSX_BYTES:
            raise RecordSyncError("Google Drive response exceeds the size limit")
    content = response.read(MAX_XLSX_BYTES + 1)
    if not content:
        raise RecordSyncError("Google Drive returned an empty response")
    if len(content) > MAX_XLSX_BYTES:
        raise RecordSyncError("Google Drive response exceeds the size limit")
    if declared_length is not None and len(content) != declared_length:
        raise RecordSyncError("Google Drive returned an incomplete response")
    return content


def _content_type(headers):
    value = headers.get("Content-Type") if headers is not None else None
    return value.split(";", 1)[0].strip().lower() if isinstance(value, str) else None


def _modified_at(headers):
    value = headers.get("Last-Modified") if headers is not None else None
    if not isinstance(value, str):
        return None
    try:
        modified = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if modified.tzinfo is None:
        return None
    return modified.astimezone(timezone.utc).isoformat()


def download_xlsx(file_id, *, opener=None, sleep=time.sleep):
    url = _download_url(file_id)
    request = Request(
        url,
        headers={
            "Accept": f"{DRIVE_MIME_TYPE}, application/octet-stream",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        method="GET",
    )
    client = opener if opener is not None else build_opener(SafeRedirectHandler())
    for attempt in range(MAX_ATTEMPTS):
        try:
            with client.open(request, timeout=TIMEOUT_SECONDS) as response:
                _validate_download_url(response.geturl())
                status = getattr(response, "status", None)
                if status is None and hasattr(response, "getcode"):
                    status = response.getcode()
                if status is not None and status != 200:
                    raise RecordSyncError("Google Drive did not return a complete download")
                if _content_type(response.headers) not in ALLOWED_CONTENT_TYPES:
                    raise RecordSyncError("Google Drive did not return an XLSX download")
                return DownloadedFile(_response_bytes(response), _modified_at(response.headers))
        except HTTPError as error:
            if (error.code == 429 or 500 <= error.code <= 599) and attempt + 1 < MAX_ATTEMPTS:
                sleep(2**attempt)
                continue
            if 300 <= error.code <= 399:
                raise RecordSyncError("Google Drive redirected to an unapproved location") from None
            raise RecordSyncError(f"Google Drive request failed with HTTP {error.code}") from None
        except (TimeoutError, socket.timeout, URLError, ConnectionError, OSError):
            if attempt + 1 < MAX_ATTEMPTS:
                sleep(2**attempt)
                continue
            raise RecordSyncError("Google Drive request failed after retries") from None
    raise RecordSyncError("Google Drive request failed after retries")


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
    existing = read_existing_records(output)
    downloaded = download_xlsx(file_id, opener=opener, sleep=sleep)
    downloaded_sha256 = hashlib.sha256(downloaded.content).hexdigest()
    validate_xlsx_structure(downloaded.content)
    if downloaded_sha256 == _existing_sha256(existing):
        return _result(False, existing)

    with tempfile.TemporaryDirectory(prefix="fcsa-record-sync-") as directory:
        temporary_directory = Path(directory)
        source_path = temporary_directory / "source.xlsx"
        candidate_path = temporary_directory / "records.js"
        try:
            source_path.write_bytes(downloaded.content)
            records = import_records(
                source_path,
                candidate_path,
                None,
                downloaded.modified_at,
                _source_title(existing),
            )
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
