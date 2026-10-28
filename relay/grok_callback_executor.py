"""Submit one Grok Routine Webhook through the configured HTTPS egress proxy."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import socket
import ssl
import time
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlsplit

from .dispatcher import ExecutionOutcome
from .grok_result import GROK_RESULT_SCHEMA
from .task_store import GROK_BACKEND_ID, ClaimedTask

ROUTINE_URL_ENV = "AMUSANT_GROK_ROUTINE_URL"
ROUTINE_TOKEN_ENV = "AMUSANT_GROK_ROUTINE_TOKEN"
CALLBACK_URL_ENV = "AMUSANT_GROK_CALLBACK_URL"
CALLBACK_TOKEN_TTL_ENV = "AMUSANT_GROK_CALLBACK_TOKEN_TTL_SECONDS"
CALLBACK_WAIT_ENV = "AMUSANT_GROK_CALLBACK_WAIT_SECONDS"
DEFAULT_CALLBACK_TOKEN_TTL_SECONDS = 3_600
DEFAULT_CALLBACK_WAIT_SECONDS = 900
MAX_CALLBACK_TOKEN_TTL_SECONDS = 86_400
MAX_CALLBACK_WAIT_SECONDS = 86_400
MAX_ROUTINE_INSTRUCTION_CHARS = 4_096
logger = logging.getLogger(__name__)


class GrokConfigError(ValueError):
    """Missing or unsafe server-owned Grok Routine configuration."""


def _https_url(value: str, name: str, *, path: str | None = None) -> None:
    try:
        parts = urlsplit(value)
    except ValueError:
        raise GrokConfigError(f"{name} is invalid") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or len(value) > 512
        or len(parts.hostname) > 253
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or (path is not None and parts.path != path)
    ):
        raise GrokConfigError(f"{name} must be an HTTPS URL without userinfo, query, or fragment")
    try:
        port = parts.port
    except ValueError:
        raise GrokConfigError(f"{name} has an invalid port") from None
    if port is not None and not 1 <= port <= 65535:
        raise GrokConfigError(f"{name} has an invalid port")
    host = parts.hostname.lower().rstrip(".")
    if host == "invalid" or host.endswith(".invalid") or any(x in value.lower() for x in ("placeholder", "your-", "<", ">")):
        raise GrokConfigError(f"{name} contains a reserved or placeholder value")
    if parts.hostname == "localhost" or parts.hostname.endswith(".localhost"):
        raise GrokConfigError(f"{name} must not use a local hostname")
    try:
        literal = ipaddress.ip_address(parts.hostname)
    except ValueError:
        pass
    else:
        if not literal.is_global:
            raise GrokConfigError(f"{name} must not use a nonpublic IP address")


def _proxy_url(value: str) -> None:
    try:
        parts = urlsplit(value)
    except ValueError:
        raise GrokConfigError("HTTPS egress proxy URL is invalid") from None
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise GrokConfigError("HTTPS egress proxy URL must be configured without credentials")
    try:
        if parts.port is not None and not 1 <= parts.port <= 65_535:
            raise ValueError
    except ValueError:
        raise GrokConfigError("HTTPS egress proxy URL has an invalid port") from None


def _bounded_positive_int(environ, name: str, default: int, maximum: int) -> int:
    raw = environ.get(name)
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit():
        raise GrokConfigError(f"{name} must be an integer")
    value = int(raw)
    if not 1 <= value <= maximum:
        raise GrokConfigError(f"{name} must be 1..{maximum}")
    return value


def _post_once(url: str, body: bytes, bearer: str, proxy_url: str, timeout_seconds: float) -> int:
    """Make one HTTPS POST through an explicit proxy, never follow redirects."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy_url}),
        NoRedirect(),
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        # Redirects and all other non-200 responses are ambiguous. Do not read
        # or log response bodies and do not issue a second request.
        return int(exc.code)


@dataclass(frozen=True, slots=True)
class GrokRoutineConfig:
    routine_url: str
    routine_token: str
    callback_url: str
    egress_proxy_url: str
    callback_token_ttl_seconds: int = DEFAULT_CALLBACK_TOKEN_TTL_SECONDS
    callback_wait_seconds: int = DEFAULT_CALLBACK_WAIT_SECONDS
    http_timeout_seconds: float = 15.0


