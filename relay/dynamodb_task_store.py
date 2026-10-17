"""DynamoDB Task Ledger adapter (ADR-0002).

Talks to an injected ledger. InMemoryDynamoLedger is the fake endpoint.
Boto3DynamoLedger talks to an existing table and never calls CreateTable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
from typing import Protocol
from uuid import UUID

from .task_store import (
    AdmissionCapacity,
    AdmissionConflict,
    AdmissionReceipt,
    AdmissionUnavailable,
    AdmissionUnknown,
    BoundedTaskResult,
    ClaimedTask,
    RecoveryCounts,
    SQLiteTaskStore,
    TaskAdmissionRequest,
    _ID_RE,
    _RUN_ID_RE,
    _sanitize_error,
    _utc_iso,
)

TASK_SK_PREFIX = "TASK#"
DEDUP_SK_PREFIX = "DEDUP#"
QUERY_ITEM_BOUND = 100
RETAIN_SECONDS = 30 * 24 * 60 * 60


class LedgerConditionFailed(RuntimeError):
    """Conditional write lost."""


class LedgerAmbiguous(RuntimeError):
    """Commit result is unknown; caller must read then AdmissionUnknown."""


class DynamoLedger(Protocol):
    def get_item(self, pk: str, sk: str, *, consistent: bool = True) -> dict[str, object] | None: ...

    def query_prefix(self, pk: str, sk_prefix: str) -> tuple[list[dict[str, object]], bool]: ...

    def has_prefix(self, pk: str, sk_prefix: str) -> bool: ...

    def transact_put(
        self,
        puts: list[dict[str, object]],
        *,
        absent: list[tuple[str, str]],
    ) -> None: ...

    def update_item(
        self,
        pk: str,
        sk: str,
        patch: dict[str, object],
        *,
        condition: str,
    ) -> dict[str, object]: ...


def _condition_holds(current: dict[str, object] | None, condition: str) -> bool:
    if current is None:
        return False
    if condition == "exists":
        return True
    if condition == "claimed":
        return current.get("outbox_state") == "claimed"
    if condition == "claimed_unsent":
        return current.get("outbox_state") == "claimed" and not current.get("dispatch_started_at")
    if condition == "claimed_started":
        return current.get("outbox_state") == "claimed" and bool(current.get("dispatch_started_at"))
    if condition == "claimed_started_run_id_absent":
        return (
            current.get("outbox_state") == "claimed"
            and bool(current.get("dispatch_started_at"))
            and not current.get("backend_run_id")
        )
    if condition == "claimed_started_run_known":
        return (
            current.get("outbox_state") == "claimed"
            and bool(current.get("dispatch_started_at"))
            and bool(current.get("backend_run_id"))
        )
    if condition == "pending_queued":
        return current.get("outbox_state") == "pending" and current.get("status") == "queued"
    if condition == "pending_running_known_run":
        return (
            current.get("outbox_state") == "pending"
            and current.get("status") == "running"
            and bool(current.get("backend_run_id"))
        )
    if condition == "delivery_can_speak":
        return (
            current.get("status") == "completed"
            and current.get("speak") is not None
            and current.get("delivery_state") != "spoken"
        )
    if condition == "delivery_can_read":
        return current.get("delivery_state") in {None, "pending", "notified"}
    raise ValueError(f"unknown ledger condition {condition}")


def _apply_patch(current: dict[str, object], patch: dict[str, object]) -> dict[str, object]:
    merged = dict(current)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


class InMemoryDynamoLedger:
    """Test double: partition+sort key map. Not DynamoDB and not AWS."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}
        self.ambiguous_next = False
        self.ambiguous_after_commit = False

    def get_item(self, pk: str, sk: str, *, consistent: bool = True) -> dict[str, object] | None:
        del consistent
        item = self.items.get((pk, sk))
        return None if item is None else dict(item)

    def query_prefix(self, pk: str, sk_prefix: str) -> tuple[list[dict[str, object]], bool]:
        rows = [
            dict(value)
            for (part, sort), value in self.items.items()
            if part == pk and sort.startswith(sk_prefix)
        ]
        truncated = len(rows) > QUERY_ITEM_BOUND
        return rows[:QUERY_ITEM_BOUND], truncated

    def has_prefix(self, pk: str, sk_prefix: str) -> bool:
        return any(part == pk and sort.startswith(sk_prefix) for part, sort in self.items)

    def transact_put(
        self,
        puts: list[dict[str, object]],
        *,
        absent: list[tuple[str, str]],
    ) -> None:
        if self.ambiguous_next:
            self.ambiguous_next = False
            raise LedgerAmbiguous()
        for key in absent:
            if key in self.items:
                raise LedgerConditionFailed()
        for item in puts:
            key = (str(item["pk"]), str(item["sk"]))
            self.items[key] = {k: v for k, v in item.items() if v is not None}
        if self.ambiguous_after_commit:
            self.ambiguous_after_commit = False
            raise LedgerAmbiguous()

    def update_item(
        self,
        pk: str,
        sk: str,
        patch: dict[str, object],
        *,
        condition: str,
    ) -> dict[str, object]:
        key = (pk, sk)
        current = self.items.get(key)
        if not _condition_holds(None if current is None else dict(current), condition):
            raise LedgerConditionFailed()
        merged = _apply_patch(dict(current or {}), patch)
        merged["pk"] = pk
        merged["sk"] = sk
        self.items[key] = merged
        return dict(merged)


