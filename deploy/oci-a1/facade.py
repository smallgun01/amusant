#!/usr/bin/env python3
"""Authenticated, fail-closed runtime facade between Relay and Hermes Runs."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from threading import Lock, Thread
from hermes_plugin.reprojection import (
    _load_committed_artifact, check_existing_reprojection,
    project_presentation, record_existing_projection,
    record_hook_projection, reproject_missing_hook,
)

STATE = Path(os.environ.get("AMUSANT_RESULT_STATE_DIR", "/var/lib/amusant-results"))
DB_PATH = STATE / "facade-ledger.sqlite3"
ARTIFACT_DIR = STATE / "artifacts"
def _read_secret(variable: str) -> str:
    path_value = os.environ.get(variable, "")
    try:
        if not path_value:
            raise ValueError
        fd = os.open(Path(path_value), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError
            if os.name == "posix":
                mode = stat.S_IMODE(metadata.st_mode)
                shared_group = (
                    metadata.st_uid == 0 and metadata.st_gid == 20000
                    and 20000 in os.getgroups() and mode == 0o440
                )
                owner_only = metadata.st_uid == os.geteuid() and mode == 0o400
                if not (shared_group or owner_only):
                    raise ValueError
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError
        value = raw.decode("utf-8").rstrip("\r\n")
        if len(value) < 12 or "\x00" in value:
            raise ValueError
        return value
    except (OSError, UnicodeDecodeError, ValueError):
        raise RuntimeError("facade secret configuration is invalid") from None


FACADE_KEY = _read_secret("AMUSANT_FACADE_API_KEY_FILE")
HERMES_KEY = _read_secret("AMUSANT_HERMES_API_KEY_FILE")
WRITER_KEY = _read_secret("AMUSANT_FACADE_WRITER_API_KEY_FILE")
if any(
    secrets.compare_digest(left, right)
    for index, left in enumerate((FACADE_KEY, HERMES_KEY, WRITER_KEY))
    for right in (FACADE_KEY, HERMES_KEY, WRITER_KEY)[index + 1:]
):
    raise RuntimeError("facade credentials must be distinct")
FACADE_BIND_IP = os.environ.get("AMUSANT_FACADE_BIND_IP", "")
WRITER_BIND_IP = os.environ.get("AMUSANT_FACADE_WRITER_BIND_IP", "")
try:
    ipaddress.IPv4Address(FACADE_BIND_IP)
    ipaddress.IPv4Address(WRITER_BIND_IP)
except ValueError:
    raise RuntimeError("facade listener address is invalid") from None
MAX_REQUEST_BYTES = 64 * 1024
MAX_REPORT_BYTES = 64 * 1024
MAX_WRITER_BODY_BYTES = MAX_REPORT_BYTES * 6 + 4096
MAX_UPSTREAM_BYTES = 1024 * 1024
MAX_ENVELOPE_BYTES = 3000
MAX_RELAY_BODY_BYTES = 4096
PRESENTATION_FAILURE = "The report is saved; its short presentation needs recovery."
RUN_ID_VALUE_RE = re.compile(r"run_[A-Za-z0-9_-]{1,250}")
RUN_RE = re.compile(r"^/v1/runs/(run_[A-Za-z0-9_-]{1,250})$")
STOP_RE = re.compile(r"^/v1/runs/(run_[A-Za-z0-9_-]{1,250})/stop$")
ARTIFACT_COMMIT_LOCK = Lock()


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _secure_sqlite_permissions():
    for path in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
        try:
            metadata = path.stat()
        except FileNotFoundError:
            continue
        if os.name == "posix" and metadata.st_uid != os.geteuid():
            raise RuntimeError("facade state file is not owned by the facade user")
        if os.name == "posix":
            os.chmod(path, 0o600)


def initialize():
    STATE.mkdir(parents=True, exist_ok=True)
    if STATE.is_symlink():
        raise RuntimeError("facade state directory must not be a symlink")
    if os.name == "posix" and STATE.stat().st_uid != os.geteuid():
        raise RuntimeError("facade state directory must be owned by the facade user")
    if os.name == "posix":
        os.chmod(STATE, 0o700)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    if ARTIFACT_DIR.is_symlink():
        raise RuntimeError("facade artifact directory must not be a symlink")
    if os.name == "posix":
        os.chmod(ARTIFACT_DIR, 0o700)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
            owner_scope TEXT NOT NULL,
            task_id TEXT NOT NULL,
            session_id TEXT NOT NULL UNIQUE,
            body_hash TEXT NOT NULL,
            run_id TEXT,
            submit_state TEXT NOT NULL,
            artifact_status TEXT NOT NULL DEFAULT 'pending',
            artifact_ref TEXT,
            presentation_error TEXT,
            raw_output_bytes INTEGER,
            terminal_seen_at REAL,
            upstream_post_count INTEGER NOT NULL DEFAULT 0,
            submit_upstream_status INTEGER,
            submit_upstream_error TEXT,
            remote_failure_type TEXT,
            remote_failure_category TEXT,
            terminal_upstream_status INTEGER,
            terminal_upstream_error TEXT,
            terminal_upstream_code TEXT,
            hermes_run_status TEXT,
            writer_call_count INTEGER NOT NULL DEFAULT 0,
            writer_result_code TEXT,
            writer_report_bytes INTEGER,
            created_at REAL NOT NULL,
            PRIMARY KEY(owner_scope, task_id)
        );
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_ref TEXT PRIMARY KEY,
            owner_scope TEXT NOT NULL,
            task_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_length INTEGER NOT NULL,
            committed_at REAL NOT NULL,
            UNIQUE(owner_scope, task_id, run_id)
        );
        CREATE INDEX IF NOT EXISTS artifacts_task_idx
            ON artifacts(owner_scope, task_id, run_id);
        CREATE TABLE IF NOT EXISTS output_hook_events (
            session_hash TEXT NOT NULL,
            outcome_code TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """)
    finally:
        conn.close()
    _secure_sqlite_permissions()


