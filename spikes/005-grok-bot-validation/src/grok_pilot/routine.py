"""One-shot Routine Webhook client with explicit live gate and no resend behavior."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx2

from .contract import SCHEMA_VERSION, MAX_INSTRUCTION_CHARS, MAX_LABEL_CHARS
from .store import InvalidTransition, PilotStore, StoreError, Unauthorized


class RoutineSendError(RuntimeError):
    pass


def validate_https_endpoint(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise RoutineSendError("Routine URL must be an HTTPS URL without embedded credentials or fragment")


def submit_once(
    store: PilotStore,
    task_id: str,
    result_token: str,
    routine_url: str,
    routine_bearer_token: str,
    *,
    callback_mode: bool = False,
    transport: httpx2.BaseTransport | None = None,
) -> str:
    """Persist sending, make one POST with redirects disabled, and record uncertainty."""
    validate_https_endpoint(routine_url)
    if not routine_bearer_token:
        raise RoutineSendError("Routine ingress credential is required")
    task = store.get_task(task_id)
    if task["submission_state"] != "prepared":
        raise InvalidTransition("task is not in prepared state; no resend is allowed")
    if callback_mode and task["result_token_expires_at"] is None:
        raise RoutineSendError("callback mode requires an explicit finite result-token TTL")
    if not store.has_task_token(task_id, result_token):
        raise Unauthorized("invalid task result capability")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "result_token": result_token,
        "task_label": task["task_label"],
        "instruction": task["instruction"],
    }
    if len(task["instruction"]) > MAX_INSTRUCTION_CHARS or len(task["task_label"]) > MAX_LABEL_CHARS:
        raise RoutineSendError("prepared task exceeds the Routine payload limits")
    store.begin_submission(
        task_id,
        result_token=result_token if callback_mode else None,
        require_result_token_expiry=callback_mode,
    )
    try:
        # httpx2 has no implicit POST retries; follow_redirects=False prevents 307/308 replay.
        with httpx2.Client(
            timeout=httpx2.Timeout(15.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post(
                routine_url,
                json=payload,
                headers={"Authorization": f"Bearer {routine_bearer_token}"},
            )
        if response.status_code == 200:
            state = "accepted"
        elif 400 <= response.status_code < 500:
            state = "rejected"
        else:
            state = "acceptance_unknown"
    except (httpx2.HTTPError, OSError):
        state = "acceptance_unknown"
    try:
        store.finish_submission(task_id, state)
    except StoreError:
        # Keep the durable sending marker. An operator may explicitly recover it
        # after the recorded sender process has exited; opening a store is read-only.
        raise
    return state
