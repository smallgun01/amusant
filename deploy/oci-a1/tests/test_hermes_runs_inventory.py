from __future__ import annotations

import hashlib
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy" / "oci-a1" / "hermes_runs_inventory.py"


def load_inventory():
    spec = importlib.util.spec_from_file_location("hermes_runs_inventory", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def create_hermes_db(path: Path, rows):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                """CREATE TABLE run_idempotency (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    status_json TEXT NOT NULL,
                    owner_pid INTEGER NOT NULL DEFAULT 0,
                    owner_started INTEGER NOT NULL DEFAULT 0,
                    retention_until REAL NOT NULL DEFAULT 0,
                    acknowledged_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, idempotency_key)
                )"""
            )
            connection.executemany(
                "INSERT INTO run_idempotency(scope,idempotency_key,fingerprint,run_id,status_json,"
                "retention_until,acknowledged_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                rows,
            )


def create_relay_db(path: Path, rows):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE slow_tasks(task_id TEXT,backend_run_id TEXT,executor_kind TEXT)"
            )
            connection.executemany("INSERT INTO slow_tasks VALUES(?,?,?)", rows)


def create_facade_db(path: Path, rows):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute("CREATE TABLE requests(owner_scope TEXT,task_id TEXT,run_id TEXT)")
            connection.executemany("INSERT INTO requests VALUES(?,?,?)", rows)


class HermesRunsInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hermes_db = self.root / "runs_idempotency.db"
        self.relay_db = self.root / "relay.sqlite3"
        self.facade_db = self.root / "facade.sqlite3"
        now = 1_800_000_000.0
        create_hermes_db(self.hermes_db, [
            ("scope-secret", "task-complete", "fingerprint", "run-complete",
             json.dumps({"status": "completed", "output": "PRIVATE_PROMPT_SECRET"}),
             0, None, now - 100_000, now - 100_000),
            ("scope-secret", "task-active", "fingerprint", "run-active",
             json.dumps({"status": "running"}), 0, None, now, now),
            ("scope-secret", "task-conflict", "fingerprint", "run-hermes-side",
             json.dumps({"status": "failed"}), 0, None, now, now),
            ("scope-secret", "unmatched-task-secret", "fingerprint", "run-unmatched-secret",
             json.dumps({"status": "new_status", "prompt": "PRIVATE_PROMPT_SECRET"}), 0, None, now, now),
        ])
        create_relay_db(self.relay_db, [
            ("task-complete", "run-complete", "hermes_runs"),
            ("task-active", "run-active", "hermes_runs"),
            ("task-conflict", "run-relay-side", "hermes_runs"),
            ("acp-task", "run-not-hermes", "openab_acp"),
        ])
        create_facade_db(self.facade_db, [
            ("owner-secret", "task-complete", "run-complete"),
            ("owner-secret", "task-active", "run-active"),
            ("owner-secret", "task-conflict", "run-facade-side"),
        ])

    def tearDown(self):
        self.temp.cleanup()

    def test_summary_matches_task_keys_and_run_ids_without_exposing_rows(self):
        inventory = load_inventory()
        report = inventory.inspect_inventory(
            hermes_db=self.hermes_db,
            relay_db=self.relay_db,
            facade_db=self.facade_db,
            now=1_800_000_000.0,
        )
        self.assertEqual(report["decision"], "operator_review_required")
        self.assertEqual(report["hermes_registry"]["registry_row_count"], 4)
        self.assertEqual(report["hermes_registry"]["rows_by_status"], {
            "completed": 1,
            "failed": 1,
            "running": 1,
            "unknown": 1,
        })
        self.assertEqual(report["hermes_registry"]["unresolved_or_unknown_row_count"], 2)
        self.assertEqual(report["hermes_registry"]["terminal_rows_eligible_for_lazy_prune_count"], 1)
        self.assertEqual(report["ledger_matches"], {
            "registry_rows_matching_relay_task_key_count": 3,
            "registry_rows_matching_relay_run_id_count": 2,
            "registry_rows_matching_facade_task_key_count": 3,
            "registry_rows_matching_facade_run_id_count": 2,
            "registry_rows_unmatched_to_all_ledgers_count": 1,
            "task_key_with_run_id_binding_conflict_count": 1,
        })
        serialized = json.dumps(report)
        for private_value in (
            "task-complete", "task-active", "run-complete", "run-unmatched-secret",
            "unmatched-task-secret", "scope-secret", "owner-secret", "PRIVATE_PROMPT_SECRET",
            "status_json", "fingerprint",
        ):
            self.assertNotIn(private_value, serialized)

    def test_inventory_does_not_modify_databases_or_create_sidecars(self):
        inventory = load_inventory()
        paths = (self.hermes_db, self.relay_db, self.facade_db)
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        inventory.inspect_inventory(
            hermes_db=self.hermes_db,
            relay_db=self.relay_db,
            facade_db=self.facade_db,
            now=1_800_000_000.0,
        )
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
        for path in paths:
            self.assertFalse(Path(str(path) + "-wal").exists())
            self.assertFalse(Path(str(path) + "-shm").exists())

    def test_missing_registry_fails_closed_without_creating_a_database(self):
        inventory = load_inventory()
        missing = self.root / "missing-runs.db"
        with self.assertRaisesRegex(inventory.InventoryError, "hermes_database_unavailable"):
            inventory.inspect_inventory(
                hermes_db=missing,
                relay_db=self.relay_db,
                facade_db=self.facade_db,
            )
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