class GrokCallbackExecutor:
    """At-most-once Routine submission; durable Callback receipt is separate."""

    def __init__(
        self,
        config: GrokRoutineConfig,
        *,
        begin_submission: Callable[..., None],
        finish_submission: Callable[..., None],
        fail_before_send: Callable[..., None],
        transport: Callable[[str, bytes, str, str, float], int] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _https_url(config.routine_url, ROUTINE_URL_ENV)
        _https_url(config.callback_url, CALLBACK_URL_ENV, path="/callback/results")
        _proxy_url(config.egress_proxy_url)
        if (not isinstance(config.routine_token, str) or len(config.routine_token.strip()) < 12
            or any(marker in config.routine_token.lower() for marker in ("placeholder", "your-token", "<", ">"))):
            raise GrokConfigError(f"{ROUTINE_TOKEN_ENV} must contain at least 12 characters")
        if (
            isinstance(config.callback_token_ttl_seconds, bool)
            or not isinstance(config.callback_token_ttl_seconds, int)
            or not 1 <= config.callback_token_ttl_seconds <= MAX_CALLBACK_TOKEN_TTL_SECONDS
            or isinstance(config.callback_wait_seconds, bool)
            or not isinstance(config.callback_wait_seconds, int)
            or not 1 <= config.callback_wait_seconds <= MAX_CALLBACK_WAIT_SECONDS
            or not isinstance(config.http_timeout_seconds, (int, float))
            or config.http_timeout_seconds <= 0
        ):
            raise GrokConfigError("Grok Callback TTL, wait deadline, and HTTP timeout must be bounded positive values")
        if config.callback_wait_seconds > config.callback_token_ttl_seconds:
            raise GrokConfigError("callback wait deadline must not outlive the task capability")
        if not callable(begin_submission) or not callable(finish_submission) or not callable(fail_before_send):
            raise GrokConfigError("Grok submission requires durable TaskStore callbacks")
        self._config = config
        self._begin_submission = begin_submission
        self._finish_submission = finish_submission
        self._fail_before_send = fail_before_send
        self._transport = transport or _post_once
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def execute(self, task: ClaimedTask) -> ExecutionOutcome:
        if task.executor_kind != "grok_callback" or task.backend != GROK_BACKEND_ID:
            self._fail_before_send(task.task_id, code="grok_backend_identity_mismatch")
            return ExecutionOutcome(status="failed", code="grok_backend_identity_mismatch")
        if task.result_contract_version != 2:
            self._fail_before_send(task.task_id, code="unsupported_result_contract")
            return ExecutionOutcome(status="failed", code="unsupported_result_contract")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            self._fail_before_send(task.task_id, code="grok_clock_invalid")
            return ExecutionOutcome(status="failed", code="grok_clock_invalid")
        now = now.astimezone(timezone.utc)
        token = secrets.token_urlsafe(32)
        token_expiry = now + timedelta(seconds=self._config.callback_token_ttl_seconds)
        callback_deadline = now + timedelta(seconds=self._config.callback_wait_seconds)
        instruction = (
            f"Owner request:\n{task.task}\n\n"
            f"Return one final result by POSTing JSON to {self._config.callback_url}. "
            "Use the task capability supplied in this Routine context as the HTTP Authorization: Bearer value; "
            "do not put that capability in the JSON body. The body must match the Amusant bounded result schema "
            f"{GROK_RESULT_SCHEMA}: task_id, reported_outcome (completed or not_completed), speak, and card "
            "with title and one to four lines. Do not claim a native run was independently verified."
        )
        if len(instruction) > MAX_ROUTINE_INSTRUCTION_CHARS:
            self._fail_before_send(task.task_id, code="grok_task_exceeds_prompt_limit")
            return ExecutionOutcome(status="failed", code="grok_task_exceeds_prompt_limit")
        task_label = f"Amusant task {task.task_id[:8]}"
        payload = {
            "schema_version": GROK_RESULT_SCHEMA,
            "task_id": task.task_id,
            "result_token": token,
            "task_label": task_label,
            "instruction": instruction,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            self._begin_submission(
                task.task_id,
                token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                token_expires_at=token_expiry,
                callback_deadline_at=callback_deadline,
                now=now,
            )
        except Exception:
            # Cancellation may have won the TaskStore transaction immediately
            # before this call. In that case the transport is never invoked.
            return ExecutionOutcome(status="failed", code="grok_submission_not_sendable")
        started = time.monotonic()
        status = None
        category = "http"
        try:
            status = await asyncio.to_thread(
                self._transport,
                self._config.routine_url,
                body,
                self._config.routine_token,
                self._config.egress_proxy_url,
                self._config.http_timeout_seconds,
            )
            submission_state = "accepted" if status == 200 else "acceptance_unknown"
        except Exception as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if isinstance(reason, (TimeoutError, socket.timeout)):
                category = "timeout"
            elif isinstance(reason, socket.gaierror):
                category = "dns"
            elif isinstance(reason, ssl.SSLError):
                category = "tls"
            elif isinstance(reason, (ConnectionError, OSError)):
                category = "connection_or_proxy"
            else:
                category = "transport_other"
            submission_state = "acceptance_unknown"
        logger.info("grok_webhook outcome=%s category=%s http_status=%s elapsed_ms=%d",
                    submission_state, category, status, int((time.monotonic() - started) * 1000))
        self._finish_submission(task.task_id, state=submission_state, now=self._clock())
        return ExecutionOutcome(status="deferred", code=f"grok_{submission_state}")


def load_grok_callback_executor(
    environ,
    *,
    begin_submission: Callable[..., None],
    finish_submission: Callable[..., None],
    fail_before_send: Callable[..., None],
) -> GrokCallbackExecutor:
    """Load explicit Grok settings without opening a socket or invoking a provider."""
    routine_url = environ.get(ROUTINE_URL_ENV, "").strip()
    routine_token = environ.get(ROUTINE_TOKEN_ENV, "").strip()
    callback_url = environ.get(CALLBACK_URL_ENV, "").strip()
    egress_proxy = (environ.get("HTTPS_PROXY") or environ.get("https_proxy") or "").strip()
    if not all((routine_url, routine_token, callback_url, egress_proxy)):
        raise GrokConfigError("Grok Routine, Callback, and HTTPS egress proxy settings are required")
    ttl = _bounded_positive_int(environ, CALLBACK_TOKEN_TTL_ENV, DEFAULT_CALLBACK_TOKEN_TTL_SECONDS, MAX_CALLBACK_TOKEN_TTL_SECONDS)
    wait = _bounded_positive_int(environ, CALLBACK_WAIT_ENV, DEFAULT_CALLBACK_WAIT_SECONDS, MAX_CALLBACK_WAIT_SECONDS)
    return GrokCallbackExecutor(
        GrokRoutineConfig(
            routine_url=routine_url,
            routine_token=routine_token,
            callback_url=callback_url,
            egress_proxy_url=egress_proxy,
            callback_token_ttl_seconds=ttl,
            callback_wait_seconds=wait,
        ),
        begin_submission=begin_submission,
        finish_submission=finish_submission,
        fail_before_send=fail_before_send,
    )
