"""Safe, idempotent reprojection for a Facade-owned committed artifact.

Only the private Facade loads this module for integrity checks and projection.
The Hermes runtime plugin uses the narrow writer API and does not access its
ledger or artifacts.
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
PRESENTATION_FIELDS_V1 = {"schema_version", "speak", "card", "details_md"}
PRESENTATION_FIELDS_V2 = {"schema_version", "speak", "card"}
V2_FAILURE_SPEAK = "The task finished, but result delivery needs recovery."
V2_FAILURE_CARD = {"title": "Result needs recovery", "lines": ["The short result could not be safely delivered."]}


def _valid_text(value, maximum):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value.strip() == value
        and all(ord(char) >= 32 or char == "\t" for char in value)
    )


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _presentation(response_text, result_contract_version=None):
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
    if not isinstance(value, dict):
        return None, "presentation_invalid"
    version = result_contract_version if result_contract_version is not None else value.get("schema_version")
    expected = PRESENTATION_FIELDS_V2 if version == 2 else PRESENTATION_FIELDS_V1
    if type(version) is not int or version not in {1, 2} or set(value) != expected:
        return None, "presentation_invalid"
    if type(value.get("schema_version")) is not int or value["schema_version"] != version:
        return None, "presentation_invalid"
    speak = value.get("speak")
    details = value.get("details_md") if version == 1 else None
    card = value.get("card")
    if not _valid_text(speak, 240):
        return None, "presentation_invalid"
    if version == 1 and (not isinstance(details, str) or len(details) > 512):
        return None, "presentation_invalid"
    if not isinstance(card, dict) or set(card) != {"title", "lines"}:
        return None, "presentation_invalid"
    if not _valid_text(card.get("title"), 80):
        return None, "presentation_invalid"
    lines = card.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 4:
        return None, "presentation_invalid"
    if any(not _valid_text(line, 120) for line in lines):
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


def project_presentation(response_text, artifact_ref, result_contract_version=1, artifact_status="committed", *, force_failure=False):
    """Apply the same allowlisted projection used by both hook and recovery."""
    if result_contract_version == 1:
        if artifact_status != "committed" or not isinstance(artifact_ref, str) or not ARTIFACT_REF_RE.fullmatch(artifact_ref):
            return None, "artifact_binding_invalid"
    elif result_contract_version == 2:
        if artifact_status not in {"none", "committed", "failed"}:
            return None, "artifact_binding_invalid"
        if artifact_status == "committed":
            if not isinstance(artifact_ref, str) or not ARTIFACT_REF_RE.fullmatch(artifact_ref):
                return None, "artifact_binding_invalid"
        elif artifact_ref is not None:
            return None, "artifact_binding_invalid"
    else:
        return None, "presentation_invalid"
    value, status = _presentation(response_text, result_contract_version)
    if value is None:
        return None, status
    if result_contract_version == 2 and (artifact_status == "failed" or force_failure):
        encoded = _compact_json({
            "schema_version": 2,
            "speak": V2_FAILURE_SPEAK,
            "card": V2_FAILURE_CARD,
            "execution_status": "completed",
            # Preserve a verified earlier commit when a later writer attempt
            # conflicts or only the short presentation fails.
            "artifact_status": artifact_status,
            "presentation_status": "failed",
            "artifact_ref": artifact_ref if artifact_status == "committed" else None,
        })
    else:
        encoded = _envelope(value, artifact_ref, result_contract_version, artifact_status)
    if len(encoded.encode("utf-8")) > MAX_PRESENTATION_BYTES:
        return None, "presentation_invalid"
    return encoded, "ready"


def _envelope(value, artifact_ref, result_contract_version=1, artifact_status="committed"):
    return _compact_json({
        "schema_version": result_contract_version,
        "speak": value["speak"],
        "card": value["card"],
        "execution_status": "completed",
        "artifact_status": artifact_status,
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



def _store_projection(row, artifact, source_text, output_text, *, require_no_hook_event=False):
    try:
        session_id = row["session_id"]
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        reference = artifact["artifact_ref"] if artifact else ""
        reference_hash = hashlib.sha256(reference.encode("utf-8")).hexdigest()
        binding_material = chr(0).join(
            (row["owner_scope"], row["task_id"], row["session_id"], row["run_id"])
        )
        binding_hash = hashlib.sha256(binding_material.encode("utf-8")).hexdigest()
        source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        output_hash = hashlib.sha256(output_text.encode("utf-8")).hexdigest()
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
                    return "conflict"
                if int(existing["replay_count"]) == 0:
                    conn.execute(
                        "UPDATE presentation_repairs SET replay_count=1 WHERE session_hash=?",
                        (session_hash,),
                    )
                    _record_event(conn, session_hash, "projection_replayed")
                conn.commit()
                return "replayed"
            if require_no_hook_event and conn.execute(
                "SELECT 1 FROM output_hook_events WHERE session_hash=? LIMIT 1", (session_hash,)
            ).fetchone():
                conn.rollback()
                return "hook_event_present"
            conn.execute(
                """INSERT INTO presentation_repairs
                   (session_hash, artifact_ref_hash, binding_sha256, source_sha256,
                    output_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_hash, reference_hash, binding_hash, source_hash,
                 output_hash, time.time()),
            )
            _record_event(conn, session_hash, "projection_recorded")
            conn.commit()
        return "recorded"
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return "unavailable"


