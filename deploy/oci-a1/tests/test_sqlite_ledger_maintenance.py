"""Offline Ledger maintenance regression tests; no provider, VM or AWS calls."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import importlib.util
import sqlite3
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "sqlite_ledger_maintenance.py"
spec = importlib.util.spec_from_file_location("oci_ledger_maintenance", SCRIPT)
assert spec and spec.loader
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class LedgerMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "ledger.sqlite3"
        with sqlite3.connect(self.source) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE slow_tasks (task_id TEXT PRIMARY KEY,status TEXT,created_at TEXT,
                  stop_requested_at TEXT,manual_reconciled_at TEXT);
                CREATE TABLE dispatch_outbox (task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id),
                  state TEXT);
                CREATE TABLE task_inbox (task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id),
                  delivery_state TEXT);
            """)
            old = (datetime.now(timezone.utc)-timedelta(days=40)).isoformat()
            for identifier,status,outbox,delivery in [
                ("done","completed","sent","read"),
                ("unread","completed","sent","pending"),
                ("uncertain","uncertain","uncertain","pending"),
                ("running","running","claimed",None)
            ]:
                db.execute("INSERT INTO slow_tasks VALUES (?,?,?,NULL,NULL)", (identifier,status,old))
                db.execute("INSERT INTO dispatch_outbox VALUES (?,?)",(identifier,outbox))
                if delivery:
                    db.execute("INSERT INTO task_inbox VALUES (?,?)",(identifier,delivery))
            db.commit()

    def test_backup_integrity_and_records(self) -> None:
        destination=self.root/"backup.sqlite3"
        maintenance.backup(self.source,destination)
        maintenance.verify(destination)
        with sqlite3.connect(destination) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM slow_tasks").fetchone()[0],4)
        self.assertEqual(destination.stat().st_mode & 0o777,0o600)
        with self.assertRaises(ValueError):
            maintenance.backup(self.source,destination)

    def test_retention_is_read_only_and_protects_unresolved_and_unread(self) -> None:
        before=self.source.read_bytes()
        report=maintenance.retention_report(self.source,days=30,now=datetime.now(timezone.utc))
        self.assertEqual(report["potential_terminal_rows"],1)
        self.assertEqual(report["protected_rows"],2)
        self.assertEqual(self.source.read_bytes(),before)

    def test_reject_missing_or_symlink_source(self) -> None:
        with self.assertRaises(ValueError):
            maintenance._existing_db(str(self.root/"missing.db"))
        link=self.root/"link.db"
        link.symlink_to(self.source)
        with self.assertRaises(ValueError):
            maintenance._existing_db(str(link))


if __name__ == "__main__":
    unittest.main()
