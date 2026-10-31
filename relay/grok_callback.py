"""Restricted ASGI Callback Receiver factory; it never starts a network listener."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import threading
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from .grok_result import GrokResultError, validate_grok_callback
from .task_store import (
    AdmissionUnavailable,
    GrokCallbackConflict,
    GrokCallbackUnauthorized,
)

CALLBACK_PATH = "/callback/results"
CALLBACK_BODY_LIMIT = 16_384
CALLBACK_BODY_TIMEOUT_SECONDS = 5.0
MAX_CALLBACK_INFLIGHT = 32
_AUTHORIZATION = re.compile(r"(?i:Bearer) ([A-Za-z0-9._~-]{1,128})\Z")
_CONTENT_LENGTH = re.compile(r"[0-9]+\Z")


def _normalize_host(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(
        char in value for char in "/:@*?#"
    ):
        raise ValueError("trusted callback hosts must be exact bare hostnames or IPv4 addresses")
    try:
        return ipaddress.IPv4Address(value).compressed
    except ipaddress.AddressValueError:
        pass
    try:
        ascii_host = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("trusted callback host is invalid") from exc
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or not labels or ascii_host.endswith(".") or any(
        not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("trusted callback host is invalid")
    return ascii_host


def _parse_host(value: str) -> tuple[str, int | None]:
    if not isinstance(value, str) or not value or value != value.strip() or value.count(":") > 1:
        raise ValueError("invalid Host")
    host, separator, raw_port = value.partition(":")
    port: int | None = None
    if separator:
        if not raw_port.isascii() or not raw_port.isdigit():
            raise ValueError("invalid Host port")
        port = int(raw_port)
        if not 1 <= port <= 65_535:
            raise ValueError("invalid Host port")
    return _normalize_host(host), port


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _contains_capability(value: Any, secret: str) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if secret in current:
                return True
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
    return False


def _headers(scope) -> dict[bytes, list[bytes]]:
    result: dict[bytes, list[bytes]] = {}
    for key, value in scope.get("headers", []):
        result.setdefault(key.lower(), []).append(value)
    return result


def _response(status: int, payload: dict[str, object]) -> tuple[int, bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return status, body, [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode("ascii"))]


class _InflightLimit:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.active = 0
        self.lock = threading.Lock()

    def enter(self) -> bool:
        with self.lock:
            if self.active >= self.maximum:
                return False
            self.active += 1
            return True

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


def create_grok_callback_app(
    store,
    client_id: str,
    *,
    trusted_hosts: Sequence[str],
    max_inflight: int,
    expected_port: int | None = None,
    store_timeout_seconds: float = 5.0,
    on_committed=None,
) -> Callable[..., Awaitable[None]]:
    """Create a one-route app. TLS termination/reverse proxy remain a separate gate.

    ``client_id`` and ``trusted_hosts`` are server-owned canonical mappings.
    This function has no bind/listen behavior and trusts no Forwarded headers.
    """
    if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", client_id):
        raise ValueError("client_id must be a bounded server-owned Owner mapping")
    if isinstance(max_inflight, bool) or not isinstance(max_inflight, int) or not 1 <= max_inflight <= MAX_CALLBACK_INFLIGHT:
        raise ValueError(f"max_inflight must be 1..{MAX_CALLBACK_INFLIGHT}")
    if expected_port is not None and (
        isinstance(expected_port, bool) or not isinstance(expected_port, int) or not 1 <= expected_port <= 65_535
    ):
        raise ValueError("expected_port must be 1..65535")
    if not isinstance(store_timeout_seconds, (int, float)) or store_timeout_seconds <= 0:
        raise ValueError("store_timeout_seconds must be positive")
    hosts = {_normalize_host(host) for host in trusted_hosts}
    if not hosts:
        raise ValueError("at least one exact trusted callback host is required")
    limiter = _InflightLimit(max_inflight)

    async def app(scope, receive, send) -> None:
        if scope.get("type") == "lifespan":
            while True:
                message = await receive()
                if message.get("type") == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message.get("type") == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope.get("type") != "http":
            status, body, headers = _response(404, {"accepted": False, "error": "not found"})
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return
        if scope.get("path") != CALLBACK_PATH:
            status, body, headers = _response(404, {"accepted": False, "error": "not found"})
        elif scope.get("method") != "POST":
            status, body, headers = _response(405, {"accepted": False, "error": "POST required"})
            headers.append((b"allow", b"POST"))
        elif not limiter.enter():
            status, body, headers = _response(429, {"accepted": False, "error": "callback capacity is full"})
        else:
            try:
                status, body, headers = await _handle(scope, receive, store, client_id, hosts, expected_port, store_timeout_seconds, on_committed)
            finally:
                limiter.leave()
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return app


async def _handle(scope, receive, store, client_id, hosts, expected_port, store_timeout_seconds, on_committed=None):
    headers = _headers(scope)
    host_values = headers.get(b"host", [])
    if len(host_values) != 1:
        return _response(400, {"accepted": False, "error": "Host is not allowlisted"})
    try:
        host, port = _parse_host(host_values[0].decode("ascii", errors="strict"))
    except (UnicodeDecodeError, ValueError):
        return _response(400, {"accepted": False, "error": "Host is not allowlisted"})
    port_matches = (
        expected_port is None
        or port == expected_port
        or (port is None and expected_port == 443)
    )
    if host not in hosts or not port_matches:
        return _response(400, {"accepted": False, "error": "Host is not allowlisted"})
    if scope.get("query_string", b""):
        return _response(400, {"accepted": False, "error": "query parameters are not accepted"})

    authorization = headers.get(b"authorization", [])
    if len(authorization) != 1:
        return _response(401, {"accepted": False, "error": "task capability is not valid"})
    try:
        authorization_value = authorization[0].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return _response(401, {"accepted": False, "error": "task capability is not valid"})
    match = _AUTHORIZATION.fullmatch(authorization_value)
    if not match:
        return _response(401, {"accepted": False, "error": "task capability is not valid"})
    token = match.group(1)

    content_types = headers.get(b"content-type", [])
    if len(content_types) != 1:
        return _response(415, {"accepted": False, "error": "application/json required"})
    try:
        content_type = content_types[0].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return _response(415, {"accepted": False, "error": "application/json required"})
    content_parts = [part.strip() for part in content_type.split(";")]
    if content_parts[0].lower() != "application/json" or any(part.lower() != "charset=utf-8" for part in content_parts[1:]):
        return _response(415, {"accepted": False, "error": "application/json required"})
    content_encodings = headers.get(b"content-encoding", [])
    if len(content_encodings) > 1 or (content_encodings and content_encodings[0].lower() != b"identity"):
        return _response(415, {"accepted": False, "error": "unsupported content encoding"})

    lengths = headers.get(b"content-length", [])
    expected_length: int | None = None
    if len(lengths) > 1:
        return _response(400, {"accepted": False, "error": "invalid content length"})
    if lengths:
        try:
            raw_length = lengths[0].decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return _response(400, {"accepted": False, "error": "invalid content length"})
        if not _CONTENT_LENGTH.fullmatch(raw_length):
            return _response(400, {"accepted": False, "error": "invalid content length"})
        normalized_length = raw_length.lstrip("0") or "0"
        if len(normalized_length) > len(str(CALLBACK_BODY_LIMIT)) or int(normalized_length) > CALLBACK_BODY_LIMIT:
            return _response(413, {"accepted": False, "error": "request body too large"})
        expected_length = int(normalized_length)

    body = bytearray()
    deadline = asyncio.get_running_loop().time() + CALLBACK_BODY_TIMEOUT_SECONDS
    while True:
        try:
            message = await asyncio.wait_for(receive(), timeout=max(0, deadline - asyncio.get_running_loop().time()))
        except asyncio.TimeoutError:
            status, response, response_headers = _response(408, {"accepted": False, "error": "request body timed out"})
            response_headers.append((b"connection", b"close"))
            return status, response, response_headers
        if message.get("type") == "http.disconnect":
            return _response(400, {"accepted": False, "error": "request body incomplete"})
        if message.get("type") != "http.request":
            continue
        chunk = message.get("body", b"")
        if len(body) + len(chunk) > CALLBACK_BODY_LIMIT:
            return _response(413, {"accepted": False, "error": "request body too large"})
        body.extend(chunk)
        if not message.get("more_body", False):
            break
    if expected_length is not None and len(body) != expected_length:
        return _response(400, {"accepted": False, "error": "invalid content length"})
    try:
        payload = json.loads(body.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return _response(400, {"accepted": False, "error": "invalid JSON body"})
    if not isinstance(payload, dict) or "result_token" in payload or _contains_capability(payload, token):
        return _response(400, {"accepted": False, "error": "report must not contain the task capability"})
    try:
        validated = validate_grok_callback(payload, result_token=token)
    except GrokResultError:
        return _response(400, {"accepted": False, "error": "report does not match the bounded schema"})
    async def commit():
        receipt = await asyncio.to_thread(
            store.commit_grok_callback,
            client_id=client_id, task_id=validated.task_id, token=token,
            digest=validated.digest, reported_outcome=validated.reported_outcome,
            result=validated.result, now=datetime.now(timezone.utc),
        )
        if not receipt.duplicate and not receipt.late and on_committed is not None:
            try:
                on_committed(client_id)
            except Exception:
                pass
        return receipt

    commit_task = asyncio.create_task(commit())
    # The SQLite thread can commit after a response timeout; still reconcile then.
    def observe_commit(done):
        if not done.cancelled():
            done.exception()
    commit_task.add_done_callback(observe_commit)
    try:
        receipt = await asyncio.wait_for(asyncio.shield(commit_task), timeout=float(store_timeout_seconds))
    except GrokCallbackUnauthorized:
        return _response(401, {"accepted": False, "error": "task capability is not valid"})
    except GrokCallbackConflict:
        return _response(409, {"accepted": False, "error": "a different callback already has a receipt"})
    except (AdmissionUnavailable, asyncio.TimeoutError):
        return _response(503, {"accepted": False, "error": "callback receipt store unavailable"})
    except Exception:
        return _response(503, {"accepted": False, "error": "callback receipt store unavailable"})
    return _response(200, {
        "accepted": True,
        "task_id": receipt.task_id,
        "receipt_id": receipt.receipt_id,
        "duplicate": receipt.duplicate,
        "late": receipt.late,
        "native_state": "unknown",
    })
