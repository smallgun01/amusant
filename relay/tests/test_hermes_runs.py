"""Phase 2B task-store tests. No AWS or Hermes process is used here."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from relay.dispatcher import AsyncDispatcher
from relay.dynamodb_task_store import DynamoDbTaskStore, InMemoryDynamoLedger
from relay.hermes_runs_executor import HermesRunsExecutor
from relay.task_store import AdmissionConflict, AdmissionUnavailable, BoundedTaskResult, TaskAdmissionRequest, SQLiteTaskStore

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
CLIENT = "phase2b-test-owner"
CONVERSATION = "phase2b-test-conversation"


def request(
    task_id: str,
    executor_kind: str = "hermes_runs",
    client_id: str = CLIENT,
    expires_at: datetime | None = None,
) -> TaskAdmissionRequest:
    return TaskAdmissionRequest(
        task_id=task_id,
        client_id=client_id,
        conversation_id=CONVERSATION,
        turn_id=1,
        call_id="phase2b-call-1",
        task="Synthetic Phase 2B task",
        task_kind="research",
        backend="openab",
        created_at=NOW,
        expires_at=expires_at or NOW + timedelta(seconds=600),
        executor_kind=executor_kind,
    )


def complete_hermes_task(store, task_id: str, client_id: str = CLIENT) -> None:
    store.admit(request(task_id, client_id=client_id))
    store.claim_due(now=NOW, lease_seconds=30)
    store.mark_dispatch_started(task_id, now=NOW)
    store.save_backend_run_id(task_id, "run-" + task_id)
    store.complete(
        task_id,
        result=BoundedTaskResult(
            speak="Safe result.",
            card={"title": "Safe", "lines": ["Owner result"]},
            details_md="Bounded details.",
        ),
    )


class SQLiteHermesMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "relay.sqlite3"

    def store(self):
        return SQLiteTaskStore(database_path=self.db, max_pending_tasks=8)

    def test_backend_run_id_survives_store_reopen_and_recovery(self):
        first = self.store()
        task_id = str(uuid4())
        first.admit(request(task_id))
        claim = first.claim_due(now=NOW, lease_seconds=30)[0]
        first.mark_dispatch_started(task_id, now=NOW)
        first.save_backend_run_id(task_id, "run-phase2b-001")

        reopened = self.store()
        counts = reopened.recover_after_restart()
        self.assertEqual((counts.requeued, counts.uncertain), (1, 0))
        row = reopened.get_by_task_id(task_id)
        self.assertEqual(row["executor_kind"], "hermes_runs")
        self.assertEqual(row["backend_run_id"], "run-phase2b-001")
        self.assertFalse(row["dispatch_started"])

        resumed = reopened.claim_due(now=NOW + timedelta(seconds=1), lease_seconds=30)[0]
        self.assertEqual(resumed.task_id, claim.task_id)
        self.assertEqual(resumed.executor_kind, "hermes_runs")
        self.assertEqual(resumed.backend_run_id, "run-phase2b-001")

    def test_sqlite_expired_hermes_queue_before_first_claim_stays_expired(self):
        store = self.store()
        task_id = str(uuid4())
        store.admit(request(task_id, expires_at=NOW + timedelta(seconds=1)))
        after_queue_deadline = NOW + timedelta(seconds=2)
        result = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=after_queue_deadline)
        self.assertEqual(result["status"], "expired")
        self.assertEqual(store.claim_due(now=after_queue_deadline, lease_seconds=30), [])

    def test_queue_deadline_does_not_expire_running_or_block_known_run_resume(self):
        store = self.store()
        task_id = str(uuid4())
        expires_at = NOW + timedelta(seconds=1)
        store.admit(request(task_id, expires_at=expires_at))
        claimed = store.claim_due(now=NOW, lease_seconds=30)[0]
        store.mark_dispatch_started(task_id, now=NOW)
        store.save_backend_run_id(task_id, "run-queue-deadline")

        after_queue_deadline = NOW + timedelta(seconds=2)
        running = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=after_queue_deadline)
        self.assertEqual(running["status"], "running")

        counts = store.recover_after_restart()
        self.assertEqual((counts.requeued, counts.uncertain), (1, 0))
        waiting_for_resume = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=after_queue_deadline)
        self.assertEqual(waiting_for_resume["status"], "queued")
        resumed = store.claim_due(now=after_queue_deadline, lease_seconds=30)
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0].backend_run_id, "run-queue-deadline")

    def test_sqlite_openab_running_expiry_preserves_legacy_behavior(self):
        store = self.store()
        task_id = str(uuid4())
        expires_at = NOW + timedelta(seconds=1)
        store.admit(request(task_id, executor_kind="openab_acp", expires_at=expires_at))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        result = store.lookup_inbox(
            client_id=CLIENT,
            task_id=task_id,
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(result["status"], "expired")

    def test_expired_remote_run_is_reported_uncertain_in_inbox_after_recovery(self):
        store = self.store()
        task_id = str(uuid4())
        expires_at = NOW + timedelta(seconds=1)
        store.admit(request(task_id, expires_at=expires_at))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        store.save_backend_run_id(task_id, "run-expired-remote")

        after_queue_deadline = NOW + timedelta(seconds=2)
        store.recover_after_restart()
        executor = HermesRunsExecutor(
            url="http://127.0.0.1:8642",
            api_key="local-test-api-key-123",
            save_backend_run_id=store.save_backend_run_id,
            allow_plaintext=True,
            request_json=lambda *args: (200, {"status": "expired"}),
        )
        handled = asyncio.run(
            AsyncDispatcher(store, executor, now=lambda: after_queue_deadline).dispatch_available()
        )
        result = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=after_queue_deadline)
        self.assertEqual(handled, 1)
        self.assertEqual(result["status"], "uncertain")
        self.assertNotIn("speak", result)
        self.assertNotIn("speak_now", result)

    def test_backend_run_id_is_write_once(self):
        store = self.store()
        task_id = str(uuid4())
        store.admit(request(task_id))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        store.save_backend_run_id(task_id, "run-phase2b-001")
        store.save_backend_run_id(task_id, "run-phase2b-001")
        with self.assertRaises(AdmissionConflict):
            store.save_backend_run_id(task_id, "run-other")

    def test_hermes_result_lookup_is_scoped_to_the_authenticated_owner(self):
        store = self.store()
        task_id = str(uuid4())
        complete_hermes_task(store, task_id)
        owner_result = store.lookup_inbox(client_id=CLIENT, task_id=task_id)
        other_result = store.lookup_inbox(client_id="client-b", task_id=task_id)
        self.assertEqual(owner_result["status"], "completed")
        self.assertEqual(owner_result["speak"], "Safe result.")
        self.assertIsNone(other_result)

    def test_v4_database_migrates_with_openab_default(self):
        connection = sqlite3.connect(self.db)
        connection.executescript(
            """
            CREATE TABLE slow_tasks (
                task_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL, call_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                task_text TEXT NOT NULL, task_kind TEXT NOT NULL, backend TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                UNIQUE(client_id, conversation_id, turn_id, call_id)
            );
            CREATE TABLE dispatch_outbox (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id), state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                claimed_until TEXT, dispatch_started_at TEXT, last_error TEXT
            );
            CREATE TABLE task_results (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id), speak TEXT NOT NULL,
                card_json TEXT NOT NULL, details_md TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE task_inbox (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id), delivery_state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            PRAGMA user_version=4;
            """
        )
        connection.execute(
            "INSERT INTO slow_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy-task", CLIENT, CONVERSATION, 1, "legacy-call", "hash", "old task", "research", "openab", "queued", NOW.isoformat(), (NOW + timedelta(seconds=600)).isoformat()),
        )
        connection.execute(
            "INSERT INTO dispatch_outbox(task_id,state,attempts,created_at) VALUES(?,?,?,?)",
            ("legacy-task", "pending", 0, NOW.isoformat()),
        )
        connection.commit()
        connection.close()

        store = self.store()
        row = store.get_by_task_id("legacy-task")
        self.assertEqual(row["executor_kind"], "openab_acp")
        self.assertIsNone(row["backend_run_id"])
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
        connection.close()


class DynamoHermesMappingTests(unittest.TestCase):
    def test_hermes_result_lookup_rejects_wrong_owner_partition(self):
        ledger = InMemoryDynamoLedger()
        owner_store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        task_id = str(uuid4())
        complete_hermes_task(owner_store, task_id)
        with self.assertRaises(AdmissionUnavailable):
            owner_store.lookup_inbox(client_id="client-b", task_id=task_id)
        other_store = DynamoDbTaskStore(ledger, client_id="client-b", max_pending_tasks=8)
        self.assertIsNone(other_store.lookup_inbox(client_id="client-b", task_id=task_id))

    def test_dynamo_expired_hermes_queue_before_first_claim_stays_expired(self):
        ledger = InMemoryDynamoLedger()
        store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        task_id = str(uuid4())
        store.admit(request(task_id, expires_at=NOW + timedelta(seconds=1)))
        after_queue_deadline = NOW + timedelta(seconds=2)
        result = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=after_queue_deadline)
        self.assertEqual(result["status"], "expired")
        self.assertEqual(store.claim_due(now=after_queue_deadline, lease_seconds=30), [])

    def test_expired_queue_deadline_does_not_block_known_run_resume(self):
        ledger = InMemoryDynamoLedger()
        store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        task_id = str(uuid4())
        expires_at = NOW + timedelta(seconds=1)
        store.admit(request(task_id, expires_at=expires_at))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        store.save_backend_run_id(task_id, "run-ddb-queue-deadline")

        after_queue_deadline = NOW + timedelta(seconds=2)
        running = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=after_queue_deadline)
        self.assertEqual(running["status"], "running")

        counts = store.recover_after_restart()
        self.assertEqual((counts.requeued, counts.uncertain), (1, 0))
        waiting_for_resume = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=after_queue_deadline)
        self.assertEqual(waiting_for_resume["status"], "queued")
        resumed = store.claim_due(now=after_queue_deadline, lease_seconds=30)
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0].backend_run_id, "run-ddb-queue-deadline")

    def test_dynamo_openab_running_expiry_preserves_legacy_behavior(self):
        ledger = InMemoryDynamoLedger()
        store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        task_id = str(uuid4())
        expires_at = NOW + timedelta(seconds=1)
        store.admit(request(task_id, executor_kind="openab_acp", expires_at=expires_at))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        result = store.lookup_inbox(
            client_id=CLIENT,
            task_id=task_id,
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(result["status"], "expired")

    def test_backend_run_id_survives_restart_recovery_without_replay(self):
        ledger = InMemoryDynamoLedger()
        first = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        task_id = str(uuid4())
        first.admit(request(task_id))
        first.claim_due(now=NOW, lease_seconds=30)
        first.mark_dispatch_started(task_id, now=NOW)
        first.save_backend_run_id(task_id, "run-phase2b-ddb-001")

        reopened = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        counts = reopened.recover_after_restart()
        self.assertEqual((counts.requeued, counts.uncertain), (1, 0))
        row = reopened.get_by_task_id(task_id)
        self.assertEqual(row["executor_kind"], "hermes_runs")
        self.assertEqual(row["backend_run_id"], "run-phase2b-ddb-001")
        resumed = reopened.claim_due(now=NOW + timedelta(seconds=1), lease_seconds=30)[0]
        self.assertEqual(resumed.backend_run_id, "run-phase2b-ddb-001")


if __name__ == "__main__":
    unittest.main()
