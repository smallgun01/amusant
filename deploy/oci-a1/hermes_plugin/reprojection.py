"""Safe, idempotent reprojection for a committed Option D artifact.

This module is shared by the Hermes plugin package and the private facade. It
never accepts identity or artifact references from presentation text.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

STATE = Path(os.environ.get("AMUSANT_RESULT_STATE_DIR", "/var/lib/amusant-results"))
DB_PATH = STATE / "facade-ledger.sqlite3"
ARTIFACT_DIR = STATE / "artifacts"
MAX_REPORT_BYTES = 64 * 1024
MAX_PRESENTATION_BYTES = 3000
ARTIFACT_REF_RE = re.compile(r"^art_[A-Za-z0-9_-]{20,80}$")
PRESENTATION_FIELDS = {"schema_version", "speak", "card", "details_md"}


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _presentation(response_text):
    if not isinstance(response_text, str):
        return None, "input_invalid"
    try:
        encoded = response_text.encode("utf-8")
    except UnicodeError:
        return None, "input_invalid"
    if len(encoded) > MAX_PRESENTATION_BYTES:
        return None, "input_over_cap"
    try:
        value = json.loads(response_text)
    except (ValueError, TypeError):
        return None, "input_invalid"
    if not isinstance(value, dict) or set(value) != PRESENTATION_FIELDS:
        return None, "presentation_invalid"
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return None, "presentation_invalid"
    speak = value.get("speak")
    details = value.get("details_md")
    card = value.get("card")
    if not isinstance(speak, str) or not 1 <= len(speak) <= 240:
        return None, "presentation_invalid"
    if not isinstance(details, str) or len(details) > 512:
        return None, "presentation_invalid"
    if not isinstance(card, dict) or set(card) != {"title", "lines"}:
        return None, "presentation_invalid"
    if not isinstance(card.get("title"), str) or not 1 <= len(card["title"]) <= 80:
        return None, "presentation_invalid"
    lines = card.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 4:
        return None, "presentation_invalid"
    if any(not isinstance(line, str) or not 1 <= len(line) <= 120 for line in lines):
        return None, "presentation_invalid"
    return value, "ready"


def _load_committed_artifact(row):
    if (
        row.get("submit_state") != "accepted"
        or row.get("artifact_status") != "committed"
        or not row.get("owner_scope")
        or not row.get("task_id")
        or not row.get("session_id")
        or not row.get("run_id")
    ):
        return None, "artifact_not_committed"

    try:
        with _connect() as conn:
            found = conn.execute(
                """SELECT * FROM artifacts
                   WHERE owner_scope=? AND task_id=? AND session_id=? AND run_id=?""",
                (row["owner_scope"], row["task_id"], row["session_id"], row["run_id"]),
            ).fetchone()
        artifact = dict(found) if found else None
        if not artifact:
            return None, "artifact_missing"
        reference = artifact.get("artifact_ref")
        if not isinstance(reference, str) or not ARTIFACT_REF_RE.fullmatch(reference):
            return None, "artifact_binding_invalid"
        if (
            reference != row.get("artifact_ref")
            or artifact.get("owner_scope") != row["owner_scope"]
            or artifact.get("task_id") != row["task_id"]
            or artifact.get("session_id") != row["session_id"]
            or artifact.get("run_id") != row["run_id"]
        ):
            return None, "artifact_binding_invalid"

        root = ARTIFACT_DIR.resolve(strict=True)
        expected = ARTIFACT_DIR / (reference + ".report")
        if artifact.get("path") != str(expected) or expected.is_symlink():
            return None, "artifact_binding_invalid"
        resolved = expected.resolve(strict=True)
        if resolved.parent != root:
            return None, "artifact_binding_invalid"
        data = resolved.read_bytes()
        if (
            not data
            or len(data) > MAX_REPORT_BYTES
            or len(data) != artifact.get("byte_length")
            or hashlib.sha256(data).hexdigest() != artifact.get("sha256")
        ):
            return None, "artifact_integrity_invalid"
        return artifact, "ready"
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None, "artifact_unavailable"


def project_presentation(response_text, artifact_ref):
    """Apply the same allowlisted projection used by both hook and recovery."""
    if not isinstance(artifact_ref, str) or not ARTIFACT_REF_RE.fullmatch(artifact_ref):
        return None, "artifact_binding_invalid"
    value, status = _presentation(response_text)
    if value is None:
        return None, status
    encoded = _envelope(value, artifact_ref)
    if len(encoded.encode("utf-8")) > MAX_PRESENTATION_BYTES:
        return None, "presentation_invalid"
    return encoded, "ready"


def _envelope(value, artifact_ref):
    return _compact_json({
        "schema_version": 1,
        "speak": value["speak"],
        "card": value["card"],
        "execution_status": "completed",
        "artifact_status": "committed",
        "presentation_status": "ready",
        "artifact_ref": artifact_ref,
    })


def _record_event(conn, session_hash, outcome):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS output_hook_events (
           session_hash TEXT NOT NULL, outcome_code TEXT NOT NULL, created_at REAL NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO output_hook_events(session_hash, outcome_code, created_at) VALUES (?, ?, ?)",
        (session_hash, outcome, time.time()),
    )



def check_existing_reprojection(response_text, session_id):
    """Compare a later GET with the exact source/output already repaired.

    Returns only a small state code; raw presentation data and stored digests
    never leave this module.
    """
    if not isinstance(session_id, str) or not session_id:
        return "unavailable", "session_missing"
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    try:
        with _connect() as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='presentation_repairs'"
            ).fetchone()
            if not table:
                return "none", None
            repair = conn.execute(
                "SELECT * FROM presentation_repairs WHERE session_hash=?",
                (session_hash,),
            ).fetchone()
            if not repair:
                return "none", None
            request = conn.execute(
                "SELECT * FROM requests WHERE session_id=?", (session_id,)
            ).fetchone()
        repair = dict(repair)
        row = dict(request) if request else None
        if not row:
            return "unavailable", "session_unmapped"
        if row.get("hermes_run_status") != "completed":
            return "unavailable", "run_not_completed"
        artifact, artifact_status = _load_committed_artifact(row)
        if not artifact:
            return "unavailable", artifact_status

        reference_hash = hashlib.sha256(artifact["artifact_ref"].encode("utf-8")).hexdigest()
        binding_material = chr(0).join(
            (row["owner_scope"], row["task_id"], row["session_id"], row["run_id"])
        )
        binding_hash = hashlib.sha256(binding_material.encode("utf-8")).hexdigest()
        if (
            repair.get("artifact_ref_hash") != reference_hash
            or repair.get("binding_sha256") != binding_hash
            or not isinstance(repair.get("source_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", repair["source_sha256"])
            or not isinstance(repair.get("output_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", repair["output_sha256"])
        ):
            return "conflict", "reprojection_binding_conflict"
        if not isinstance(response_text, str):
            return "conflict", "reprojection_conflict"
        try:
            response_hash = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
        except UnicodeError:
            return "conflict", "reprojection_conflict"
        if response_hash == repair["source_sha256"]:
            return "source", "source_match"
        if response_hash == repair["output_sha256"]:
            return "projection", "projection_match"
        return "conflict", "reprojection_conflict"
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return "unavailable", "reprojection_unavailable"

def reproject_missing_hook(response_text, session_id):
    """Rebuild a bounded envelope only when no output-hook event was recorded.

    The source must be Hermes's short presentation, not a model-supplied identity
    or reference. Replays of the exact same presentation return the same bytes;
    different input or artifact state fails closed.
    """
    if not isinstance(session_id, str) or not session_id:
        return None, "session_missing"
    try:
        with _connect() as conn:
            request = conn.execute(
                "SELECT * FROM requests WHERE session_id=?", (session_id,)
            ).fetchone()
        row = dict(request) if request else None
        if not row:
            return None, "session_unmapped"
        if row.get("hermes_run_status") != "completed":
            return None, "run_not_completed"

        value, input_status = _presentation(response_text)
        if value is None:
            return None, input_status
        artifact, artifact_status = _load_committed_artifact(row)
        if not artifact:
            return None, artifact_status
        projected = _envelope(value, artifact["artifact_ref"])
        source_hash = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
        output_hash = hashlib.sha256(projected.encode("utf-8")).hexdigest()
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        reference_hash = hashlib.sha256(artifact["artifact_ref"].encode("utf-8")).hexdigest()
        binding_material = chr(0).join((row["owner_scope"], row["task_id"], row["session_id"], row["run_id"]))
        binding_hash = hashlib.sha256(binding_material.encode("utf-8")).hexdigest()

        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS presentation_repairs (
                    session_hash TEXT PRIMARY KEY,
                    artifact_ref_hash TEXT NOT NULL,
                    binding_sha256 TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    output_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    replay_count INTEGER NOT NULL DEFAULT 0
                )"""
            )
            existing = conn.execute(
                "SELECT * FROM presentation_repairs WHERE session_hash=?",
                (session_hash,),
            ).fetchone()
            if existing:
                if (
                    existing["artifact_ref_hash"] != reference_hash
                    or existing["binding_sha256"] != binding_hash
                    or existing["source_sha256"] != source_hash
                    or existing["output_sha256"] != output_hash
                ):
                    conn.rollback()
                    return None, "reprojection_conflict"
                if int(existing["replay_count"]) == 0:
                    conn.execute(
                        "UPDATE presentation_repairs SET replay_count=1 WHERE session_hash=?",
                        (session_hash,),
                    )
                    _record_event(conn, session_hash, "reprojection_replayed")
                conn.commit()
                return projected, "replayed"

            prior_hook_event = conn.execute(
                "SELECT 1 FROM output_hook_events WHERE session_hash=? LIMIT 1",
                (session_hash,),
            ).fetchone()
            if prior_hook_event:
                conn.rollback()
                return None, "hook_event_present"
            conn.execute(
                """INSERT INTO presentation_repairs
                   (session_hash, artifact_ref_hash, binding_sha256, source_sha256, output_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_hash, reference_hash, binding_hash, source_hash, output_hash, time.time()),
            )
            _record_event(conn, session_hash, "reprojected")
            conn.commit()
        return projected, "reprojected"
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return None, "reprojection_unavailable"
