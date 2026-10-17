"""ACP-only one-shot using the real Dispatcher, executor, store, and Inbox.

This does not start Gemini Live, ECS, Jellii, or AWS. Exit 0 only when one
admitted task completes with a persisted bounded Inbox result.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import re
from uuid import uuid4

from .acp_client import load_acp_executor
from .dispatcher import AsyncDispatcher
from .store_factory import build_task_store
from .task_store import (
    MAX_TASK_CHARS,
    TASK_KINDS,
    AdmissionBusy,
    AdmissionCapacity,
    AdmissionConflict,
    AdmissionUnavailable,
    AdmissionUnknown,
    TaskAdmissionRequest,
)

EXIT_PASS = 0
EXIT_FAILED = 2
EXIT_UNCERTAIN = 3
EXIT_CONFIG = 4
DEFAULT_SMOKE_TASK = "Reply with a short comparison of two local laptop options."
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class SmokeReport:
    exit_code: int
    task_id: str | None
    status: str | None
    delivery_state: str | None
    error: str | None
    has_result: bool


def _require_id(environ, name: str) -> str:
    raw = environ.get(name)
    if not isinstance(raw, str) or not _ID_RE.fullmatch(raw.strip()):
        raise ValueError(f"{name} must be a bounded identity")
    return raw.strip()


def _smoke_task(environ) -> tuple[str, str]:
    task = environ.get("AMUSANT_SMOKE_TASK", DEFAULT_SMOKE_TASK)
    kind = environ.get("AMUSANT_DEFAULT_TASK_KIND", "compare")
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK_CHARS:
        raise ValueError("AMUSANT_SMOKE_TASK must be bounded non-empty text")
    if not isinstance(kind, str) or kind not in TASK_KINDS:
        raise ValueError("AMUSANT_DEFAULT_TASK_KIND must be research, compare, or summarize")
    return task.strip(), kind


async def run_smoke(environ) -> SmokeReport:
    """Admit one task and dispatch it once through the production ACP path."""
    try:
        if environ.get("AMUSANT_LIVE_PROVIDER") == "gemini":
            raise ValueError("ACP smoke must not enable Gemini Live")
        client_id = _require_id(environ, "AMUSANT_CLIENT_ID")
        backend_id = environ.get("AMUSANT_BACKEND_ID")
        if not isinstance(backend_id, str) or not backend_id.strip() or len(backend_id.strip()) > 64:
            raise ValueError("AMUSANT_BACKEND_ID must be set")
        task_text, task_kind = _smoke_task(environ)
        executor = load_acp_executor(environ)
        if executor is None:
            raise ValueError("ACP executor requires AMUSANT_ACP_URL, AMUSANT_ACP_AUTH_KEY, and AMUSANT_ACP_CWD")
        store = build_task_store(environ, max_pending_tasks=1)
    except ValueError:
        return SmokeReport(EXIT_CONFIG, None, None, None, "invalid_config", False)

    task_id = str(uuid4())
    try:
        store.recover_after_restart()
        now = datetime.now(timezone.utc)
        receipt = store.admit(
            TaskAdmissionRequest(
                task_id=task_id,
                client_id=client_id,
                conversation_id=f"acp-smoke-{uuid4()}",
                turn_id=1,
                call_id=f"smoke-{uuid4()}",
                task=task_text,
                task_kind=task_kind,
                backend=backend_id.strip(),
                created_at=now,
                expires_at=now + timedelta(seconds=600),
            )
        )
    except AdmissionUnknown:
        return SmokeReport(EXIT_UNCERTAIN, task_id, None, None, "acceptance_unconfirmed", False)
    except AdmissionUnavailable:
        return SmokeReport(EXIT_CONFIG, None, None, None, "task_store_unavailable", False)
    except AdmissionBusy:
        return SmokeReport(EXIT_FAILED, None, None, None, "slow_busy", False)
    except AdmissionCapacity:
        return SmokeReport(EXIT_FAILED, None, None, None, "task_capacity", False)
    except AdmissionConflict:
        return SmokeReport(EXIT_FAILED, None, None, None, "call_id_conflict", False)
    except ValueError:
        return SmokeReport(EXIT_CONFIG, None, None, None, "invalid_config", False)
    if receipt.duplicate:
        return SmokeReport(EXIT_FAILED, receipt.task_id, None, None, "duplicate_admission", False)
    task_id = receipt.task_id
    dispatcher = AsyncDispatcher(store, executor, max_attempts=1, now=lambda: now)
    try:
        await dispatcher.dispatch_available()
        row = store.get_by_task_id(task_id) or {}
        inbox = store.get_inbox(task_id)
        result = store.get_result(task_id)
    except (AdmissionUnavailable, AdmissionUnknown, Exception):
        return SmokeReport(
            EXIT_UNCERTAIN,
            task_id,
            None,
            None,
            "acp_result_unconfirmed",
            False,
        )
    status = row.get("status") if isinstance(row.get("status"), str) else None
    error = row.get("last_error") if isinstance(row.get("last_error"), str) else None
    delivery = inbox.get("delivery_state") if inbox else None
    has_result = bool(result and result.get("speak") and result.get("card") and result.get("details_md"))
    if status == "completed" and has_result and delivery is not None:
        return SmokeReport(EXIT_PASS, task_id, status, delivery, None, True)
    if status == "uncertain":
        return SmokeReport(EXIT_UNCERTAIN, task_id, status, delivery, error or "acp_result_unconfirmed", False)
    return SmokeReport(EXIT_FAILED, task_id, status, delivery, error or "acp_failed", has_result)


def _print_report(report: SmokeReport) -> None:
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
    try:
        report = asyncio.run(run_smoke(os.environ))
    except Exception:
        report = SmokeReport(EXIT_UNCERTAIN, None, None, None, "acp_result_unconfirmed", False)
    _print_report(report)
    raise SystemExit(report.exit_code)


if __name__ == "__main__":
    main()
