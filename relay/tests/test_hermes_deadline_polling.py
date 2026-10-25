from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import threading
import unittest

from relay.dispatcher import AsyncDispatcher
from relay.hermes_runs_executor import HermesRunsExecutor
from relay.task_store import SQLiteTaskStore, TaskAdmissionRequest


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
TASK_ID = "3f5e1c5a-822e-4a29-bc38-58f4982a7855"
RUN_ID = "run-deadline-polling-repro"
OWNER = "deadline-polling-owner"


@contextmanager
def _workspace_sqlite_path():
    fd, raw_path = tempfile.mkstemp(
        prefix=".hermes-deadline-",
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


class FakeClock:
    def __init__(self, value: datetime):
        self.value = value
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.value += timedelta(seconds=seconds)


class HermesDeadlinePollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_grace_polling_respects_next_poll_schedule(self):
        """Replay the OCI profile and prove STOP grace is interval bounded."""
        with _workspace_sqlite_path() as database_path:
            store = SQLiteTaskStore(database_path=database_path, max_pending_tasks=8)
            clock = FakeClock(NOW + timedelta(seconds=120))
            request = TaskAdmissionRequest(
                task_id=TASK_ID,
                client_id=OWNER,
                conversation_id="conversation-deadline-repro",
                turn_id=1,
                call_id="call-deadline-repro",
                task="Summarize the synthetic fixture.",
                task_kind="summarize",
                backend="test",
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=20),
                executor_kind="hermes_runs",
            )
            store.admit(request)
            [claimed] = store.claim_due(now=NOW, lease_seconds=30)
            store.mark_dispatch_started(TASK_ID, now=NOW)
            deadline = NOW + timedelta(seconds=120)
            store.save_backend_run_id(
                TASK_ID,
                RUN_ID,
                run_started_at=NOW,
                run_deadline_at=deadline,
                next_poll_at=deadline,
                poll_attempts=0,
            )
            store.defer_hermes_run(
                TASK_ID,
                backend_run_id=RUN_ID,
                run_started_at=NOW,
                run_deadline_at=deadline,
                next_poll_at=deadline,
                poll_attempts=0,
            )

            calls: list[tuple[str, str]] = []

            def fake_facade(method, path, _body, _headers):
                calls.append((method, path))
                if method == "POST":
                    self.assertEqual(path, f"/v1/runs/{RUN_ID}/stop")
                    return 200, {"run_id": RUN_ID, "status": "stopping"}
                self.assertEqual((method, path), ("GET", f"/v1/runs/{RUN_ID}"))
                # 30ms synthetic Facade latency gives a realistic fast internal
                # status path without sleeping or reaching a network/provider.
                clock.advance(0.03)
                return 200, {"run_id": RUN_ID, "status": "running"}

            executor = HermesRunsExecutor(
                url="http://127.0.0.1:8642",
                api_key="local-test-api-key-123",
                save_backend_run_id=store.save_backend_run_id,
                update_poll_state=store.update_hermes_poll_state,
                update_stop_state=store.update_hermes_stop_state,
                run_deadline_seconds=120,
                poll_cycle_seconds=10,
                http_timeout_seconds=5,
                poll_interval_seconds=2,
                stop_grace_seconds=20,
                stop_max_attempts=3,
                stop_retry_base_seconds=2,
                stop_retry_max_seconds=10,
                allow_plaintext=True,
                request_json=fake_facade,
                clock=clock.now,
            )
            dispatcher = AsyncDispatcher(
                store,
                executor,
                max_attempts=3,
                poll_interval_seconds=1,
                now=clock.now,
            )

            await dispatcher.dispatch_available()
            row = store.get_by_task_id(TASK_ID)
            self.assertEqual(row["status"], "running")
            self.assertEqual(row["poll_attempts"], 1)
            first_due = datetime.fromisoformat(row["next_poll_at"])
            self.assertAlmostEqual((first_due - clock.now()).total_seconds(), 2.0)

            # Repeated drains before the persisted UTC due time must not claim
            # or poll again even though the Run deadline remains in the past.
            await dispatcher.dispatch_available()
            self.assertEqual(len([method for method, _ in calls if method == "GET"]), 1)

            for _ in range(12):
                row = store.get_by_task_id(TASK_ID)
                if row["status"] == "uncertain":
                    break
                clock.advance((datetime.fromisoformat(row["next_poll_at"]) - clock.now()).total_seconds())
                await dispatcher.dispatch_available()

            gets = [path for method, path in calls if method == "GET"]
            stops = [path for method, path in calls if method == "POST"]
            row = store.get_by_task_id(TASK_ID)
            self.assertEqual(stops, [f"/v1/runs/{RUN_ID}/stop"])
            self.assertLessEqual(len(gets), 12)
            self.assertEqual(row["status"], "uncertain")
            self.assertEqual(row["last_error"], "hermes_stop_unconfirmed")
            self.assertEqual(row["poll_attempts"], len(gets))
            self.assertGreater(datetime.fromisoformat(row["next_poll_at"]), clock.now())


if __name__ == "__main__":
    unittest.main()
