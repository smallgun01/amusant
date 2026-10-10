import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest

from fast_voice.contracts import ToolCallEvent
from relay.slow_path import SlowPathToolRouter
from relay.task_store import AdmissionUnavailable, SQLiteTaskStore

TASK_ID = "4db5aabc-284a-4a01-8a6d-5cddcc7934f1"
RETRY_TASK_ID = "f8f3c5a2-4f97-4c91-ae62-6b0d3f84ea28"
NOW = datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc)
CLIENT = "authenticated-client-1"
CONVERSATION = "conversation-1"


class FailingStore:
    def admit(self, _request):
        raise AdmissionUnavailable("test-only")


class SlowStore:
    def __init__(self, inner):
        self.inner = inner

    def admit(self, request):
        time.sleep(0.05)
        return self.inner.admit(request)


def make_router(store, *, timeout=1.0, task_id=TASK_ID, conversation_id=CONVERSATION):
    return SlowPathToolRouter(
        store=store,
        client_id=CLIENT,
        conversation_id=conversation_id,
        backend_id="openab",
        default_task_kind="research",
        max_task_ttl_seconds=600,
        acceptance_timeout_seconds=timeout,
        now=lambda: NOW,
        new_task_id=lambda: task_id,
    )


class SQLiteSlowPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tasks.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def store(self, *, capacity=8):
        return SQLiteTaskStore(database_path=self.db_path, max_pending_tasks=capacity)

    async def test_valid_call_commits_task_and_dispatch_intent_before_acceptance(self):
        store = self.store()
        router = make_router(store)
        event = ToolCallEvent(
            "call-1", "delegate_task", {"task": "Compare current laptop options", "task_kind": "compare"}
        )
        result = await router(event, 7)
        self.assertEqual(result, {"status": "accepted", "task_id": TASK_ID, "duplicate": False})
        row = store.get_by_call(client_id=CLIENT, conversation_id=CONVERSATION, turn_id=7, call_id="call-1")
        self.assertEqual(row, {
            "task_id": TASK_ID,
            "task": "Compare current laptop options",
            "task_kind": "compare",
            "backend": "openab",
            "status": "queued",
            "dispatch_state": "pending",
        })
        self.assertEqual(store.pending_dispatch_count(), 1)
        self.assertEqual(self.db_path.stat().st_mode & 0o777, 0o600)

    async def test_same_call_replays_durable_receipt_after_store_reopen(self):
        first = await make_router(self.store(), task_id=TASK_ID)(
            ToolCallEvent("call-repeat", "delegate_task", {"task": "Summarize sources"}), 3
        )
        reopened = self.store()
        replay = await make_router(reopened, task_id=RETRY_TASK_ID)(
            ToolCallEvent("call-repeat", "delegate_task", {"task": "Summarize sources"}), 3
        )
        self.assertEqual(first, {"status": "accepted", "task_id": TASK_ID, "duplicate": False})
        self.assertEqual(replay, {"status": "accepted", "task_id": TASK_ID, "duplicate": True})
        self.assertEqual(reopened.pending_dispatch_count(), 1)

    async def test_same_call_id_with_different_payload_fails_closed(self):
        store = self.store()
        router = make_router(store)
        first = await router(ToolCallEvent("call-conflict", "delegate_task", {"task": "First"}), 4)
        conflict = await router(ToolCallEvent("call-conflict", "delegate_task", {"task": "Different"}), 4)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(conflict, {"status": "rejected", "code": "call_id_conflict"})
        self.assertEqual(store.pending_dispatch_count(), 1)

    async def test_model_cannot_select_agent_backend_url_or_command(self):
        store = self.store()
        router = make_router(store)
        for index, extra in enumerate((
            {"agent": "admin"},
            {"preferred_agent": "jellii"},
            {"backend": "other"},
            {"url": "https://example.invalid"},
            {"command": "id"},
        )):
            result = await router(
                ToolCallEvent(f"call-extra-{index}", "delegate_task", {"task": "Do work", **extra}), index + 1
            )
            self.assertEqual(result, {"status": "rejected", "code": "invalid_arguments"})
        self.assertEqual(store.pending_dispatch_count(), 0)

    async def test_unknown_tool_and_invalid_task_kind_do_not_dispatch(self):
        store = self.store()
        router = make_router(store)
        unknown = await router(ToolCallEvent("call-unknown", "run_command", {"task": "id"}), 1)
        invalid_kind = await router(
            ToolCallEvent("call-kind", "delegate_task", {"task": "Do work", "task_kind": "execute-shell"}), 2
        )
        self.assertEqual(unknown, {"status": "rejected", "code": "unsupported_tool"})
        self.assertEqual(invalid_kind, {"status": "rejected", "code": "invalid_task_kind"})
        self.assertEqual(store.pending_dispatch_count(), 0)

    async def test_missing_and_oversized_tasks_fail_closed(self):
        store = self.store()
        router = make_router(store)
        missing = await router(ToolCallEvent("call-missing", "delegate_task", {}), 1)
        oversized = await router(ToolCallEvent("call-oversize", "delegate_task", {"task": "x" * 4097}), 2)
        self.assertEqual(missing, {"status": "rejected", "code": "invalid_task"})
        self.assertEqual(oversized, {"status": "rejected", "code": "invalid_task"})
        self.assertEqual(store.pending_dispatch_count(), 0)

    async def test_capacity_and_store_failure_never_claim_acceptance(self):
        store = self.store(capacity=1)
        router = make_router(store)
        first = await router(ToolCallEvent("call-cap-1", "delegate_task", {"task": "First"}), 1)
        second = await router(ToolCallEvent("call-cap-2", "delegate_task", {"task": "Second"}), 2)
        failed = await make_router(FailingStore())(
            ToolCallEvent("call-fail", "delegate_task", {"task": "Third"}), 3
        )
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second, {"status": "unavailable", "code": "task_capacity"})
        self.assertEqual(failed, {"status": "unavailable", "code": "task_store_unavailable"})

    async def test_timeout_is_unknown_then_retry_finds_committed_task(self):
        store = self.store()
        slow_router = make_router(SlowStore(store), timeout=0.005, task_id=TASK_ID)
        event = ToolCallEvent("call-timeout", "delegate_task", {"task": "Research this"})
        uncertain = await slow_router(event, 6)
        self.assertEqual(uncertain, {"status": "unknown", "task_id": TASK_ID, "code": "acceptance_unconfirmed"})
        await asyncio.sleep(0.1)  # The timed-out thread may finish its durable commit.
        retry_store = self.store()
        retry = await make_router(retry_store, task_id=RETRY_TASK_ID)(event, 6)
        self.assertEqual(retry, {"status": "accepted", "task_id": TASK_ID, "duplicate": True})
        self.assertEqual(retry_store.pending_dispatch_count(), 1)


if __name__ == "__main__":
    unittest.main()