def _version(row):
    value = row.get("result_contract_version", 1)
    return value if type(value) is int and value in {1, 2} else 1


def _v2_artifact_projection(row):
    status = row.get("artifact_status")
    attempted = bool(row.get("writer_call_count") or row.get("writer_result_code"))
    if status == "committed":
        artifact, error = _load_committed_artifact(row)
        if not artifact:
            return "failed", None, error
        if row.get("writer_result_code") in {"artifact_conflict", "artifact_integrity_invalid"}:
            return "committed", artifact, "artifact_conflict"
        return "committed", artifact, None
    if status == "failed" or attempted:
        return "failed", None, row.get("writer_result_code") or "artifact_write_failed"
    return "none", None, None


def _valid_envelope(output_text, row, artifact_status, artifact):
    try:
        encoded = output_text.encode("utf-8")
        value = json.loads(output_text)
    except (AttributeError, UnicodeError, json.JSONDecodeError):
        return False
    if len(encoded) > MAX_PRESENTATION_BYTES or not isinstance(value, dict):
        return False
    version = _version(row)
    expected = {
        "schema_version", "speak", "card", "execution_status",
        "artifact_status", "presentation_status", "artifact_ref",
    }
    if version == 1 and set(value) != expected:
        return False
    if version == 2 and set(value) != expected:
        return False
    ref = artifact["artifact_ref"] if artifact else None
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != version
        or value.get("execution_status") != "completed"
        or value.get("artifact_status") != artifact_status
        or value.get("artifact_ref") != ref
        or value.get("presentation_status") not in {"ready", "failed"}
    ):
        return False
    if artifact_status == "failed" and value.get("presentation_status") != "failed":
        return False
    if value.get("presentation_status") == "failed":
        return (
            version == 1
            and artifact_status == "committed"
            and value.get("speak") == "The report is saved; its short presentation needs recovery."
            and value.get("card") == {"title": "Report saved", "lines": ["Short presentation needs recovery."]}
        ) or (
            version == 2
            and value.get("speak") == V2_FAILURE_SPEAK
            and value.get("card") == V2_FAILURE_CARD
        )
    presentation, status = _presentation(
        _compact_json({
            "schema_version": version,
            "speak": value.get("speak"),
            "card": value.get("card"),
            **({"details_md": value.get("speak")} if version == 1 else {}),
        }),
        version,
    )
    return presentation is not None and status == "ready"


