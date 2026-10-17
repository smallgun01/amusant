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
from typing import Any, Callable
from urllib.parse import quote, urlparse

from .dispatcher import ExecutionOutcome
from .hermes_result import build_hermes_run_prompt, parse_hermes_run_result
from .task_store import ClaimedTask

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_SECONDS = 0.5
MAX_RESPONSE_BYTES = 1024 * 1024
_RFC1918 = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16"))
_TERMINAL_SUCCESS = {"completed"}
_TERMINAL_FAILURE = {"failed"}
_TERMINAL_UNCERTAIN = {"cancelled", "interrupted", "expired"}
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


class HermesRunsExecutor:
    """Submit once with a durable idempotency key, then poll the persisted Run ID."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        save_backend_run_id: Callable[[str, str], None],
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_SECONDS,
        allow_plaintext: bool = False,
        request_json=None,
    ) -> None:
        self._endpoint = self._parse_endpoint(url, allow_plaintext=allow_plaintext)
        if not isinstance(api_key, str) or len(api_key.strip()) < 12:
            raise HermesConfigError("AMUSANT_HERMES_API_KEY must contain at least 12 characters")
        if not callable(save_backend_run_id):
            raise HermesConfigError("task store must support durable Hermes Run ID mapping")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(float(timeout_seconds)) or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS):
            raise HermesConfigError(f"Hermes timeout must be finite and between 0 and {MAX_TIMEOUT_SECONDS:g} seconds")
        if (isinstance(poll_interval_seconds, bool) or not isinstance(poll_interval_seconds, (int, float))
                or not math.isfinite(float(poll_interval_seconds)) or not 0 < poll_interval_seconds <= 10):
            raise HermesConfigError("Hermes poll interval must be finite, positive, and at most 10 seconds")
        self._api_key = api_key.strip()
        self._save_backend_run_id = save_backend_run_id
        self._timeout = float(timeout_seconds)
        self._poll_interval = float(poll_interval_seconds)
        self._request_json = request_json or self._http_request

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
            raw = socket.create_connection((selected, endpoint.port), timeout=10.0)
            if endpoint.scheme == "https":
                context = ssl.create_default_context()
                raw = context.wrap_socket(raw, server_hostname=endpoint.host)
            conn = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=10.0)
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
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-Hermes-Session-Key": self._session_key(task.client_id),
            "Accept": "application/json",
        }
        run_id = task.backend_run_id
        if run_id is None:
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
                # The request may have been accepted despite a lost response. Never replay blindly.
                raise HermesRequestError("Hermes submission outcome is unknown") from None
            if 400 <= status < 500:
                return ExecutionOutcome(status="failed", code="hermes_submit_rejected")
            if status >= 500:
                raise HermesRequestError("Hermes submission outcome is unknown")
            candidate = payload.get("run_id")
            if not isinstance(candidate, str) or not candidate or len(candidate) > 256:
                raise HermesRequestError("Hermes submission response did not confirm a Run ID")
            run_id = candidate
            try:
                await asyncio.to_thread(self._save_backend_run_id, task.task_id, run_id)
            except Exception:
                # The Run exists but the local mapping is not durably confirmed.
                raise HermesRequestError("Hermes Run ID persistence is unconfirmed") from None

        deadline = asyncio.get_running_loop().time() + self._timeout
        quoted_id = quote(run_id, safe="")
        while True:
            try:
                status_code, payload = await asyncio.to_thread(
                    self._request_json, "GET", f"/v1/runs/{quoted_id}", None, headers
                )
            except Exception:
                raise HermesRequestError("Hermes Run status is unconfirmed") from None
            if status_code >= 400:
                # A rejected status read says nothing about whether the remote Run completed.
                return ExecutionOutcome(status="uncertain", code="hermes_result_unconfirmed")
            state = payload.get("status")
            if not isinstance(state, str):
                raise HermesRequestError("Hermes Run status is unconfirmed")
            state = state.lower()
            if state in _TERMINAL_SUCCESS:
                if payload.get("partial") is True or payload.get("interrupted") is True:
                    return ExecutionOutcome(status="uncertain", code="hermes_result_unconfirmed")
                output = _extract_output(payload)
                if output is None:
                    return ExecutionOutcome(status="uncertain", code="hermes_result_unconfirmed")
                try:
                    result = parse_hermes_run_result(output)
                except ValueError:
                    return ExecutionOutcome(status="uncertain", code="hermes_result_unconfirmed")
                return ExecutionOutcome(status="completed", result=result)
            if state in _TERMINAL_FAILURE:
                return ExecutionOutcome(status="failed", code=f"hermes_{state}")
            if state in _TERMINAL_UNCERTAIN:
                return ExecutionOutcome(status="uncertain", code=f"hermes_{state}")
            if state not in _NONTERMINAL:
                raise HermesRequestError("Hermes Run returned an unknown status")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise HermesRequestError("Hermes Run did not reach a terminal state before timeout")
            await asyncio.sleep(min(self._poll_interval, remaining))


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


def load_hermes_runs_executor(environ, *, save_backend_run_id) -> HermesRunsExecutor | None:
    url = environ.get("AMUSANT_HERMES_RUNS_URL")
    key = environ.get("AMUSANT_HERMES_API_KEY")
    present = [value for value in (url, key) if isinstance(value, str) and value.strip()]
    if not present:
        return None
    if len(present) != 2:
        raise HermesConfigError("Hermes executor requires AMUSANT_HERMES_RUNS_URL and AMUSANT_HERMES_API_KEY")
    try:
        timeout = float(environ.get("AMUSANT_HERMES_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
        poll_interval = float(environ.get("AMUSANT_HERMES_POLL_INTERVAL_SECONDS", str(DEFAULT_POLL_SECONDS)))
    except (TypeError, ValueError):
        raise HermesConfigError("Hermes timeout and poll interval must be numeric") from None
    allow_plaintext = environ.get("AMUSANT_HERMES_ALLOW_PLAINTEXT", "") == "1"
    return HermesRunsExecutor(
        url=url.strip(),
        api_key=key.strip(),
        save_backend_run_id=save_backend_run_id,
        timeout_seconds=timeout,
        poll_interval_seconds=poll_interval,
        allow_plaintext=allow_plaintext,
    )
