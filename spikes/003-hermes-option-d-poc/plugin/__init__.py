"""Option D proof plugin: tool writes an artifact; output hook joins trusted metadata."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

STATE = Path(os.environ.get("POC_STATE_DIR", "/state"))
DB_PATH = STATE / "option-d.sqlite3"
ARTIFACT_DIR = STATE / "artifacts"
MAX_REPORT_BYTES = 64 * 1024
WRITE_TIMEOUT_SECONDS = 0.10
PRESENTATION_FAILURE = "The report is saved; its short presentation needs recovery."


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _request_for_session(session_id):
    if not isinstance(session_id, str) or not session_id:
        return None
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        with _db() as conn:
            row = conn.execute(
                "SELECT * FROM requests WHERE session_id=?", (session_id,)
            ).fetchone()
        if row and row["submit_state"] == "accepted" and row["run_id"]:
            return dict(row)
        if row and row["submit_state"] == "uncertain":
            return None
        time.sleep(0.025)
    return None


def _artifact_for_request(row):
    with _db() as conn:
        artifact = conn.execute(
            """SELECT * FROM artifacts
               WHERE owner_scope=? AND task_id=? AND session_id=? AND run_id=?""",
            (row["owner_scope"], row["task_id"], row["session_id"], row["run_id"]),
        ).fetchone()
    return dict(artifact) if artifact else None


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _tool_error(code):
    return _json({"error": code})


def _mark_write_failed(row):
    try:
        with _db() as conn:
            conn.execute(
                "UPDATE requests SET artifact_status='failed' WHERE owner_scope=? AND task_id=? AND session_id=? AND run_id=?",
                (row["owner_scope"], row["task_id"], row["session_id"], row["run_id"]),
            )
    except Exception:
        pass


def _record_writer_result(row, code, report_bytes):
    allowed = {
        "committed", "artifact_write_failed", "artifact_write_timeout",
        "artifact_conflict", "artifact_commit_failed",
    }
    if code not in allowed:
        return
    try:
        with _db() as conn:
            conn.execute(
                """UPDATE requests
                   SET writer_call_count=writer_call_count+1,
                       writer_result_code=?, writer_report_bytes=?
                   WHERE owner_scope=? AND task_id=? AND session_id=? AND run_id=?""",
                (code, report_bytes, row["owner_scope"], row["task_id"], row["session_id"], row["run_id"]),
            )
    except Exception:
        pass


def save_report(args, *, session_id=None, **_kwargs):
    """Commit the report under adapter-minted identity; model args may contain only report."""
    if not isinstance(args, dict) or set(args) != {"report"}:
        return _tool_error("invalid_arguments")
    report = args.get("report")
    if not isinstance(report, str) or not report.strip():
        return _tool_error("report_invalid")
    encoded = report.encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        return _tool_error("report_over_cap")
    row = _request_for_session(session_id)
    if not row:
        return _tool_error("trusted_request_context_unavailable")
    mode = row["poc_case"]
    if mode == "writer_fail":
        _mark_write_failed(row)
        _record_writer_result(row, "artifact_write_failed", len(encoded))
        return _tool_error("artifact_write_failed")
    if mode == "writer_slow":
        # Deterministic sink delay exceeds the test-only write budget before any write starts.
        time.sleep(WRITE_TIMEOUT_SECONDS * 2)
        _mark_write_failed(row)
        _record_writer_result(row, "artifact_write_timeout", len(encoded))
        return _tool_error("artifact_write_timeout")
    digest = hashlib.sha256(encoded).hexdigest()
    existing = _artifact_for_request(row)
    if existing:
        if existing["sha256"] == digest:
            _record_writer_result(row, "committed", len(encoded))
            return _json({"committed": True})
        _record_writer_result(row, "artifact_conflict", len(encoded))
        return _tool_error("artifact_conflict")

    artifact_ref = "art_" + secrets.token_urlsafe(24)
    target = ARTIFACT_DIR / (artifact_ref + ".report")
    temporary = ARTIFACT_DIR / ("." + artifact_ref + ".tmp")
    try:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        with open(temporary, "xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        dir_fd = os.open(ARTIFACT_DIR, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        with _db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO artifacts
                   (artifact_ref, owner_scope, task_id, session_id, run_id, path,
                    sha256, byte_length, poc_case, committed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (artifact_ref, row["owner_scope"], row["task_id"], row["session_id"],
                 row["run_id"], str(target), digest, len(encoded), mode, time.time()),
            )
            conn.execute(
                """UPDATE requests SET artifact_status='committed', artifact_ref=?
                   WHERE owner_scope=? AND task_id=? AND session_id=? AND run_id=?""",
                (artifact_ref, row["owner_scope"], row["task_id"], row["session_id"], row["run_id"]),
            )
            conn.commit()
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        _mark_write_failed(row)
        _record_writer_result(row, "artifact_commit_failed", len(encoded))
        return _tool_error("artifact_commit_failed")
    _record_writer_result(row, "committed", len(encoded))
    # The opaque reference is deliberately withheld from model-visible tool output.
    return _json({"committed": True})


def _valid_presentation(value):
    if not isinstance(value, dict):
        return False
    if set(value) != {"schema_version", "speak", "card", "details_md"}:
        return False
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return False
    speak = value.get("speak")
    card = value.get("card")
    details = value.get("details_md")
    if not isinstance(speak, str) or not 1 <= len(speak) <= 240:
        return False
    if not isinstance(details, str) or len(details) > 512:
        return False
    if not isinstance(card, dict) or set(card) != {"title", "lines"}:
        return False
    if not isinstance(card.get("title"), str) or not 1 <= len(card["title"]) <= 80:
        return False
    lines = card.get("lines")
    return (
        isinstance(lines, list)
        and 1 <= len(lines) <= 4
        and all(isinstance(line, str) and 1 <= len(line) <= 120 for line in lines)
    )


def _record_output_hook_event(session_id, outcome_code):
    if not isinstance(outcome_code, str) or outcome_code not in {
        "session_unmapped", "hook_exception", "hook_empty", "timeout_injected",
        "input_invalid", "presentation_invalid", "artifact_missing",
        "artifact_not_committed", "artifact_binding_invalid", "ready",
        "request_lookup_error", "artifact_lookup_error",
    }:
        return
    session_text = session_id if isinstance(session_id, str) else ""
    session_hash = hashlib.sha256(session_text.encode("utf-8", errors="replace")).hexdigest()
    try:
        with _db() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS output_hook_events (
                   session_hash TEXT NOT NULL, outcome_code TEXT NOT NULL, created_at REAL NOT NULL)"""
            )
            conn.execute(
                "INSERT INTO output_hook_events(session_hash, outcome_code, created_at) VALUES (?, ?, ?)",
                (session_hash, outcome_code, time.time()),
            )
    except Exception:
        pass


