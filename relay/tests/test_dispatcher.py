import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from relay.application import build_supported_relay_handler
from relay.dispatcher import AsyncDispatcher, ExecutionOutcome
from relay.task_store import SQLiteTaskStore, TaskAdmissionRequest

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
TASK_ID = "4db5aabc-284a-4a01-8a6d-5cddcc7934f1"
TASK_ID_2 = "f8f3c5a2-4f97-4c91-ae62-6b0d3f84ea28"
CLIENT = "authenticated-client-1"
CONVERSATION = "conversation-1"


class FakeSchemaTypes:
    class Behavior:
        BLOCKING = "BLOCKING"

    class Schema:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FunctionDeclaration:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Tool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


class FakeExecutor:
    def __init__(self, outcome=None, error=None):
        self.outcome = outcome or ExecutionOutcome(status="completed")
        self.error = error
        self.calls = []

    async def execute(self, task):
        self.calls.append(task.task_id)
        if self.error is not None:
            raise self.error
        return self.outcome


def request_for(task_id, *, turn_id=1, call_id="call-1", task="Compare current options"):
    return TaskAdmissionRequest(
        task_id=task_id,
        client_id=CLIENT,
        conversation_id=CONVERSATION,
        turn_id=turn_id,
        call_id=call_id,
        task=task,
        task_kind="compare",
        backend="openab",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=600),
    )


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tasks.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def store(self):
        return SQLiteTaskStore(database_path=self.db_path, max_pending_tasks=8)

    async def test_fake_executor_completes_admitted_task(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        executor = FakeExecutor()
        dispatcher = AsyncDispatcher(store, executor, now=lambda: NOW)
        handled = await dispatcher.dispatch_available()
        self.assertEqual(handled, 1)
        self.assertEqual(executor.calls, [TASK_ID])
        row = store.get_by_task_id(TASK_ID)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["dispatch_state"], "sent")
        self.assertTrue(row["dispatch_started"])
        self.assertEqual(store.pending_dispatch_count(), 0)

    async def test_restart_before_acp_send_requeues_and_dispatches(self):
        first = self.store()
        first.admit(request_for(TASK_ID))
        claimed = first.claim_due(now=NOW, lease_seconds=30)
        self.assertEqual(claimed[0].task_id, TASK_ID)
        restarted = self.store()
        recovered = restarted.recover_after_restart()
        self.assertEqual((recovered.requeued, recovered.uncertain), (1, 0))
        executor = FakeExecutor()
        handled = await AsyncDispatcher(restarted, executor, now=lambda: NOW).dispatch_available()
        self.assertEqual(handled, 1)
        self.assertEqual(executor.calls, [TASK_ID])
        self.assertEqual(restarted.get_by_task_id(TASK_ID)["dispatch_state"], "sent")

    async def test_restart_after_acp_send_is_uncertain_and_not_retried(self):
        first = self.store()
        first.admit(request_for(TASK_ID))
        first.claim_due(now=NOW, lease_seconds=30)
        first.mark_dispatch_started(TASK_ID, now=NOW)
        restarted = self.store()
        recovered = restarted.recover_after_restart()
        self.assertEqual((recovered.requeued, recovered.uncertain), (0, 1))
        executor = FakeExecutor()
        handled = await AsyncDispatcher(restarted, executor, now=lambda: NOW).dispatch_available()
        self.assertEqual(handled, 0)
        self.assertEqual(executor.calls, [])
        row = restarted.get_by_task_id(TASK_ID)
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["dispatch_state"], "uncertain")
        self.assertEqual(row["last_error"], "acp_result_unconfirmed")

    async def test_executor_exception_after_send_is_uncertain(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        executor = FakeExecutor(error=RuntimeError("lost ACP socket"))
        await AsyncDispatcher(store, executor, now=lambda: NOW).dispatch_available()
        row = store.get_by_task_id(TASK_ID)
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["dispatch_state"], "uncertain")
        self.assertEqual(row["last_error"], "acp_result_unconfirmed")

    async def test_executor_failed_outcome_is_failed_not_completed(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        executor = FakeExecutor(outcome=ExecutionOutcome(status="failed", code="acp_timeout"))
        await AsyncDispatcher(store, executor, now=lambda: NOW).dispatch_available()
        row = store.get_by_task_id(TASK_ID)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["dispatch_state"], "failed")
        self.assertEqual(row["last_error"], "acp_timeout")

    async def test_attempts_exhausted_fail_closed_without_executor(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        store.claim_due(now=NOW, lease_seconds=30)
        store.recover_after_restart()
        executor = FakeExecutor()
        await AsyncDispatcher(store, executor, max_attempts=1, now=lambda: NOW).dispatch_available()
        self.assertEqual(executor.calls, [])
        row = store.get_by_task_id(TASK_ID)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["last_error"], "dispatch_attempts_exhausted")
        self.assertEqual(row["attempts"], 2)

    async def test_v1_admission_db_migrates_and_remaining_work_can_dispatch(self):
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            """
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
                status TEXT NOT NULL CHECK(status = 'queued'),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                UNIQUE(client_id, conversation_id, turn_id, call_id)
            );
            CREATE TABLE dispatch_outbox (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
                state TEXT NOT NULL CHECK(state IN ('pending','sent')),
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        fingerprint = request_for(TASK_ID).fingerprint()
        connection.execute(
            "INSERT INTO slow_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                TASK_ID, CLIENT, CONVERSATION, 1, "call-1", fingerprint,
                "Compare current options", "compare", "openab", "queued",
                NOW.isoformat(), (NOW + timedelta(seconds=600)).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO dispatch_outbox VALUES(?,?,?,?)",
            (TASK_ID, "pending", 0, NOW.isoformat()),
        )
        connection.commit()
        connection.close()
        store = self.store()
        executor = FakeExecutor()
        handled = await AsyncDispatcher(store, executor, now=lambda: NOW).dispatch_available()
        self.assertEqual(handled, 1)
        self.assertEqual(store.get_by_task_id(TASK_ID)["dispatch_state"], "sent")

    def test_supported_startup_recovers_started_work_without_executor(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(TASK_ID, now=NOW)
        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "offline-test-key",
            "AMUSANT_CLIENT_ID": CLIENT,
            "AMUSANT_BACKEND_ID": "openab",
            "AMUSANT_TASK_DB": str(self.db_path),
        }
        with patch("relay.application.build_live_provider", return_value=object()):
            build_supported_relay_handler(
                expected_device_token="device-token-12",
                environ=env,
                sdk_types=FakeSchemaTypes,
            )
        row = SQLiteTaskStore(database_path=self.db_path, max_pending_tasks=8).get_by_task_id(TASK_ID)
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["dispatch_state"], "uncertain")

    async def test_background_loop_recovers_pending_work(self):
        store = self.store()
        store.admit(request_for(TASK_ID, turn_id=1, call_id="call-1"))
        store.admit(request_for(TASK_ID_2, turn_id=2, call_id="call-2", task="Summarize sources"))
        executor = FakeExecutor()
        dispatcher = AsyncDispatcher(
            store,
            executor,
            poll_interval_seconds=0.01,
            now=lambda: NOW,
        )
        await dispatcher.start()
        try:
            for _ in range(50):
                if len(executor.calls) == 2:
                    break
                await asyncio.sleep(0.01)
        finally:
            await dispatcher.stop()
        self.assertEqual(sorted(executor.calls), [TASK_ID, TASK_ID_2])
        self.assertEqual(store.get_by_task_id(TASK_ID)["status"], "completed")
        self.assertEqual(store.get_by_task_id(TASK_ID_2)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
