#!/usr/bin/env python3
"""Read-only, sanitized precheck for the operator-controlled v2 cutover."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3


ACTIVE_RELAY_STATUSES = {"queued", "running", "uncertain"}
TERMINAL_HERMES_STATUSES = {"completed", "failed", "cancelled", "interrupted", "expired"}
KNOWN_RELAY_STATUSES = ACTIVE_RELAY_STATUSES | {"completed", "failed"}


class PrecheckError(RuntimeError):
    pass


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise PrecheckError("database_missing_or_not_regular")
    wal_path = Path(str(path) + "-wal")
    shm_path = Path(str(path) + "-shm")
    if any(sidecar.is_symlink() for sidecar in (wal_path, shm_path)):
        raise PrecheckError("database_sidecar_not_regular")
    if any(sidecar.exists() and not sidecar.is_file() for sidecar in (wal_path, shm_path)):
        raise PrecheckError("database_sidecar_not_regular")
    try:
        wal_is_nonempty = wal_path.is_file() and wal_path.stat().st_size > 0
    except OSError:
        raise PrecheckError("database_sidecar_unreadable") from None
    if wal_is_nonempty and not shm_path.is_file():
        # SQLite may create -shm when opening a WAL database read-only if the
        # directory is writable. Refuse this state instead of changing it.
        raise PrecheckError("database_wal_shared_memory_missing")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            connection.close()
            raise PrecheckError("database_integrity_check_failed")
        return connection
    except sqlite3.Error:
        raise PrecheckError("database_unreadable") from None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        raise PrecheckError("database_schema_unreadable") from None


def _require_columns(connection: sqlite3.Connection, table: str, required: set[str]) -> None:
    if not required <= _columns(connection, table):
        raise PrecheckError("database_schema_incomplete")


def _count_rows(connection: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    try:
        return list(connection.execute(sql))
    except sqlite3.Error:
        raise PrecheckError("database_rows_unreadable") from None


def inspect_relay(path: Path) -> dict:
    connection = _readonly_connection(path)
    try:
        _require_columns(
            connection,
            "slow_tasks",
            {"executor_kind", "result_contract_version", "status"},
        )
        rows = _count_rows(
            connection,
            "SELECT executor_kind, result_contract_version, status, COUNT(*) AS amount "
            "FROM slow_tasks GROUP BY executor_kind, result_contract_version, status",
        )
    finally:
        connection.close()

    by_status: Counter[str] = Counter()
    by_contract: Counter[str] = Counter()
    by_executor_contract: Counter[str] = Counter()
    active = 0
    invalid_versions = 0
    unknown_statuses = 0
    for row in rows:
        executor = str(row["executor_kind"] or "unknown")
        version_value = row["result_contract_version"]
        status = str(row["status"] or "unknown")
        amount = int(row["amount"])
        by_status[status] += amount
        if status not in KNOWN_RELAY_STATUSES:
            unknown_statuses += amount
        if status in ACTIVE_RELAY_STATUSES:
            active += amount
        version = str(version_value) if type(version_value) is int else "invalid"
        by_executor_contract[f"{executor}:{version}"] += amount
        if executor == "hermes_runs":
            by_contract[version] += amount
            if version not in {"1", "2"}:
                invalid_versions += amount
    return {
        "task_count": sum(by_status.values()),
        "tasks_by_status": dict(sorted(by_status.items())),
        "tasks_by_executor_and_contract_version": dict(sorted(by_executor_contract.items())),
        "hermes_tasks_by_contract_version": dict(sorted(by_contract.items())),
        "active_task_count": active,
        "invalid_hermes_contract_count": invalid_versions,
        "unknown_status_count": unknown_statuses,
    }


def inspect_facade(path: Path) -> dict:
    connection = _readonly_connection(path)
    try:
        _require_columns(
            connection,
            "requests",
            {"result_contract_version", "submit_state", "run_id", "hermes_run_status"},
        )
        rows = _count_rows(
            connection,
            "SELECT result_contract_version, submit_state, run_id, hermes_run_status, COUNT(*) AS amount "
            "FROM requests GROUP BY result_contract_version, submit_state, run_id, hermes_run_status",
        )
    finally:
        connection.close()

    by_contract: Counter[str] = Counter()
    by_remote_status: Counter[str] = Counter()
    unresolved = 0
    invalid_versions = 0
    missing_run_id = 0
    for row in rows:
        version_value = row["result_contract_version"]
        version = str(version_value) if type(version_value) is int else "invalid"
        submit_state = str(row["submit_state"] or "unknown")
        run_id = row["run_id"]
        remote_status = str(row["hermes_run_status"] or "unknown")
        amount = int(row["amount"])
        by_contract[version] += amount
        by_remote_status[remote_status] += amount
        if version not in {"1", "2"}:
            invalid_versions += amount
        if not run_id:
            missing_run_id += amount
        if submit_state != "accepted" or not run_id or remote_status not in TERMINAL_HERMES_STATUSES:
            unresolved += amount
    return {
        "request_count": sum(by_contract.values()),
        "requests_by_contract_version": dict(sorted(by_contract.items())),
        "requests_by_saved_hermes_status": dict(sorted(by_remote_status.items())),
        "unresolved_request_count": unresolved,
        "invalid_contract_count": invalid_versions,
        "missing_run_id_count": missing_run_id,
    }


def run_precheck(*, relay_db: Path, facade_db: Path, artifacts_dir: Path, hermes_home: Path) -> dict:
    report = {"mode": "read_only_dry_run", "decision": "blocked", "findings": []}
    try:
        report["relay"] = inspect_relay(relay_db)
    except PrecheckError as exc:
        report["findings"].append("relay_" + str(exc))
    try:
        report["facade"] = inspect_facade(facade_db)
    except PrecheckError as exc:
        report["findings"].append("facade_" + str(exc))

    for name, path in (("facade_artifacts", artifacts_dir), ("hermes_home", hermes_home)):
        if not path.is_dir() or path.is_symlink():
            report["findings"].append(name + "_directory_missing_or_not_regular")

    relay = report.get("relay")
    if relay and relay["active_task_count"]:
        report["findings"].append("relay_has_queued_running_or_uncertain_tasks")
    if relay and relay["unknown_status_count"]:
        report["findings"].append("relay_has_unrecognized_task_statuses")
    if relay and relay["invalid_hermes_contract_count"]:
        report["findings"].append("relay_has_unrecognized_hermes_contract_versions")
    facade = report.get("facade")
    if facade and facade["unresolved_request_count"]:
        report["findings"].append("facade_has_unresolved_or_nonterminal_remote_runs")
    if facade and facade["invalid_contract_count"]:
        report["findings"].append("facade_has_unrecognized_contract_versions")
    if not report["findings"]:
        report["decision"] = "clear_for_operator_review_and_backup"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--relay-db", type=Path, required=True)
    parser.add_argument("--facade-db", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--hermes-home", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_precheck(
        relay_db=args.relay_db,
        facade_db=args.facade_db,
        artifacts_dir=args.artifacts_dir,
        hermes_home=args.hermes_home,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["decision"] == "clear_for_operator_review_and_backup" else 2


if __name__ == "__main__":
    raise SystemExit(main())