def transform_llm_output(response_text, session_id="", **_kwargs):
    """Return an envelope only after joining the model presentation to a committed artifact."""
    try:
        row = _request_for_session(session_id)
    except Exception:
        _record_output_hook_event(session_id, "request_lookup_error")
        raise
    if not row:
        _record_output_hook_event(session_id, "session_unmapped")
        return None
    mode = row["poc_case"]
    if mode == "hook_exception":
        _record_output_hook_event(session_id, "hook_exception")
        raise RuntimeError("synthetic hook exception")
    if mode == "hook_empty":
        _record_output_hook_event(session_id, "hook_empty")
        return ""
    if mode == "hook_timeout":
        _record_output_hook_event(session_id, "timeout_injected")
        time.sleep(1.0)
    try:
        value = json.loads(response_text) if isinstance(response_text, str) else None
    except (ValueError, TypeError):
        _record_output_hook_event(session_id, "input_invalid")
        return None
    if not _valid_presentation(value):
        _record_output_hook_event(session_id, "presentation_invalid")
        return None
    try:
        artifact = _artifact_for_request(row)
    except Exception:
        _record_output_hook_event(session_id, "artifact_lookup_error")
        raise
    if not artifact:
        _record_output_hook_event(session_id, "artifact_missing")
        return None
    if row.get("artifact_status") != "committed":
        _record_output_hook_event(session_id, "artifact_not_committed")
        return None
    if (
        artifact["artifact_ref"] != row.get("artifact_ref")
        or artifact["owner_scope"] != row["owner_scope"]
        or artifact["task_id"] != row["task_id"]
        or artifact["run_id"] != row["run_id"]
        or artifact["session_id"] != row["session_id"]
    ):
        _record_output_hook_event(session_id, "artifact_binding_invalid")
        return None

    envelope = {
        "schema_version": 1,
        "speak": value["speak"],
        "card": value["card"],
        "details_md": value["details_md"],
        "execution_status": "completed",
        "artifact_status": "committed",
        "presentation_status": "ready",
        "artifact_ref": artifact["artifact_ref"],
    }
    _record_output_hook_event(session_id, "ready")
    return _json(envelope)


def register(ctx):
    ctx.register_tool(
        name="poc_save_report",
        toolset="amusant_option_d",
        schema={
            "name": "poc_save_report",
            "description": "Save the complete report to the trusted artifact sink. Pass only report text.",
            "parameters": {
                "type": "object",
                "properties": {"report": {"type": "string"}},
                "required": ["report"],
                "additionalProperties": False,
            },
        },
        handler=save_report,
        description="Write a report to the local Option D proof artifact sink.",
        emoji="📄",
    )
    ctx.register_hook("transform_llm_output", transform_llm_output)