def _task_sk(task_id: str) -> str:
    return f"{TASK_SK_PREFIX}{task_id}"


def _dedup_sk(conversation_id: str, turn_id: int, call_id: str) -> str:
    return f"{DEDUP_SK_PREFIX}{conversation_id}#{turn_id}#{call_id}"


def _retain_until(created_at: datetime) -> int:
    return int(created_at.astimezone(timezone.utc).timestamp()) + RETAIN_SECONDS


class DynamoDbTaskStore:
    """Single-owner ledger over an injected Dynamo-like backend."""

    def __init__(
        self,
        ledger: DynamoLedger,
        *,
        client_id: str,
        max_pending_tasks: int,
    ) -> None:
        if not isinstance(client_id, str) or not _ID_RE.fullmatch(client_id):
            raise ValueError("client_id must be a valid owner identity")
        if type(max_pending_tasks) is not int or max_pending_tasks <= 0:
            raise ValueError("max_pending_tasks must be a positive integer")
        self._ledger = ledger
        self._client_id = client_id
        self._max_pending_tasks = max_pending_tasks
        self._lock = threading.Lock()
        self._next_hermes_wake: datetime | None = None

    def _task_rows(self) -> list[dict[str, object]]:
        rows, truncated = self._ledger.query_prefix(self._client_id, TASK_SK_PREFIX)
        if truncated:
            raise AdmissionCapacity("task_capacity")
        return rows

    def admit(self, request: TaskAdmissionRequest) -> AdmissionReceipt:
        SQLiteTaskStore._validate(request)
        if request.client_id != self._client_id:
            raise AdmissionUnavailable("invalid_identity")
        fingerprint = request.fingerprint()
        dedup_sk = _dedup_sk(request.conversation_id, request.turn_id, request.call_id)
        with self._lock:
            return self._admit_locked(request, fingerprint, dedup_sk)

    def _admit_locked(
        self,
        request: TaskAdmissionRequest,
        fingerprint: str,
        dedup_sk: str,
    ) -> AdmissionReceipt:
        existing = self._ledger.get_item(request.client_id, dedup_sk, consistent=True)
        if existing is not None:
            if existing.get("payload_sha256") != fingerprint:
                raise AdmissionConflict("call_id_conflict")
            return AdmissionReceipt(task_id=str(existing["task_id"]), duplicate=True)

        queued = [row for row in self._task_rows() if row.get("status") == "queued"]
        if len(queued) >= self._max_pending_tasks:
            raise AdmissionCapacity("task_capacity")

        created = _utc_iso(request.created_at)
        retain = _retain_until(request.created_at)
        task_item = {
            "pk": request.client_id,
            "sk": _task_sk(request.task_id),
            "task_id": request.task_id,
            "client_id": request.client_id,
            "conversation_id": request.conversation_id,
            "turn_id": request.turn_id,
            "call_id": request.call_id,
            "payload_sha256": fingerprint,
            "task_text": request.task,
            "task_kind": request.task_kind,
            "backend": request.backend,
            "executor_kind": request.executor_kind,
            "status": "queued",
            "created_at": created,
            "expires_at": _utc_iso(request.expires_at),
            "outbox_state": "pending",
            "attempts": 0,
            "claimed_until": None,
            "dispatch_started_at": None,
            "last_error": None,
            "speak": None,
            "card_json": None,
            "details_md": None,
            "delivery_state": None,
            "retain_until": retain,
        }
        dedup_item = {
            "pk": request.client_id,
            "sk": dedup_sk,
            "task_id": request.task_id,
            "payload_sha256": fingerprint,
            "retain_until": retain,
        }
        try:
            self._ledger.transact_put(
                [task_item, dedup_item],
                absent=[(request.client_id, dedup_sk), (request.client_id, _task_sk(request.task_id))],
            )
        except LedgerConditionFailed as exc:
            again = self._ledger.get_item(request.client_id, dedup_sk, consistent=True)
            if again is not None and again.get("payload_sha256") == fingerprint:
                return AdmissionReceipt(task_id=str(again["task_id"]), duplicate=True)
            if again is not None:
                raise AdmissionConflict("call_id_conflict") from exc
            raise AdmissionUnavailable("task_store_unavailable") from exc
        except LedgerAmbiguous as exc:
            try:
                again = self._ledger.get_item(request.client_id, dedup_sk, consistent=True)
            except (AdmissionUnavailable, LedgerAmbiguous):
                raise AdmissionUnknown("acceptance_unconfirmed") from exc
            if again is not None and again.get("payload_sha256") == fingerprint:
                return AdmissionReceipt(task_id=str(again["task_id"]), duplicate=True)
            raise AdmissionUnknown("acceptance_unconfirmed") from exc
        return AdmissionReceipt(task_id=request.task_id, duplicate=False)

    def recover_after_restart(self) -> RecoveryCounts:
        with self._lock:
            return self._recover_locked()

    def _recover_locked(self) -> RecoveryCounts:
        self._next_hermes_wake = None
        requeued = 0
        uncertain = 0
        for row in self._task_rows():
            if row.get("outbox_state") != "claimed":
                continue
            pk = str(row["pk"])
            sk = str(row["sk"])
            if row.get("dispatch_started_at"):
                if row.get("executor_kind", "openab_acp") == "hermes_runs" and row.get("backend_run_id"):
                    updated = self._ledger.update_item(
                        pk,
                        sk,
                        {
                            "outbox_state": "pending",
                            "status": "running",
                            "claimed_until": None,
                            "last_error": None,
                        },
                        condition="claimed_started_run_known",
                    )
                    self._align_dedup_ttl(updated)
                    requeued += 1
                else:
                    error = (
                        "hermes_submission_unconfirmed"
                        if row.get("executor_kind", "openab_acp") == "hermes_runs"
                        else "acp_result_unconfirmed"
                    )
                    updated = self._ledger.update_item(
                        pk,
                        sk,
                        {
                            "outbox_state": "uncertain",
                            "status": "uncertain",
                            "last_error": error,
                            "retain_until": int(datetime.now(timezone.utc).timestamp()) + RETAIN_SECONDS,
                        },
                        condition="claimed_started",
                    )
                    self._align_dedup_ttl(updated)
                    uncertain += 1
            else:
                self._ledger.update_item(
                    pk,
                    sk,
                    {
                        "outbox_state": "pending",
                        "status": "queued",
                        "claimed_until": None,
                    },
                    condition="claimed_unsent",
                )
                requeued += 1
        return RecoveryCounts(requeued=requeued, uncertain=uncertain)

    def next_hermes_wake_at(self) -> datetime | None:
        # _claim_due_locked derives this from its bounded strong Query, avoiding
        # an additional DynamoDB read on every idle Dispatcher cycle.
        with self._lock:
            return self._next_hermes_wake

    def claim_due(self, *, now: datetime, lease_seconds: int, limit: int = 1) -> list[ClaimedTask]:
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        now_iso = _utc_iso(now)
        claimed_until = _utc_iso(now + timedelta(seconds=lease_seconds))
        with self._lock:
            return self._claim_due_locked(now_iso, claimed_until, limit)

    def _claim_due_locked(self, now_iso: str, claimed_until: str, limit: int) -> list[ClaimedTask]:
        rows = self._task_rows()
        eligible = [
            row
            for row in rows
            if row.get("outbox_state") == "pending"
            and (
                (
                    row.get("status") == "queued"
                    and str(row.get("expires_at") or "") > now_iso
                    and not (row.get("executor_kind") == "hermes_runs" and row.get("backend_run_id"))
                )
                or (
                    row.get("executor_kind") == "hermes_runs"
                    and row.get("backend_run_id")
                    and row.get("status") in {"queued", "running"}
                    and (
                        not row.get("next_poll_at")
                        or str(row.get("next_poll_at")) <= now_iso
                        or (row.get("run_deadline_at") and str(row.get("run_deadline_at")) <= now_iso)
                    )
                )
            )
        ]
        eligible.sort(key=lambda row: str(row.get("created_at") or ""))
        claimed: list[ClaimedTask] = []
        claimed_ids: set[str] = set()
        for row in eligible[:limit]:
            pk = str(row["pk"])
            sk = str(row["sk"])
            try:
                updated = self._ledger.update_item(
                    pk,
                    sk,
                    {
                        "outbox_state": "claimed",
                        "status": "running",
                        "attempts": int(row.get("attempts") or 0) + 1,
                        "claimed_until": claimed_until,
                    },
                    condition=(
                        "pending_running_known_run"
                        if row.get("status") == "running" and row.get("backend_run_id")
                        else "pending_queued"
                    ),
                )
            except LedgerConditionFailed:
                continue
            claimed_ids.add(str(row["task_id"]))
            claimed.append(
                ClaimedTask(
                    task_id=str(updated["task_id"]),
                    client_id=str(updated["client_id"]),
                    conversation_id=str(updated["conversation_id"]),
                    turn_id=int(updated["turn_id"]),
                    call_id=str(updated["call_id"]),
                    task=str(updated["task_text"]),
                    task_kind=str(updated["task_kind"]),
                    backend=str(updated["backend"]),
                    attempts=int(updated["attempts"]),
                    executor_kind=str(updated.get("executor_kind") or "openab_acp"),
                    backend_run_id=str(updated["backend_run_id"]) if updated.get("backend_run_id") else None,
                    dispatch_started_at=str(updated["dispatch_started_at"]) if updated.get("dispatch_started_at") else None,
                    run_started_at=str(updated["run_started_at"]) if updated.get("run_started_at") else None,
                    run_deadline_at=str(updated["run_deadline_at"]) if updated.get("run_deadline_at") else None,
                    next_poll_at=str(updated["next_poll_at"]) if updated.get("next_poll_at") else None,
                    poll_attempts=int(updated.get("poll_attempts") or 0),
                )
            )
        wake_candidates: list[datetime] = []
        for row in rows:
            if (
                str(row.get("task_id") or "") in claimed_ids
                or row.get("outbox_state") != "pending"
                or row.get("executor_kind") != "hermes_runs"
                or not row.get("backend_run_id")
                or row.get("status") not in {"queued", "running"}
            ):
                continue
            for value in (row.get("next_poll_at"), row.get("run_deadline_at")):
                if not isinstance(value, str) or not value:
                    continue
                try:
                    parsed = datetime.fromisoformat(value)
                except ValueError:
                    raise AdmissionUnavailable("hermes_poll_schedule_unavailable") from None
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise AdmissionUnavailable("hermes_poll_schedule_unavailable")
                wake_candidates.append(parsed.astimezone(timezone.utc))
        self._next_hermes_wake = min(wake_candidates) if wake_candidates else None
        return claimed

    def mark_dispatch_started(self, task_id: str, *, now: datetime) -> None:
        started = _utc_iso(now)
        with self._lock:
            self._mark_dispatch_started_locked(task_id, started)

    def _mark_dispatch_started_locked(self, task_id: str, started: str) -> None:
        try:
            self._ledger.update_item(
                self._client_id,
                _task_sk(task_id),
                {"dispatch_started_at": started},
                condition="claimed_unsent",
            )
        except LedgerConditionFailed as exc:
            raise AdmissionUnavailable("dispatch_not_claimed") from exc

    def save_backend_run_id(
        self,
        task_id: str,
        run_id: str,
        *,
        run_started_at: datetime | None = None,
        run_deadline_at: datetime | None = None,
        next_poll_at: datetime | None = None,
        poll_attempts: int = 0,
    ) -> None:
        """Store one Run ID and its first timing state with owner-scoped conditions."""
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise AdmissionUnavailable("invalid_backend_run_id")
        if type(poll_attempts) is not int or poll_attempts < 0:
            raise AdmissionUnavailable("invalid_poll_attempts")
        timing = (run_started_at, run_deadline_at, next_poll_at)
        if any(value is not None for value in timing) and not all(value is not None for value in timing):
            raise AdmissionUnavailable("incomplete_hermes_run_timing")
        started_iso = _utc_iso(run_started_at) if run_started_at is not None else None
        deadline_iso = _utc_iso(run_deadline_at) if run_deadline_at is not None else None
        next_poll_iso = _utc_iso(next_poll_at) if next_poll_at is not None else None
        if started_iso is not None and deadline_iso <= started_iso:
            raise AdmissionUnavailable("invalid_hermes_run_deadline")
        with self._lock:
            key = _task_sk(task_id)
            current = self._ledger.get_item(self._client_id, key, consistent=True)
            if current is None:
                raise AdmissionUnavailable("task_not_found")
            if current.get("executor_kind", "openab_acp") != "hermes_runs":
                raise AdmissionUnavailable("executor_mismatch")
            existing = current.get("backend_run_id")
            if existing is not None:
                if existing != run_id:
                    raise AdmissionConflict("backend_run_id_conflict")
                if started_iso is not None and not current.get("run_deadline_at"):
                    if current.get("dispatch_started_at") and started_iso != current.get("dispatch_started_at"):
                        raise AdmissionUnavailable("run_start_anchor_mismatch")
                    patch = {
                        "run_started_at": current.get("run_started_at") or started_iso,
                        "run_deadline_at": current.get("run_deadline_at") or deadline_iso,
                        "next_poll_at": current.get("next_poll_at") or next_poll_iso,
                        "poll_attempts": int(current.get("poll_attempts") or poll_attempts),
                    }
                    self._ledger.update_item(
                        self._client_id,
                        key,
                        patch,
                        condition="claimed_started_run_known",
                    )
                return
            if current.get("outbox_state") != "claimed" or not current.get("dispatch_started_at"):
                raise AdmissionUnavailable("dispatch_not_claimed")
            if started_iso is not None and started_iso != current.get("dispatch_started_at"):
                raise AdmissionUnavailable("run_start_anchor_mismatch")
            patch = {"backend_run_id": run_id}
            if started_iso is not None:
                patch.update({
                    "run_started_at": started_iso,
                    "run_deadline_at": deadline_iso,
                    "next_poll_at": next_poll_iso,
                    "poll_attempts": poll_attempts,
                })
            try:
                self._ledger.update_item(
                    self._client_id,
                    key,
                    patch,
                    condition="claimed_started_run_id_absent",
                )
            except LedgerConditionFailed as exc:
                fresh = self._ledger.get_item(self._client_id, key, consistent=True)
                if fresh is not None and fresh.get("backend_run_id") == run_id:
                    return
                if fresh is not None and fresh.get("backend_run_id") is not None:
                    raise AdmissionConflict("backend_run_id_conflict") from exc
                raise AdmissionUnavailable("dispatch_not_claimed") from exc
            except LedgerAmbiguous as exc:
                try:
                    fresh = self._ledger.get_item(self._client_id, key, consistent=True)
                except (AdmissionUnavailable, LedgerAmbiguous):
                    raise AdmissionUnknown("backend_run_mapping_unconfirmed") from exc
                if (fresh is not None and fresh.get("backend_run_id") == run_id
                        and (deadline_iso is None or fresh.get("run_deadline_at") == deadline_iso)):
                    return
                raise AdmissionUnknown("backend_run_mapping_unconfirmed") from exc

    def update_hermes_poll_state(
        self, task_id: str, *, next_poll_at: datetime, poll_attempts: int
    ) -> None:
        if type(poll_attempts) is not int or poll_attempts < 0:
            raise AdmissionUnavailable("invalid_poll_attempts")
        next_poll_iso = _utc_iso(next_poll_at)
        with self._lock:
            key = _task_sk(task_id)
            try:
                self._ledger.update_item(
                    self._client_id,
                    key,
                    {"next_poll_at": next_poll_iso, "poll_attempts": poll_attempts},
                    condition="claimed_started_run_known",
                )
            except LedgerConditionFailed as exc:
                raise AdmissionUnavailable("hermes_poll_state_unavailable") from exc
            except LedgerAmbiguous as exc:
                try:
                    fresh = self._ledger.get_item(self._client_id, key, consistent=True)
                except (AdmissionUnavailable, LedgerAmbiguous):
                    raise AdmissionUnknown("hermes_poll_state_unconfirmed") from exc
                if (fresh is not None and fresh.get("next_poll_at") == next_poll_iso
                        and int(fresh.get("poll_attempts") or 0) == poll_attempts):
                    return
                raise AdmissionUnknown("hermes_poll_state_unconfirmed") from exc

    def defer_hermes_run(
        self,
        task_id: str,
        *,
        backend_run_id: str,
        run_started_at: datetime,
        run_deadline_at: datetime,
        next_poll_at: datetime,
        poll_attempts: int,
    ) -> None:
        if not isinstance(backend_run_id, str) or not _RUN_ID_RE.fullmatch(backend_run_id):
            raise AdmissionUnavailable("invalid_backend_run_id")
        if type(poll_attempts) is not int or poll_attempts < 0:
            raise AdmissionUnavailable("invalid_poll_attempts")
        started_iso = _utc_iso(run_started_at)
        deadline_iso = _utc_iso(run_deadline_at)
        next_poll_iso = _utc_iso(next_poll_at)
        if deadline_iso <= started_iso:
            raise AdmissionUnavailable("invalid_hermes_run_deadline")
        with self._lock:
            key = _task_sk(task_id)
            try:
                current = self._ledger.get_item(self._client_id, key, consistent=True)
                if (
                    current is None
                    or current.get("executor_kind") != "hermes_runs"
                    or current.get("backend_run_id") != backend_run_id
                    or current.get("run_started_at") != started_iso
                    or current.get("run_deadline_at") != deadline_iso
                    or current.get("status") != "running"
                ):
                    raise AdmissionUnavailable("hermes_defer_state_unavailable")
                self._ledger.update_item(
                    self._client_id,
                    key,
                    {
                        "outbox_state": "pending",
                        "status": "running",
                        "claimed_until": None,
                        "last_error": None,
                        "next_poll_at": next_poll_iso,
                        "poll_attempts": poll_attempts,
                    },
                    condition="claimed_started_run_known",
                )
            except LedgerConditionFailed as exc:
                raise AdmissionUnavailable("hermes_defer_state_unavailable") from exc
            except LedgerAmbiguous as exc:
                try:
                    fresh = self._ledger.get_item(self._client_id, key, consistent=True)
                except (AdmissionUnavailable, LedgerAmbiguous):
                    raise AdmissionUnknown("hermes_defer_state_unconfirmed") from exc
                if (
                    fresh is not None
                    and fresh.get("outbox_state") == "pending"
                    and fresh.get("backend_run_id") == backend_run_id
                    and fresh.get("run_started_at") == started_iso
                    and fresh.get("run_deadline_at") == deadline_iso
                    and fresh.get("next_poll_at") == next_poll_iso
                    and int(fresh.get("poll_attempts") or 0) == poll_attempts
                ):
                    return
                raise AdmissionUnknown("hermes_defer_state_unconfirmed") from exc

    def complete(self, task_id: str, *, result: BoundedTaskResult | None = None) -> None:
        self._finish(task_id, task_status="completed", outbox_state="sent", error=None, result=result)

    def fail(self, task_id: str, *, code: str) -> None:
        self._finish(task_id, task_status="failed", outbox_state="failed", error=_sanitize_error(code))

    def mark_uncertain(self, task_id: str, *, code: str = "acp_result_unconfirmed") -> None:
        self._finish(
            task_id,
            task_status="uncertain",
            outbox_state="uncertain",
            error=_sanitize_error(code),
        )

    def _finish(
        self,
        task_id: str,
        *,
        task_status: str,
        outbox_state: str,
        error: str | None,
        result: BoundedTaskResult | None = None,
    ) -> None:
        if not isinstance(task_id, str) or not task_id:
            raise AdmissionUnavailable("invalid_task_id")
        with self._lock:
            self._finish_locked(
                task_id,
                task_status=task_status,
                outbox_state=outbox_state,
                error=error,
                result=result,
            )

    def _finish_locked(
        self,
        task_id: str,
        *,
        task_status: str,
        outbox_state: str,
        error: str | None,
        result: BoundedTaskResult | None,
    ) -> None:
        finished_at = datetime.now(timezone.utc)
        patch: dict[str, object] = {
            "outbox_state": outbox_state,
            "status": task_status,
            "last_error": error,
            "next_poll_at": None,
            "retain_until": _retain_until(finished_at),
            "delivery_state": "pending",
            "inbox_updated_at": _utc_iso(finished_at),
        }
        if result is not None:
            patch["speak"] = result.speak
            patch["card_json"] = result.card
            patch["details_md"] = result.details_md
        try:
            self._ledger.update_item(
                self._client_id,
                _task_sk(task_id),
                patch,
                condition="claimed",
            )
        except LedgerConditionFailed as exc:
            raise AdmissionUnavailable("dispatch_not_claimed") from exc
        task = self._ledger.get_item(self._client_id, _task_sk(task_id), consistent=True)
        if task is not None:
            self._align_dedup_ttl(task)

    def _align_dedup_ttl(self, task: dict[str, object]) -> None:
        sk = _dedup_sk(str(task["conversation_id"]), int(task["turn_id"]), str(task["call_id"]))
        retain = task.get("retain_until")
        try:
            self._ledger.update_item(
                str(task["pk"]),
                sk,
                {"retain_until": retain},
                condition="exists",
            )
        except LedgerConditionFailed:
            return

    def get_by_task_id(self, task_id: str) -> dict[str, object] | None:
        with self._lock:
            return self._get_by_task_id_locked(task_id)

    def _get_by_task_id_locked(self, task_id: str) -> dict[str, object] | None:
        row = self._ledger.get_item(self._client_id, _task_sk(task_id), consistent=True)
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "status": row["status"],
            "dispatch_state": row["outbox_state"],
            "attempts": row.get("attempts"),
            "dispatch_started": row.get("dispatch_started_at") is not None,
            "dispatch_started_at": row.get("dispatch_started_at"),
            "last_error": row.get("last_error"),
            "executor_kind": row.get("executor_kind", "openab_acp"),
            "backend_run_id": row.get("backend_run_id"),
            "run_started_at": row.get("run_started_at"),
            "run_deadline_at": row.get("run_deadline_at"),
            "next_poll_at": row.get("next_poll_at"),
            "poll_attempts": int(row.get("poll_attempts") or 0),
        }

    def lookup_inbox(
        self,
        *,
        client_id: str,
        task_id: str | None = None,
        speak: bool = False,
        now: datetime | None = None,
    ) -> dict[str, object] | None:
        if client_id != self._client_id or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        if task_id is not None:
            try:
                parsed = str(UUID(task_id))
            except (ValueError, TypeError, AttributeError):
                return None
            if parsed != task_id.lower():
                return None
        clock = now or datetime.now(timezone.utc)
        now_iso = _utc_iso(clock)
        with self._lock:
            return self._lookup_inbox_locked(client_id, task_id, speak, now_iso)

    def _lookup_inbox_locked(
        self,
        client_id: str,
        task_id: str | None,
        speak: bool,
        now_iso: str,
    ) -> dict[str, object] | None:
        if task_id is None:
            rows = self._task_rows()
            if not rows:
                return None
            row = max(rows, key=lambda item: str(item.get("created_at") or ""))
        else:
            found = self._ledger.get_item(client_id, _task_sk(task_id), consistent=True)
            if found is None:
                return None
            row = found
        status = str(row["status"])
        if status == "uncertain" and row.get("last_error") == "hermes_run_deadline_exceeded":
            # Surface the Relay policy timeout while preserving possible remote activity.
            status = "timed_out"
        expires = str(row.get("expires_at") or "")
        if status in {"queued", "running"} and expires <= now_iso:
            # Preserve OpenAB's legacy expiry behavior. For Hermes, this is a queue
            # deadline only; active work and known Runs use the executor/result state.
            is_hermes = row.get("executor_kind", "openab_acp") == "hermes_runs"
            if not is_hermes or (status == "queued" and not row.get("backend_run_id")):
                status = "expired"
        delivery = row.get("delivery_state")
        result = None
        if row.get("speak") is not None:
            result = {
                "speak": row["speak"],
                "card": row.get("card_json") or {},
                "details_md": row.get("details_md") or "",
            }
        next_delivery = delivery
        if status in {"completed", "failed", "uncertain", "timed_out", "expired"}:
            # Lookup only advances pending/notified to read. Spoken is explicit.
            if delivery in {None, "pending", "notified"}:
                next_delivery = "read"
            if next_delivery is not None and next_delivery != delivery:
                try:
                    updated = self._ledger.update_item(
                        str(row["pk"]),
                        str(row["sk"]),
                        {"delivery_state": next_delivery, "inbox_updated_at": now_iso},
                        condition="delivery_can_read",
                    )
                    delivery = updated.get("delivery_state")
                except LedgerConditionFailed:
                    fresh = self._ledger.get_item(str(row["pk"]), str(row["sk"]), consistent=True)
                    if fresh is not None:
                        delivery = fresh.get("delivery_state")
        payload: dict[str, object] = {
            "task_id": row["task_id"],
            "status": status,
            "task_kind": row["task_kind"],
            "delivery_state": delivery,
        }
        if status in {"failed", "uncertain", "timed_out"} and isinstance(row.get("last_error"), str):
            payload["code"] = row["last_error"]
        if status == "completed" and result is not None:
            payload.update(result)
        if speak and status == "completed" and result is not None:
            payload["speak_now"] = True
        return payload

    def mark_inbox_spoken(
        self,
        *,
        client_id: str,
        task_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Conditionally acknowledge completed audio delivery for this owner."""
        if client_id != self._client_id or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        if not isinstance(task_id, str):
            return False
        try:
            parsed = str(UUID(task_id))
        except (ValueError, TypeError, AttributeError):
            return False
        if parsed != task_id.lower():
            return False
        now_iso = _utc_iso(now or datetime.now(timezone.utc))
        with self._lock:
            row = self._ledger.get_item(client_id, _task_sk(task_id), consistent=True)
            if row is None or row.get("status") != "completed" or row.get("speak") is None:
                return False
            if row.get("delivery_state") == "spoken":
                return True
            try:
                updated = self._ledger.update_item(
                    client_id,
                    _task_sk(task_id),
                    {"delivery_state": "spoken", "inbox_updated_at": now_iso},
                    condition="delivery_can_speak",
                )
                return updated.get("delivery_state") == "spoken"
            except LedgerConditionFailed:
                fresh = self._ledger.get_item(client_id, _task_sk(task_id), consistent=True)
                return fresh is not None and fresh.get("delivery_state") == "spoken"

    def get_inbox(self, task_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._ledger.get_item(self._client_id, _task_sk(task_id), consistent=True)
            if row is None or row.get("delivery_state") is None:
                return None
            return {
                "delivery_state": row.get("delivery_state"),
                "updated_at": row.get("inbox_updated_at"),
            }

    def get_result(self, task_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._ledger.get_item(self._client_id, _task_sk(task_id), consistent=True)
            if row is None or row.get("speak") is None:
                return None
            return {
                "speak": row.get("speak"),
                "card": row.get("card_json") or {},
                "details_md": row.get("details_md") or "",
            }

    def pending_dispatch_count(self) -> int:
        with self._lock:
            return sum(1 for row in self._task_rows() if row.get("outbox_state") == "pending")

    def has_client_tasks(self, client_id: str) -> bool:
        """Strongly test whether this owner partition has any TASK# row."""
        if not isinstance(client_id, str) or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        if client_id != self._client_id:
            raise AdmissionUnavailable("invalid_identity")
        with self._lock:
            has_prefix = getattr(self._ledger, "has_prefix", None)
            if callable(has_prefix):
                return bool(has_prefix(self._client_id, TASK_SK_PREFIX))
            rows, truncated = self._ledger.query_prefix(self._client_id, TASK_SK_PREFIX)
            if truncated:
                raise AdmissionCapacity("task_capacity")
            return bool(rows)