def compact_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def owner_scope_from_header(value: str | None) -> str | None:
    if not value or len(value) > 256 or not value.isascii():
        return None
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def request_by_session(session_id: str):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM requests WHERE session_id=?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def request_by_owner_task(owner_scope: str, task_id: str):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM requests WHERE owner_scope=? AND task_id=?",
            (owner_scope, task_id),
        ).fetchone()
    return dict(row) if row else None


def request_by_owner_run(owner_scope: str, run_id: str):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM requests WHERE owner_scope=? AND run_id=?",
            (owner_scope, run_id),
        ).fetchone()
    return dict(row) if row else None


def artifact_for(owner_scope: str, task_id: str, run_id: str):
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM artifacts
               WHERE owner_scope=? AND task_id=? AND run_id=?""",
            (owner_scope, task_id, run_id),
        ).fetchone()
    return dict(row) if row else None


def upstream_request(method: str, path: str, body=None, *, session_key: str | None = None, idem: str | None = None):
    headers = {
        "Authorization": "Bearer " + HERMES_KEY,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = compact_json(body)
        headers["Content-Type"] = "application/json"
    if session_key:
        headers["X-Hermes-Session-Key"] = session_key
    if idem:
        headers["Idempotency-Key"] = idem
    conn = http.client.HTTPConnection("hermes", 8642, timeout=15)
    try:
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        raw = response.read(MAX_UPSTREAM_BYTES + 1)
        if len(raw) > MAX_UPSTREAM_BYTES:
            return 502, None, "upstream_body_over_cap", len(raw)
        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, UnicodeDecodeError):
            return 502, None, "upstream_invalid_json", len(raw)
        if not isinstance(payload, dict):
            return 502, None, "upstream_invalid_shape", len(raw)
        return response.status, payload, None, len(raw)
    except Exception as exc:
        return 502, None, "upstream_" + type(exc).__name__, 0
    finally:
        conn.close()


def bounded_failure(run_id: str, code: str, artifact_status="unavailable"):
    return {
        "run_id": run_id,
        "status": "failed",
        "error": {"code": code},
        "artifact_status": artifact_status,
    }


def presentation_failure_envelope(artifact_ref: str):
    return {
        "schema_version": 1,
        "speak": PRESENTATION_FAILURE,
        "card": {"title": "Report saved", "lines": ["Short presentation needs recovery."]},
        "execution_status": "completed",
        "artifact_status": "committed",
        "presentation_status": "failed",
        "artifact_ref": artifact_ref,
    }


def validate_envelope(output: str, artifact: dict) -> tuple[bool, str]:
    try:
        if not isinstance(output, str):
            return False, "output_missing"
        encoded = output.encode("utf-8")
        if len(encoded) > MAX_ENVELOPE_BYTES:
            return False, "output_over_cap"
        value = json.loads(output)
    except UnicodeError:
        return False, "output_invalid_unicode"
    except json.JSONDecodeError:
        return False, "output_malformed_json"
    if not isinstance(value, dict):
        return False, "output_invalid_shape"
    expected = {
        "schema_version", "speak", "card",
        "execution_status", "artifact_status", "presentation_status", "artifact_ref",
    }
    if set(value) != expected:
        return False, "output_field_set"
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return False, "output_version"
    speak = value.get("speak")
    card = value.get("card")
    if not isinstance(speak, str) or not 1 <= len(speak) <= 240:
        return False, "speak_invalid"
    if not isinstance(card, dict) or set(card) != {"title", "lines"}:
        return False, "card_invalid"
    if not isinstance(card["title"], str) or not 1 <= len(card["title"]) <= 80:
        return False, "card_title_invalid"
    lines = card["lines"]
    if not isinstance(lines, list) or not 1 <= len(lines) <= 4:
        return False, "card_lines_invalid"
    if any(not isinstance(line, str) or not 1 <= len(line) <= 120 for line in lines):
        return False, "card_line_invalid"
    if value.get("execution_status") != "completed":
        return False, "execution_status_invalid"
    if value.get("artifact_status") != "committed":
        return False, "artifact_status_invalid"
    if value.get("presentation_status") != "ready":
        return False, "presentation_status_invalid"
    if value.get("artifact_ref") != artifact["artifact_ref"]:
        return False, "artifact_ref_mismatch"
    if (
        not artifact.get("owner_scope")
        or not artifact.get("run_id")
        or not artifact.get("task_id")
    ):
        return False, "artifact_binding_invalid"
    return True, "ready"


def valid_artifact_for_request(owner_scope: str, row: dict):
    artifact = artifact_for(owner_scope, row["task_id"], row["run_id"])
    if not artifact or artifact["session_id"] != row["session_id"]:
        return None
    if artifact["artifact_ref"] != row.get("artifact_ref"):
        return None
    return artifact


def _valid_writer_session(value):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 256
        and value.isascii()
        and value.startswith("amusant-session-")
        and all(ord(char) >= 33 and ord(char) <= 126 for char in value)
    )



WRITER_ACCEPTANCE_TIMEOUT_SECONDS = 1.5
WRITER_ACCEPTANCE_POLL_SECONDS = 0.025


def _accepted_writer_request(session_id):
    """Wait briefly for the one in-flight submit to publish its trusted Run ID."""
    deadline = time.monotonic() + WRITER_ACCEPTANCE_TIMEOUT_SECONDS
    while True:
        row = request_by_session(session_id)
        if not row:
            return None, "not_found"
        state = row.get("submit_state")
        if state == "accepted":
            run_id = row.get("run_id")
            if isinstance(run_id, str) and RUN_ID_VALUE_RE.fullmatch(run_id):
                return row, None
            return None, "invalid_run_id"
        if state != "submitting":
            return None, "not_accepted"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, "acceptance_timeout"
        time.sleep(min(WRITER_ACCEPTANCE_POLL_SECONDS, remaining))


def _writer_context_failure(reason):
    status = 503 if reason == "acceptance_timeout" else 404
    return status, {"error": {"code": "trusted_request_context_unavailable"}}


def _record_writer_result(session_id, code, report_bytes=0, *, artifact_status=None):
    allowed = {
        "committed", "artifact_write_failed", "artifact_conflict",
        "artifact_commit_failed", "artifact_integrity_invalid",
    }
    if code not in allowed:
        return
    try:
        with connect() as conn:
            conn.execute(
                """UPDATE requests
                   SET writer_call_count=writer_call_count+1,
                       writer_result_code=?, writer_report_bytes=?,
                       artifact_status=COALESCE(?, artifact_status)
                   WHERE session_id=? AND submit_state='accepted'""",
                (code, report_bytes, artifact_status, session_id),
            )
    except sqlite3.Error:
        pass


def commit_report(session_id, report):
    if not _valid_writer_session(session_id):
        return 400, {"error": {"code": "trusted_request_context_unavailable"}}
    if not isinstance(report, str) or not report.strip():
        return 400, {"error": {"code": "report_invalid"}}
    try:
        encoded = report.encode("utf-8")
    except UnicodeError:
        return 400, {"error": {"code": "report_invalid"}}
    if len(encoded) > MAX_REPORT_BYTES:
        return 413, {"error": {"code": "report_over_cap"}}
    row, context_error = _accepted_writer_request(session_id)
    if not row:
        return _writer_context_failure(context_error)
    digest = hashlib.sha256(encoded).hexdigest()
    with ARTIFACT_COMMIT_LOCK:
        existing, _status = _load_committed_artifact(row)
        if existing:
            if existing["sha256"] == digest:
                _record_writer_result(session_id, "committed", len(encoded))
                return 200, {"committed": True}
            _record_writer_result(session_id, "artifact_conflict", len(encoded))
            return 409, {"error": {"code": "artifact_conflict"}}
        if row.get("artifact_status") == "committed" or artifact_for(
            row["owner_scope"], row["task_id"], row["run_id"]
        ):
            _record_writer_result(session_id, "artifact_integrity_invalid", len(encoded))
            return 409, {"error": {"code": "artifact_integrity_invalid"}}

        artifact_ref = "art_" + secrets.token_urlsafe(24)
        target = ARTIFACT_DIR / (artifact_ref + ".report")
        temporary = ARTIFACT_DIR / ("." + artifact_ref + ".tmp")
        try:
            with open(temporary, "xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            if os.name == "posix":
                dir_fd = os.open(ARTIFACT_DIR, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)

            conn = connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute(
                    "SELECT * FROM requests WHERE session_id=?", (session_id,)
                ).fetchone()
                found = conn.execute(
                    """SELECT 1 FROM artifacts
                       WHERE owner_scope=? AND task_id=? AND run_id=?""",
                    (row["owner_scope"], row["task_id"], row["run_id"]),
                ).fetchone()
                if (
                    not current or current["submit_state"] != "accepted"
                    or current["owner_scope"] != row["owner_scope"]
                    or current["task_id"] != row["task_id"]
                    or current["run_id"] != row["run_id"]
                ):
                    conn.rollback()
                    target.unlink(missing_ok=True)
                    return 404, {"error": {"code": "trusted_request_context_unavailable"}}
                if found:
                    conn.rollback()
                    target.unlink(missing_ok=True)
                    latest = request_by_session(session_id)
                    prior, _ = _load_committed_artifact(latest) if latest else (None, None)
                    if prior and prior["sha256"] == digest:
                        _record_writer_result(session_id, "committed", len(encoded))
                        return 200, {"committed": True}
                    _record_writer_result(session_id, "artifact_conflict", len(encoded))
                    return 409, {"error": {"code": "artifact_conflict"}}
                conn.execute(
                    """INSERT INTO artifacts
                       (artifact_ref, owner_scope, task_id, session_id, run_id, path,
                        sha256, byte_length, committed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (artifact_ref, row["owner_scope"], row["task_id"], session_id,
                     row["run_id"], str(target), digest, len(encoded), time.time()),
                )
                conn.execute(
                    """UPDATE requests
                       SET artifact_status='committed', artifact_ref=?,
                           writer_call_count=writer_call_count+1,
                           writer_result_code='committed', writer_report_bytes=?
                       WHERE session_id=? AND submit_state='accepted'""",
                    (artifact_ref, len(encoded), session_id),
                )
                conn.commit()
            finally:
                conn.close()
            return 200, {"committed": True}
        except OSError:
            for path in (temporary, target):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            _record_writer_result(
                session_id, "artifact_write_failed", len(encoded), artifact_status="failed"
            )
            return 500, {"error": {"code": "artifact_write_failed"}}
        except sqlite3.Error:
            for path in (temporary, target):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            _record_writer_result(
                session_id, "artifact_commit_failed", len(encoded), artifact_status="failed"
            )
            return 500, {"error": {"code": "artifact_commit_failed"}}