def record_hook_projection(response_text, projected_output, session_id):
    """Persist exact hook input/output digests against the Facade-owned binding."""
    if not isinstance(session_id, str) or not session_id:
        return "session_missing"
    if not isinstance(response_text, str) or not isinstance(projected_output, str):
        return "presentation_invalid"
    try:
        with _connect() as conn:
            found = conn.execute(
                "SELECT * FROM requests WHERE session_id=?", (session_id,)
            ).fetchone()
        row = dict(found) if found else None
        if not row or row.get("submit_state") != "accepted" or not row.get("run_id"):
            return "session_unmapped"
        version = _version(row)
        value, status = _presentation(response_text, version)
        if value is None:
            return status
        if version == 1:
            artifact, artifact_status = _load_committed_artifact(row)
            if not artifact:
                return artifact_status
            state, reference = "committed", artifact["artifact_ref"]
        else:
            state, artifact, _error = _v2_artifact_projection(row)
            reference = artifact["artifact_ref"] if artifact else None
        expected, projection_status = project_presentation(
            response_text, reference, version, state,
            force_failure=bool(version == 2 and _error),
        )
        if not expected or expected != projected_output:
            return "projection_mismatch"
        return _store_projection(row, artifact, response_text, projected_output)
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return "unavailable"


def record_existing_projection(projected_output, session_id, *, source_text=None):
    """Pin an already projected envelope when the hook's persistence was absent."""
    if not isinstance(session_id, str) or not session_id or not isinstance(projected_output, str):
        return "presentation_invalid"
    try:
        with _connect() as conn:
            found = conn.execute(
                "SELECT * FROM requests WHERE session_id=?", (session_id,)
            ).fetchone()
        row = dict(found) if found else None
        if not row or row.get("submit_state") != "accepted" or not row.get("run_id"):
            return "session_unmapped"
        version = _version(row)
        if version == 1:
            artifact, artifact_status = _load_committed_artifact(row)
            if not artifact:
                return artifact_status
            state = "committed"
        else:
            state, artifact, _error = _v2_artifact_projection(row)
        if not _valid_envelope(projected_output, row, state, artifact):
            return "presentation_invalid"
        source = source_text if isinstance(source_text, str) else projected_output
        return _store_projection(row, artifact, source, projected_output)
    except (json.JSONDecodeError, sqlite3.Error, OSError, TypeError, ValueError):
        return "presentation_invalid"


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
        version = _version(row)
        if version == 1:
            artifact, artifact_status = _load_committed_artifact(row)
            if not artifact:
                return "unavailable", artifact_status
            state = "committed"
        else:
            state, artifact, artifact_status = _v2_artifact_projection(row)
            if artifact_status and state == "failed" and row.get("artifact_status") == "committed":
                return "unavailable", artifact_status
        reference = artifact["artifact_ref"] if artifact else ""
        reference_hash = hashlib.sha256(reference.encode("utf-8")).hexdigest()
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
        if row.get("submit_state") != "accepted" or row.get("hermes_run_status") != "completed":
            return None, "run_not_completed"

        version = _version(row)
        value, input_status = _presentation(response_text, version)
        if value is None:
            return None, input_status
        if version == 1:
            artifact, artifact_status = _load_committed_artifact(row)
            if not artifact:
                return None, artifact_status
            state, reference, force_failure = "committed", artifact["artifact_ref"], False
        else:
            state, artifact, artifact_error = _v2_artifact_projection(row)
            reference = artifact["artifact_ref"] if artifact else None
            force_failure = bool(artifact_error)
        projected, projection_status = project_presentation(
            response_text, reference, version, state, force_failure=force_failure
        )
        if not projected:
            return None, projection_status
        outcome = _store_projection(
            row, artifact, response_text, projected, require_no_hook_event=True
        )
        if outcome not in {"recorded", "replayed"}:
            return None, "reprojection_" + outcome
        return projected, "reprojected" if outcome == "recorded" else "replayed"
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return None, "reprojection_unavailable"
