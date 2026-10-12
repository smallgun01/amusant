"""After-stop Task Ledger reader. Does not admit, dispatch, or call ACP."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from uuid import UUID

from .store_factory import build_task_store
from .task_store import AdmissionUnavailable, AdmissionUnknown

EXIT_PASS = 0
EXIT_FAILED = 2
EXIT_UNCERTAIN = 3
EXIT_CONFIG = 4


@dataclass(frozen=True, slots=True)
class GetReport:
    exit_code: int
    task_id: str | None
    status: str | None
    delivery_state: str | None
    error: str | None
    has_result: bool


def _task_id(environ) -> str:
    raw = environ.get("AMUSANT_TASK_ID")
    if not isinstance(raw, str):
        raise ValueError("AMUSANT_TASK_ID must be a UUID")
    parsed = str(UUID(raw.strip()))
    if parsed != raw.strip().lower():
        raise ValueError("AMUSANT_TASK_ID must be a UUID")
    return parsed


def run_get(environ) -> GetReport:
    try:
        if environ.get("AMUSANT_LIVE_PROVIDER") == "gemini":
            raise ValueError("store get must not enable Gemini Live")
        kind = (environ.get("AMUSANT_TASK_STORE") or "").strip().lower()
        if kind != "dynamodb":
            raise ValueError("AMUSANT_TASK_STORE must be dynamodb")
        if (environ.get("AMUSANT_DDB_ENDPOINT") or "").strip():
            raise ValueError("AMUSANT_DDB_ENDPOINT is not allowed for store_get proof")
        task_id = _task_id(environ)
        store = build_task_store(environ)
    except (ValueError, TypeError, AttributeError):
        return GetReport(EXIT_CONFIG, None, None, None, "invalid_config", False)
    try:
        row = store.get_by_task_id(task_id) or {}
        inbox = store.get_inbox(task_id)
        result = store.get_result(task_id)
    except (AdmissionUnavailable, AdmissionUnknown, Exception):
        return GetReport(EXIT_UNCERTAIN, task_id, None, None, "store_read_unconfirmed", False)
    status = row.get("status") if isinstance(row.get("status"), str) else None
    error = row.get("last_error") if isinstance(row.get("last_error"), str) else None
    delivery = inbox.get("delivery_state") if inbox else None
    has_result = bool(result and result.get("speak") and result.get("card") and result.get("details_md"))
    if status is None:
        return GetReport(EXIT_FAILED, task_id, None, None, "task_not_found", False)
    if status == "completed" and has_result and delivery is not None:
        return GetReport(EXIT_PASS, task_id, status, delivery, None, True)
    if status == "uncertain":
        return GetReport(EXIT_UNCERTAIN, task_id, status, delivery, error or "acp_result_unconfirmed", False)
    return GetReport(EXIT_FAILED, task_id, status, delivery, error or "not_completed", has_result)


def _print_report(report: GetReport) -> None:
    payload = {
        "exit_code": report.exit_code,
        "task_id": report.task_id,
        "status": report.status,
        "delivery_state": report.delivery_state,
        "error": report.error,
        "has_result": report.has_result,
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def main() -> None:
    report = run_get(os.environ)
    _print_report(report)
    raise SystemExit(report.exit_code)


if __name__ == "__main__":
    main()
