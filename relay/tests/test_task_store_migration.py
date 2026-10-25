from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from relay.task_store import AdmissionReceipt, SQLiteTaskStore, TaskAdmissionRequest

MAINTENANCE_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "oci-a1" / "sqlite_ledger_maintenance.py"
maintenance_spec = importlib.util.spec_from_file_location("oci_ledger_maintenance", MAINTENANCE_SCRIPT)
assert maintenance_spec and maintenance_spec.loader
maintenance = importlib.util.module_from_spec(maintenance_spec)
maintenance_spec.loader.exec_module(maintenance)

NOW = datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)
ACTIVE_TASK_ID = "4db5aabc-284a-4a01-8a6d-5cddcc7934f1"
TERMINAL_TASK_ID = "f8f3c5a2-4f97-4c91-ae62-6b0d3f84ea28"
GENERIC_TASK_ID = "2b3e516c-48ba-4916-af09-2d68018be291"
REPLAY_TASK_ID = "e065dbf2-6f91-4c1f-9f0a-d03b3bb6f651"

V8_SCHEMA = """
CREATE TABLE slow_tasks (
    task_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    turn_id INTEGER NOT NULL,
    call_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    task_text TEXT NOT NULL,
    task_kind TEXT NOT NULL CHECK(task_kind IN ('research','compare','summarize')),
    backend TEXT NOT NULL,
    executor_kind TEXT NOT NULL DEFAULT 'openab_acp',
    backend_run_id TEXT,
    run_started_at TEXT,
    run_deadline_at TEXT,
    next_poll_at TEXT,
    poll_attempts INTEGER NOT NULL DEFAULT 0,
    stop_requested_at TEXT,
    stop_reason TEXT,
    stop_attempts INTEGER NOT NULL DEFAULT 0,
    stop_accepted_at TEXT,
    manual_reconciled_at TEXT,
    manual_reconciled_by TEXT,
    manual_reconciliation_ref TEXT,
    manual_reconciled_state TEXT,
    manual_reconciled_run_id TEXT,
    result_contract_version INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','uncertain')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(client_id, conversation_id, turn_id, call_id)
);
CREATE TABLE dispatch_outbox (
    task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('pending','claimed','sent','failed','uncertain')),
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    claimed_until TEXT,
    dispatch_started_at TEXT,
    last_error TEXT
);
CREATE TABLE task_results (
    task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
    speak TEXT NOT NULL,
    card_json TEXT NOT NULL,
    details_md TEXT NOT NULL,
    artifact_status TEXT,
    artifact_ref TEXT,
    presentation_status TEXT,
    delivery_error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE task_inbox (
    task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
    delivery_state TEXT NOT NULL CHECK(delivery_state IN ('pending','notified','read','spoken')),
    updated_at TEXT NOT NULL
);
PRAGMA user_version=8;
"""


def _request(
    *,
    task_id: str,
    client_id: str,
    conversation_id: str,
    turn_id: int,
    call_id: str,
    task: str,
    task_kind: str,
    backend: str,
    executor_kind: str,
) -> TaskAdmissionRequest:
    return TaskAdmissionRequest(
        task_id=task_id,
        client_id=client_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        call_id=call_id,
        task=task,
        task_kind=task_kind,
        backend=backend,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        executor_kind=executor_kind,
    )


def _insert_v8_task(connection, request, *, status, run_state=None):
    values = {
        "task_id": request.task_id,
        "client_id": request.client_id,
        "conversation_id": request.conversation_id,
        "turn_id": request.turn_id,
        "call_id": request.call_id,
        "payload_sha256": request.fingerprint(),
        "task_text": request.task,
        "task_kind": request.task_kind,
        "backend": request.backend,
        "executor_kind": request.executor_kind,
        "backend_run_id": "run-historical" if run_state else None,
        "run_started_at": NOW.isoformat() if run_state else None,
        "run_deadline_at": (NOW + timedelta(minutes=5)).isoformat() if run_state else None,
        "next_poll_at": (NOW + timedelta(seconds=20)).isoformat() if run_state else None,
        "poll_attempts": 7 if run_state else 0,
        "stop_requested_at": NOW.isoformat() if run_state else None,
        "stop_reason": "owner_cancel" if run_state else None,
        "stop_attempts": 2 if run_state else 0,
        "stop_accepted_at": None,
        "manual_reconciled_at": None,
        "manual_reconciled_by": None,
        "manual_reconciliation_ref": None,
        "manual_reconciled_state": None,
        "manual_reconciled_run_id": None,
        "result_contract_version": request.result_contract_version,
        "status": status,
        "created_at": request.created_at.isoformat(),
        "expires_at": request.expires_at.isoformat(),
    }
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO slow_tasks({columns}) VALUES({placeholders})",
        tuple(values.values()),
    )


