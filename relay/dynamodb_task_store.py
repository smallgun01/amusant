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
    AdmissionBusy,
    AdmissionCapacity,
    AdmissionConflict,
    AdmissionReceipt,
    AdmissionUnavailable,
    AdmissionUnknown,
    BoundedTaskResult,
    CancelReceipt,
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
SLOW_SLOT_SK = "SLOT#SLOW"
SLOW_SLOT_READY_SK = "META#SLOW_SINGLE_FLIGHT_V1"
LATEST_TASK_PREFIX = "LATEST#"
QUERY_ITEM_BOUND = 100
RETAIN_SECONDS = 30 * 24 * 60 * 60


class LedgerConditionFailed(RuntimeError):
    """Conditional write lost."""


class LedgerAmbiguous(RuntimeError):
    """Commit result is unknown; caller must read then AdmissionUnknown."""


class DynamoLedger(Protocol):
    def get_item(self, pk: str, sk: str, *, consistent: bool = True) -> dict[str, object] | None: ...

    def query_prefix(self, pk: str, sk_prefix: str) -> tuple[list[dict[str, object]], bool]: ...

    def find_active_tasks(self, pk: str, sk_prefix: str, now_iso: str) -> tuple[list[dict[str, object]], bool]: ...

    def query_latest(self, pk: str, sk_prefix: str) -> dict[str, object] | None: ...

    def latest_task(self, pk: str, sk_prefix: str) -> dict[str, object] | None: ...

    def has_prefix(self, pk: str, sk_prefix: str) -> bool: ...

    def delete_item(self, pk: str, sk: str, *, condition: str, expected_value: str) -> None: ...

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
    if condition == "uncertain_task":
        return current.get("outbox_state") == "uncertain" and current.get("status") == "uncertain"
    if condition == "uncertain_task":
        return current.get("outbox_state") == "uncertain" and current.get("status") == "uncertain"
    if condition == "pending_queued":
        return current.get("outbox_state") == "pending" and current.get("status") == "queued"
    if condition == "pending_running_known_run":
        return (
            current.get("outbox_state") == "pending"
            and current.get("status") == "running"
            and bool(current.get("backend_run_id"))
        )
    if condition == "cancel_pending_unsubmitted":
        return (
            current.get("outbox_state") == "pending"
            and current.get("status") == "queued"
            and not current.get("dispatch_started_at")
            and not current.get("backend_run_id")
        )
    if condition == "cancel_claimed_unsubmitted":
        return (
            current.get("outbox_state") == "claimed"
            and current.get("status") == "running"
            and not current.get("dispatch_started_at")
            and not current.get("backend_run_id")
        )
    if condition == "pending_started_run_id_absent":
        return (
            current.get("outbox_state") == "pending"
            and current.get("status") in {"queued", "running", "uncertain"}
            and bool(current.get("dispatch_started_at"))
            and not current.get("backend_run_id")
        )
    if condition == "cancel_ambiguous_no_run":
        return (
            current.get("outbox_state") in {"pending", "claimed", "uncertain"}
            and current.get("status") in {"queued", "running", "uncertain"}
            and bool(current.get("dispatch_started_at"))
            and not current.get("backend_run_id")
        )
    if condition == "request_stop_known_run":
        return (
            current.get("outbox_state") in {"pending", "claimed", "uncertain"}
            and current.get("status") in {"queued", "running", "uncertain"}
            and bool(current.get("dispatch_started_at"))
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
        self._lock = threading.RLock()

    def get_item(self, pk: str, sk: str, *, consistent: bool = True) -> dict[str, object] | None:
        del consistent
        with self._lock:
            item = self.items.get((pk, sk))
            return None if item is None else dict(item)

    def query_prefix(self, pk: str, sk_prefix: str) -> tuple[list[dict[str, object]], bool]:
        with self._lock:
            rows = [
                dict(value)
                for (part, sort), value in self.items.items()
                if part == pk and sort.startswith(sk_prefix)
            ]
        rows.sort(key=lambda row: str(row.get("sk") or ""))
        truncated = len(rows) > QUERY_ITEM_BOUND
        return rows[:QUERY_ITEM_BOUND], truncated

    def find_active_tasks(self, pk: str, sk_prefix: str, now_iso: str) -> tuple[list[dict[str, object]], bool]:
        with self._lock:
            rows = [
                dict(value)
                for (part, sort), value in self.items.items()
                if part == pk
                and sort.startswith(sk_prefix)
                and (
                    value.get("status") in {"running", "uncertain"}
                    or (
                        value.get("status") == "queued"
                        and (
                            str(value.get("expires_at") or "") > now_iso
                            or value.get("backend_run_id")
                            or value.get("dispatch_started_at")
                        )
                    )
                )
            ]
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        truncated = len(rows) > QUERY_ITEM_BOUND
        return rows[:QUERY_ITEM_BOUND], truncated

    def query_latest(self, pk: str, sk_prefix: str) -> dict[str, object] | None:
        with self._lock:
            rows = [
                dict(value)
                for (part, sort), value in self.items.items()
                if part == pk and sort.startswith(sk_prefix)
            ]
        return max(rows, key=lambda row: str(row.get("sk") or "")) if rows else None

    def latest_task(self, pk: str, sk_prefix: str) -> dict[str, object] | None:
        with self._lock:
            rows = [
                dict(value)
                for (part, sort), value in self.items.items()
                if part == pk and sort.startswith(sk_prefix)
            ]
        return max(rows, key=lambda row: str(row.get("created_at") or "")) if rows else None

    def has_prefix(self, pk: str, sk_prefix: str) -> bool:
        with self._lock:
            return any(part == pk and sort.startswith(sk_prefix) for part, sort in self.items)

    def transact_put(
        self,
        puts: list[dict[str, object]],
        *,
        absent: list[tuple[str, str]],
    ) -> None:
        with self._lock:
            if self.ambiguous_next:
                self.ambiguous_next = False
                raise LedgerAmbiguous()
            for key in absent:
                if key in self.items:
                    raise LedgerConditionFailed()
            for item in puts:
                key = (str(item["pk"]), str(item["sk"]))
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
        with self._lock:
            key = (pk, sk)
            current = self.items.get(key)
            if not _condition_holds(None if current is None else dict(current), condition):
                raise LedgerConditionFailed()
            merged = _apply_patch(dict(current or {}), patch)
            merged["pk"] = pk
            merged["sk"] = sk
            self.items[key] = merged
            return dict(merged)

    def delete_item(self, pk: str, sk: str, *, condition: str, expected_value: str) -> None:
        with self._lock:
            key = (pk, sk)
            current = self.items.get(key)
            if condition != "slot_matches_task" or current is None or current.get("active_task_id") != expected_value:
                raise LedgerConditionFailed()
            del self.items[key]


def _task_sk(task_id: str) -> str:
    return f"{TASK_SK_PREFIX}{task_id}"


def _dedup_sk(conversation_id: str, turn_id: int, call_id: str) -> str:
    return f"{DEDUP_SK_PREFIX}{conversation_id}#{turn_id}#{call_id}"

def _slot_sk() -> str:
    return SLOW_SLOT_SK


def _slot_ready_sk() -> str:
    return SLOW_SLOT_READY_SK


def _latest_sk(created_at: str, task_id: str) -> str:
    return f"{LATEST_TASK_PREFIX}{created_at}#{task_id}"


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

    def _find_legacy_active_tasks(self, now_iso: str) -> tuple[list[dict[str, object]], bool]:
        finder = getattr(self._ledger, "find_active_tasks", None)
        if callable(finder):
            return finder(self._client_id, TASK_SK_PREFIX, now_iso)
        rows, truncated = self._ledger.query_prefix(self._client_id, TASK_SK_PREFIX)
        active = [
            row for row in rows
            if row.get("status") in {"running", "uncertain"}
            or (
                row.get("status") == "queued"
                and (
                    str(row.get("expires_at") or "") > now_iso
                    or row.get("backend_run_id")
                    or row.get("dispatch_started_at")
                )
            )
        ]
        return active, truncated

    def _release_slot_if_matches(self, task_id: str) -> bool:
        deleter = getattr(self._ledger, "delete_item", None)
        if not callable(deleter):
            raise AdmissionUnavailable("single_flight_slot_release_unavailable")
        try:
            deleter(
                self._client_id,
                _slot_sk(),
                condition="slot_matches_task",
                expected_value=task_id,
            )
            return True
        except LedgerConditionFailed:
            return False
        except LedgerAmbiguous:
            current = self._ledger.get_item(self._client_id, _slot_sk(), consistent=True)
            if current is None or current.get("active_task_id") != task_id:
                return True
            raise AdmissionUnknown("single_flight_slot_release_unconfirmed") from None

    def _active_slot(self, now_iso: str) -> dict[str, object] | None:
        slot = self._ledger.get_item(self._client_id, _slot_sk(), consistent=True)
        if slot is None:
            return None
        task_id = slot.get("active_task_id")
        if not isinstance(task_id, str) or not task_id:
            return slot
        task = self._ledger.get_item(self._client_id, _task_sk(task_id), consistent=True)
        if task is None:
            return slot
        if task.get("status") in {"completed", "failed"}:
            self._release_slot_if_matches(task_id)
            return self._ledger.get_item(self._client_id, _slot_sk(), consistent=True)
        if (
            task.get("status") == "queued"
            and not task.get("backend_run_id")
            and not task.get("dispatch_started_at")
            and str(task.get("expires_at") or "") <= now_iso
        ):
            if self._expire_unsubmitted(task):
                self._release_slot_if_matches(task_id)
                return self._ledger.get_item(self._client_id, _slot_sk(), consistent=True)
        return slot

    def _expire_unsubmitted(self, task: dict[str, object]) -> bool:
        key = str(task["sk"])
        try:
            updated = self._ledger.update_item(
                self._client_id,
                key,
                {
                    "status": "failed",
                    "outbox_state": "failed",
                    "last_error": "slow_queue_expired",
                    "next_poll_at": None,
                    "retain_until": int(datetime.now(timezone.utc).timestamp()) + RETAIN_SECONDS,
                    "delivery_state": "pending",
                    "inbox_updated_at": datetime.now(timezone.utc).isoformat(),
                },
                condition="pending_queued",
            )
            return bool(updated and updated.get("status") == "failed" and updated.get("outbox_state") == "failed")
        except LedgerConditionFailed:
            current = self._ledger.get_item(self._client_id, key, consistent=True)
            return bool(
                current
                and (
                    (current.get("status") == "completed" and current.get("outbox_state") == "sent")
                    or (current.get("status") == "failed" and current.get("outbox_state") == "failed")
                )
            )

    def _slot_ready(self) -> bool:
        return self._ledger.get_item(self._client_id, _slot_ready_sk(), consistent=True) is not None

    def _mark_slot_ready(self) -> None:
        if self._slot_ready():
            return
        item = {
            "pk": self._client_id,
            "sk": _slot_ready_sk(),
            "single_flight_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._ledger.transact_put(
                [item],
                absent=[(self._client_id, _slot_ready_sk())],
            )
        except LedgerConditionFailed:
            return
        except LedgerAmbiguous:
            if self._slot_ready():
                return
            raise AdmissionUnknown("single_flight_migration_unconfirmed") from None

    def _work_rows(self, now_iso: str) -> list[dict[str, object]]:
        slot = self._active_slot(now_iso)
        if slot is not None:
            task_id = slot.get("active_task_id")
            if not isinstance(task_id, str) or not task_id:
                # A malformed/stuck slot is deliberately fail-closed.
                return []
            task = self._ledger.get_item(self._client_id, _task_sk(task_id), consistent=True)
            return [] if task is None else [task]
        if self._slot_ready():
            return []
        rows, truncated = self._find_legacy_active_tasks(now_iso)
        if truncated:
            raise AdmissionCapacity("legacy_active_reconciliation_required")
        if not rows:
            self._mark_slot_ready()
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

        created = _utc_iso(request.created_at)
        expires = _utc_iso(request.expires_at)
        retain = _retain_until(request.created_at)
        ready_sk = _slot_ready_sk()
        slot_sk = _slot_sk()
        for attempt in range(3):
            slot = self._active_slot(created)
            if slot is not None:
                active_id = slot.get("active_task_id")
                raise AdmissionBusy(active_id if isinstance(active_id, str) else None)
            ready = self._ledger.get_item(request.client_id, ready_sk, consistent=True)
            if ready is None:
                legacy, truncated = self._find_legacy_active_tasks(created)
                if truncated:
                    raise AdmissionBusy(str(legacy[0].get("task_id")) if legacy else None)
                if legacy:
                    first = legacy[0].get("task_id")
                    raise AdmissionBusy(str(first) if isinstance(first, str) else None)

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
                "expires_at": expires,
                "outbox_state": "pending",
                "attempts": 0,
                "claimed_until": None,
                "dispatch_started_at": None,
                "last_error": None,
                "run_started_at": None,
                "run_deadline_at": None,
                "next_poll_at": None,
                "poll_attempts": 0,
                "stop_requested_at": None,
                "stop_reason": None,
                "stop_attempts": 0,
                "stop_accepted_at": None,
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
            slot_item = {
                "pk": request.client_id,
                "sk": slot_sk,
                "slot_state": "busy",
                "active_task_id": request.task_id,
                "created_at": created,
            }
            latest_item = {
                "pk": request.client_id,
                "sk": _latest_sk(created, request.task_id),
                "task_id": request.task_id,
                "created_at": created,
                "retain_until": retain,
            }
            puts = [task_item, dedup_item, slot_item, latest_item]
            absent = [
                (request.client_id, dedup_sk),
                (request.client_id, _task_sk(request.task_id)),
                (request.client_id, slot_sk),
                (request.client_id, _latest_sk(created, request.task_id)),
            ]
            if ready is None:
                puts.append({
                    "pk": request.client_id,
                    "sk": ready_sk,
                    "single_flight_version": 1,
                    "created_at": created,
                })
                absent.append((request.client_id, ready_sk))
            try:
                self._ledger.transact_put(puts, absent=absent)
            except LedgerConditionFailed as exc:
                again = self._ledger.get_item(request.client_id, dedup_sk, consistent=True)
                if again is not None:
                    if again.get("payload_sha256") == fingerprint:
                        return AdmissionReceipt(task_id=str(again["task_id"]), duplicate=True)
                    raise AdmissionConflict("call_id_conflict") from exc
                current_slot = self._active_slot(created)
                if current_slot is not None:
                    active_id = current_slot.get("active_task_id")
                    raise AdmissionBusy(active_id if isinstance(active_id, str) else None) from exc
                collision = self._ledger.get_item(request.client_id, _task_sk(request.task_id), consistent=True)
                if collision is not None:
                    raise AdmissionConflict("task_id_conflict") from exc
                if attempt < 2:
                    continue
                raise AdmissionUnavailable("task_store_unavailable") from exc
            except LedgerAmbiguous as exc:
                try:
                    again = self._ledger.get_item(request.client_id, dedup_sk, consistent=True)
                except (AdmissionUnavailable, LedgerAmbiguous):
                    raise AdmissionUnknown("acceptance_unconfirmed") from exc
                if again is not None and again.get("payload_sha256") == fingerprint:
                    return AdmissionReceipt(task_id=str(again["task_id"]), duplicate=True)
                current_slot = self._ledger.get_item(request.client_id, slot_sk, consistent=True)
                if current_slot is not None and current_slot.get("active_task_id") != request.task_id:
                    active_id = current_slot.get("active_task_id")
                    raise AdmissionBusy(active_id if isinstance(active_id, str) else None) from exc
                raise AdmissionUnknown("acceptance_unconfirmed") from exc
            return AdmissionReceipt(task_id=request.task_id, duplicate=False)
        raise AdmissionUnavailable("task_store_unavailable")

    def recover_after_restart(self) -> RecoveryCounts:
        with self._lock:
            return self._recover_locked()

    def _recover_locked(self) -> RecoveryCounts:
        self._next_hermes_wake = None
        now_iso = _utc_iso(datetime.now(timezone.utc))
        rows = self._work_rows(now_iso)
        requeued = 0
        uncertain = 0
        for row in rows:
            state = row.get("outbox_state")
            started = row.get("dispatch_started_at")
            has_run = row.get("executor_kind", "openab_acp") == "hermes_runs" and bool(row.get("backend_run_id"))
            if started and not has_run and state in {"claimed", "pending"}:
                error = "hermes_submission_unconfirmed" if row.get("executor_kind") == "hermes_runs" else "acp_result_unconfirmed"
                updated = self._ledger.update_item(
                    str(row["pk"]), str(row["sk"]),
                    {
                        "outbox_state": "uncertain",
                        "status": "uncertain",
                        "last_error": error,
                        "retain_until": int(datetime.now(timezone.utc).timestamp()) + RETAIN_SECONDS,
                    },
                    condition="claimed_started" if state == "claimed" else "pending_started_run_id_absent",
                )
                self._align_dedup_ttl(updated)
                uncertain += 1
                continue
            if state != "claimed":
                continue
            if started and has_run:
                updated = self._ledger.update_item(
                    str(row["pk"]), str(row["sk"]),
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
                updated = self._ledger.update_item(
                    str(row["pk"]), str(row["sk"]),
                    {"outbox_state": "pending", "status": "queued", "claimed_until": None},
                    condition="claimed_unsent",
                )
                self._align_dedup_ttl(updated)
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
        rows = self._work_rows(now_iso)
        legacy_mode = self._ledger.get_item(self._client_id, _slot_sk(), consistent=True) is None and not self._slot_ready()
        eligible = [
            row for row in rows
            if row.get("outbox_state") == "pending"
            and (
                (
                    row.get("status") == "queued"
                    and str(row.get("expires_at") or "") > now_iso
                    and not row.get("dispatch_started_at")
                    and not row.get("backend_run_id")
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
        # During one-time legacy reconciliation, drain existing known Runs by
        # GET only. If no Run exists yet, submit at most the oldest untouched
        # legacy queue entry; its resulting Run then blocks other legacy POSTs.
        if legacy_mode and len(rows) > 1:
            known_runs = [row for row in rows if row.get("backend_run_id")]
            ambiguous = [
                row for row in rows
                if row.get("status") == "uncertain"
                or (row.get("dispatch_started_at") and not row.get("backend_run_id"))
            ]
            if known_runs:
                eligible = [row for row in eligible if row.get("backend_run_id")]
            elif ambiguous:
                eligible = []
            else:
                unsubmitted = [
                    row for row in eligible
                    if row.get("status") == "queued"
                    and not row.get("backend_run_id")
                    and not row.get("dispatch_started_at")
                ]
                oldest = min(unsubmitted, key=lambda row: str(row.get("created_at") or "")) if unsubmitted else None
                eligible = [] if oldest is None else [oldest]
        eligible.sort(key=lambda row: str(row.get("created_at") or ""))
        claimed: list[ClaimedTask] = []
        claimed_ids: set[str] = set()
        for row in eligible[:limit]:
            state = row.get("outbox_state")
            condition = (
                "pending_running_known_run"
                if row.get("status") == "running" and row.get("backend_run_id")
                else "pending_queued"
            )
            try:
                updated = self._ledger.update_item(
                    str(row["pk"]),
                    str(row["sk"]),
                    {
                        "outbox_state": "claimed",
                        "status": "running",
                        "attempts": int(row.get("attempts") or 0) + 1,
                        "claimed_until": claimed_until,
                    },
                    condition=condition,
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
                    stop_requested_at=str(updated["stop_requested_at"]) if updated.get("stop_requested_at") else None,
                    stop_reason=str(updated["stop_reason"]) if updated.get("stop_reason") else None,
                    stop_attempts=int(updated.get("stop_attempts") or 0),
                    stop_accepted_at=str(updated["stop_accepted_at"]) if updated.get("stop_accepted_at") else None,
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
                # A stop requested during an already-running poll gets one immediate
                # wake; after a stop attempt/ack, keep the durable retry or GET schedule.
                effective_poll_iso = next_poll_iso
                if (current.get("stop_requested_at") and int(current.get("stop_attempts") or 0) == 0
                        and not current.get("stop_accepted_at")):
                    effective_poll_iso = min(next_poll_iso, str(current["stop_requested_at"]))
                self._ledger.update_item(
                    self._client_id,
                    key,
                    {
                        "outbox_state": "pending",
                        "status": "running",
                        "claimed_until": None,
                        "last_error": None,
                        "next_poll_at": effective_poll_iso,
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
                    and fresh.get("next_poll_at") == effective_poll_iso
                    and int(fresh.get("poll_attempts") or 0) == poll_attempts
                ):
                    return
                raise AdmissionUnknown("hermes_defer_state_unconfirmed") from exc

    def request_cancel(self, *, client_id: str, task_id: str, now: datetime) -> CancelReceipt:
        if client_id != self._client_id or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        return self.request_stop(task_id, reason="owner_cancel", now=now)

    def request_stop(self, task_id: str, *, reason: str, now: datetime) -> CancelReceipt:
        if reason not in {"owner_cancel", "run_deadline"}:
            raise AdmissionUnavailable("invalid_stop_reason")
        if not isinstance(task_id, str):
            return CancelReceipt(str(task_id), "not_found", "task_not_found")
        now_iso = _utc_iso(now)
        with self._lock:
            return self._request_stop_locked(task_id, reason=reason, now_iso=now_iso)

    def _request_stop_locked(self, task_id: str, *, reason: str, now_iso: str) -> CancelReceipt:
        key = _task_sk(task_id)
        current = self._ledger.get_item(self._client_id, key, consistent=True)
        if current is None:
            return CancelReceipt(task_id, "not_found", "task_not_found")
        status = str(current.get("status") or "")
        code = str(current.get("last_error") or "")
        run_id = current.get("backend_run_id")
        outbox_state = str(current.get("outbox_state") or "")
        dispatch_started = bool(current.get("dispatch_started_at"))
        executor_kind = str(current.get("executor_kind") or "openab_acp")
        if status == "completed":
            return CancelReceipt(task_id, "completed", "already_terminal")
        if status == "failed":
            visible = "cancelled" if code in {"slow_cancelled_before_dispatch", "hermes_cancelled"} else ("timed_out" if code == "hermes_run_timed_out" else "failed")
            return CancelReceipt(task_id, visible, code or "already_terminal")
        if current.get("stop_requested_at"):
            if status == "uncertain" and code == "hermes_stop_unconfirmed":
                return CancelReceipt(task_id, "uncertain", code, duplicate=True)
            waiting_for_id = executor_kind == "hermes_runs" and not run_id and dispatch_started and outbox_state == "claimed"
            return CancelReceipt(
                task_id,
                "stopping" if run_id or waiting_for_id else "uncertain",
                "hermes_stop_pending" if run_id else ("hermes_stop_waiting_run_id" if waiting_for_id else (code or "hermes_submission_unconfirmed")),
                duplicate=True,
            )

        if run_id and executor_kind == "hermes_runs":
            patch: dict[str, object] = {
                "status": "running",
                "stop_requested_at": now_iso,
                "stop_reason": reason,
                "stop_attempts": 0,
                "stop_accepted_at": None,
                "next_poll_at": now_iso,
                "last_error": None,
            }
            if outbox_state in {"pending", "uncertain"}:
                patch.update({"outbox_state": "pending", "claimed_until": None})
            try:
                self._ledger.update_item(
                    self._client_id,
                    key,
                    patch,
                    condition="request_stop_known_run",
                )
            except LedgerConditionFailed as exc:
                raise AdmissionUnavailable("cancel_state_unavailable") from exc
            except LedgerAmbiguous as exc:
                fresh = self._ledger.get_item(self._client_id, key, consistent=True)
                if fresh is not None and fresh.get("stop_requested_at") == now_iso and fresh.get("backend_run_id") == run_id:
                    return CancelReceipt(task_id, "stopping", "hermes_stop_requested")
                raise AdmissionUnknown("cancel_state_unconfirmed") from exc
            slot = self._ledger.get_item(self._client_id, _slot_sk(), consistent=True)
            if slot is None:
                self._ensure_slot_for_existing_run(current, now_iso)
            return CancelReceipt(task_id, "stopping", "hermes_stop_requested")

        if (
            executor_kind == "hermes_runs"
            and not run_id
            and dispatch_started
            and outbox_state == "claimed"
            and status == "running"
        ):
            try:
                self._ledger.update_item(
                    self._client_id,
                    key,
                    {
                        "stop_requested_at": now_iso,
                        "stop_reason": reason,
                        "stop_attempts": 0,
                        "stop_accepted_at": None,
                        "next_poll_at": now_iso,
                    },
                    condition="claimed_started_run_id_absent",
                )
            except LedgerConditionFailed as exc:
                raise AdmissionUnavailable("cancel_state_unavailable") from exc
            return CancelReceipt(task_id, "stopping", "hermes_stop_waiting_run_id")

        if not dispatch_started and status in {"queued", "running"}:
            terminal_code = "slow_cancelled_before_dispatch" if reason == "owner_cancel" else "hermes_deadline_without_run"
            condition = "cancel_pending_unsubmitted" if outbox_state == "pending" else "cancel_claimed_unsubmitted"
            try:
                self._ledger.update_item(
                    self._client_id,
                    key,
                    {
                        "status": "failed",
                        "outbox_state": "failed",
                        "claimed_until": None,
                        "last_error": terminal_code,
                        "stop_requested_at": now_iso,
                        "stop_reason": reason,
                        "stop_accepted_at": None,
                        "next_poll_at": None,
                        "retain_until": int(datetime.now(timezone.utc).timestamp()) + RETAIN_SECONDS,
                        "delivery_state": "pending",
                        "inbox_updated_at": now_iso,
                    },
                    condition=condition,
                )
            except LedgerConditionFailed as exc:
                raise AdmissionUnavailable("cancel_state_unavailable") from exc
            task = self._ledger.get_item(self._client_id, key, consistent=True)
            if task is not None:
                self._align_dedup_ttl(task)
            self._release_slot_if_matches(task_id)
            return CancelReceipt(task_id, "cancelled", terminal_code)

        # A request may have reached a remote executor without a durable Run ID.
        # Keep the owner slot occupied and never infer that cancellation worked.
        ambiguous_code = "hermes_submission_unconfirmed" if executor_kind == "hermes_runs" else "acp_cancel_unconfirmed"
        try:
            self._ledger.update_item(
                self._client_id,
                key,
                {
                    "status": "uncertain",
                    "outbox_state": "uncertain",
                    "last_error": ambiguous_code,
                    "stop_requested_at": now_iso,
                    "stop_reason": reason,
                    "next_poll_at": None,
                    "retain_until": int(datetime.now(timezone.utc).timestamp()) + RETAIN_SECONDS,
                    "delivery_state": "pending",
                    "inbox_updated_at": now_iso,
                },
                condition="cancel_ambiguous_no_run",
            )
        except LedgerConditionFailed as exc:
            raise AdmissionUnavailable("cancel_state_unavailable") from exc
        task = self._ledger.get_item(self._client_id, key, consistent=True)
        if task is not None:
            self._align_dedup_ttl(task)
        return CancelReceipt(task_id, "uncertain", ambiguous_code)

    def _ensure_slot_for_existing_run(self, task: dict[str, object], now_iso: str) -> None:
        """Restore a missing reservation for a known active Run; never create a new Run."""
        active_id = str(task.get("task_id") or "")
        if not active_id:
            return
        slot = {
            "pk": self._client_id,
            "sk": _slot_sk(),
            "slot_state": "busy",
            "active_task_id": active_id,
            "created_at": str(task.get("created_at") or now_iso),
            "recovered": True,
        }
        try:
            self._ledger.transact_put(
                [slot],
                absent=[(self._client_id, _slot_sk())],
            )
        except LedgerConditionFailed:
            return
        except LedgerAmbiguous:
            current = self._ledger.get_item(self._client_id, _slot_sk(), consistent=True)
            if current is None or current.get("active_task_id") != active_id:
                raise AdmissionUnknown("single_flight_slot_recovery_unconfirmed") from None

    def update_hermes_stop_state(
        self,
        task_id: str,
        *,
        stop_attempts: int,
        stop_accepted_at: datetime | None,
        next_poll_at: datetime,
    ) -> None:
        if type(stop_attempts) is not int or stop_attempts < 0:
            raise AdmissionUnavailable("invalid_stop_attempts")
        patch: dict[str, object] = {
            "stop_attempts": stop_attempts,
            "next_poll_at": _utc_iso(next_poll_at),
        }
        if stop_accepted_at is not None:
            patch["stop_accepted_at"] = _utc_iso(stop_accepted_at)
        with self._lock:
            key = _task_sk(task_id)
            try:
                self._ledger.update_item(
                    self._client_id,
                    key,
                    patch,
                    condition="claimed_started_run_known",
                )
            except LedgerConditionFailed as exc:
                raise AdmissionUnavailable("hermes_stop_state_unavailable") from exc
            except LedgerAmbiguous as exc:
                fresh = self._ledger.get_item(self._client_id, key, consistent=True)
                if (
                    fresh is not None
                    and int(fresh.get("stop_attempts") or 0) == stop_attempts
                    and fresh.get("next_poll_at") == patch["next_poll_at"]
                    and (stop_accepted_at is None or fresh.get("stop_accepted_at") == patch["stop_accepted_at"])
                ):
                    return
                raise AdmissionUnknown("hermes_stop_state_unconfirmed") from exc

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

    def operator_reconcile_uncertain(
        self,
        *,
        client_id: str,
        task_id: str,
        terminal_state: str,
        verified_run_id: str | None,
        operator_id: str,
        evidence_ref: str,
        now: datetime,
    ) -> CancelReceipt:
        if client_id != self._client_id or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        if terminal_state not in {"completed", "failed", "cancelled", "interrupted", "no_remote_run"}:
            raise AdmissionUnavailable("invalid_reconciliation_state")
        if not isinstance(operator_id, str) or not _ID_RE.fullmatch(operator_id):
            raise AdmissionUnavailable("invalid_operator_id")
        if not isinstance(evidence_ref, str) or not evidence_ref.strip() or len(evidence_ref) > 512:
            raise AdmissionUnavailable("reconciliation_evidence_required")
        if verified_run_id is not None and (not isinstance(verified_run_id, str) or not _RUN_ID_RE.fullmatch(verified_run_id)):
            raise AdmissionUnavailable("invalid_reconciled_run_id")
        now_iso = _utc_iso(now)
        with self._lock:
            key = _task_sk(task_id)
            row = self._ledger.get_item(client_id, key, consistent=True)
            if row is None:
                return CancelReceipt(task_id, "not_found", "task_not_found")
            if row.get("status") != "uncertain" or row.get("outbox_state") != "uncertain":
                raise AdmissionUnavailable("task_not_in_manual_reconciliation")
            saved_run_id = row.get("backend_run_id")
            if saved_run_id:
                if verified_run_id != saved_run_id or terminal_state == "no_remote_run":
                    raise AdmissionUnavailable("reconciliation_run_id_mismatch")
            elif terminal_state == "no_remote_run":
                if verified_run_id is not None:
                    raise AdmissionUnavailable("reconciliation_run_id_mismatch")
            elif verified_run_id is None:
                raise AdmissionUnavailable("reconciliation_run_id_required")
            stop_reason = row.get("stop_reason")
            if terminal_state == "completed":
                code, visible = "hermes_operator_reconciled_completed_no_result", "failed"
            elif terminal_state in {"cancelled", "interrupted"}:
                code = "hermes_run_timed_out" if stop_reason == "run_deadline" else "hermes_cancelled"
                visible = "timed_out" if stop_reason == "run_deadline" else "cancelled"
            elif terminal_state == "no_remote_run":
                code = "hermes_operator_confirmed_no_remote_run"
                visible = "cancelled" if stop_reason == "owner_cancel" else "failed"
            else:
                code, visible = "hermes_failed", "failed"
            patch = {
                "status": "failed",
                "outbox_state": "failed",
                "last_error": code,
                "next_poll_at": None,
                "retain_until": _retain_until(now),
                "delivery_state": "pending",
                "inbox_updated_at": now_iso,
                "manual_reconciled_at": now_iso,
                "manual_reconciled_by": operator_id,
                "manual_reconciliation_ref": evidence_ref.strip(),
                "manual_reconciled_state": terminal_state,
                "manual_reconciled_run_id": verified_run_id,
            }
            try:
                updated = self._ledger.update_item(client_id, key, patch, condition="uncertain_task")
            except LedgerConditionFailed as exc:
                raise AdmissionUnavailable("task_not_in_manual_reconciliation") from exc
            self._align_dedup_ttl(updated)
            self._release_slot_if_matches(task_id)
            return CancelReceipt(task_id, visible, code)

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
        if task_status in {"completed", "failed"}:
            self._release_slot_if_matches(task_id)

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
            "stop_requested_at": row.get("stop_requested_at"),
            "stop_reason": row.get("stop_reason"),
            "stop_attempts": int(row.get("stop_attempts") or 0),
            "stop_accepted_at": row.get("stop_accepted_at"),
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
            slot = self._ledger.get_item(client_id, _slot_sk(), consistent=True)
            active_id = slot.get("active_task_id") if slot is not None else None
            row = self._ledger.get_item(client_id, _task_sk(active_id), consistent=True) if isinstance(active_id, str) else None
            if row is None:
                pointer = self._ledger.query_latest(client_id, LATEST_TASK_PREFIX)
                latest_id = pointer.get("task_id") if pointer is not None else None
                row = self._ledger.get_item(client_id, _task_sk(latest_id), consistent=True) if isinstance(latest_id, str) else None
            if row is None:
                row = self._ledger.latest_task(client_id, TASK_SK_PREFIX)
            if row is None:
                return None
        else:
            found = self._ledger.get_item(client_id, _task_sk(task_id), consistent=True)
            if found is None:
                return None
            row = found
        status = str(row["status"])
        if status == "uncertain" and (
            row.get("last_error") in {"hermes_run_deadline_exceeded", "hermes_run_timed_out"}
            or (row.get("stop_reason") == "run_deadline" and row.get("last_error") == "hermes_stop_unconfirmed")
        ):
            # Surface Relay timeout policy without asserting remote cancellation.
            status = "timed_out"
        elif status == "failed" and row.get("last_error") in {"hermes_cancelled", "slow_cancelled_before_dispatch"}:
            status = "cancelled"
        elif status == "failed" and row.get("last_error") == "hermes_run_timed_out":
            status = "timed_out"
        elif status == "running" and row.get("stop_requested_at"):
            status = "stopping"
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
        if status in {"completed", "failed", "uncertain", "timed_out", "cancelled", "expired"}:
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
        if status in {"failed", "uncertain", "timed_out", "cancelled"} and isinstance(row.get("last_error"), str):
            payload["code"] = row["last_error"]
        elif status == "stopping":
            payload["code"] = "hermes_stop_pending"
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
            slot = self._ledger.get_item(self._client_id, _slot_sk(), consistent=True)
            if slot is not None:
                active_id = slot.get("active_task_id")
                if not isinstance(active_id, str):
                    return 0
                row = self._ledger.get_item(self._client_id, _task_sk(active_id), consistent=True)
                return int(row is not None and row.get("outbox_state") == "pending")
            if self._slot_ready():
                return 0
            rows, truncated = self._find_legacy_active_tasks(_utc_iso(datetime.now(timezone.utc)))
            if truncated:
                raise AdmissionCapacity("legacy_active_reconciliation_required")
            return sum(1 for row in rows if row.get("outbox_state") == "pending")

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
