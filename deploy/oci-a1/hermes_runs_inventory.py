"""Read-only aggregate inventory of Hermes keyed Runs and their Relay/Facade bindings."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys
import time


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
KNOWN_STATUSES = TERMINAL_STATUSES | frozenset({"queued", "running", "stopping", "waiting_for_approval"})
RETENTION_SECONDS = 24 * 60 * 60
ACKNOWLEDGED_RETENTION_SECONDS = 24 * 60 * 60


class InventoryError(ValueError):
    """Sanitized inventory failure; never includes database contents or paths."""


def _readonly_connection(path: Path, label: str) -> sqlite3.Connection:
    try:
        if not path.is_file():
            raise InventoryError(f"{label}_database_unavailable")
        wal_path = Path(str(path) + "-wal")
        shm_path = Path(str(path) + "-shm")
        if wal_path.exists() and wal_path.stat().st_size > 0 and not shm_path.exists():
            raise InventoryError(f"{label}_wal_sidecar_incomplete")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return connection
    except InventoryError:
        raise
    except (OSError, sqlite3.Error, ValueError):
        raise InventoryError(f"{label}_database_unreadable") from None


def _require_columns(connection: sqlite3.Connection, table: str, required: set[str], label: str) -> None:
    try:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        raise InventoryError(f"{label}_schema_unreadable") from None
    if not required <= columns:
        raise InventoryError(f"{label}_schema_incomplete")


def _read_rows(connection: sqlite3.Connection, sql: str, label: str) -> list[sqlite3.Row]:
    try:
        return list(connection.execute(sql))
    except sqlite3.Error:
        raise InventoryError(f"{label}_rows_unreadable") from None


def _close_readonly_connection(connection: sqlite3.Connection, label: str) -> None:
    try:
        # Closing the read-only connection rolls back its read transaction.
        connection.close()
    except sqlite3.Error:
        raise InventoryError(f"{label}_database_close_failed") from None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _status(status_json: object) -> str:
    try:
        value = json.loads(status_json) if isinstance(status_json, (str, bytes, bytearray)) else None
    except (ValueError, TypeError):
        return "unknown"
    raw = value.get("status") if isinstance(value, dict) else None
    return raw if isinstance(raw, str) and raw in KNOWN_STATUSES else "unknown"


def _eligible_for_lazy_prune(row: sqlite3.Row, status: str, now: float) -> bool:
    if status not in TERMINAL_STATUSES:
        return False
    try:
        acknowledged = row["acknowledged_at"]
        retention = float(row["retention_until"] or 0)
        updated = float(row["updated_at"] or 0)
        return (
            (acknowledged is not None and float(acknowledged) <= now - ACKNOWLEDGED_RETENTION_SECONDS)
            or (retention > 0 and retention <= now)
            or (retention <= 0 and updated < now - RETENTION_SECONDS)
        )
    except (TypeError, ValueError):
        return False


def _read_hermes(path: Path, *, now: float) -> tuple[list[tuple[str | None, str | None, str]], dict]:
    connection = _readonly_connection(path, "hermes")
    try:
        _require_columns(
            connection,
            "run_idempotency",
            {"idempotency_key", "run_id", "status_json", "retention_until", "acknowledged_at", "updated_at"},
            "hermes",
        )
        rows = _read_rows(
            connection,
            "SELECT idempotency_key,run_id,status_json,retention_until,acknowledged_at,updated_at "
            "FROM run_idempotency",
            "hermes",
        )
    finally:
        _close_readonly_connection(connection, "hermes")

    by_status: Counter[str] = Counter()
    records = []
    unresolved = 0
    prune_eligible = 0
    for row in rows:
        status = _status(row["status_json"])
        by_status[status] += 1
        unresolved += status not in TERMINAL_STATUSES
        prune_eligible += _eligible_for_lazy_prune(row, status, now)
        records.append((_text(row["idempotency_key"]), _text(row["run_id"]), status))
    return records, {
        "registry_row_count": len(rows),
        "rows_by_status": dict(sorted(by_status.items())),
        "unresolved_or_unknown_row_count": unresolved,
        "terminal_rows_eligible_for_lazy_prune_count": prune_eligible,
        "default_retention_seconds": RETENTION_SECONDS,
        "acknowledged_retention_seconds": ACKNOWLEDGED_RETENTION_SECONDS,
    }


def _read_relay(path: Path) -> tuple[set[str], dict[str, set[str]]]:
    connection = _readonly_connection(path, "relay")
    try:
        _require_columns(connection, "slow_tasks", {"task_id", "backend_run_id", "executor_kind"}, "relay")
        rows = _read_rows(
            connection,
            "SELECT task_id,backend_run_id FROM slow_tasks WHERE executor_kind='hermes_runs'",
            "relay",
        )
    finally:
        _close_readonly_connection(connection, "relay")
    by_task: dict[str, set[str]] = {}
    for row in rows:
        task_id = _text(row["task_id"])
        if task_id:
            by_task.setdefault(task_id, set())
            run_id = _text(row["backend_run_id"])
            if run_id:
                by_task[task_id].add(run_id)
    return set(by_task), by_task


def _read_facade(path: Path) -> tuple[set[str], dict[str, set[str]]]:
    connection = _readonly_connection(path, "facade")
    try:
        _require_columns(connection, "requests", {"task_id", "run_id"}, "facade")
        rows = _read_rows(connection, "SELECT task_id,run_id FROM requests", "facade")
    finally:
        _close_readonly_connection(connection, "facade")
    by_task: dict[str, set[str]] = {}
    for row in rows:
        task_id = _text(row["task_id"])
        if task_id:
            by_task.setdefault(task_id, set())
            run_id = _text(row["run_id"])
            if run_id:
                by_task[task_id].add(run_id)
    return set(by_task), by_task


def inspect_inventory(*, hermes_db: Path, relay_db: Path, facade_db: Path, now: float | None = None) -> dict:
    """Read all three ledgers and return counts only; never return identifiers or raw JSON."""
    checked_at = time.time() if now is None else float(now)
    records, hermes_summary = _read_hermes(hermes_db, now=checked_at)
    relay_keys, relay_by_task = _read_relay(relay_db)
    facade_keys, facade_by_task = _read_facade(facade_db)
    relay_run_owners: dict[str, set[str]] = {}
    facade_run_owners: dict[str, set[str]] = {}
    for task_id, run_ids in relay_by_task.items():
        for run_id in run_ids:
            relay_run_owners.setdefault(run_id, set()).add(task_id)
    for task_id, run_ids in facade_by_task.items():
        for run_id in run_ids:
            facade_run_owners.setdefault(run_id, set()).add(task_id)

    relay_key_matches = relay_run_matches = facade_key_matches = facade_run_matches = 0
    relay_pair_matches = facade_pair_matches = 0
    fully_bound_both = pair_matched_one_ledger = 0
    partial_bindings = mismatched_bindings = unmatched = 0
    task_run_conflicts = run_id_owner_conflicts = 0
    for task_key, run_id, _status_value in records:
        relay_key_match = task_key is not None and task_key in relay_keys
        relay_run_match = run_id is not None and run_id in relay_run_owners
        facade_key_match = task_key is not None and task_key in facade_keys
        facade_run_match = run_id is not None and run_id in facade_run_owners
        relay_pair_match = (
            task_key is not None and run_id is not None and run_id in relay_by_task.get(task_key, set())
        )
        facade_pair_match = (
            task_key is not None and run_id is not None and run_id in facade_by_task.get(task_key, set())
        )
        relay_key_matches += relay_key_match
        relay_run_matches += relay_run_match
        facade_key_matches += facade_key_match
        facade_run_matches += facade_run_match
        relay_pair_matches += relay_pair_match
        facade_pair_matches += facade_pair_match

        task_run_conflict = (
            (relay_key_match and run_id is not None and bool(relay_by_task[task_key])
             and run_id not in relay_by_task[task_key])
            or (facade_key_match and run_id is not None and bool(facade_by_task[task_key])
                and run_id not in facade_by_task[task_key])
        )
        run_id_owner_conflict = (
            run_id is not None and any(owner != task_key for owner in relay_run_owners.get(run_id, set()))
        ) or (
            run_id is not None and any(owner != task_key for owner in facade_run_owners.get(run_id, set()))
        )
        task_run_conflicts += task_run_conflict
        run_id_owner_conflicts += run_id_owner_conflict
        evidence = relay_key_match or relay_run_match or facade_key_match or facade_run_match
        if task_run_conflict or run_id_owner_conflict:
            mismatched_bindings += 1
        elif relay_pair_match and facade_pair_match:
            fully_bound_both += 1
        elif relay_pair_match or facade_pair_match:
            pair_matched_one_ledger += 1
        elif evidence:
            partial_bindings += 1
        else:
            unmatched += 1

    return {
        "mode": "read_only_dry_run",
        "decision": "operator_review_required",
        "hermes_registry": hermes_summary,
        "ledger_matches": {
            "registry_rows_matching_relay_task_key_count": relay_key_matches,
            "registry_rows_matching_relay_run_id_count": relay_run_matches,
            "registry_rows_matching_facade_task_key_count": facade_key_matches,
            "registry_rows_matching_facade_run_id_count": facade_run_matches,
            "registry_rows_pair_matched_in_relay_count": relay_pair_matches,
            "registry_rows_pair_matched_in_facade_count": facade_pair_matches,
            "registry_rows_fully_bound_to_both_ledgers_count": fully_bound_both,
            "registry_rows_pair_matched_in_one_ledger_only_count": pair_matched_one_ledger,
            "registry_rows_partial_binding_count": partial_bindings,
            "registry_rows_mismatched_binding_count": mismatched_bindings,
            "registry_rows_unmatched_to_all_ledgers_count": unmatched,
            "registry_rows_task_key_run_id_conflict_count": task_run_conflicts,
            "registry_rows_run_id_bound_to_different_task_count": run_id_owner_conflicts,
        },
        "limitations": [
            "only keyed Runs still retained in the durable registry are inventoried",
            "this report cannot prove absence of arbitrary non-idempotent or in-memory Runs",
            "counts are not a cutover approval; inspect partial, mismatched, unmatched, and unresolved rows",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="required; the inventory never writes")
    parser.add_argument("--hermes-db", type=Path, required=True)
    parser.add_argument("--relay-db", type=Path, required=True)
    parser.add_argument("--facade-db", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("--dry-run is required")
    try:
        report = inspect_inventory(
            hermes_db=args.hermes_db,
            relay_db=args.relay_db,
            facade_db=args.facade_db,
        )
    except InventoryError as exc:
        print(json.dumps({"mode": "read_only_dry_run", "decision": "blocked", "finding": str(exc)}))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
