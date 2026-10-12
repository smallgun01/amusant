"""DynamoDB Task Ledger adapter (ADR-0002).

Talks to an injected ledger. InMemoryDynamoLedger is the fake endpoint.
Boto3DynamoLedger talks to an existing table and never calls CreateTable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    if condition == "pending_queued":
        return current.get("outbox_state") == "pending" and current.get("status") == "queued"
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
            again = self._ledger.get_item(request.client_id, dedup_sk, consistent=True)
            if again is not None and again.get("payload_sha256") == fingerprint:
                return AdmissionReceipt(task_id=str(again["task_id"]), duplicate=True)
            raise AdmissionUnknown("acceptance_unconfirmed") from exc
        return AdmissionReceipt(task_id=request.task_id, duplicate=False)

    def recover_after_restart(self) -> RecoveryCounts:
        requeued = 0
        uncertain = 0
        for row in self._task_rows():
            if row.get("outbox_state") != "claimed":
                continue
            pk = str(row["pk"])
            sk = str(row["sk"])
            if row.get("dispatch_started_at"):
                self._ledger.update_item(
                    pk,
                    sk,
                    {
                        "outbox_state": "uncertain",
                        "status": "uncertain",
                        "last_error": "acp_result_unconfirmed",
                        "retain_until": int(datetime.now(timezone.utc).timestamp()) + RETAIN_SECONDS,
                    },
                    condition="claimed_started",
                )
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

    def claim_due(self, *, now: datetime, lease_seconds: int, limit: int = 1) -> list[ClaimedTask]:
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        now_iso = _utc_iso(now)
        claimed_until = _utc_iso(now + timedelta(seconds=lease_seconds))
        eligible = [
            row
            for row in self._task_rows()
            if row.get("outbox_state") == "pending"
            and row.get("status") == "queued"
            and str(row.get("expires_at") or "") > now_iso
        ]
        eligible.sort(key=lambda row: str(row.get("created_at") or ""))
        claimed: list[ClaimedTask] = []
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
                    condition="pending_queued",
                )
            except LedgerConditionFailed:
                continue
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
                )
            )
        return claimed

    def mark_dispatch_started(self, task_id: str, *, now: datetime) -> None:
        started = _utc_iso(now)
        try:
            self._ledger.update_item(
                self._client_id,
                _task_sk(task_id),
                {"dispatch_started_at": started},
                condition="claimed_unsent",
            )
        except LedgerConditionFailed as exc:
            raise AdmissionUnavailable("dispatch_not_claimed") from exc

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
        finished_at = datetime.now(timezone.utc)
        patch: dict[str, object] = {
            "outbox_state": outbox_state,
            "status": task_status,
            "last_error": error,
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
        if result is not None:
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
        row = self._ledger.get_item(self._client_id, _task_sk(task_id), consistent=True)
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "status": row["status"],
            "dispatch_state": row["outbox_state"],
            "attempts": row.get("attempts"),
            "dispatch_started": row.get("dispatch_started_at") is not None,
            "last_error": row.get("last_error"),
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
        expires = str(row.get("expires_at") or "")
        if status in {"queued", "running"} and expires <= now_iso:
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
        if status in {"completed", "failed", "uncertain", "expired"}:
            if speak and status == "completed" and result is not None:
                next_delivery = "spoken"
            elif delivery in {None, "pending", "notified"}:
                next_delivery = "read"
            if next_delivery is not None and next_delivery != delivery:
                self._ledger.update_item(
                    str(row["pk"]),
                    str(row["sk"]),
                    {"delivery_state": next_delivery, "inbox_updated_at": now_iso},
                    condition="exists",
                )
                delivery = next_delivery
        payload: dict[str, object] = {
            "task_id": row["task_id"],
            "status": status,
            "task_kind": row["task_kind"],
            "delivery_state": delivery,
        }
        if status == "completed" and result is not None:
            payload.update(result)
        if speak and status == "completed" and result is not None:
            payload["speak_now"] = True
        return payload

    def pending_dispatch_count(self) -> int:
        return sum(1 for row in self._task_rows() if row.get("outbox_state") == "pending")
