from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4

from relay.dispatcher import AsyncDispatcher
from relay.dynamodb_task_store import DynamoDbTaskStore, InMemoryDynamoLedger
from relay.hermes_runs_executor import HermesRunsExecutor
from relay.task_store import SQLiteTaskStore, TaskAdmissionRequest


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
OWNER = "hermes-recovery-owner"
RUN_ID = "run-recovery-race"


@contextmanager
def _sqlite_path():
    fd, raw_path = tempfile.mkstemp(
        prefix=".hermes-recovery-race-",
        suffix=".sqlite3",
        dir=Path(__file__).resolve().parents[2],
    )
    os.close(fd)
    path = Path(raw_path)
    try:
        yield path
    finally:
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            candidate.unlink(missing_ok=True)


def _task(task_id: str) -> TaskAdmissionRequest:
    return TaskAdmissionRequest(
        task_id=task_id,
        client_id=OWNER,
        conversation_id="hermes-recovery-race",
        turn_id=1,
        call_id="call-" + task_id,
        task="Check the synthetic recovery fixture.",
        task_kind="research",
        backend="hermes",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        executor_kind="hermes_runs",
        result_contract_version=2,
    )


def _store(kind: str, database_path: Path):
    if kind == "sqlite":
        return SQLiteTaskStore(database_path=database_path, max_pending_tasks=8)
    return DynamoDbTaskStore(InMemoryDynamoLedger(), client_id=OWNER, max_pending_tasks=8)


def _seed_known_run(store, task_id: str, now: datetime, deadline: datetime) -> None:
    store.admit(_task(task_id))
    claimed = store.claim_due(now=now, lease_seconds=30, limit=1)
    assert claimed and claimed[0].task_id == task_id
    store.mark_dispatch_started(task_id, now=now)
    store.save_backend_run_id(
        task_id,
        RUN_ID,
        run_started_at=now,
        run_deadline_at=deadline,
        next_poll_at=now,
        poll_attempts=0,
    )
    store.defer_hermes_run(
        task_id,
        backend_run_id=RUN_ID,
        run_started_at=now,
        run_deadline_at=deadline,
        next_poll_at=now,
        poll_attempts=0,
    )


def _executor(store, now, request_json):
    return HermesRunsExecutor(
        url="http://127.0.0.1:8642",
        api_key="local-test-api-key-123",
        save_backend_run_id=store.save_backend_run_id,
        update_poll_state=store.update_hermes_poll_state,
        update_stop_state=store.update_hermes_stop_state,
        run_deadline_seconds=10,
        poll_cycle_seconds=1,
        http_timeout_seconds=0.1,
        poll_interval_seconds=1,
        stop_grace_seconds=30,
        stop_max_attempts=3,
        stop_retry_base_seconds=1,
        stop_retry_max_seconds=5,
        allow_plaintext=True,
        request_json=request_json,
        clock=lambda: now[0],
    )


class HermesRecoveryRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_pre_deadline_get_still_issues_first_deadline_stop_in_both_stores(self):
        for kind in ("sqlite", "dynamo"):
            with self.subTest(store=kind), _sqlite_path() as database_path:
                store = _store(kind, database_path)
                now = [NOW]
                deadline = NOW + timedelta(seconds=10)
                task_id = str(uuid4())
                _seed_known_run(store, task_id, NOW, deadline)
                calls: list[tuple[str, str]] = []
                gets = 0

                def request_json(method, path, _body, _headers):
                    nonlocal gets
                    calls.append((method, path))
                    if method == "POST":
                        return 202, {"run_id": RUN_ID, "status": "stopping"}
                    gets += 1
                    state = "future_remote_state" if gets == 1 else "running"
                    return 200, {"run_id": RUN_ID, "status": state}

                dispatcher = AsyncDispatcher(
                    store, _executor(store, now, request_json), max_attempts=1, now=lambda: now[0]
                )
                self.assertEqual(await dispatcher.dispatch_available(), 1)
                before_deadline = store.get_by_task_id(task_id)
                self.assertEqual((before_deadline["status"], before_deadline["dispatch_state"]), ("uncertain", "uncertain"))
                self.assertEqual(before_deadline["poll_attempts"], 1)

                now[0] = deadline + timedelta(seconds=1)
                self.assertEqual(await dispatcher.dispatch_available(), 1)
                row = store.get_by_task_id(task_id)
                self.assertEqual(calls, [
                    ("GET", f"/v1/runs/{RUN_ID}"),
                    ("GET", f"/v1/runs/{RUN_ID}"),
                    ("POST", f"/v1/runs/{RUN_ID}/stop"),
                    ("GET", f"/v1/runs/{RUN_ID}"),
                ])
                self.assertEqual((row["status"], row["stop_reason"], row["stop_attempts"]),
                                 ("running", "run_deadline", 1))
                self.assertIsNotNone(row["stop_requested_at"])
                self.assertIsNotNone(row["stop_accepted_at"])
                self.assertEqual(row["poll_attempts"], 3)

    async def test_cancel_during_recovery_get_survives_and_sends_one_stop_in_both_stores(self):
        for kind in ("sqlite", "dynamo"):
            with self.subTest(store=kind), _sqlite_path() as database_path:
                store = _store(kind, database_path)
                now = [NOW]
                deadline = NOW + timedelta(seconds=60)
                task_id = str(uuid4())
                _seed_known_run(store, task_id, NOW, deadline)
                calls: list[tuple[str, str]] = []
                get_started = threading.Event()
                release_get = threading.Event()
                gets = 0

                def request_json(method, path, _body, _headers):
                    nonlocal gets
                    calls.append((method, path))
                    if method == "POST":
                        return 202, {"run_id": RUN_ID, "status": "stopping"}
                    gets += 1
                    if gets == 1:
                        return 200, {"run_id": RUN_ID, "status": "future_remote_state"}
                    if gets == 2:
                        get_started.set()
                        if not release_get.wait(10):
                            raise TimeoutError("test did not release blocked GET")
                        return 200, {"run_id": RUN_ID, "status": "running"}
                    if gets == 3:
                        return 200, {"run_id": RUN_ID, "status": "running"}
                    return 200, {"run_id": RUN_ID, "status": "cancelled"}

                dispatcher = AsyncDispatcher(
                    store, _executor(store, now, request_json), max_attempts=1, now=lambda: now[0]
                )
                self.assertEqual(await dispatcher.dispatch_available(), 1)
                first = store.get_by_task_id(task_id)
                self.assertEqual(first["status"], "uncertain")
                now[0] = datetime.fromisoformat(first["next_poll_at"])

                recovery = asyncio.create_task(dispatcher.dispatch_available())
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(get_started.wait, 5), timeout=6))
                receipt = store.request_cancel(client_id=OWNER, task_id=task_id, now=now[0])
                self.assertEqual(receipt.status, "stopping")
                requested = store.get_by_task_id(task_id)
                self.assertEqual(requested["stop_requested_at"], now[0].isoformat())
                release_get.set()
                # The due stop is consumed by the dispatcher's next claim cycle.
                self.assertEqual(await asyncio.wait_for(recovery, timeout=5), 2)

                after_stop = store.get_by_task_id(task_id)
                self.assertEqual(after_stop["stop_requested_at"], now[0].isoformat())
                self.assertEqual((after_stop["status"], after_stop["dispatch_state"]), ("running", "pending"))
                self.assertEqual(after_stop["stop_attempts"], 1)
                self.assertIsNotNone(after_stop["stop_accepted_at"])
                self.assertEqual(calls.count(("POST", f"/v1/runs/{RUN_ID}/stop")), 1)
                self.assertEqual(calls, [
                    ("GET", f"/v1/runs/{RUN_ID}"),
                    ("GET", f"/v1/runs/{RUN_ID}"),
                    ("POST", f"/v1/runs/{RUN_ID}/stop"),
                    ("GET", f"/v1/runs/{RUN_ID}"),
                ])

                now[0] = datetime.fromisoformat(after_stop["next_poll_at"])
                self.assertEqual(await dispatcher.dispatch_available(), 1)
                terminal = store.get_by_task_id(task_id)
                self.assertEqual((terminal["status"], terminal["last_error"]), ("failed", "hermes_cancelled"))
                self.assertEqual(calls.count(("POST", f"/v1/runs/{RUN_ID}/stop")), 1)
                release_get.set()


if __name__ == "__main__":
    unittest.main()