def project_for_session(session_id, presentation):
    if not _valid_writer_session(session_id):
        return 400, {"error": {"code": "trusted_request_context_unavailable"}}
    if not isinstance(presentation, str) or len(presentation.encode("utf-8", errors="replace")) > MAX_ENVELOPE_BYTES:
        return 413, {"error": {"code": "presentation_invalid"}}
    row, context_error = _accepted_writer_request(session_id)
    if not row:
        return _writer_context_failure(context_error)
    artifact, artifact_status = _load_committed_artifact(row)
    if not artifact:
        code = artifact_status if artifact_status in {
            "artifact_missing", "artifact_integrity_invalid", "artifact_binding_invalid",
        } else "artifact_unavailable"
        return 409, {"error": {"code": code}}
    output, projection_status = project_presentation(
        presentation, artifact["artifact_ref"]
    )
    if not output:
        return 422, {"error": {"code": "presentation_invalid"}}
    record_status = record_hook_projection(
        presentation, output, session_id
    )
    if record_status not in {"recorded", "replayed"}:
        code = "presentation_conflict" if record_status == "conflict" else "artifact_unavailable"
        return 409, {"error": {"code": code}}
    return 200, {"output": output}


class Handler(BaseHTTPRequestHandler):
    server_version = "AmusantFacade/1"

    def log_message(self, _fmt, *_args):
        # Request paths, headers and bodies can carry private identifiers; never access-log them.
        return

    def send_json(self, status: int, value):
        data = compact_json(value)
        if len(data) > MAX_RELAY_BODY_BYTES:
            status = 502
            data = compact_json({"error": {"code": "response_over_cap"}})
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authenticated_identity(self):
        auth = self.headers.get("Authorization", "")
        if not secrets.compare_digest(auth, "Bearer " + FACADE_KEY):
            return None, None
        owner_scope = owner_scope_from_header(self.headers.get("X-Hermes-Session-Key"))
        return (owner_scope, self.headers.get("X-Hermes-Session-Key")) if owner_scope else (None, None)

    def read_json_body(self, max_bytes=MAX_REQUEST_BYTES):
        self.body_error = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.body_error = "invalid_length"
            return None
        if length <= 0:
            self.body_error = "invalid_length"
            return None
        if length > max_bytes:
            self.body_error = "body_over_cap"
            self.close_connection = True
            return None
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self.body_error = "invalid_json"
            return None
        return value if isinstance(value, dict) else None

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True})
            return
        owner_scope, _ = self.authenticated_identity()
        if not owner_scope:
            self.send_json(401, {"error": {"code": "unauthorized"}})
            return
        match = RUN_RE.fullmatch(self.path)
        if not match:
            self.send_json(404, {"error": {"code": "not_found"}})
            return
        run_id = match.group(1)
        row = request_by_owner_run(owner_scope, run_id)
        if not row:
            self.send_json(404, {"error": {"code": "not_found"}})
            return
        status, payload, upstream_error, _raw_bytes = upstream_request(
            "GET", "/v1/runs/" + quote(run_id, safe=""),
            session_key=self.headers.get("X-Hermes-Session-Key"),
        )
        if upstream_error or status >= 400:
            safe_error = upstream_error if isinstance(upstream_error, str) and re.fullmatch(r"upstream_[A-Za-z][A-Za-z0-9]{0,40}", upstream_error) else None
            raw_code = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(raw_code, dict):
                raw_code = raw_code.get("code") or raw_code.get("type")
            safe_code = raw_code if isinstance(raw_code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", raw_code) else None
            safe_status = status if type(status) is int and 100 <= status <= 599 else None
            with connect() as conn:
                conn.execute(
                    "UPDATE requests SET terminal_upstream_status=?, terminal_upstream_error=?, terminal_upstream_code=? WHERE owner_scope=? AND task_id=?",
                    (safe_status, safe_error, safe_code, owner_scope, row["task_id"]),
                )
            self.send_json(502, bounded_failure(run_id, "upstream_unavailable", row["artifact_status"]))
            return
        # The writer may commit while this upstream GET is in flight. Refresh
        # the trusted mapping before validating the terminal envelope.
        latest_row = request_by_owner_run(owner_scope, run_id)
        if latest_row:
            row = latest_row
        run_status = str(payload.get("status", "")).lower()
        with connect() as conn:
            conn.execute(
                "UPDATE requests SET hermes_run_status=? WHERE owner_scope=? AND task_id=?",
                (run_status, owner_scope, row["task_id"]),
            )
        if run_status not in {"completed", "failed", "cancelled", "interrupted", "expired", "queued", "pending", "running", "waiting_for_approval", "stopping"}:
            self.send_json(502, bounded_failure(run_id, "upstream_status_invalid", row["artifact_status"]))
            return
        if run_status in {"failed", "cancelled", "interrupted", "expired"}:
            raw_error = payload.get("error")
            error_text = ""
            error_type = None
            if isinstance(raw_error, str):
                error_text = raw_error
            elif isinstance(raw_error, dict):
                candidate = raw_error.get("type") or raw_error.get("code")
                if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,40}", candidate):
                    error_type = candidate
                detail = raw_error.get("message") or raw_error.get("detail")
                if isinstance(detail, str):
                    error_text = detail
            if error_type is None:
                match = re.match(r"^([A-Za-z][A-Za-z0-9_.-]{0,40})", error_text)
                candidate_type = match.group(1) if match else ""
                error_type = candidate_type if candidate_type.endswith(("Error", "Exception")) else None
            lowered_error = error_text.lower()
            if any(token in lowered_error for token in ("timeout", "timed out", "deadline")):
                error_category = "timeout"
            elif any(token in lowered_error for token in ("tool", "plugin")):
                error_category = "tool_or_plugin"
            elif any(token in lowered_error for token in ("provider", "model")):
                error_category = "provider"
            elif any(token in lowered_error for token in ("sqlite", "database", "storage", "filesystem", "file")):
                error_category = "persistence"
            elif any(token in lowered_error for token in ("memory", "oom", "resource")):
                error_category = "resource"
            elif error_type:
                error_category = "typed_failure"
            else:
                error_category = "other"
            with connect() as conn:
                conn.execute(
                    "UPDATE requests SET remote_failure_type=?, remote_failure_category=? WHERE owner_scope=? AND task_id=?",
                    (error_type, error_category, owner_scope, row["task_id"]),
                )
        if run_status not in {"completed", "failed", "cancelled", "interrupted", "expired"}:
            self.send_json(200, {"run_id": run_id, "status": run_status})
            return
        if run_status != "completed":
            artifact = valid_artifact_for_request(owner_scope, row)
            if artifact:
                safe = presentation_failure_envelope(artifact["artifact_ref"])
                safe["execution_status"] = run_status
                safe["presentation_status"] = "failed"
                self.send_json(200, {"run_id": run_id, "status": run_status, "output": compact_json(safe).decode("utf-8")})
            else:
                self.send_json(200, bounded_failure(run_id, "run_" + run_status, row["artifact_status"]))
            return

        output = payload.get("output")
        output_bytes = len(output.encode("utf-8")) if isinstance(output, str) else 0
        with connect() as conn:
            conn.execute(
                "UPDATE requests SET raw_output_bytes=?, terminal_seen_at=? WHERE owner_scope=? AND task_id=?",
                (output_bytes, time.time(), owner_scope, row["task_id"]),
            )
        artifact, artifact_error = _load_committed_artifact(row)
        if not artifact:
            if row.get("artifact_status") == "committed":
                artifact_status = "failed"
                error_code = artifact_error if artifact_error in {
                    "artifact_missing", "artifact_integrity_invalid",
                    "artifact_binding_invalid", "artifact_unavailable",
                } else "artifact_unavailable"
            elif row.get("artifact_status") == "failed":
                artifact_status = "failed"
                writer_code = row.get("writer_result_code")
                error_code = writer_code if writer_code in {
                    "artifact_write_failed", "artifact_conflict",
                    "artifact_commit_failed", "artifact_integrity_invalid",
                } else "artifact_write_failed"
            else:
                artifact_status = "pending"
                error_code = "artifact_not_written"
            with connect() as conn:
                conn.execute(
                    "UPDATE requests SET artifact_status=?, presentation_error=? WHERE owner_scope=? AND task_id=?",
                    (artifact_status, error_code, owner_scope, row["task_id"]),
                )
            self.send_json(502, bounded_failure(run_id, error_code, artifact_status))
            return
        repair_state, repair_error = check_existing_reprojection(output, row["session_id"])
        if repair_state in {"conflict", "unavailable"}:
            reason = repair_error or "reprojection_conflict"
            with connect() as conn:
                conn.execute(
                    "UPDATE requests SET presentation_error=? WHERE owner_scope=? AND task_id=?",
                    (reason, owner_scope, row["task_id"]),
                )
            fallback = presentation_failure_envelope(artifact["artifact_ref"])
            encoded = compact_json(fallback).decode("utf-8")
            if len(encoded.encode("utf-8")) > MAX_ENVELOPE_BYTES:
                self.send_json(502, bounded_failure(run_id, "safe_projection_invalid", "committed"))
                return
            self.send_json(200, {"run_id": run_id, "status": "completed", "output": encoded})
            return

        good, reason = validate_envelope(output, artifact)
        if not good:
            # If the pinned output hook left no event, reproject only the bounded
            # short presentation against the committed, integrity-checked artifact.
            repaired_output, _repair_status = reproject_missing_hook(output, row["session_id"])
            if repaired_output:
                repaired_good, _repair_reason = validate_envelope(repaired_output, artifact)
                if repaired_good:
                    with connect() as conn:
                        conn.execute(
                            "UPDATE requests SET presentation_error=NULL WHERE owner_scope=? AND task_id=?",
                            (owner_scope, row["task_id"]),
                        )
                    self.send_json(200, {"run_id": run_id, "status": "completed", "output": repaired_output})
                    return
            with connect() as conn:
                conn.execute(
                    "UPDATE requests SET presentation_error=? WHERE owner_scope=? AND task_id=?",
                    (reason, owner_scope, row["task_id"]),
                )
            fallback = presentation_failure_envelope(artifact["artifact_ref"])
            encoded = compact_json(fallback).decode("utf-8")
            if len(encoded.encode("utf-8")) > MAX_ENVELOPE_BYTES:
                self.send_json(502, bounded_failure(run_id, "safe_projection_invalid", "committed"))
                return
            self.send_json(200, {"run_id": run_id, "status": "completed", "output": encoded})
            return
        if repair_state == "none":
            pin_status = record_existing_projection(output, row["session_id"])
            if pin_status not in {"recorded", "replayed"}:
                with connect() as conn:
                    conn.execute(
                        "UPDATE requests SET presentation_error=? WHERE owner_scope=? AND task_id=?",
                        ("presentation_binding_unavailable", owner_scope, row["task_id"]),
                    )
                fallback = presentation_failure_envelope(artifact["artifact_ref"])
                fallback["presentation_status"] = "failed"
                self.send_json(200, {
                    "run_id": run_id, "status": "completed",
                    "output": compact_json(fallback).decode("utf-8"),
                })
                return
        with connect() as conn:
            conn.execute(
                "UPDATE requests SET presentation_error=NULL WHERE owner_scope=? AND task_id=?",
                (owner_scope, row["task_id"]),
            )
        self.send_json(200, {"run_id": run_id, "status": "completed", "output": output})

    def do_POST(self):
        owner_scope, session_key = self.authenticated_identity()
        if not owner_scope:
            self.send_json(401, {"error": {"code": "unauthorized"}})
            return
        if self.path == "/v1/runs":
            self.handle_submit(owner_scope, session_key)
            return
        match = STOP_RE.fullmatch(self.path)
        if match:
            run_id = match.group(1)
            row = request_by_owner_run(owner_scope, run_id)
            if not row:
                self.send_json(404, {"error": {"code": "not_found"}})
                return
            status, payload, error, _ = upstream_request(
                "POST", "/v1/runs/" + quote(run_id, safe="") + "/stop",
                session_key=session_key,
            )
            if error or status >= 400:
                self.send_json(502, {"run_id": run_id, "status": "stopping", "error": {"code": "stop_unconfirmed"}})
                return
            out_status = str(payload.get("status", "stopping")).lower()
            if out_status not in {"stopping", "cancelled", "interrupted", "completed", "failed"}:
                out_status = "stopping"
            self.send_json(min(status, 200), {"run_id": run_id, "status": out_status})
            return
        self.send_json(404, {"error": {"code": "not_found"}})

    def handle_submit(self, owner_scope: str, session_key: str):
        task_id = self.headers.get("Idempotency-Key", "").strip()
        if not task_id or len(task_id) > 255 or any(ord(ch) < 33 or ord(ch) > 126 for ch in task_id):
            self.send_json(400, {"error": {"code": "invalid_idempotency_key"}})
            return
        body = self.read_json_body()
        if not body or "input" not in body:
            code = "request_over_cap" if self.body_error == "body_over_cap" else "invalid_request"
            self.send_json(413 if self.body_error == "body_over_cap" else 400, {"error": {"code": code}})
            return
        input_value = body["input"]
        input_bytes = compact_json(input_value)
        if len(input_bytes) > MAX_REQUEST_BYTES:
            self.send_json(413, {"error": {"code": "input_over_cap"}})
            return
        body_hash = hashlib.sha256(input_bytes).hexdigest()
        existing = request_by_owner_task(owner_scope, task_id)
        if existing:
            if existing["body_hash"] != body_hash:
                self.send_json(409, {"error": {"code": "idempotency_conflict"}})
                return
            if existing["run_id"] and existing["submit_state"] == "accepted":
                self.send_json(200, {"run_id": existing["run_id"], "status": "started"})
                return
            self.send_json(503, {"error": {"code": "submission_uncertain"}})
            return

        session_id = "amusant-session-" + secrets.token_urlsafe(24)
        now = time.time()
        try:
            with connect() as conn:
                conn.execute(
                    """INSERT INTO requests
                       (owner_scope, task_id, session_id, body_hash, submit_state, created_at)
                       VALUES (?, ?, ?, ?, 'submitting', ?)""",
                    (owner_scope, task_id, session_id, body_hash, now),
                )
        except sqlite3.IntegrityError:
            self.send_json(503, {"error": {"code": "submission_uncertain"}})
            return

        upstream_body = {"input": input_value, "session_id": session_id}
        with connect() as conn:
            conn.execute(
                "UPDATE requests SET upstream_post_count=upstream_post_count+1 WHERE owner_scope=? AND task_id=?",
                (owner_scope, task_id),
            )
        status, payload, error, _ = upstream_request(
            "POST", "/v1/runs", upstream_body, session_key=session_key, idem=task_id,
        )
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        safe_error = error if isinstance(error, str) and re.fullmatch(r"upstream_[A-Za-z][A-Za-z0-9]{0,40}", error) else None
        safe_status = status if type(status) is int and 100 <= status <= 599 else None
        with connect() as conn:
            conn.execute(
                "UPDATE requests SET submit_upstream_status=?, submit_upstream_error=? WHERE owner_scope=? AND task_id=?",
                (safe_status, safe_error, owner_scope, task_id),
            )
        if error or status >= 400 or not isinstance(run_id, str) or not RUN_ID_VALUE_RE.fullmatch(run_id):
            with connect() as conn:
                conn.execute(
                    "UPDATE requests SET submit_state='uncertain' WHERE owner_scope=? AND task_id=?",
                    (owner_scope, task_id),
                )
            self.send_json(502, {"error": {"code": "submission_uncertain"}})
            return
        with connect() as conn:
            conn.execute(
                "UPDATE requests SET run_id=?, submit_state='accepted' WHERE owner_scope=? AND task_id=?",
                (run_id, owner_scope, task_id),
            )
        self.send_json(202, {"run_id": run_id, "status": "started"})


