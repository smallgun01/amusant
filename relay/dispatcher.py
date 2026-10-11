"""In-process Async Dispatcher for admitted slow tasks.

This is a single-Relay application component. It does not provision a Worker
ECS service, SQS, or distributed leases. ACP execution is injected; the default
CLI does not call OpenAB.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from .task_store import ClaimedTask, SQLiteTaskStore

ExecutionStatus = Literal["completed", "failed"]


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    status: ExecutionStatus
    code: str = "ok"


class TaskExecutor(Protocol):
    async def execute(self, task: ClaimedTask) -> ExecutionOutcome:
        """Run one claimed task. Raise only when the terminal result is unknown."""
        ...


NowFactory = Callable[[], datetime]


class AsyncDispatcher:
    """Claim pending outbox rows, record ACP-start, and persist terminal/uncertain state."""

    def __init__(
        self,
        store: SQLiteTaskStore,
        executor: TaskExecutor,
        *,
        lease_seconds: int = 30,
        max_attempts: int = 3,
        poll_interval_seconds: float = 0.05,
        now: NowFactory | None = None,
    ) -> None:
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        if type(max_attempts) is not int or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        if (
            not isinstance(poll_interval_seconds, (int, float))
            or poll_interval_seconds <= 0
        ):
            raise ValueError("poll_interval_seconds must be positive")
        self._store = store
        self._executor = executor
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._wakeup = asyncio.Event()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def notify(self) -> None:
        self._wakeup.set()

    def recover(self):
        return self._store.recover_after_restart()

    async def start(self) -> None:
        if self._running:
            return
        self.recover()
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        self._wakeup.set()
        task = self._task
        self._task = None
        if task is not None:
            await task

    async def dispatch_available(self) -> int:
        """Synchronously drain currently due work. Used by tests and startup."""
        handled = 0
        while True:
            claimed = self._store.claim_due(
                now=self._now(),
                lease_seconds=self._lease_seconds,
                limit=1,
            )
            if not claimed:
                return handled
            await self._dispatch_one(claimed[0])
            handled += 1

    async def _run_loop(self) -> None:
        while self._running:
            await self.dispatch_available()
            self._wakeup.clear()
            if not self._running:
                return
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _dispatch_one(self, task: ClaimedTask) -> None:
        if task.attempts > self._max_attempts:
            self._store.fail(task.task_id, code="dispatch_attempts_exhausted")
            return
        self._store.mark_dispatch_started(task.task_id, now=self._now())
        try:
            outcome = await self._executor.execute(task)
        except Exception:
            self._store.mark_uncertain(task.task_id, code="acp_result_unconfirmed")
            return
        if outcome.status == "completed":
            self._store.complete(task.task_id)
            return
        self._store.fail(task.task_id, code=outcome.code or "dispatch_failed")
