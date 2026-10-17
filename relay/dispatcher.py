"""In-process Async Dispatcher for admitted slow tasks.

This is a single-Relay application component. It does not provision a Worker
ECS service, SQS, or distributed leases. Agent execution is injected; the default
CLI does not call an external agent.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal, Protocol

from .task_store import BoundedTaskResult, ClaimedTask, TaskStore

ExecutionStatus = Literal["completed", "failed", "uncertain", "deferred"]


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    status: ExecutionStatus
    code: str = "ok"
    result: BoundedTaskResult | None = None
    backend_run_id: str | None = None
    run_started_at: str | None = None
    run_deadline_at: str | None = None
    next_poll_at: str | None = None
    poll_attempts: int = 0


class TaskExecutor(Protocol):
    async def execute(self, task: ClaimedTask) -> ExecutionOutcome:
        """Run one claimed task. Raise only when the terminal result is unknown."""
        ...


NowFactory = Callable[[], datetime]
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
MAX_POLL_INTERVAL_SECONDS = 60.0


class AsyncDispatcher:
    """Claim pending outbox rows, record dispatch-start, and persist terminal/uncertain state."""

    def __init__(
        self,
        store: TaskStore,
        executor: TaskExecutor,
        *,
        lease_seconds: int = 30,
        max_attempts: int = 3,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        now: NowFactory | None = None,
    ) -> None:
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        if type(max_attempts) is not int or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(float(poll_interval_seconds))
            or poll_interval_seconds <= 0
            or poll_interval_seconds > MAX_POLL_INTERVAL_SECONDS
        ):
            raise ValueError("poll_interval_seconds must be finite, positive, and at most 60 seconds")
        self._store = store
        self._executor = executor
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._wakeup = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def notify(self) -> None:
        """Wake the loop owner; cross-thread callers use the loop-safe scheduler."""
        loop = self._loop
        if loop is None or loop.is_closed():
            # Startup recovery plus the first drain covers notifications before start.
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            self._wakeup.set()
            return
        try:
            loop.call_soon_threadsafe(self._wakeup.set)
        except RuntimeError:
            # The owner loop is closing; durable polling/recovery remains authoritative.
            return

    def recover(self):
        return self._store.recover_after_restart()

    async def start(self) -> None:
        if self._running:
            return
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            self._wakeup = asyncio.Event()
        self._loop = loop
        self._stopping = False
        self._stop_event = asyncio.Event()
        await self._store_call(self._store.recover_after_restart)
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        self._stopping = True
        self._stop_event.set()
        self._wakeup.set()
        task = self._task
        self._task = None
        if task is not None:
            await task

    async def _store_call(self, fn, /, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def dispatch_available(self) -> int:
        """Drain currently due work without blocking the event loop on store I/O."""
        handled = 0
        while True:
            try:
                claimed = await self._store_call(
                    self._store.claim_due,
                    now=self._now(),
                    lease_seconds=self._lease_seconds,
                    limit=1,
                )
            except Exception:
                return handled
            if not claimed:
                return handled
            await self._dispatch_one(claimed[0])
            handled += 1
            if self._stopping:
                return handled

    async def _run_loop(self) -> None:
        while self._running:
            # Clear before draining: notifications arriving during/after the
            # empty claim remain set and make wait() return immediately.
            self._wakeup.clear()
            try:
                await self.dispatch_available()
            except Exception:
                pass
            if not self._running:
                return
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _safe_store(self, fn, /, *args, **kwargs) -> None:
        try:
            await self._store_call(fn, *args, **kwargs)
        except Exception:
            return

    async def _dispatch_one(self, task: ClaimedTask) -> None:
        if task.attempts > self._max_attempts:
            await self._safe_store(self._store.fail, task.task_id, code="dispatch_attempts_exhausted")
            return
        if task.executor_kind != "hermes_runs" or not task.backend_run_id:
            started_at = self._now()
            try:
                await self._store_call(
                    self._store.mark_dispatch_started,
                    task.task_id,
                    now=started_at,
                )
            except Exception:
                return
            task = replace(task, dispatch_started_at=started_at.astimezone(timezone.utc).isoformat())
        while True:
            if self._stopping:
                # Leave a claimed Hermes Run with its durable ID for startup recovery.
                return
            try:
                outcome = await self._executor.execute(task)
            except Exception as exc:
                code = getattr(exc, "code", "acp_result_unconfirmed")
                await self._safe_store(
                    self._store.mark_uncertain,
                    task.task_id,
                    code=code,
                )
                return
            if outcome.status == "deferred":
                if (
                    task.executor_kind != "hermes_runs"
                    or not outcome.backend_run_id
                    or not outcome.run_started_at
                    or not outcome.run_deadline_at
                    or not outcome.next_poll_at
                ):
                    await self._safe_store(
                        self._store.mark_uncertain,
                        task.task_id,
                        code="hermes_poll_state_unconfirmed",
                    )
                    return
                task = replace(
                    task,
                    backend_run_id=outcome.backend_run_id,
                    run_started_at=outcome.run_started_at,
                    run_deadline_at=outcome.run_deadline_at,
                    next_poll_at=outcome.next_poll_at,
                    poll_attempts=outcome.poll_attempts,
                )
                try:
                    next_poll = datetime.fromisoformat(outcome.next_poll_at)
                    run_deadline = datetime.fromisoformat(outcome.run_deadline_at)
                    if next_poll.tzinfo is None or run_deadline.tzinfo is None:
                        raise ValueError("poll timestamps must be timezone-aware")
                except (TypeError, ValueError):
                    await self._safe_store(
                        self._store.mark_uncertain,
                        task.task_id,
                        code="hermes_poll_state_unconfirmed",
                    )
                    return
                wake_at = min(next_poll.astimezone(timezone.utc), run_deadline.astimezone(timezone.utc))
                delay = max(0.0, (wake_at - self._now().astimezone(timezone.utc)).total_seconds())
                if delay > 0:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                if self._stopping:
                    return
                continue
            try:
                if outcome.status == "completed":
                    await self._store_call(self._store.complete, task.task_id, result=outcome.result)
                    return
                if outcome.status == "uncertain":
                    await self._store_call(
                        self._store.mark_uncertain,
                        task.task_id,
                        code=outcome.code or "dispatch_result_unconfirmed",
                    )
                    return
                await self._store_call(self._store.fail, task.task_id, code=outcome.code or "dispatch_failed")
                return
            except Exception:
                code = "hermes_result_unconfirmed" if task.executor_kind == "hermes_runs" else "acp_result_unconfirmed"
                await self._safe_store(
                    self._store.mark_uncertain,
                    task.task_id,
                    code=code,
                )
                return
