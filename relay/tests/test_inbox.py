import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fast_voice.contracts import ToolCallEvent
from relay.application import build_supported_relay_handler
from relay.dispatcher import AsyncDispatcher, ExecutionOutcome
from relay.slow_path import INBOX_TOOL_NAME, SlowPathToolRouter, build_relay_tools
from relay.task_store import BoundedTaskResult, SQLiteTaskStore, TaskAdmissionRequest

NOW = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
TASK_ID = "4db5aabc-284a-4a01-8a6d-5cddcc7934f1"
TASK_ID_2 = "f8f3c5a2-4f97-4c91-ae62-6b0d3f84ea28"
CLIENT = "authenticated-client-1"
OTHER = "authenticated-client-2"
CONVERSATION = "conversation-1"
RESULT = BoundedTaskResult(
    speak="Option B is the better local value.",
    card={"title": "Option B is the better local value.", "lines": ["Option B is the better local value."]},
    details_md="Option B is the better local value.",
)


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
    def __init__(self, outcome=None):
        self.outcome = outcome or ExecutionOutcome(status="completed", result=RESULT)

    async def execute(self, _task):
        return self.outcome


def request_for(
    task_id,
    *,
    client_id=CLIENT,
    turn_id=1,
    call_id="call-1",
    created=NOW,
    ttl=600,
    executor_kind="openab_acp",
):
    return TaskAdmissionRequest(
        task_id=task_id,
        client_id=client_id,
        conversation_id=CONVERSATION,
        turn_id=turn_id,
        call_id=call_id,
        task="Compare current laptop options",
        task_kind="compare",
        backend="openab",
        created_at=created,
        expires_at=created + timedelta(seconds=ttl),
        executor_kind=executor_kind,
    )


def make_router(store, *, client_id=CLIENT, now=NOW):
    return SlowPathToolRouter(
        store=store,
        client_id=client_id,
        conversation_id=CONVERSATION,
        backend_id="openab",
        default_task_kind="research",
        max_task_ttl_seconds=600,
        acceptance_timeout_seconds=1.0,
        now=lambda: now,
    )


class InboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tasks.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def store(self):
        return SQLiteTaskStore(database_path=self.db_path, max_pending_tasks=8)

    async def test_queued_lookup_does_not_invent_a_result(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        result = await make_router(store)(ToolCallEvent("lookup-1", INBOX_TOOL_NAME, {}), 2)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["task_id"], TASK_ID)
        self.assertNotIn("speak", result)
        self.assertIsNone(store.get_result(TASK_ID))
        self.assertFalse(store.mark_inbox_spoken(client_id=CLIENT, task_id=TASK_ID, now=NOW))

    async def test_completed_lookup_returns_bounded_result_and_marks_read(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        await AsyncDispatcher(store, FakeExecutor(), now=lambda: NOW).dispatch_available()
        self.assertEqual(store.get_inbox(TASK_ID)["delivery_state"], "pending")
        result = await make_router(store)(ToolCallEvent("lookup-2", INBOX_TOOL_NAME, {}), 2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["speak"], RESULT.speak)
        self.assertEqual(result["delivery_state"], "read")
        self.assertNotIn("speak_now", result)
        self.assertEqual(store.get_inbox(TASK_ID)["delivery_state"], "read")

    async def test_speech_request_stays_read_until_explicit_delivery_confirmation(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        await AsyncDispatcher(store, FakeExecutor(), now=lambda: NOW).dispatch_available()
        router = make_router(store)
        result = await router(
            ToolCallEvent("lookup-speak", INBOX_TOOL_NAME, {"task_id": TASK_ID, "speak": True}),
            3,
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["speak_now"])
        self.assertEqual(result["speak"], RESULT.speak)
        self.assertEqual(result["delivery_state"], "read")
        self.assertEqual(store.get_inbox(TASK_ID)["delivery_state"], "read")
        self.assertTrue(await router.mark_spoken(TASK_ID))
        self.assertEqual(store.get_inbox(TASK_ID)["delivery_state"], "spoken")
        self.assertTrue(await router.mark_spoken(TASK_ID))
        self.assertEqual(store.get_inbox(TASK_ID)["delivery_state"], "spoken")

    async def test_other_client_cannot_read_inbox(self):
        store = self.store()
        store.admit(request_for(TASK_ID))
        await AsyncDispatcher(store, FakeExecutor(), now=lambda: NOW).dispatch_available()
        result = await make_router(store, client_id=OTHER)(
            ToolCallEvent("lookup-other", INBOX_TOOL_NAME, {"task_id": TASK_ID}),
            1,
        )
        self.assertEqual(result, {"status": "not_found"})
        self.assertEqual(store.get_inbox(TASK_ID)["delivery_state"], "pending")
        self.assertFalse(await make_router(store, client_id=OTHER).mark_spoken(TASK_ID))
        self.assertEqual(store.get_inbox(TASK_ID)["delivery_state"], "pending")

    async def test_failed_and_uncertain_are_honest_and_have_no_speak(self):
        store = self.store()
        store.admit(request_for(TASK_ID, turn_id=1, call_id="call-fail"))
        await AsyncDispatcher(
            store,
            FakeExecutor(outcome=ExecutionOutcome(status="failed", code="acp_overloaded")),
            now=lambda: NOW,
        ).dispatch_available()
        failed = await make_router(store)(
            ToolCallEvent("lookup-fail", INBOX_TOOL_NAME, {"task_id": TASK_ID}),
            2,
        )
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("speak", failed)
        store.admit(request_for(TASK_ID_2, turn_id=2, call_id="call-uncertain"))
        claimed = store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(claimed[0].task_id, now=NOW)
        store.mark_uncertain(TASK_ID_2)
        uncertain = await make_router(store)(
            ToolCallEvent("lookup-uncertain", INBOX_TOOL_NAME, {"task_id": TASK_ID_2, "speak": True}),
            3,
        )
        self.assertEqual(uncertain["status"], "uncertain")
        self.assertNotIn("speak", uncertain)
        self.assertNotIn("speak_now", uncertain)

    async def test_hermes_run_deadline_reports_policy_timeout_and_possible_remote_activity(self):
        store = self.store()
        store.admit(request_for(TASK_ID, executor_kind="hermes_runs"))
        store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(TASK_ID, now=NOW)
        store.save_backend_run_id(
            TASK_ID,
            "run-deadline",
            run_started_at=NOW,
            run_deadline_at=NOW + timedelta(minutes=1),
            next_poll_at=NOW,
        )
        store.mark_uncertain(TASK_ID, code="hermes_run_deadline_exceeded")

        result = await make_router(store)(
            ToolCallEvent("lookup-deadline", INBOX_TOOL_NAME, {"task_id": TASK_ID, "speak": True}),
            1,
        )
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(result["code"], "hermes_run_deadline_exceeded")
        self.assertIn("may still be active", result["message"])
        self.assertIn("cancellation was not confirmed", result["message"])
        self.assertNotIn("speak", result)
        self.assertNotIn("speak_now", result)

    async def test_expired_queued_task_is_reported_expired_and_not_dispatched(self):
        store = self.store()
        past = NOW - timedelta(seconds=120)
        store.admit(request_for(TASK_ID, created=past, ttl=60))
        result = await make_router(store, now=NOW)(ToolCallEvent("lookup-exp", INBOX_TOOL_NAME, {}), 1)
        self.assertEqual(result["status"], "expired")
        self.assertNotIn("speak", result)
        claimed = store.claim_due(now=NOW, lease_seconds=30)
        self.assertEqual(claimed, [])

    async def test_new_session_can_read_previous_result_after_store_reopen(self):
        first = self.store()
        first.admit(request_for(TASK_ID))
        await AsyncDispatcher(first, FakeExecutor(), now=lambda: NOW).dispatch_available()
        reopened = self.store()
        result = await make_router(reopened)(
            ToolCallEvent("lookup-reopen", INBOX_TOOL_NAME, {"task_id": TASK_ID, "speak": True}),
            9,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["speak"], RESULT.speak)
        self.assertEqual(reopened.get_inbox(TASK_ID)["delivery_state"], "read")
        self.assertTrue(await make_router(reopened).mark_spoken(TASK_ID))
        self.assertEqual(reopened.get_inbox(TASK_ID)["delivery_state"], "spoken")

    async def test_latest_task_is_used_when_task_id_omitted(self):
        store = self.store()
        older = NOW - timedelta(seconds=30)
        store.admit(request_for(TASK_ID, turn_id=1, call_id="call-old", created=older))
        store.claim_due(now=older, lease_seconds=30, limit=1)
        store.complete(TASK_ID, result=RESULT)
        store.admit(request_for(TASK_ID_2, turn_id=2, call_id="call-new", created=NOW))
        result = await make_router(store)(ToolCallEvent("lookup-latest", INBOX_TOOL_NAME, {}), 3)
        self.assertEqual(result["task_id"], TASK_ID_2)
        self.assertEqual(result["status"], "queued")

    async def test_invalid_inbox_arguments_fail_closed(self):
        store = self.store()
        router = make_router(store)
        bad = await router(ToolCallEvent("lookup-bad", INBOX_TOOL_NAME, {"backend": "openab"}), 1)
        self.assertEqual(bad, {"status": "rejected", "code": "invalid_arguments"})
        missing = await router(ToolCallEvent("lookup-missing", INBOX_TOOL_NAME, {"task_id": "not-a-uuid"}), 2)
        self.assertEqual(missing, {"status": "not_found"})

    def test_supported_startup_attaches_inbox_tool(self):
        names = [item.name for item in build_relay_tools(FakeSchemaTypes).function_declarations]
        self.assertEqual(names, ["delegate_task", "check_task_status", "cancel_task"])
        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "offline-test-key",
            "AMUSANT_CLIENT_ID": CLIENT,
            "AMUSANT_BACKEND_ID": "openab",
            "AMUSANT_SLOW_EXECUTOR": "openab_acp",  # explicit legacy fixture
            "AMUSANT_TASK_DB": str(self.db_path),
        }
        with patch("relay.application.build_live_provider", return_value=object()):
            build_supported_relay_handler(
                expected_device_token="device-token-12",
                environ=env,
                sdk_types=FakeSchemaTypes,
            )


if __name__ == "__main__":
    unittest.main()
