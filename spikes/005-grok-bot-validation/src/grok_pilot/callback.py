"""Offline-testable, task-scoped HTTP callback adapter for the G2 receipt store."""

from __future__ import annotations

import ipaddress
import json
import re
import threading
from collections.abc import Sequence
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .contract import ContractError, validate_report
from .store import Conflict, NotFound, PilotStore, StoreError, Unauthorized

CALLBACK_PATH = "/callback/results"
CALLBACK_BODY_LIMIT = 16_384
MAX_CALLBACK_INFLIGHT = 32
_AUTHORIZATION = re.compile(r"(?i:Bearer) ([A-Za-z0-9._~-]{1,128})\Z")
_CONTENT_LENGTH = re.compile(r"[0-9]+\Z")


def _normalize_host(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(char in value for char in "/:@*?#"):
        raise ValueError("trusted callback hosts must be exact bare hostnames or IPv4 addresses")
    try:
        return ipaddress.IPv4Address(value).compressed
    except ipaddress.AddressValueError:
        pass
    try:
        ascii_host = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("trusted callback host is invalid") from exc
    if len(ascii_host) > 253 or not ascii_host or ascii_host.endswith("."):
        raise ValueError("trusted callback host is invalid")
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("trusted callback host is invalid")
    return ascii_host


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class _InflightLimit:
    def __init__(self, maximum: int):
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


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"accepted": False, "error": message}, status_code=status_code)


def _bearer(request: Request) -> str | None:
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        return None
    match = _AUTHORIZATION.fullmatch(values[0])
    return match.group(1) if match else None


def _content_type_is_json(request: Request) -> bool:
    values = request.headers.getlist("content-type")
    if len(values) != 1:
        return False
    parts = [part.strip() for part in values[0].split(";")]
    if parts[0].lower() != "application/json":
        return False
    return all(part.lower() == "charset=utf-8" for part in parts[1:])


async def _read_bounded_body(request: Request) -> bytes | Response:
    encodings = request.headers.getlist("content-encoding")
    if len(encodings) > 1 or (encodings and encodings[0].lower() != "identity"):
        return _error(415, "unsupported content encoding")

    lengths = request.headers.getlist("content-length")
    expected_length: int | None = None
    if len(lengths) > 1:
        return _error(400, "invalid content length")
    if lengths:
        if not _CONTENT_LENGTH.fullmatch(lengths[0]):
            return _error(400, "invalid content length")
        normalized_length = lengths[0].lstrip("0") or "0"
        if len(normalized_length) > len(str(CALLBACK_BODY_LIMIT)):
            return _error(413, "request body too large")
        expected_length = int(normalized_length)
        if expected_length > CALLBACK_BODY_LIMIT:
            return _error(413, "request body too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > CALLBACK_BODY_LIMIT:
            return _error(413, "request body too large")
        body.extend(chunk)
    if expected_length is not None and len(body) != expected_length:
        return _error(400, "invalid content length")
    return bytes(body)


def create_callback_app(
    store: PilotStore,
    owner_id: str,
    *,
    trusted_hosts: Sequence[str],
    max_inflight: int,
) -> Any:
    """Build the one-route ASGI adapter; this function does not start a listener.

    The Owner mapping and host allowlist are trusted local configuration. The only
    result credential is a task-scoped Bearer capability, checked by PilotStore.
    The in-flight cap applies to this ASGI app instance.
    """
    if not isinstance(owner_id, str) or not owner_id or owner_id != owner_id.strip():
        raise ValueError("owner_id must be a non-empty canonical value")
    if not isinstance(max_inflight, int) or isinstance(max_inflight, bool) or not 1 <= max_inflight <= MAX_CALLBACK_INFLIGHT:
        raise ValueError(f"max_inflight must be 1..{MAX_CALLBACK_INFLIGHT}")
    hosts = sorted({_normalize_host(host) for host in trusted_hosts})
    if not hosts:
        raise ValueError("at least one exact trusted callback host is required")

    limiter = _InflightLimit(max_inflight)

    async def submit(request: Request) -> Response:
        if not limiter.enter():
            return _error(429, "callback capacity is full")
        try:
            incoming_hosts = request.headers.getlist("host")
            if len(incoming_hosts) != 1 or incoming_hosts[0].lower() not in hosts:
                return _error(400, "Host is not allowlisted")
            if request.scope.get("query_string", b""):
                return _error(400, "query parameters are not accepted")

            result_token = _bearer(request)
            if result_token is None:
                return _error(401, "task capability required")
            if not _content_type_is_json(request):
                return _error(415, "application/json required")

            body_bytes = await _read_bounded_body(request)
            if isinstance(body_bytes, Response):
                return body_bytes
            try:
                decoded = body_bytes.decode("utf-8", errors="strict")
                payload = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return _error(400, "invalid JSON body")
            if not isinstance(payload, dict):
                return _error(400, "JSON body must be an object")
            if "result_token" in payload:
                return _error(400, "result_token must be sent in the Authorization header")

            try:
                validated = validate_report({**payload, "result_token": result_token})
            except ContractError:
                return _error(400, "report does not match the accepted schema")

            try:
                receipt = store.submit_report(owner_id, validated, require_token_expiry=True)
            except (NotFound, Unauthorized):
                # Keep task existence, Owner mismatch, expiry, and token validity indistinguishable.
                return _error(401, "task capability is not valid for this task")
            except Conflict:
                return _error(409, "a different report already has a receipt")
            except StoreError:
                return _error(503, "receipt store unavailable")

            # submit_report returns only after the SQLite transaction has committed.
            return JSONResponse(
                {
                    "accepted": True,
                    "task_id": receipt.task_id,
                    "receipt_id": receipt.receipt_id,
                    "duplicate": receipt.duplicate,
                    "late": receipt.late,
                },
                status_code=200,
            )
        finally:
            limiter.leave()

    app = Starlette(
        routes=[Route(CALLBACK_PATH, submit, methods=["POST"])],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=hosts, www_redirect=False)],
    )
    # Avoid Starlette's default 307 for a trailing slash on this POST-only endpoint.
    app.router.redirect_slashes = False
    return app