class TaskStoreMigrationTests(unittest.TestCase):
    def test_v8_migration_preserves_rows_fingerprints_run_state_and_replay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy-v8.sqlite3"
            active = _request(
                task_id=ACTIVE_TASK_ID,
                client_id="owner-active",
                conversation_id="conv-active",
                turn_id=1,
                call_id="legacy-active-call",
                task="Resume the existing task",
                task_kind="research",
                backend="owner-selected",
                executor_kind="hermes_runs",
            )
            terminal = _request(
                task_id=TERMINAL_TASK_ID,
                client_id="owner-terminal",
                conversation_id="conv-terminal",
                turn_id=2,
                call_id="legacy-terminal-call",
                task="Compare the completed result",
                task_kind="compare",
                backend="legacy-acp",
                executor_kind="openab_acp",
            )

            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(V8_SCHEMA)
            _insert_v8_task(connection, active, status="uncertain", run_state=True)
            _insert_v8_task(connection, terminal, status="completed")
            connection.execute(
                "INSERT INTO dispatch_outbox(task_id,state,attempts,created_at,dispatch_started_at,last_error) "
                "VALUES(?,?,?,?,?,?)",
                (ACTIVE_TASK_ID, "uncertain", 2, NOW.isoformat(), NOW.isoformat(), "hermes_run_interrupted"),
            )
            connection.execute(
                "INSERT INTO dispatch_outbox(task_id,state,attempts,created_at,dispatch_started_at) "
                "VALUES(?,?,?,?,?)",
                (TERMINAL_TASK_ID, "sent", 1, NOW.isoformat(), NOW.isoformat()),
            )
            connection.execute(
                "INSERT INTO task_results(task_id,speak,card_json,details_md,artifact_status,artifact_ref,"
                "presentation_status,delivery_error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (TERMINAL_TASK_ID, "Historical bounded result.", '{"title":"Done","lines":["Old result"]}',
                 "Historical bounded result.", "committed", "art_historicalartifactreference123", "failed",
                 "presentation_failed", NOW.isoformat()),
            )
            connection.execute(
                "INSERT INTO task_inbox(task_id,delivery_state,updated_at) VALUES(?,?,?)",
                (TERMINAL_TASK_ID, "spoken", NOW.isoformat()),
            )
            connection.commit()
            connection.close()

            backup_path = Path(temp_dir) / "pre-v9-backup.sqlite3"
            maintenance.backup(path, backup_path)
            maintenance.verify(path)
            maintenance.verify(backup_path)
            with sqlite3.connect(backup_path) as backup_connection:
                self.assertEqual(backup_connection.execute("PRAGMA user_version").fetchone()[0], 8)
                self.assertEqual(backup_connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            restored_path = Path(temp_dir) / "isolated-restore.sqlite3"
            shutil.copyfile(backup_path, restored_path)
            store = SQLiteTaskStore(database_path=restored_path, max_pending_tasks=8)
            maintenance.verify(restored_path)
            connection = sqlite3.connect(restored_path)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 9)
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='slow_tasks'"
            ).fetchone()[0]
            self.assertIn("'agent'", schema)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            active_row = connection.execute(
                "SELECT task_kind,payload_sha256,backend_run_id,run_started_at,run_deadline_at,next_poll_at,"
                "poll_attempts,stop_requested_at,stop_reason,stop_attempts,status FROM slow_tasks WHERE task_id=?",
                (ACTIVE_TASK_ID,),
            ).fetchone()
            self.assertEqual(active_row, (
                "research", active.fingerprint(), "run-historical", NOW.isoformat(),
                (NOW + timedelta(minutes=5)).isoformat(), (NOW + timedelta(seconds=20)).isoformat(),
                7, NOW.isoformat(), "owner_cancel", 2, "uncertain",
            ))
            terminal_row = connection.execute(
                "SELECT task_kind,payload_sha256,result_contract_version,status FROM slow_tasks WHERE task_id=?",
                (TERMINAL_TASK_ID,),
            ).fetchone()
            self.assertEqual(terminal_row, ("compare", terminal.fingerprint(), 1, "completed"))
            self.assertEqual(connection.execute(
                "SELECT state,attempts,last_error FROM dispatch_outbox WHERE task_id=?", (ACTIVE_TASK_ID,)
            ).fetchone(), ("uncertain", 2, "hermes_run_interrupted"))
            self.assertEqual(connection.execute(
                "SELECT speak,artifact_status,artifact_ref,presentation_status,delivery_error "
                "FROM task_results WHERE task_id=?", (TERMINAL_TASK_ID,)
            ).fetchone(), ("Historical bounded result.", "committed", "art_historicalartifactreference123",
                           "failed", "presentation_failed"))
            self.assertEqual(connection.execute(
                "SELECT delivery_state FROM task_inbox WHERE task_id=?", (TERMINAL_TASK_ID,)
            ).fetchone(), ("spoken",))
            connection.close()

            replay = replace(active, task_id=REPLAY_TASK_ID, task_kind="agent")
            self.assertEqual(store.admit(replay), AdmissionReceipt(task_id=ACTIVE_TASK_ID, duplicate=True))
            replay_terminal = replace(terminal, task_id=REPLAY_TASK_ID, task_kind="agent")
            self.assertEqual(store.admit(replay_terminal), AdmissionReceipt(task_id=TERMINAL_TASK_ID, duplicate=True))
            maintenance.verify(restored_path)
            connection = sqlite3.connect(restored_path)
            self.assertEqual(connection.execute(
                "SELECT task_kind,payload_sha256 FROM slow_tasks WHERE task_id=?", (ACTIVE_TASK_ID,)
            ).fetchone(), ("research", active.fingerprint()))
            self.assertEqual(connection.execute(
                "SELECT speak,artifact_status,artifact_ref,presentation_status,delivery_error "
                "FROM task_results WHERE task_id=?", (TERMINAL_TASK_ID,)
            ).fetchone(), ("Historical bounded result.", "committed", "art_historicalartifactreference123",
                           "failed", "presentation_failed"))
            self.assertEqual(connection.execute(
                "SELECT delivery_state FROM task_inbox WHERE task_id=?", (TERMINAL_TASK_ID,)
            ).fetchone(), ("spoken",))
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            connection.close()
            maintenance.verify(backup_path)
            with sqlite3.connect(backup_path) as backup_connection:
                self.assertEqual(backup_connection.execute("PRAGMA user_version").fetchone()[0], 8)
            maintenance.verify(path)
            with sqlite3.connect(path) as source_connection:
                self.assertEqual(source_connection.execute("PRAGMA user_version").fetchone()[0], 8)

            generic = _request(
                task_id=GENERIC_TASK_ID,
                client_id="owner-generic",
                conversation_id="conv-generic",
                turn_id=1,
                call_id="generic-call",
                task="Perform a generic Agent task",
                task_kind="agent",
                backend="owner-selected",
                executor_kind="hermes_runs",
            )
            self.assertFalse(store.admit(generic).duplicate)
            reopened = SQLiteTaskStore(database_path=restored_path, max_pending_tasks=8)
            connection = sqlite3.connect(restored_path)
            self.assertEqual(connection.execute(
                "SELECT task_kind FROM slow_tasks WHERE task_id=?", (GENERIC_TASK_ID,)
            ).fetchone(), ("agent",))
            connection.close()
            self.assertEqual(
                reopened.admit(replace(generic, task_id=REPLAY_TASK_ID)).task_id,
                GENERIC_TASK_ID,
            )


if __name__ == "__main__":
    unittest.main()
