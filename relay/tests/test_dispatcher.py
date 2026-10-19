import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fast_voice.contracts import ToolCallEvent
from relay.application import build_supported_relay_handler, build_supported_relay_runtime
from relay.dispatcher import AsyncDispatcher, ExecutionOutcome
from relay.dynamodb_task_store import DynamoDbTaskStore, InMemoryDynamoLedger
from relay.slow_path import SlowPathToolRouter
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
        self.started_event = asyncio.Event()

    async def execute(self, task):
        self.calls.append(task.task_id)
        self.started_event.set()
        if self.error is not None:
            raise self.error
        return self.outcome


def request_for(
    task_id,
    *,
    turn_id=1,
    call_id="call-1",
    task="Compare current options",
    executor_kind="openab_acp",
):
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
        executor_kind=executor_kind,
    )


class SlowClaimStore:
    def __init__(self, inner, started: threading.Event, delay: float = 0.15):
        self._inner = inner
        self.started = started
        self.delay = delay

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def claim_due(self, **kwargs):
        self.started.set()
        time.sleep(self.delay)
        return self._inner.claim_due(**kwargs)


class SlowAdmitStore:
    def __init__(self, inner, delay: float = 0.08):
        self._inner = inner
        self.delay = delay

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def admit(self, request):
        time.sleep(self.delay)
        return self._inner.admit(request)




