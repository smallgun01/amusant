"""Offline-safe SQLite Ledger maintenance for the OCI single-host target.

Backup uses SQLite's online backup API, never a raw copy of a live WAL file.
Retention is intentionally READ ONLY until a separately reviewed deletion policy exists.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import sqlite3
import sys

EXPECTED_LEDGER_SCHEMA_VERSION = 8
REQUIRED_LEDGER_COLUMNS = {
    "slow_tasks": {"task_id", "status", "created_at", "stop_requested_at", "manual_reconciled_at", "result_contract_version"},
    "dispatch_outbox": {"task_id", "state"},
    "task_inbox": {"task_id", "delivery_state", "updated_at"},
    "task_results": {
        "task_id", "created_at", "artifact_status", "artifact_ref",
        "presentation_status", "delivery_error",
    },
}


def _existing_db(value: str) -> Path:
    source = Path(value).expanduser()
    if not source.is_absolute() or source.is_symlink() or not source.is_file():
        raise ValueError("source must be an existing absolute regular SQLite file, not a symlink")
    return source


def _connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _assert_integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise ValueError("SQLite foreign-key check failed")
    _assert_ledger_schema(connection)


def _assert_ledger_schema(connection: sqlite3.Connection) -> None:
    # Keep this version synchronized with relay.task_store.SCHEMA_VERSION without
    # importing the Relay package into this standalone offline utility.
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != EXPECTED_LEDGER_SCHEMA_VERSION:
        raise ValueError("SQLite Ledger schema version mismatch")
    for table, required_columns in REQUIRED_LEDGER_COLUMNS.items():
        columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if not required_columns.issubset(columns):
            raise ValueError("SQLite Ledger schema is incomplete")


def backup(source: Path, destination: Path) -> None:
    source = _existing_db(str(source))
    if not destination.is_absolute() or destination.is_symlink() or destination.exists():
        raise ValueError("destination must be a new absolute path, not an existing file/symlink")
    if not destination.parent.is_dir():
        raise ValueError("destination directory does not exist")
    if source.resolve() == destination.resolve():
        raise ValueError("source and destination must differ")
    # Ensure restrictive mode before writing any potentially sensitive task text.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    try:
        with closing(_connect_readonly(source)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=128, sleep=0.05)
            _assert_integrity(dst)
            dst.commit()
        os.chmod(destination, 0o600)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def verify(source: Path) -> None:
    source = _existing_db(str(source))
    with closing(_connect_readonly(source)) as connection:
        _assert_integrity(connection)


def retention_report(source: Path, *, days: int, now: datetime) -> dict[str, int]:
    if days <= 0 or days > 3650:
        raise ValueError("retention days must be 1..3650")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    source = _existing_db(str(source))
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=days)).isoformat()
    with closing(_connect_readonly(source)) as connection:
        # One read statement gives both counts the same WAL snapshot. Only acknowledged
        # terminal rows are candidates; every other ledger row is reported as protected.
        total, potential = connection.execute(
            """SELECT COUNT(*), COALESCE(SUM(CASE WHEN
                   t.status IN ('completed','failed')
                   AND o.state IN ('sent','failed')
                   AND t.created_at < ?
                   AND i.delivery_state IN ('spoken','read')
                   AND i.updated_at < ?
                   AND (r.task_id IS NULL OR r.created_at < ?)
                   AND t.manual_reconciled_at IS NULL
                   AND t.stop_requested_at IS NULL
                   THEN 1 ELSE 0 END), 0)
               FROM slow_tasks AS t
               LEFT JOIN dispatch_outbox AS o ON o.task_id=t.task_id
               LEFT JOIN task_inbox AS i ON i.task_id=t.task_id
               LEFT JOIN task_results AS r ON r.task_id=t.task_id""",
            (cutoff, cutoff, cutoff),
        ).fetchone()
        return {
            "potential_terminal_rows": int(potential),
            "protected_rows": int(total) - int(potential),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite ledger maintenance (non-destructive)")
    parser.add_argument("operation", choices=("backup", "verify", "retention-report"))
    parser.add_argument("--db", required=True, help="absolute source SQLite file")
    parser.add_argument("--output", help="new absolute path for backup only")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        source = _existing_db(args.db)
        if args.operation == "backup":
            if not args.output:
                raise ValueError("--output is required for backup")
            backup(source, Path(args.output))
            print("backup created and integrity verified")
        elif args.operation == "verify":
            if args.output:
                raise ValueError("--output only valid with backup")
            verify(source)
            print("integrity and foreign-key checks passed")
        else:
            if args.output:
                raise ValueError("--output only valid with backup")
            print(retention_report(source, days=args.days, now=datetime.now(timezone.utc)))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"ledger maintenance failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