class WriterHandler(Handler):
    """Hermes-only internal writer/projection API on the hermes_api interface."""

    def authenticated_writer(self):
        auth = self.headers.get("Authorization", "")
        return secrets.compare_digest(auth, "Bearer " + WRITER_KEY)

    def do_GET(self):
        if not self.authenticated_writer():
            self.send_json(401, {"error": {"code": "unauthorized"}})
            return
        self.send_json(405, {"error": {"code": "method_not_allowed"}})

    def do_POST(self):
        if not self.authenticated_writer():
            self.send_json(401, {"error": {"code": "unauthorized"}})
            return
        session_id = self.headers.get("X-Amusant-Session-ID", "")
        if self.path not in {"/internal/v1/artifacts", "/internal/v1/presentation"}:
            self.send_json(404, {"error": {"code": "not_found"}})
            return
        body = self.read_json_body(max_bytes=MAX_WRITER_BODY_BYTES)
        if body is None:
            status = 413 if self.body_error == "body_over_cap" else 400
            code = "request_over_cap" if status == 413 else "invalid_request"
            self.send_json(status, {"error": {"code": code}})
            return
        if self.path == "/internal/v1/artifacts":
            if set(body) != {"report"}:
                self.send_json(400, {"error": {"code": "invalid_request"}})
                return
            try:
                status, response = commit_report(session_id, body["report"])
            except Exception:
                status, response = 500, {"error": {"code": "artifact_commit_failed"}}
            self.send_json(status, response)
            return
        if set(body) != {"presentation"}:
            self.send_json(400, {"error": {"code": "invalid_request"}})
            return
        try:
            status, response = project_for_session(session_id, body["presentation"])
        except Exception:
            status, response = 500, {"error": {"code": "writer_unavailable"}}
        self.send_json(status, response)


if __name__ == "__main__":
    os.umask(0o077)
    initialize()
    relay_server = ThreadingHTTPServer((FACADE_BIND_IP, 8643), Handler)
    writer_server = ThreadingHTTPServer((WRITER_BIND_IP, 8644), WriterHandler)
    relay_server.daemon_threads = True
    writer_server.daemon_threads = True
    writer_thread = Thread(target=writer_server.serve_forever, daemon=True)
    writer_thread.start()
    print(json.dumps({
        "event": "facade_ready",
        "listeners": ["relay_api", "hermes_api_writer"],
        "upstream": "private_hermes",
    }, separators=(",", ":")), flush=True)
    relay_server.serve_forever()