class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tasks.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def store(self):
        return SQLiteTaskStore(database_path=self.db_path, max_pending_tasks=8)

    async def test_deferred_known_run_releases_claim_and_resumes_only_when_due(self):
        store = self.store()
        store.admit(request_for(TASK_ID, executor_kind="hermes_runs"))

        class DeferredThenComplete:
            def __init__(self):
                self.tasks = []
                self.deadline = None
                self.next_poll = None

            async def execute(self, task):
                self.tasks.append(task)
                if len(self.tasks) == 1:
                    started = datetime.fromisoformat(task.dispatch_started_at)
                    self.deadline = started + timedelta(days=1)
                    self.next_poll = started + timedelta(hours=1)
                    store.save_backend_run_id(
                        task.task_id,
                        "run-deferred",
                        run_started_at=started,
                        run_deadline_at=self.deadline,
                        next_poll_at=self.next_poll,
                    )
                    return ExecutionOutcome(
                        status="deferred",
                        backend_run_id="run-deferred",
                        run_started_at=started.isoformat(),
                        run_deadline_at=self.deadline.isoformat(),
                        next_poll_at=self.next_poll.isoformat(),
                        poll_attempts=1,
                    )
                return ExecutionOutcome(status="completed")

        executor = DeferredThenComplete()
        dispatcher = AsyncDispatcher(store, executor, now=lambda: NOW)
        fast_operation_completed = asyncio.Event()

        async def fast_operation():
            await asyncio.sleep(0.005)
            fast_operation_completed.set()

        fast = asyncio.create_task(fast_operation())
        handled = await asyncio.wait_for(dispatcher.dispatch_available(), timeout=0.5)
        await fast
        self.assertEqual(handled, 1)
        self.assertTrue(fast_operation_completed.is_set())
        self.assertEqual(len(executor.tasks), 1)
        row = store.get_by_task_id(TASK_ID)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["dispatch_state"], "pending")
        self.assertEqual(row["backend_run_id"], "run-deferred")
        self.assertEqual(row["run_deadline_at"], executor.deadline.isoformat())
        self.assertEqual(row["next_poll_at"], executor.next_poll.isoformat())
        self.assertEqual(store.claim_due(now=NOW + timedelta(minutes=30), lease_seconds=30), [])

        resumed = AsyncDispatcher(store, executor, now=lambda: executor.next_poll)
        self.assertEqual(await resumed.dispatch_available(), 1)
        self.assertEqual(len(executor.tasks), 2)
        self.assertEqual(executor.tasks[1].backend_run_id, "run-deferred")
        self.assertEqual(store.get_by_task_id(TASK_ID)["status"], "completed")

    async def test_deferred_first_slow_task_releases_dispatcher_for_second_slow_task(self):
        store = self.store()
        store.admit(request_for(TASK_ID, turn_id=1, call_id="slow-first", executor_kind="hermes_runs"))
        other_owner = "authenticated-client-2"
        store.admit(TaskAdmissionRequest(
            task_id=TASK_ID_2,
            client_id=other_owner,
            conversation_id="conversation-2",
            turn_id=2,
            call_id="slow-second",
            task="Compare second owner's options",
            task_kind="compare",
            backend="openab",
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=600),
            executor_kind="hermes_runs",
        ))

        class TwoSlowTasks:
            def __init__(self):
                self.calls = []

            async def execute(self, task):
                self.calls.append(task.task_id)
                if task.task_id == TASK_ID:
                    started = datetime.fromisoformat(task.dispatch_started_at)
                    deadline = started + timedelta(days=1)
                    next_poll = started + timedelta(hours=1)
                    store.save_backend_run_id(
                        task.task_id,
                        "run-first-slow",
                        run_started_at=started,
                        run_deadline_at=deadline,
                        next_poll_at=next_poll,
                    )
                    return ExecutionOutcome(
                        status="deferred",
                        backend_run_id="run-first-slow",
                        run_started_at=started.isoformat(),
                        run_deadline_at=deadline.isoformat(),
                        next_poll_at=next_poll.isoformat(),
                        poll_attempts=1,
                    )
                return ExecutionOutcome(status="completed")

        executor = TwoSlowTasks()
        dispatcher = AsyncDispatcher(store, executor, now=lambda: NOW)
        handled = await asyncio.wait_for(dispatcher.dispatch_available(), timeout=1.0)
        self.assertEqual(handled, 2)
        self.assertEqual(executor.calls, [TASK_ID, TASK_ID_2])
        first = store.get_by_task_id(TASK_ID)
        second = store.get_by_task_id(TASK_ID_2)
        self.assertEqual((first["status"], first["dispatch_state"]), ("running", "pending"))
        self.assertEqual(first["backend_run_id"], "run-first-slow")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(store.claim_due(now=NOW + timedelta(minutes=30), lease_seconds=30), [])

    async def test_background_loop_schedules_get_from_persisted_next_poll_at(self):
        store = self.store()
        admitted_at = datetime.now(timezone.utc)
        store.admit(TaskAdmissionRequest(
            task_id=TASK_ID,
            client_id=CLIENT,
            conversation_id=CONVERSATION,
            turn_id=1,
            call_id="scheduled-poll",
            task="Compare current options",
            task_kind="compare",
            backend="openab",
            created_at=admitted_at,
            expires_at=admitted_at + timedelta(minutes=10),
            executor_kind="hermes_runs",
        ))

        class PollAtPersistedTime:
            def __init__(self):
                self.calls = []
                self.next_poll = None

            async def execute(self, task):
                self.calls.append((task.task_id, datetime.now(timezone.utc)))
                if len(self.calls) == 1:
                    started = datetime.fromisoformat(task.dispatch_started_at)
                    deadline = started + timedelta(seconds=30)
                    self.next_poll = datetime.now(timezone.utc) + timedelta(seconds=0.15)
                    store.save_backend_run_id(
                        task.task_id,
                        "run-scheduled",
                        run_started_at=started,
                        run_deadline_at=deadline,
                        next_poll_at=self.next_poll,
                    )
                    return ExecutionOutcome(
                        status="deferred",
                        backend_run_id="run-scheduled",
                        run_started_at=started.isoformat(),
                        run_deadline_at=deadline.isoformat(),
                        next_poll_at=self.next_poll.isoformat(),
                        poll_attempts=1,
                    )
                return ExecutionOutcome(status="completed")

        executor = PollAtPersistedTime()
        dispatcher = AsyncDispatcher(store, executor, poll_interval_seconds=30.0)
        await dispatcher.start()
        try:
            async def wait_completed():
                while store.get_by_task_id(TASK_ID)["status"] != "completed":
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_completed(), timeout=2.0)
        finally:
            await dispatcher.stop()
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(store.get_by_task_id(TASK_ID)["status"], "completed")

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

    async def test_blocking_store_does_not_freeze_event_loop(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        started = threading.Event()
        dispatcher = AsyncDispatcher(
            SlowClaimStore(store, started),
            FakeExecutor(),
            now=lambda: NOW,
        )
        ticks = []

        async def heartbeat():
            while len(ticks) < 4:
                ticks.append(True)
                await asyncio.sleep(0.02)

        handled, _ = await asyncio.gather(
            dispatcher.dispatch_available(),
            heartbeat(),
        )
        self.assertTrue(started.is_set())
        self.assertEqual(handled, 1)
        self.assertGreaterEqual(len(ticks), 4)

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

    async def test_uncertain_execution_outcome_is_uncertain_not_failed(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        executor = FakeExecutor(outcome=ExecutionOutcome(status="uncertain", code="hermes_run_interrupted"))
        await AsyncDispatcher(store, executor, now=lambda: NOW).dispatch_available()
        row = store.get_by_task_id(TASK_ID)
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(row["dispatch_state"], "uncertain")
        self.assertEqual(row["last_error"], "hermes_run_interrupted")

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

    def test_hermes_default_requires_configuration_and_never_silently_uses_acp(self):
        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "offline-test-key",
            "AMUSANT_CLIENT_ID": CLIENT,
            "AMUSANT_BACKEND_ID": "server-backend",
            "AMUSANT_TASK_DB": str(self.db_path),
        }
        with patch("relay.application.load_acp_executor", return_value=FakeExecutor()), \
             patch("relay.application.load_hermes_runs_executor", return_value=None):
            with self.assertRaisesRegex(ValueError, "Hermes selection requires"):
                build_supported_relay_runtime(
                    expected_device_token="device-token-12",
                    environ=env,
                    sdk_types=FakeSchemaTypes,
                )

    def test_hermes_default_selects_configured_executor_without_acp(self):
        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "offline-test-key",
            "AMUSANT_CLIENT_ID": CLIENT,
            "AMUSANT_BACKEND_ID": "server-backend",
            "AMUSANT_TASK_DB": str(self.db_path),
        }
        with patch("relay.application.load_acp_executor", return_value=None), \
             patch("relay.application.load_hermes_runs_executor", return_value=FakeExecutor()), \
             patch("relay.application.build_live_provider", return_value=object()):
            _handler, dispatcher, _store = build_supported_relay_runtime(
                expected_device_token="device-token-12",
                environ=env,
                sdk_types=FakeSchemaTypes,
            )
        self.assertIsNotNone(dispatcher)

    def test_explicit_acp_rollback_ignores_stale_invalid_hermes_configuration(self):
        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "offline-test-key",
            "AMUSANT_CLIENT_ID": CLIENT,
            "AMUSANT_BACKEND_ID": "server-backend",
            "AMUSANT_TASK_DB": str(self.db_path),
            "AMUSANT_SLOW_EXECUTOR": "openab_acp",
            "AMUSANT_HERMES_RUNS_URL": "http://127.0.0.1:8642",
            "AMUSANT_FACADE_API_KEY": "local-test-api-key-123",
            # Pre-58A configuration can remain in a rollback environment.
            "AMUSANT_HERMES_TIMEOUT_SECONDS": "120",
        }
        with patch("relay.application.load_acp_executor", return_value=FakeExecutor()), \
             patch("relay.application.build_live_provider", return_value=object()):
            _handler, dispatcher, _store = build_supported_relay_runtime(
                expected_device_token="device-token-12",
                environ=env,
                sdk_types=FakeSchemaTypes,
            )
        self.assertIsNotNone(dispatcher)

    def test_hermes_default_rejects_stale_ambiguous_configuration(self):
        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "offline-test-key",
            "AMUSANT_CLIENT_ID": CLIENT,
            "AMUSANT_BACKEND_ID": "server-backend",
            "AMUSANT_TASK_DB": str(self.db_path),
            "AMUSANT_HERMES_RUNS_URL": "http://127.0.0.1:8642",
            "AMUSANT_FACADE_API_KEY": "local-test-api-key-123",
            "AMUSANT_HERMES_TIMEOUT_SECONDS": "120",
        }
        with patch("relay.application.load_acp_executor", return_value=FakeExecutor()):
            with self.assertRaisesRegex(ValueError, "AMUSANT_HERMES_TIMEOUT_SECONDS"):
                build_supported_relay_runtime(
                    expected_device_token="device-token-12",
                    environ=env,
                    sdk_types=FakeSchemaTypes,
                )

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
            "AMUSANT_SLOW_EXECUTOR": "openab_acp",  # explicit legacy recovery fixture
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


    async def test_late_commit_after_router_timeout_is_found_by_fallback_poll(self):
        store = self.store()
        executor = FakeExecutor()
        dispatcher = AsyncDispatcher(store, executor, poll_interval_seconds=0.05, now=lambda: NOW)
        notifications = []
        router = SlowPathToolRouter(
            store=SlowAdmitStore(store),
            client_id=CLIENT,
            conversation_id=CONVERSATION,
            backend_id="openab",
            default_task_kind="research",
            max_task_ttl_seconds=600,
            acceptance_timeout_seconds=0.005,
            now=lambda: NOW,
            new_task_id=lambda: TASK_ID,
            on_admitted=lambda: (notifications.append(True), dispatcher.notify()),
        )
        await dispatcher.start()
        try:
            result = await router(
                ToolCallEvent("call-late-commit", "delegate_task", {"task": "Compare options"}),
                1,
            )
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(notifications, [])
            await asyncio.wait_for(executor.started_event.wait(), timeout=0.5)
            for _ in range(30):
                row = store.get_by_task_id(TASK_ID)
                if row is not None and row["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(executor.calls, [TASK_ID])
            self.assertEqual(store.get_by_task_id(TASK_ID)["status"], "completed")
        finally:
            await dispatcher.stop()


    async def test_runtime_uses_dispatcher_as_single_recovery_owner(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(TASK_ID, now=NOW)
        class CountingStore:
            def __init__(self, inner):
                self.inner = inner
                self.recover_calls = 0
            def __getattr__(self, name):
                return getattr(self.inner, name)
            def recover_after_restart(self):
                self.recover_calls += 1
                return self.inner.recover_after_restart()
        counted = CountingStore(store)
        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "offline-test-key",
            "AMUSANT_CLIENT_ID": CLIENT,
            "AMUSANT_BACKEND_ID": "openab",
            "AMUSANT_SLOW_EXECUTOR": "openab_acp",  # explicit legacy recovery fixture
            "AMUSANT_TASK_DB": str(self.db_path),
        }
        with patch("relay.application.build_task_store", return_value=counted), \
             patch("relay.application.load_acp_executor", return_value=FakeExecutor()), \
             patch("relay.application.build_live_provider", return_value=object()):
            _handler, dispatcher, runtime_store = build_supported_relay_runtime(
                expected_device_token="device-token-12",
                environ=env,
                sdk_types=FakeSchemaTypes,
            )
        self.assertIsNotNone(dispatcher)
        self.assertIs(runtime_store, counted)
        self.assertEqual(counted.recover_calls, 0)
        self.assertEqual(store.get_by_task_id(TASK_ID)["status"], "running")
        await dispatcher.start()
        try:
            self.assertEqual(counted.recover_calls, 1)
            self.assertEqual(store.get_by_task_id(TASK_ID)["status"], "uncertain")
        finally:
            await dispatcher.stop()




    async def test_duplicate_admission_wakes_pending_task_without_second_execution(self):
        store = self.store()
        class PausingExecutor:
            def __init__(self):
                self.calls = []
                self.started = asyncio.Event()
                self.release = asyncio.Event()
            async def execute(self, task):
                self.calls.append(task.task_id)
                self.started.set()
                await self.release.wait()
                return ExecutionOutcome(status="completed")
        executor = PausingExecutor()
        dispatcher = AsyncDispatcher(store, executor, poll_interval_seconds=30.0, now=lambda: NOW)
        router = SlowPathToolRouter(
            store=store,
            client_id=CLIENT,
            conversation_id=CONVERSATION,
            backend_id="openab",
            default_task_kind="research",
            max_task_ttl_seconds=600,
            acceptance_timeout_seconds=1,
            now=lambda: NOW,
            new_task_id=lambda: TASK_ID,
            on_admitted=dispatcher.notify,
        )
        event = ToolCallEvent("call-duplicate-wake", "delegate_task", {"task": "Compare options"})
        await dispatcher.start()
        try:
            first = await router(event, 1)
            self.assertEqual(first["status"], "accepted")
            await asyncio.wait_for(executor.started.wait(), timeout=0.5)
            duplicate = await router(event, 1)
            self.assertTrue(duplicate["duplicate"])
            await asyncio.sleep(0.05)
            self.assertEqual(executor.calls, [TASK_ID])
            executor.release.set()
            for _ in range(50):
                if store.get_by_task_id(TASK_ID)["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(store.get_by_task_id(TASK_ID)["status"], "completed")
            self.assertEqual(executor.calls, [TASK_ID])
        finally:
            executor.release.set()
            await dispatcher.stop()

    async def test_background_loop_recovers_pending_work(self):
        store = self.store()
        store.admit(request_for(TASK_ID, turn_id=1, call_id="call-1"))
        store.admit(TaskAdmissionRequest(
            task_id=TASK_ID_2,
            client_id="authenticated-client-2",
            conversation_id="conversation-2",
            turn_id=2,
            call_id="call-2",
            task="Summarize sources",
            task_kind="compare",
            backend="openab",
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=600),
        ))
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


    def test_poll_interval_rejects_nonfinite_bool_and_out_of_bounds(self):
        self.assertEqual(AsyncDispatcher(self.store(), FakeExecutor())._poll_interval_seconds, 1.0)
        for invalid in (True, False, 0, -1, float("nan"), float("inf"), float("-inf"), 60.01):
            with self.subTest(interval=invalid), self.assertRaises(ValueError):
                AsyncDispatcher(self.store(), FakeExecutor(), poll_interval_seconds=invalid)

    async def test_empty_claim_race_notifies_from_worker_thread_without_lost_wakeup(self):
        inner = self.store()
        executor = FakeExecutor()

        class CommitAfterEmptyClaim:
            def __init__(self, store):
                self.store = store
                self.dispatcher = None
                self.first = True

            def __getattr__(self, name):
                return getattr(self.store, name)

            def claim_due(self, **kwargs):
                claimed = self.store.claim_due(**kwargs)
                if self.first:
                    self.first = False
                    if claimed:
                        raise AssertionError("race setup expected an empty first claim")
                    # Simulate an admission commit just after the empty query, then
                    # notify from the to_thread worker that owns this synchronous call.
                    self.store.admit(request_for(TASK_ID))
                    self.dispatcher.notify()
                return claimed

        wrapped = CommitAfterEmptyClaim(inner)
        dispatcher = AsyncDispatcher(
            wrapped, executor, poll_interval_seconds=30.0, now=lambda: NOW
        )
        wrapped.dispatcher = dispatcher
        await dispatcher.start()
        try:
            await asyncio.wait_for(executor.started_event.wait(), timeout=0.5)
            for _ in range(20):
                if inner.get_by_task_id(TASK_ID)["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
        finally:
            await dispatcher.stop()
        self.assertEqual(executor.calls, [TASK_ID])
        self.assertEqual(inner.get_by_task_id(TASK_ID)["status"], "completed")

    async def test_dynamodb_idle_query_rate_uses_bounded_fallback(self):
        class CountingLedger(InMemoryDynamoLedger):
            def __init__(self):
                super().__init__()
                self.query_prefix_calls = 0
            def query_prefix(self, pk, prefix):
                self.query_prefix_calls += 1
                return super().query_prefix(pk, prefix)

        ledger = CountingLedger()
        store = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        dispatcher = AsyncDispatcher(store, FakeExecutor(), poll_interval_seconds=15.0, now=lambda: NOW)
        await dispatcher.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            await dispatcher.stop()
        # One startup recovery Query + one immediate drain Query; no 20Hz idle loop.
        self.assertLessEqual(ledger.query_prefix_calls, 2)


if __name__ == "__main__":
    unittest.main()
