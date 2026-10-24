"""Thin client for Hermes native HTTP Runs API; no agent logic lives in Relay."""
from __future__ import annotations

import asyncio
import http.client
import hmac
import ipaddress
import json
import math
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote, urlparse

from .dispatcher import ExecutionOutcome
from .hermes_result import build_hermes_run_prompt, parse_hermes_run_result
from .task_store import ClaimedTask

MAX_RESPONSE_BYTES = 1024 * 1024
_RFC1918 = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16"))
_TERMINAL_FAILURE = {"failed"}
_NONTERMINAL = {"queued", "pending", "running", "waiting_for_approval", "stopping"}


class HermesConfigError(ValueError):
    """Invalid server-owned Hermes endpoint configuration."""


class HermesRequestError(RuntimeError):
    """Transport or response failure; never includes credentials or response text."""

    code = "hermes_result_unconfirmed"


def _allowed_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_loopback:
        return True
    if address.is_unspecified or address.is_link_local or address.is_multicast or address.is_reserved:
        return False
    return address.version == 4 and any(address in network for network in _RFC1918)


@dataclass(frozen=True, slots=True)
class _Endpoint:
    scheme: str
    host: str
    port: int


def _parse_utc_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp missing")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value.astimezone(timezone.utc).isoformat()


class HermesRunsExecutor:
    """Submit once with a durable idempotency key, then poll the persisted Run ID."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        save_backend_run_id: Callable[..., None],
        update_poll_state: Callable[..., None],
        update_stop_state: Callable[..., None],
        run_deadline_seconds: float,
        poll_cycle_seconds: float,
        http_timeout_seconds: float,
        poll_interval_seconds: float,
        stop_grace_seconds: float,
        stop_max_attempts: int,
        stop_retry_base_seconds: float,
        stop_retry_max_seconds: float,
        allow_plaintext: bool = False,
        request_json=None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._endpoint = self._parse_endpoint(url, allow_plaintext=allow_plaintext)
        if not isinstance(api_key, str) or len(api_key.strip()) < 12:
            raise HermesConfigError("AMUSANT_FACADE_API_KEY must contain at least 12 characters")
        if not all(callable(callback) for callback in (save_backend_run_id, update_poll_state, update_stop_state)):
            raise HermesConfigError("task store must support durable Run, poll-state, and stop-state mapping")
        if clock is not None and not callable(clock):
            raise HermesConfigError("Hermes clock must be callable")
        values = {
            "Run deadline": run_deadline_seconds,
            "poll cycle": poll_cycle_seconds,
            "HTTP timeout": http_timeout_seconds,
            "poll interval": poll_interval_seconds,
            "stop grace": stop_grace_seconds,
            "stop retry base": stop_retry_base_seconds,
            "stop retry maximum": stop_retry_max_seconds,
        }
        for name, value in values.items():
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value)) or float(value) <= 0):
                raise HermesConfigError(f"Hermes {name} must be finite and positive")
        if type(stop_max_attempts) is not int or stop_max_attempts <= 0:
            raise HermesConfigError("Hermes stop max attempts must be a positive integer")
        if float(stop_retry_max_seconds) < float(stop_retry_base_seconds):
            raise HermesConfigError("Hermes stop retry maximum must be at least its base interval")
        try:
            timedelta(seconds=float(run_deadline_seconds))
            timedelta(seconds=float(stop_grace_seconds))
        except OverflowError:
            raise HermesConfigError("Hermes deadline or stop grace is outside the supported datetime range") from None
        self._api_key = api_key.strip()
        self._save_backend_run_id = save_backend_run_id
        self._update_poll_state = update_poll_state
        self._update_stop_state = update_stop_state
        self._run_deadline_seconds = float(run_deadline_seconds)
        self._poll_cycle_seconds = float(poll_cycle_seconds)
        self._http_timeout_seconds = float(http_timeout_seconds)
        self._poll_interval = float(poll_interval_seconds)
        self._stop_grace_seconds = float(stop_grace_seconds)
        self._stop_max_attempts = stop_max_attempts
        self._stop_retry_base_seconds = float(stop_retry_base_seconds)
        self._stop_retry_max_seconds = float(stop_retry_max_seconds)
        self._request_json = request_json or self._http_request
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _parse_endpoint(url: str, *, allow_plaintext: bool) -> _Endpoint:
        if not isinstance(url, str) or not url.strip():
            raise HermesConfigError("AMUSANT_HERMES_RUNS_URL is required")
        parsed = urlparse(url.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise HermesConfigError("Hermes URL must be an http(s) origin without credentials, path, query, or fragment")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise HermesConfigError("Hermes URL must use a valid port") from exc
        if not 1 <= port <= 65535:
            raise HermesConfigError("Hermes URL must use a valid port")
        if parsed.scheme == "http" and not allow_plaintext:
            try:
                literal = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                literal = None
            if literal is None or not literal.is_loopback:
                raise HermesConfigError("plaintext Hermes HTTP requires AMUSANT_HERMES_ALLOW_PLAINTEXT=1 (loopback is always allowed)")
        return _Endpoint(parsed.scheme, parsed.hostname, port)

    def _session_key(self, client_id: str) -> str:
        # Stable opaque owner namespace; raw device/client identity never leaves Relay.
        return hmac.new(self._api_key.encode(), client_id.encode(), "sha256").hexdigest()

    def _http_request(self, method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str]):
        endpoint = self._endpoint
        conn = None
        try:
            infos = socket.getaddrinfo(endpoint.host, endpoint.port, type=socket.SOCK_STREAM)
            addresses: list[str] = []
            for info in infos:
                address = ipaddress.ip_address(info[4][0])
                if not _allowed_ip(address):
                    raise HermesRequestError("Hermes endpoint resolved outside loopback/private network")
                if address.compressed not in addresses:
                    addresses.append(address.compressed)
            if not addresses:
                raise HermesRequestError("Hermes endpoint has no permitted address")
            selected = next((ip for ip in addresses if "." in ip), addresses[0])
            raw = socket.create_connection((selected, endpoint.port), timeout=self._http_timeout_seconds)
            if endpoint.scheme == "https":
                context = ssl.create_default_context()
                raw = context.wrap_socket(raw, server_hostname=endpoint.host)
            conn = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=self._http_timeout_seconds)
            # Pin the socket to the address that passed the private-network check.
            conn.sock = raw
            encoded = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            conn.request(method, path, body=encoded, headers=headers)
            response = conn.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise HermesRequestError("Hermes response exceeded the configured bound")
            try:
                payload = json.loads(data or b"{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise HermesRequestError("Hermes response was not valid JSON") from None
            if not isinstance(payload, dict):
                raise HermesRequestError("Hermes response had an invalid shape")
            return response.status, payload
        except HermesRequestError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
            raise HermesRequestError("Hermes HTTP request failed") from None
        finally:
            if conn is not None:
                conn.close()

    async def execute(self, task: ClaimedTask) -> ExecutionOutcome:
        if task.executor_kind != "hermes_runs":
            return ExecutionOutcome(status="failed", code="executor_kind_mismatch")
        if not isinstance(task.client_id, str) or not task.client_id:
            return ExecutionOutcome(status="failed", code="invalid_task_owner")
        if type(getattr(task, "result_contract_version", None)) is not int or task.result_contract_version != 2:
            return ExecutionOutcome(status="uncertain", code="unsupported_result_contract")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-Hermes-Session-Key": self._session_key(task.client_id),
            "X-Amusant-Result-Contract": "2",
            "Accept": "application/json",
        }
        now = self._clock()
        run_id = task.backend_run_id
        if run_id is not None and not task.run_started_at and not task.dispatch_started_at:
            return ExecutionOutcome(status="uncertain", code="hermes_run_deadline_anchor_missing")
        try:
            run_started_at = _parse_utc_timestamp(task.run_started_at) if task.run_started_at else (
                _parse_utc_timestamp(task.dispatch_started_at) if task.dispatch_started_at else now
            )
            run_deadline_at = _parse_utc_timestamp(task.run_deadline_at) if task.run_deadline_at else (
                run_started_at + timedelta(seconds=self._run_deadline_seconds)
            )
            next_poll_at = _parse_utc_timestamp(task.next_poll_at) if task.next_poll_at else now
            stop_requested_at = _parse_utc_timestamp(task.stop_requested_at) if task.stop_requested_at else None
            stop_accepted_at = _parse_utc_timestamp(task.stop_accepted_at) if task.stop_accepted_at else None
        except (OverflowError, TypeError, ValueError):
            return ExecutionOutcome(status="uncertain", code="hermes_run_state_unconfirmed")
        poll_attempts = int(task.poll_attempts or 0)
        stop_attempts = int(task.stop_attempts or 0)
        stop_reason = task.stop_reason

        if run_id is None:
            if stop_requested_at is not None or task.status == "uncertain":
                return ExecutionOutcome(status="uncertain", code="hermes_submission_unconfirmed")
            try:
                prompt = build_hermes_run_prompt(task)
            except ValueError as exc:
                return ExecutionOutcome(status="failed", code=str(exc))
            try:
                status, payload = await asyncio.to_thread(
                    self._request_json, "POST", "/v1/runs", {"input": prompt},
                    {**headers, "Content-Type": "application/json", "Idempotency-Key": task.task_id},
                )
            except Exception:
                # A lost POST response is ambiguous. Without a durable Run ID, never replay it.
                raise HermesRequestError("Hermes submission outcome is unknown") from None
            if 400 <= status < 500:
                return ExecutionOutcome(status="failed", code="hermes_submit_rejected")
            if status >= 500:
                raise HermesRequestError("Hermes submission outcome is unknown")
            candidate = payload.get("run_id")
            if not isinstance(candidate, str) or not candidate or len(candidate) > 256:
                raise HermesRequestError("Hermes submission response did not confirm a Run ID")
            run_id = candidate
            next_poll_at = self._clock()
            try:
                await asyncio.to_thread(
                    self._save_backend_run_id,
                    task.task_id,
                    run_id,
                    run_started_at=run_started_at,
                    run_deadline_at=run_deadline_at,
                    next_poll_at=next_poll_at,
                    poll_attempts=poll_attempts,
                )
            except Exception:
                raise HermesRequestError("Hermes Run ID/deadline persistence is unconfirmed") from None
        elif not task.run_deadline_at or not task.run_started_at:
            # Backfill known legacy Runs only; this path never submits.
            try:
                await asyncio.to_thread(
                    self._save_backend_run_id,
                    task.task_id,
                    run_id,
                    run_started_at=run_started_at,
                    run_deadline_at=run_deadline_at,
                    next_poll_at=next_poll_at,
                    poll_attempts=poll_attempts,
                )
            except Exception:
                return ExecutionOutcome(status="uncertain", code="hermes_run_deadline_unconfirmed")

        quoted_id = quote(run_id, safe="")

        def outcome(status: str, code: str) -> ExecutionOutcome:
            return ExecutionOutcome(
                status=status,
                code=code,
                backend_run_id=run_id,
                run_started_at=_utc_iso(run_started_at),
                run_deadline_at=_utc_iso(run_deadline_at),
                next_poll_at=_utc_iso(next_poll_at),
                poll_attempts=poll_attempts,
            )

        async def persist_poll_state() -> None:
            try:
                await asyncio.to_thread(
                    self._update_poll_state,
                    task.task_id,
                    next_poll_at=next_poll_at,
                    poll_attempts=poll_attempts,
                )
            except Exception:
                raise HermesRequestError("Hermes poll state persistence is unconfirmed") from None

        async def persist_stop_state(*, accepted_at: datetime | None, next_at: datetime) -> None:
            try:
                await asyncio.to_thread(
                    self._update_stop_state,
                    task.task_id,
                    stop_attempts=stop_attempts,
                    stop_accepted_at=accepted_at,
                    next_poll_at=next_at,
                )
            except Exception:
                raise HermesRequestError("Hermes stop state persistence is unconfirmed") from None

        def retry_delay(attempts: int) -> float:
            exponent = min(max(0, attempts - 1), 16)
            return min(self._stop_retry_base_seconds * (2 ** exponent), self._stop_retry_max_seconds)

        async def get_once():
            """Count an initiated GET durably and schedule from its completion time."""
            nonlocal poll_attempts, next_poll_at
            poll_attempts += 1
            # If Relay restarts while this GET is in flight, the next claim waits
            # through the configured request bound plus the poll interval.
            next_poll_at = self._clock() + timedelta(
                seconds=self._http_timeout_seconds + self._poll_interval
            )
            await persist_poll_state()
            try:
                response = await asyncio.to_thread(
                    self._request_json, "GET", f"/v1/runs/{quoted_id}", None, headers
                )
            except Exception:
                response = None
            next_poll_at = self._clock() + timedelta(seconds=self._poll_interval)
            await persist_poll_state()
            if response is None:
                return None, "hermes_get_failed"
            return response, None

        def classify_terminal(state: str, payload: dict[str, Any]) -> ExecutionOutcome | None:
            if state == "completed":
                if payload.get("partial") is True or payload.get("interrupted") is True:
                    return ExecutionOutcome(status="failed", code="hermes_result_incomplete")
                output = _extract_output(payload)
                if output is None:
                    return ExecutionOutcome(status="failed", code="hermes_result_missing")
                try:
                    result = parse_hermes_run_result(output)
                except ValueError:
                    return ExecutionOutcome(status="failed", code="hermes_result_invalid")
                if result.artifact_status == "failed" or result.presentation_status == "failed":
                    return ExecutionOutcome(status="failed", code="hermes_delivery_failed")
                return ExecutionOutcome(status="completed", result=result)
            if state in _TERMINAL_FAILURE:
                return ExecutionOutcome(status="failed", code=f"hermes_{state}")
            if state in {"cancelled", "interrupted"}:
                code = "hermes_run_timed_out" if stop_reason == "run_deadline" else "hermes_cancelled"
                return ExecutionOutcome(status="failed", code=code)
            # Hermes `expired` does not prove that the Run stopped executing.
            return None

        def validate_status_response(response):
            if response is None:
                return None, "hermes_get_failed"
            http_status, payload = response
            # Only a successful, exact Run binding can establish remote state.
            if http_status != 200:
                return None, "hermes_run_status_unconfirmed"
            if not isinstance(payload, dict) or payload.get("run_id") != run_id:
                return None, "hermes_run_binding_unconfirmed"
            state = payload.get("status")
            if not isinstance(state, str):
                return None, "hermes_run_status_unconfirmed"
            return (state.lower(), payload), None

        async def process_get(*, recovery: bool, stop_grace_deadline: datetime | None = None):
            response, request_error = await get_once()
            classified, validation_error = validate_status_response(response)
            error = request_error or validation_error
            if error is None:
                state, payload = classified
                terminal_result = classify_terminal(state, payload)
                if terminal_result is not None:
                    return terminal_result
                if state == "expired":
                    return outcome("uncertain", "hermes_expired")
                if state in _NONTERMINAL:
                    if recovery:
                        return outcome("uncertain", "hermes_recovery_nonterminal")
                    if stop_grace_deadline is not None:
                        if self._clock() >= stop_grace_deadline:
                            return outcome("uncertain", "hermes_stop_unconfirmed")
                        return outcome("deferred", "hermes_stop_pending")
                    if self._clock() >= run_deadline_at:
                        return ExecutionOutcome(status="uncertain", code="hermes_run_deadline_exceeded")
                    return outcome("deferred", "hermes_poll_cycle_complete")
                return outcome("uncertain", "hermes_remote_state_unconfirmed")
            if recovery:
                return outcome("uncertain", error)
            if stop_grace_deadline is not None:
                if self._clock() >= stop_grace_deadline:
                    return outcome("uncertain", "hermes_stop_unconfirmed")
                return outcome("deferred", "hermes_stop_pending")
            if self._clock() >= run_deadline_at:
                return ExecutionOutcome(status="uncertain", code="hermes_run_deadline_exceeded")
            return outcome("deferred", error)

        # Uncertain known Runs are recoverable only by GET. This path can never
        # submit again or issue a second stop request.
        if task.status == "uncertain":
            return await process_get(recovery=True)

        if stop_requested_at is not None:
            grace_deadline = stop_requested_at + timedelta(seconds=self._stop_grace_seconds)
            now = self._clock()
            if stop_accepted_at is None and stop_attempts < self._stop_max_attempts and now < grace_deadline:
                stop_attempts += 1
                stop_http = None
                stop_payload: dict[str, Any] = {}
                try:
                    stop_http, stop_payload = await asyncio.to_thread(
                        self._request_json, "POST", f"/v1/runs/{quoted_id}/stop", None, headers
                    )
                except Exception:
                    stop_http = 599
                if 200 <= stop_http < 300:
                    # Stop Accepted records an accepted request only. GET below
                    # remains the sole source of terminal confirmation.
                    stop_accepted_at = self._clock()
                    await persist_stop_state(accepted_at=stop_accepted_at, next_at=self._clock())
                elif stop_http in {408, 429, 599} or stop_http >= 500:
                    now = self._clock()
                    retry_at = now + timedelta(seconds=retry_delay(stop_attempts))
                    await persist_stop_state(accepted_at=None, next_at=retry_at)
                    if stop_attempts >= self._stop_max_attempts or now >= grace_deadline:
                        next_poll_at = retry_at
                        return outcome("uncertain", "hermes_stop_unconfirmed")
                    next_poll_at = retry_at
                    return outcome("deferred", "hermes_stop_retry_scheduled")
                elif stop_http >= 400 and stop_http not in {404, 409, 410}:
                    await persist_stop_state(accepted_at=None, next_at=self._clock())
                    # Still perform GET: a trusted terminal status can close the
                    # Run even when the stop endpoint rejected the request.
            result = await process_get(recovery=False, stop_grace_deadline=grace_deadline)
            if result.status == "deferred" and result.next_poll_at:
                # Persist the STOP retry/GET cadence together with the claimed row.
                await persist_stop_state(accepted_at=stop_accepted_at, next_at=_parse_utc_timestamp(result.next_poll_at))
            return result

        now = self._clock()
        if run_deadline_at <= now:
            return ExecutionOutcome(status="uncertain", code="hermes_run_deadline_exceeded")
        if next_poll_at > now:
            await asyncio.sleep((next_poll_at - now).total_seconds())
            now = self._clock()
            if next_poll_at > now:
                return outcome("deferred", "hermes_poll_not_due")

        loop = asyncio.get_running_loop()
        cycle_deadline = loop.time() + self._poll_cycle_seconds
        while True:
            result = await process_get(recovery=False)
            if result.status != "deferred" or result.code != "hermes_poll_cycle_complete":
                return result
            remaining = min(
                cycle_deadline - loop.time(),
                (run_deadline_at - self._clock()).total_seconds(),
            )
            if remaining <= self._poll_interval:
                return result
            await asyncio.sleep(self._poll_interval)


def _extract_output(payload: dict[str, Any]) -> str | None:
    """Read textual result fields without serializing arbitrary status metadata."""
    for key in ("output", "result", "content", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            for nested in ("output", "text", "content"):
                candidate = value.get(nested)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
        if isinstance(value, list):
            texts = [part.get("text") for part in value if isinstance(part, dict) and isinstance(part.get("text"), str)]
            if texts:
                return "\n".join(texts)
    return None


def load_hermes_runs_executor(
    environ,
    *,
    save_backend_run_id,
    update_poll_state,
    update_stop_state,
) -> HermesRunsExecutor | None:
    url = environ.get("AMUSANT_HERMES_RUNS_URL")
    key = environ.get("AMUSANT_FACADE_API_KEY")
    present = [value for value in (url, key) if isinstance(value, str) and value.strip()]
    if not present:
        return None
    if len(present) != 2:
        raise HermesConfigError("Hermes executor requires AMUSANT_HERMES_RUNS_URL and AMUSANT_FACADE_API_KEY")
    if "AMUSANT_HERMES_TIMEOUT_SECONDS" in environ:
        raise HermesConfigError(
            "AMUSANT_HERMES_TIMEOUT_SECONDS is ambiguous; use separate HTTP, poll-cycle, and Run-deadline settings"
        )
    timing_names = (
        "AMUSANT_HERMES_RUN_DEADLINE_SECONDS",
        "AMUSANT_HERMES_POLL_CYCLE_SECONDS",
        "AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS",
        "AMUSANT_HERMES_POLL_INTERVAL_SECONDS",
        "AMUSANT_HERMES_STOP_GRACE_SECONDS",
        "AMUSANT_HERMES_STOP_MAX_ATTEMPTS",
        "AMUSANT_HERMES_STOP_RETRY_BASE_SECONDS",
        "AMUSANT_HERMES_STOP_RETRY_MAX_SECONDS",
    )
    timing_raw = {name: environ.get(name) for name in timing_names}
    for name, value in timing_raw.items():
        if not isinstance(value, str) or not value.strip():
            raise HermesConfigError(f"{name} is required; no product default is approved")
    try:
        run_deadline = float(timing_raw["AMUSANT_HERMES_RUN_DEADLINE_SECONDS"])
        poll_cycle = float(timing_raw["AMUSANT_HERMES_POLL_CYCLE_SECONDS"])
        http_timeout = float(timing_raw["AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS"])
        poll_interval = float(timing_raw["AMUSANT_HERMES_POLL_INTERVAL_SECONDS"])
        stop_grace = float(timing_raw["AMUSANT_HERMES_STOP_GRACE_SECONDS"])
        stop_max_attempts_raw = timing_raw["AMUSANT_HERMES_STOP_MAX_ATTEMPTS"]
        if not stop_max_attempts_raw.isdigit():
            raise ValueError("stop max attempts must be an integer")
        stop_max_attempts = int(stop_max_attempts_raw)
        stop_retry_base = float(timing_raw["AMUSANT_HERMES_STOP_RETRY_BASE_SECONDS"])
        stop_retry_max = float(timing_raw["AMUSANT_HERMES_STOP_RETRY_MAX_SECONDS"])
    except (TypeError, ValueError):
        raise HermesConfigError("Hermes timing settings must be numeric") from None
    allow_plaintext = environ.get("AMUSANT_HERMES_ALLOW_PLAINTEXT", "") == "1"
    return HermesRunsExecutor(
        url=url.strip(),
        api_key=key.strip(),
        save_backend_run_id=save_backend_run_id,
        update_poll_state=update_poll_state,
        update_stop_state=update_stop_state,
        run_deadline_seconds=run_deadline,
        poll_cycle_seconds=poll_cycle,
        http_timeout_seconds=http_timeout,
        poll_interval_seconds=poll_interval,
        stop_grace_seconds=stop_grace,
        stop_max_attempts=stop_max_attempts,
        stop_retry_base_seconds=stop_retry_base,
        stop_retry_max_seconds=stop_retry_max,
        allow_plaintext=allow_plaintext,
    )
