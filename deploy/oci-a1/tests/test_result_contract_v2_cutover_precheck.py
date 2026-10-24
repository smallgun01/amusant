from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from result_contract_v2_cutover_precheck import run_precheck


class ResultContractV2CutoverPrecheckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / (".cutover-precheck-" + secrets.token_hex(6))
        self.root.mkdir()
        self.relay_db = self.root / "task-ledger.sqlite3"
        self.facade_db = self.root / "facade-ledger.sqlite3"
        self.artifacts = self.root / "artifacts"
        self.hermes = self.root / "hermes-home"
        self.artifacts.mkdir()
        self.hermes.mkdir()
        with self._connection(self.relay_db) as conn:
            conn.execute(
                "CREATE TABLE slow_tasks (executor_kind TEXT, result_contract_version INTEGER, status TEXT)"
            )
            conn.execute(
                "INSERT INTO slow_tasks VALUES ('hermes_runs', 1, 'completed'), ('hermes_runs', 1, 'failed')"
            )
        with self._connection(self.facade_db) as conn:
            conn.execute(
                "CREATE TABLE requests (result_contract_version INTEGER, submit_state TEXT, "
                "run_id TEXT, hermes_run_status TEXT)"
            )
            conn.execute(
                "INSERT INTO requests VALUES (1, 'accepted', 'run_old_success', 'completed'), "
                "(1, 'accepted', 'run_old_failure', 'failed')"
            )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _precheck(self):
        return run_precheck(
            relay_db=self.relay_db,
            facade_db=self.facade_db,
            artifacts_dir=self.artifacts,
            hermes_home=self.hermes,
        )

    @contextmanager
    def _connection(self, path):
        connection = sqlite3.connect(path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def test_terminal_v1_history_is_counted_and_not_rewritten(self):
        before = self.relay_db.read_bytes(), self.facade_db.read_bytes()
        report = self._precheck()
        after = self.relay_db.read_bytes(), self.facade_db.read_bytes()
        self.assertEqual(report["decision"], "clear_for_operator_review_and_backup")
        self.assertEqual(report["relay"]["hermes_tasks_by_contract_version"], {"1": 2})
        self.assertEqual(report["facade"]["requests_by_contract_version"], {"1": 2})
        self.assertEqual(before, after)
        self.assertNotIn("run_old_success", json.dumps(report))

    def test_any_active_relay_task_blocks_cutover(self):
        with self._connection(self.relay_db) as conn:
            conn.execute("INSERT INTO slow_tasks VALUES ('hermes_runs', 2, 'running')")
        report = self._precheck()
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("relay_has_queued_running_or_uncertain_tasks", report["findings"])
        self.assertEqual(report["relay"]["active_task_count"], 1)

    def test_unresolved_or_nonterminal_facade_run_blocks_cutover(self):
        with self._connection(self.facade_db) as conn:
            conn.execute(
                "INSERT INTO requests VALUES (2, 'accepted', 'run_active', 'running')"
            )
        report = self._precheck()
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("facade_has_unresolved_or_nonterminal_remote_runs", report["findings"])
        self.assertEqual(report["facade"]["unresolved_request_count"], 1)

    def test_unknown_facade_status_blocks_cutover(self):
        with self._connection(self.facade_db) as conn:
            conn.execute(
                "INSERT INTO requests VALUES (2, 'accepted', 'run_unknown', NULL)"
            )
        report = self._precheck()
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("facade_has_unresolved_or_nonterminal_remote_runs", report["findings"])

    def test_unknown_relay_status_blocks_cutover(self):
        with self._connection(self.relay_db) as conn:
            conn.execute("INSERT INTO slow_tasks VALUES ('hermes_runs', 2, 'mystery')")
        report = self._precheck()
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("relay_has_unrecognized_task_statuses", report["findings"])

    def test_nonempty_wal_without_existing_shm_blocks_without_creating_sidecar(self):
        wal = Path(str(self.relay_db) + "-wal")
        shm = Path(str(self.relay_db) + "-shm")
        wal.write_bytes(b"uncheckpointed WAL marker")

        report = self._precheck()

        self.assertEqual(report["decision"], "blocked")
        self.assertIn("relay_database_wal_shared_memory_missing", report["findings"])
        self.assertFalse(shm.exists())


if __name__ == "__main__":
    unittest.main()
