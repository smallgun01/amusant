"""Phase 2B task-store tests. No AWS or Hermes process is used here."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from relay.dispatcher import AsyncDispatcher
from relay.dynamodb_task_store import DynamoDbTaskStore, InMemoryDynamoLedger
from relay.hermes_runs_executor import HermesRunsExecutor
from relay.task_store import (
    AdmissionBusy,
    AdmissionConflict,
    AdmissionUnavailable,
    BoundedTaskResult,
    TaskAdmissionRequest,
    SQLiteTaskStore,
)

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


def seed_legacy_sqlite_task(
    store: SQLiteTaskStore,
    task_id: str,
    *,
    created_at: datetime,
    status: str = "queued",
    outbox_state: str = "pending",
    dispatch_started_at: str | None = None,
    backend_run_id: str | None = None,
    run_deadline_at: str | None = None,
    next_poll_at: str | None = None,
) -> None:
    connection = store._connect()
    try:
        connection.execute(
            "INSERT INTO slow_tasks(task_id,client_id,conversation_id,turn_id,call_id,payload_sha256,"
            "task_text,task_kind,backend,executor_kind,backend_run_id,run_started_at,run_deadline_at,"
            "next_poll_at,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id, CLIENT, "legacy-" + task_id[:8], 1, "legacy-call-" + task_id[:8], "legacy-hash",
                "Legacy task", "research", "openab", "hermes_runs", backend_run_id,
                dispatch_started_at, run_deadline_at, next_poll_at, status, created_at.isoformat(),
                (created_at + timedelta(minutes=10)).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO dispatch_outbox(task_id,state,attempts,created_at,dispatch_started_at) VALUES(?,?,?,?,?)",
            (task_id, outbox_state, 1 if dispatch_started_at else 0, created_at.isoformat(), dispatch_started_at),
        )
        connection.commit()
    finally:
        connection.close()


def seed_legacy_dynamo_task(
    ledger: InMemoryDynamoLedger,
    task_id: str,
    *,
    created_at: datetime,
    status: str = "queued",
    outbox_state: str = "pending",
    dispatch_started_at: str | None = None,
    backend_run_id: str | None = None,
    run_deadline_at: str | None = None,
    next_poll_at: str | None = None,
) -> None:
    item = {
        "pk": CLIENT,
        "sk": "TASK#" + task_id,
        "task_id": task_id,
        "client_id": CLIENT,
        "conversation_id": "legacy-" + task_id[:8],
        "turn_id": 1,
        "call_id": "legacy-call-" + task_id[:8],
        "payload_sha256": "legacy-hash",
        "task_text": "Legacy task",
        "task_kind": "research",
        "backend": "openab",
        "executor_kind": "hermes_runs",
        "status": status,
        "created_at": created_at.isoformat(),
        "expires_at": (created_at + timedelta(minutes=10)).isoformat(),
        "outbox_state": outbox_state,
        "attempts": 1 if dispatch_started_at else 0,
        "dispatch_started_at": dispatch_started_at,
        "backend_run_id": backend_run_id,
        "run_started_at": dispatch_started_at if backend_run_id else None,
        "run_deadline_at": run_deadline_at,
        "next_poll_at": next_poll_at,
        "poll_attempts": 0,
        "stop_attempts": 0,
    }
    ledger.items[(CLIENT, "TASK#" + task_id)] = item


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


def exercise_multiple_known_run_restarts(store_factory, task_id: str) -> None:
    store = store_factory()
    now = [NOW]
    deadline = NOW + timedelta(seconds=60)  # test fixture only; not a product default
    run_id = "run-multi-restart-" + task_id
    store.admit(request(task_id, expires_at=NOW + timedelta(milliseconds=10)))
    store.claim_due(now=NOW, lease_seconds=30)
    store.mark_dispatch_started(task_id, now=NOW)
    store.save_backend_run_id(
        task_id,
        run_id,
        run_started_at=NOW,
        run_deadline_at=deadline,
        next_poll_at=NOW,
    )
    replies = [
        {"status": "running"},
        {"status": "running"},
        {"status": "completed", "output": "Recovered after repeated Relay restarts."},
    ]
    calls = []

    def request_json(method, path, body, headers):
        calls.append((method, path))
        if method != "GET" or not path.endswith("/" + run_id):
            raise AssertionError("known Run must resume by GET of the persisted ID")
        return 200, replies.pop(0)

    for cycle in range(8):
        # Each iteration opens a fresh TaskStore/Dispatcher/Executor, mirroring
        # repeated Relay process replacement over the same durable database.
        if cycle:
            store = store_factory()
        store.recover_after_restart()
        executor = HermesRunsExecutor(
            url="http://127.0.0.1:8642",
            api_key="local-test-api-key-123",
            save_backend_run_id=store.save_backend_run_id,
            update_poll_state=store.update_hermes_poll_state,
            update_stop_state=store.update_hermes_stop_state,
            run_deadline_seconds=30,
            poll_cycle_seconds=5,
            http_timeout_seconds=2,
            poll_interval_seconds=10,
            stop_grace_seconds=30,
            stop_max_attempts=3,
            stop_retry_base_seconds=1,
            stop_retry_max_seconds=5,
            allow_plaintext=True,
            clock=lambda: now[0],
            request_json=request_json,
        )
        dispatcher = AsyncDispatcher(store, executor, max_attempts=1, now=lambda: now[0])
        handled = asyncio.run(dispatcher.dispatch_available())
        assert handled == 1
        row = store.get_by_task_id(task_id)
        assert row["backend_run_id"] == run_id
        assert row["dispatch_started_at"] == NOW.isoformat()
        assert row["run_started_at"] == NOW.isoformat()
        assert row["run_deadline_at"] == deadline.isoformat()
        assert row["attempts"] >= 2
        if row["status"] == "completed":
            break
        assert row["status"] == "running"
        assert row["dispatch_state"] == "pending"
        next_poll = datetime.fromisoformat(row["next_poll_at"])
        assert store.next_hermes_wake_at() == next_poll
        # Queue TTL has expired, and a claim before persisted next_poll_at must
        # not be repeated or increment the durable attempt count.
        attempts = row["attempts"]
        assert store.claim_due(now=now[0] + timedelta(seconds=5), lease_seconds=30) == []
        assert store.get_by_task_id(task_id)["attempts"] == attempts
        now[0] = next_poll
    else:
        raise AssertionError("known Run did not complete within repeated restart attempts")

    assert calls == [("GET", "/v1/runs/" + run_id)] * 3
    result = store.get_result(task_id)
    assert result is not None and result["speak"] == "Recovered after repeated Relay restarts."
    assert store.next_hermes_wake_at() is None


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
        deadline = NOW + timedelta(seconds=30)  # test fixture only; not an approved product default
        first.save_backend_run_id(
            task_id,
            "run-phase2b-001",
            run_started_at=NOW,
            run_deadline_at=deadline,
            next_poll_at=NOW + timedelta(seconds=5),
            poll_attempts=2,
        )

        reopened = self.store()
        counts = reopened.recover_after_restart()
        self.assertEqual((counts.requeued, counts.uncertain), (1, 0))
        row = reopened.get_by_task_id(task_id)
        self.assertEqual(row["executor_kind"], "hermes_runs")
        self.assertEqual(row["backend_run_id"], "run-phase2b-001")
        self.assertTrue(row["dispatch_started"])
        self.assertEqual(row["run_started_at"], NOW.isoformat())
        self.assertEqual(row["run_deadline_at"], deadline.isoformat())
        self.assertEqual(row["next_poll_at"], (NOW + timedelta(seconds=5)).isoformat())
        self.assertEqual(row["poll_attempts"], 2)

        self.assertEqual(reopened.claim_due(now=NOW + timedelta(seconds=1), lease_seconds=30), [])
        resumed = reopened.claim_due(now=NOW + timedelta(seconds=5), lease_seconds=30)[0]
        self.assertEqual(resumed.task_id, claim.task_id)
        self.assertEqual(resumed.executor_kind, "hermes_runs")
        self.assertEqual(resumed.backend_run_id, "run-phase2b-001")
        self.assertEqual(resumed.dispatch_started_at, NOW.isoformat())
        self.assertEqual(resumed.run_deadline_at, deadline.isoformat())
        reopened.update_hermes_poll_state(
            task_id, next_poll_at=NOW + timedelta(seconds=10), poll_attempts=3
        )
        self.assertEqual(reopened.get_by_task_id(task_id)["next_poll_at"], (NOW + timedelta(seconds=10)).isoformat())

    def test_legacy_known_run_is_polled_before_older_queued_task_is_submitted(self):
        store = self.store()
        run_id = str(uuid4())
        queued_id = str(uuid4())
        seed_legacy_sqlite_task(
            store,
            run_id,
            created_at=NOW,
            status="running",
            outbox_state="pending",
            dispatch_started_at=NOW.isoformat(),
            backend_run_id="run-legacy-known",
            run_deadline_at=(NOW + timedelta(minutes=5)).isoformat(),
            next_poll_at=NOW.isoformat(),
        )
        seed_legacy_sqlite_task(store, queued_id, created_at=NOW + timedelta(seconds=1))

        claimed = store.claim_due(now=NOW + timedelta(seconds=2), lease_seconds=30, limit=2)
        self.assertEqual([task.task_id for task in claimed], [run_id])
        self.assertEqual(claimed[0].backend_run_id, "run-legacy-known")
        self.assertEqual(store.claim_due(now=NOW + timedelta(seconds=2), lease_seconds=30), [])

        store.complete(run_id)
        next_claim = store.claim_due(now=NOW + timedelta(seconds=3), lease_seconds=30)
        self.assertEqual([task.task_id for task in next_claim], [queued_id])
        self.assertIsNone(next_claim[0].backend_run_id)
        self.assertFalse(next_claim[0].dispatch_started_at)

    def test_legacy_ambiguous_task_blocks_other_queued_tasks_and_new_admission(self):
        store = self.store()
        ambiguous_id = str(uuid4())
        queued_id = str(uuid4())
        seed_legacy_sqlite_task(
            store,
            ambiguous_id,
            created_at=NOW,
            status="uncertain",
            outbox_state="uncertain",
            dispatch_started_at=NOW.isoformat(),
        )
        seed_legacy_sqlite_task(store, queued_id, created_at=NOW + timedelta(seconds=1))

        self.assertEqual(store.claim_due(now=NOW + timedelta(seconds=2), lease_seconds=30), [])
        with self.assertRaises(AdmissionBusy):
            store.admit(request(str(uuid4())))
        self.assertEqual(store.get_by_task_id(ambiguous_id)["status"], "uncertain")
        self.assertEqual(store.get_by_task_id(queued_id)["status"], "queued")

    def test_operator_no_remote_run_reconciliation_is_audited_and_unlocks_owner(self):
        store = self.store()
        task_id = str(uuid4())
        store.admit(request(task_id))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        store.mark_uncertain(task_id, code="hermes_submission_unconfirmed")

        with self.assertRaises(AdmissionUnavailable):
            store.operator_reconcile_uncertain(
                client_id=CLIENT,
                task_id=task_id,
                terminal_state="cancelled",
                verified_run_id=None,
                operator_id="operator-1",
                evidence_ref="INC-61-no-run-check",
                now=NOW,
            )
        next_request = replace(
            request(str(uuid4())),
            conversation_id="after-operator-reconciliation",
            turn_id=2,
            call_id="after-operator-reconciliation-call",
        )
        with self.assertRaises(AdmissionBusy):
            store.admit(next_request)

        receipt = store.operator_reconcile_uncertain(
            client_id=CLIENT,
            task_id=task_id,
            terminal_state="no_remote_run",
            verified_run_id=None,
            operator_id="operator-1",
            evidence_ref="INC-61-no-run-check",
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(receipt.code, "hermes_operator_confirmed_no_remote_run")
        connection = store._connect()
        try:
            audit = connection.execute(
                "SELECT manual_reconciled_by,manual_reconciliation_ref,manual_reconciled_state "
                "FROM slow_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(audit, ("operator-1", "INC-61-no-run-check", "no_remote_run"))
        self.assertFalse(store.admit(next_request).duplicate)

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
        self.assertEqual(waiting_for_resume["status"], "running")
        resumed = store.claim_due(now=after_queue_deadline, lease_seconds=30)
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0].backend_run_id, "run-queue-deadline")

    def test_sqlite_known_run_survives_multiple_restarts_without_post_or_deadline_reset(self):
        exercise_multiple_known_run_restarts(self.store, str(uuid4()))

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
        store.save_backend_run_id(
            task_id,
            "run-expired-remote",
            run_started_at=NOW,
            run_deadline_at=NOW + timedelta(seconds=60),
            next_poll_at=NOW,
        )

        after_queue_deadline = NOW + timedelta(seconds=2)
        store.recover_after_restart()
        executor = HermesRunsExecutor(
            url="http://127.0.0.1:8642",
            api_key="local-test-api-key-123",
            save_backend_run_id=store.save_backend_run_id,
            update_poll_state=store.update_hermes_poll_state,
            update_stop_state=store.update_hermes_stop_state,
            run_deadline_seconds=60,
            poll_cycle_seconds=0.01,
            http_timeout_seconds=0.05,
            poll_interval_seconds=0.001,
            stop_grace_seconds=30,
            stop_max_attempts=3,
            stop_retry_base_seconds=0.01,
            stop_retry_max_seconds=0.1,
            allow_plaintext=True,
            clock=lambda: after_queue_deadline,
            request_json=lambda *args: (200, {"status": "expired"}),
        )
        handled = asyncio.run(
            AsyncDispatcher(store, executor, now=lambda: after_queue_deadline).dispatch_available()
        )
        result = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=after_queue_deadline)
        self.assertEqual(handled, 1)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["code"], "hermes_expired")
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
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(slow_tasks)")}
        self.assertTrue({
            "run_started_at", "run_deadline_at", "next_poll_at", "poll_attempts",
            "stop_requested_at", "stop_reason", "stop_attempts", "stop_accepted_at",
        } <= columns)
        connection.close()

    def test_v5_known_run_migration_backfills_start_anchor_only(self):
        connection = sqlite3.connect(self.db)
        connection.executescript(
            """
            CREATE TABLE slow_tasks (
                task_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL, call_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                task_text TEXT NOT NULL, task_kind TEXT NOT NULL, backend TEXT NOT NULL,
                executor_kind TEXT NOT NULL, backend_run_id TEXT, status TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                UNIQUE(client_id, conversation_id, turn_id, call_id)
            );
            CREATE TABLE dispatch_outbox (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id), state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                claimed_until TEXT, dispatch_started_at TEXT, last_error TEXT
            );
            PRAGMA user_version=5;
            """
        )
        task_id = str(uuid4())
        dispatch_started = NOW.isoformat()
        connection.execute(
            "INSERT INTO slow_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, CLIENT, CONVERSATION, 1, "legacy-hermes-call", "hash", "old task", "research", "openab", "hermes_runs", "run-legacy", "running", NOW.isoformat(), (NOW + timedelta(seconds=600)).isoformat()),
        )
        connection.execute(
            "INSERT INTO dispatch_outbox(task_id,state,attempts,created_at,dispatch_started_at) VALUES(?,?,?,?,?)",
            (task_id, "claimed", 1, NOW.isoformat(), dispatch_started),
        )
        connection.commit()
        connection.close()

        store = self.store()
        row = store.get_by_task_id(task_id)
        self.assertEqual(row["run_started_at"], dispatch_started)
        self.assertIsNone(row["run_deadline_at"])
        self.assertIsNone(row["next_poll_at"])
        self.assertEqual(row["poll_attempts"], 0)
        self.assertIsNone(row["stop_requested_at"])
        self.assertEqual(row["stop_attempts"], 0)
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
        connection.close()


class DynamoHermesMappingTests(unittest.TestCase):
    def test_legacy_known_run_is_polled_before_queued_task_is_submitted(self):
        ledger = InMemoryDynamoLedger()
        store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        run_id = str(uuid4())
        queued_id = str(uuid4())
        seed_legacy_dynamo_task(
            ledger,
            run_id,
            created_at=NOW,
            status="running",
            outbox_state="pending",
            dispatch_started_at=NOW.isoformat(),
            backend_run_id="run-legacy-known-ddb",
            run_deadline_at=(NOW + timedelta(minutes=5)).isoformat(),
            next_poll_at=NOW.isoformat(),
        )
        seed_legacy_dynamo_task(ledger, queued_id, created_at=NOW + timedelta(seconds=1))

        claimed = store.claim_due(now=NOW + timedelta(seconds=2), lease_seconds=30, limit=2)
        self.assertEqual([task.task_id for task in claimed], [run_id])
        self.assertEqual(claimed[0].backend_run_id, "run-legacy-known-ddb")
        self.assertEqual(store.claim_due(now=NOW + timedelta(seconds=2), lease_seconds=30), [])

        store.complete(run_id)
        next_claim = store.claim_due(now=NOW + timedelta(seconds=3), lease_seconds=30)
        self.assertEqual([task.task_id for task in next_claim], [queued_id])
        self.assertIsNone(next_claim[0].backend_run_id)

    def test_legacy_ambiguous_task_blocks_other_queued_tasks_and_new_admission(self):
        ledger = InMemoryDynamoLedger()
        store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        ambiguous_id = str(uuid4())
        queued_id = str(uuid4())
        seed_legacy_dynamo_task(
            ledger,
            ambiguous_id,
            created_at=NOW,
            status="uncertain",
            outbox_state="uncertain",
            dispatch_started_at=NOW.isoformat(),
        )
        seed_legacy_dynamo_task(ledger, queued_id, created_at=NOW + timedelta(seconds=1))

        self.assertEqual(store.claim_due(now=NOW + timedelta(seconds=2), lease_seconds=30), [])
        with self.assertRaises(AdmissionBusy):
            store.admit(request(str(uuid4())))
        self.assertEqual(store.get_by_task_id(ambiguous_id)["status"], "uncertain")
        self.assertEqual(ledger.get_item(CLIENT, "TASK#" + queued_id)["status"], "queued")

    def test_operator_reconciliation_requires_matching_run_id_then_releases_slot(self):
        ledger = InMemoryDynamoLedger()
        store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        task_id = str(uuid4())
        run_id = "run-operator-reconcile"
        store.admit(request(task_id))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        store.save_backend_run_id(task_id, run_id)
        store.mark_uncertain(task_id, code="hermes_stop_unconfirmed")

        with self.assertRaises(AdmissionUnavailable):
            store.operator_reconcile_uncertain(
                client_id=CLIENT,
                task_id=task_id,
                terminal_state="cancelled",
                verified_run_id="run-wrong",
                operator_id="operator-1",
                evidence_ref="INC-61-terminal-check",
                now=NOW,
            )
        slot = ledger.get_item(CLIENT, "SLOT#SLOW")
        self.assertEqual(slot["active_task_id"], task_id)
        next_request = replace(
            request(str(uuid4())),
            conversation_id="after-operator-reconciliation",
            turn_id=2,
            call_id="after-operator-reconciliation-call",
        )
        with self.assertRaises(AdmissionBusy):
            store.admit(next_request)

        receipt = store.operator_reconcile_uncertain(
            client_id=CLIENT,
            task_id=task_id,
            terminal_state="cancelled",
            verified_run_id=run_id,
            operator_id="operator-1",
            evidence_ref="INC-61-terminal-check",
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(receipt.status, "cancelled")
        self.assertIsNone(ledger.get_item(CLIENT, "SLOT#SLOW"))
        audit = ledger.get_item(CLIENT, "TASK#" + task_id)
        self.assertEqual(audit["manual_reconciled_by"], "operator-1")
        self.assertEqual(audit["manual_reconciliation_ref"], "INC-61-terminal-check")
        self.assertEqual(audit["manual_reconciled_run_id"], run_id)
        self.assertFalse(store.admit(next_request).duplicate)

    def test_hermes_result_lookup_rejects_wrong_owner_partition(self):
        ledger = InMemoryDynamoLedger()
        owner_store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        task_id = str(uuid4())
        complete_hermes_task(owner_store, task_id)
        with self.assertRaises(AdmissionUnavailable):
            owner_store.lookup_inbox(client_id="client-b", task_id=task_id)
        other_store = DynamoDbTaskStore(ledger, client_id="client-b", max_pending_tasks=8)
        self.assertIsNone(other_store.lookup_inbox(client_id="client-b", task_id=task_id))

    def test_dynamo_run_deadline_surfaces_timed_out_without_claiming_cancellation(self):
        ledger = InMemoryDynamoLedger()
        store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        task_id = str(uuid4())
        store.admit(request(task_id))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        store.save_backend_run_id(
            task_id,
            "run-ddb-deadline",
            run_started_at=NOW,
            run_deadline_at=NOW + timedelta(seconds=30),
            next_poll_at=NOW,
        )
        store.mark_uncertain(task_id, code="hermes_run_deadline_exceeded")
        result = store.lookup_inbox(client_id=CLIENT, task_id=task_id, now=NOW)
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(result["code"], "hermes_run_deadline_exceeded")
        self.assertEqual(store.get_by_task_id(task_id)["status"], "uncertain")
        self.assertNotIn("speak", result)

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
        self.assertEqual(waiting_for_resume["status"], "running")
        resumed = store.claim_due(now=after_queue_deadline, lease_seconds=30)
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0].backend_run_id, "run-ddb-queue-deadline")

    def test_dynamo_known_run_survives_multiple_restarts_without_post_or_deadline_reset(self):
        ledger = InMemoryDynamoLedger()
        factory = lambda: DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        exercise_multiple_known_run_restarts(factory, str(uuid4()))

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
        deadline = NOW + timedelta(seconds=30)  # test fixture only; not an approved product default
        first.save_backend_run_id(
            task_id,
            "run-phase2b-ddb-001",
            run_started_at=NOW,
            run_deadline_at=deadline,
            next_poll_at=NOW + timedelta(seconds=5),
            poll_attempts=2,
        )

        reopened = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        counts = reopened.recover_after_restart()
        self.assertEqual((counts.requeued, counts.uncertain), (1, 0))
        row = reopened.get_by_task_id(task_id)
        self.assertEqual(row["executor_kind"], "hermes_runs")
        self.assertEqual(row["backend_run_id"], "run-phase2b-ddb-001")
        self.assertEqual(row["run_started_at"], NOW.isoformat())
        self.assertEqual(row["run_deadline_at"], deadline.isoformat())
        self.assertEqual(row["next_poll_at"], (NOW + timedelta(seconds=5)).isoformat())
        self.assertEqual(row["poll_attempts"], 2)
        self.assertEqual(reopened.claim_due(now=NOW + timedelta(seconds=1), lease_seconds=30), [])
        resumed = reopened.claim_due(now=NOW + timedelta(seconds=5), lease_seconds=30)[0]
        self.assertEqual(resumed.backend_run_id, "run-phase2b-ddb-001")
        self.assertEqual(resumed.run_deadline_at, deadline.isoformat())
        self.assertEqual(resumed.dispatch_started_at, NOW.isoformat())
        reopened.update_hermes_poll_state(
            task_id, next_poll_at=NOW + timedelta(seconds=10), poll_attempts=3
        )
        self.assertEqual(reopened.get_by_task_id(task_id)["next_poll_at"], (NOW + timedelta(seconds=10)).isoformat())


if __name__ == "__main__":
    unittest.main()
