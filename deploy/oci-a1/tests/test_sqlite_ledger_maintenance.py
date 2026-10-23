"""Offline Ledger maintenance regression tests; no provider, VM or AWS calls."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
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
        self._seed(
            "done-read", status="completed", outbox="sent", inbox="read", created=old,
            result_created_at=old + timedelta(hours=1),
        )
        self._seed(
            "done-spoken", status="completed", outbox="sent", inbox="spoken", created=old,
            result_created_at=old + timedelta(hours=1),
        )
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
        self._seed(
            "recent-delivery", status="completed", outbox="sent", inbox="read", created=old,
            inbox_updated_at=NOW, result_created_at=old + timedelta(hours=1),
        )
        self._seed(
            "recent-result", status="completed", outbox="sent", inbox="spoken", created=old,
            inbox_updated_at=old + timedelta(hours=1), result_created_at=NOW,
        )

    def _cleanup_root(self) -> None:
        for path in self.root.iterdir():
            if not path.is_symlink():
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
        inbox_updated_at: datetime | None = None,
        result_created_at: datetime | None = None,
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
                    (task_id, inbox, (inbox_updated_at or created).isoformat()),
                )
            if result_created_at is not None:
                connection.execute(
                    "INSERT INTO task_results(task_id,speak,card_json,details_md,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (task_id, "done", "{}", "fixture result", result_created_at.isoformat()),
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
                tuple(db.execute("SELECT task_id,created_at FROM task_results ORDER BY task_id")),
                db.execute("PRAGMA user_version").fetchone()[0],
            )

    def test_backup_integrity_restore_fixture_and_mode(self) -> None:
        destination = self.root / "backup.sqlite3"
        maintenance.backup(self.source, destination)
        maintenance.verify(destination)
        restored_store = SQLiteTaskStore(database_path=destination, max_pending_tasks=16)
        self.assertEqual(restored_store.get_by_task_id(self._task_id_for("done-read"))["status"], "completed")
        with closing(sqlite3.connect(destination)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM slow_tasks").fetchone()[0], 12)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
            task_columns = {row[1] for row in db.execute("PRAGMA table_info(slow_tasks)")}
            result_columns = {row[1] for row in db.execute("PRAGMA table_info(task_results)")}
            self.assertIn("result_contract_version", task_columns)
            self.assertTrue({"artifact_status", "artifact_ref", "presentation_status", "delivery_error"} <= result_columns)
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
        self.assertEqual(report, {"potential_terminal_rows": 2, "protected_rows": 10})
        self.assertEqual(after, before)

    def test_verify_and_backup_reject_unrelated_sqlite_databases(self) -> None:
        fixtures = (
            ("empty.sqlite3", ""),
            ("unrelated.sqlite3", "CREATE TABLE unrelated (value TEXT)"),
            ("incomplete-v8.sqlite3", "PRAGMA user_version=8; CREATE TABLE slow_tasks (task_id TEXT)"),
        )
        for filename, setup_sql in fixtures:
            with self.subTest(filename=filename):
                source = self.root / filename
                with closing(sqlite3.connect(source)) as db:
                    if setup_sql:
                        db.executescript(setup_sql)
                with self.assertRaises(ValueError):
                    maintenance.verify(source)
                destination = self.root / f"backup-{filename}"
                with self.assertRaises(ValueError):
                    maintenance.backup(source, destination)
                self.assertFalse(destination.exists())

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
