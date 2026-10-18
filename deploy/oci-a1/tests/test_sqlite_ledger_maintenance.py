"""Offline Ledger maintenance regression tests; no provider, VM or AWS calls."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone, timedelta
import importlib.util
import os
from pathlib import Path
import sqlite3
import unittest
from uuid import uuid4

from relay.task_store import SQLiteTaskStore


SCRIPT = Path(__file__).resolve().parents[1] / "sqlite_ledger_maintenance.py"
spec = importlib.util.spec_from_file_location("oci_ledger_maintenance", SCRIPT)
assert spec and spec.loader
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


class LedgerMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).parent / f".sqlite-ledger-maintenance-{uuid4().hex}"
        self.root.mkdir()
        self.addCleanup(self._cleanup_root)
        self.source = self.root / "ledger.sqlite3"
        self.store = SQLiteTaskStore(database_path=self.source, max_pending_tasks=16)
        old = NOW - timedelta(days=40)
        recent = NOW - timedelta(days=5)
        self._seed("done-read", status="completed", outbox="sent", inbox="read", created=old)
        self._seed("done-spoken", status="completed", outbox="sent", inbox="spoken", created=old)
        self._seed("unread", status="completed", outbox="sent", inbox="pending", created=old)
        self._seed("missing-inbox", status="completed", outbox="sent", inbox=None, created=old)
        self._seed("uncertain", status="uncertain", outbox="uncertain", inbox="pending", created=old)
        self._seed("running", status="running", outbox="claimed", inbox=None, created=old)
        self._seed(
            "stopping", status="running", outbox="claimed", inbox=None, created=old,
            stop_requested_at=old.isoformat(),
        )
        self._seed(
            "reconciled", status="failed", outbox="failed", inbox="read", created=old,
            manual_reconciled_at=old.isoformat(),
        )
        self._seed("dispatch-pending", status="failed", outbox="pending", inbox="read", created=old)
        self._seed("recent", status="completed", outbox="sent", inbox="read", created=recent)

    def _cleanup_root(self) -> None:
        for path in self.root.iterdir():
            os.chmod(path, 0o666)
            path.unlink()
        self.root.rmdir()

    def _seed(
        self,
        label: str,
        *,
        status: str,
        outbox: str,
        inbox: str | None,
        created: datetime,
        stop_requested_at: str | None = None,
        manual_reconciled_at: str | None = None,
    ) -> str:
        task_id = str(uuid4())
        created_at = created.isoformat()
        connection = self.store._connect()
        try:
            connection.execute(
                "INSERT INTO slow_tasks(task_id,client_id,conversation_id,turn_id,call_id,payload_sha256,"
                "task_text,task_kind,backend,status,created_at,expires_at,stop_requested_at,manual_reconciled_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id, "maintenance-tests", f"{label}-{task_id}", 1, f"call-{task_id}",
                    "a" * 64, f"test {label}", "research", "openab", status,
                    created_at, (created + timedelta(minutes=10)).isoformat(),
                    stop_requested_at, manual_reconciled_at,
                ),
            )
            connection.execute(
                "INSERT INTO dispatch_outbox(task_id,state,attempts,created_at) VALUES(?,?,?,?)",
                (task_id, outbox, 1, created_at),
            )
            if inbox is not None:
                connection.execute(
                    "INSERT INTO task_inbox(task_id,delivery_state,updated_at) VALUES(?,?,?)",
                    (task_id, inbox, created_at),
                )
        finally:
            connection.close()
        return task_id

    def _ledger_snapshot(self) -> tuple[object, ...]:
        with closing(sqlite3.connect(self.source)) as db:
            return (
                tuple(db.execute(
                    "SELECT task_id,status,created_at,stop_requested_at,manual_reconciled_at "
                    "FROM slow_tasks ORDER BY task_id"
                )),
                tuple(db.execute("SELECT task_id,state FROM dispatch_outbox ORDER BY task_id")),
                tuple(db.execute("SELECT task_id,delivery_state FROM task_inbox ORDER BY task_id")),
                db.execute("PRAGMA user_version").fetchone()[0],
            )

    def test_backup_integrity_restore_fixture_and_mode(self) -> None:
        destination = self.root / "backup.sqlite3"
        maintenance.backup(self.source, destination)
        maintenance.verify(destination)
        restored_store = SQLiteTaskStore(database_path=destination, max_pending_tasks=16)
        self.assertEqual(restored_store.get_by_task_id(self._task_id_for("done-read"))["status"], "completed")
        with closing(sqlite3.connect(destination)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM slow_tasks").fetchone()[0], 10)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)
        if os.name == "posix":
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            maintenance.backup(self.source, destination)

    def _task_id_for(self, label: str) -> str:
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT task_id FROM slow_tasks WHERE conversation_id LIKE ?",
                (f"{label}-%",),
            ).fetchone()
        finally:
            connection.close()
        assert row is not None
        return str(row[0])

    def test_retention_partitions_rows_and_leaves_the_source_unchanged(self) -> None:
        before = self._ledger_snapshot()
        report = maintenance.retention_report(self.source, days=30, now=NOW)
        after = self._ledger_snapshot()
        self.assertEqual(report, {"potential_terminal_rows": 2, "protected_rows": 8})
        self.assertEqual(after, before)

    def test_reject_missing_or_symlink_source(self) -> None:
        with self.assertRaises(ValueError):
            maintenance._existing_db(str(self.root / "missing.db"))
        link = self.root / "link.db"
        try:
            link.symlink_to(self.source)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        with self.assertRaises(ValueError):
            maintenance._existing_db(str(link))


if __name__ == "__main__":
    unittest.main()
