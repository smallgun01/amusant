#!/usr/bin/env python3
"""Read-only, body-free audit of the fresh Facade ledger and artifact volume."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

root = Path(os.environ.get("FACADE_STATE_DIR", "/audit"))
database = root / "facade-ledger.sqlite3"
evidence_path = Path(os.environ.get("E2E_EVIDENCE_PATH", "/evidence/facade-audit.json"))
status = "PASS"
failure = None
metrics = {}
try:
    if not database.is_file() or database.is_symlink():
        raise AssertionError("facade ledger unavailable")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    requests = connection.execute(
        "SELECT owner_scope,task_id,session_id,run_id,submit_state,artifact_status,artifact_ref,"
        "upstream_post_count,writer_report_bytes,raw_output_bytes "
        "FROM requests ORDER BY created_at"
    ).fetchall()
    artifacts = connection.execute(
        "SELECT artifact_ref,owner_scope,task_id,session_id,run_id,path,sha256,byte_length FROM artifacts"
    ).fetchall()
    connection.close()

    post_counts = [int(row["upstream_post_count"]) for row in requests]
    if not requests:
        raise AssertionError("no facade request rows")
    if any(value != 1 for value in post_counts):
        raise AssertionError("a task caused zero or repeated upstream Hermes POSTs")
    sizes = []
    artifact_by_ref = {}
    request_by_owner_task = {
        (str(row["owner_scope"]), str(row["task_id"])): row for row in requests
    }
    for row in artifacts:
        artifact_ref = str(row["artifact_ref"])
        if not artifact_ref or artifact_ref in artifact_by_ref:
            raise AssertionError("artifact reference is missing or duplicated")
        artifact_by_ref[artifact_ref] = row
        request = request_by_owner_task.get((str(row["owner_scope"]), str(row["task_id"])))
        if (
            request is None
            or request["artifact_status"] != "committed"
            or request["artifact_ref"] != artifact_ref
            or request["session_id"] != row["session_id"]
            or request["run_id"] != row["run_id"]
        ):
            raise AssertionError("artifact is not bound to its trusted request")
        name = Path(row["path"]).name
        if not name or name in {".", ".."} or Path(name).name != name:
            raise AssertionError("artifact path is not a single trusted filename")
        path = root / "artifacts" / name
        if not path.is_file() or path.is_symlink():
            raise AssertionError("committed artifact file is missing")
        data = path.read_bytes()
        if len(data) != int(row["byte_length"]):
            raise AssertionError("committed artifact length mismatch")
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise AssertionError("committed artifact digest mismatch")
        sizes.append(len(data))
    for row in requests:
        if row["artifact_status"] == "committed":
            reference = row["artifact_ref"]
            artifact = artifact_by_ref.get(str(reference)) if reference else None
            if (
                artifact is None
                or artifact["owner_scope"] != row["owner_scope"]
                or artifact["task_id"] != row["task_id"]
                or artifact["session_id"] != row["session_id"]
                or artifact["run_id"] != row["run_id"]
            ):
                raise AssertionError("committed request has no matching trusted artifact")
    artifact_dir = root / "artifacts"
    actual_files = list(artifact_dir.iterdir()) if artifact_dir.is_dir() and not artifact_dir.is_symlink() else []
    if len(actual_files) != len(artifacts) or any(not path.is_file() or path.is_symlink() for path in actual_files):
        raise AssertionError("artifact directory contains missing or unregistered files")
    if 10_000 not in sizes or 30_000 not in sizes:
        raise AssertionError("expected 10K and 30K report artifacts were not committed")
    metrics = {
        "request_count": len(requests),
        "artifact_count": len(artifacts),
        "upstream_hermes_post_max_per_task": max(post_counts),
        "report_artifact_sizes_bytes": sorted(sizes),
        "committed_artifact_digests_verified": True,
        "artifact_request_bindings_verified": True,
        "raw_output_max_bytes": max((int(row["raw_output_bytes"] or 0) for row in requests), default=0),
        "request_artifact_statuses": sorted({str(row["artifact_status"]) for row in requests}),
    }
except Exception as exc:
    status = "FAIL"
    failure = type(exc).__name__

result = {
    "schema": "amusant.issue-85.pr-b2.facade-audit.v1",
    "status": status,
    "failure_type": failure,
    "checks": {
        "upstream_post_once_per_task": status == "PASS",
        "committed_report_digests_verified": metrics.get("committed_artifact_digests_verified", False),
        "artifact_request_bindings_verified": metrics.get("artifact_request_bindings_verified", False),
        "ten_k_and_thirty_k_artifacts_committed": 10_000 in metrics.get("report_artifact_sizes_bytes", []) and 30_000 in metrics.get("report_artifact_sizes_bytes", []),
    },
    "metrics": metrics,
}
evidence_path.parent.mkdir(parents=True, exist_ok=True)
evidence_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
if status != "PASS":
    raise SystemExit(1)
