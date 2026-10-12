"""OpenAB ACP executor for P3.3: authenticated JSON-RPC over GET /acp.

This talks to a configured ACP WebSocket. It does not start ECS, Jellii, or AWS.
Prompt payloads are bounded text only; raw audio is never forwarded.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import socket
import ssl
from typing import Any
from urllib.parse import urlparse

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .dispatcher import ExecutionOutcome
from .task_store import BoundedTaskResult, ClaimedTask

ACP_PROTOCOL_VERSION = 1
DEFAULT_ACP_TIMEOUT_SECONDS = 120.0
MAX_ACP_TIMEOUT_SECONDS = 120.0
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
MAX_SPEAK_CHARS = 240
MAX_DETAILS_CHARS = 4096
MAX_CARD_LINES = 4
MAX_CARD_TITLE = 80
MAX_PROMPT_CHARS = 4096
MAX_ACP_CA_PEM_CHARS = 64 * 1024

# websockets DEBUG logs handshake headers and, depending on version, frame data.
# Keep the transport logger at INFO even when the application's root is DEBUG.
_ACP_WEBSOCKETS_LOGGER = logging.getLogger("amusant.acp.websocket")
_ACP_WEBSOCKETS_LOGGER.setLevel(logging.INFO)


class AcpUncertainError(RuntimeError):
    """Prompt was sent; the terminal ACP result is unknown."""


def _is_allowed_destination(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Allow loopback and RFC1918 only. Not a substitute for Security Group egress."""
    # ipaddress classifies IPv6 ::1 as reserved; explicit loopback policy wins.
    if address.is_loopback:
        return True
    if address.is_unspecified or address.is_link_local or address.is_multicast or address.is_reserved:
        return False
    if address.version == 4:
        return any(address in network for network in _RFC1918)
    return False


def _require_timeout(timeout_seconds: object) -> float:
    if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("ACP timeout must be a finite positive number")
    return min(float(timeout_seconds), MAX_ACP_TIMEOUT_SECONDS)


def _require_private_host(host: str) -> None:
    """Validate literal IPs at config time; resolve DNS only when dispatching."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Cloud Map may have no records while the Agent service is stopped.
        # The name is validated against private addresses immediately before dial.
        return
    if not _is_allowed_destination(address):
        raise ValueError("AMUSANT_ACP_URL may target only loopback or an RFC1918 address")


async def _resolve_private_target(url: str, deadline: float) -> tuple[str, int, str]:
    """Resolve once, reject public answers, and pin the socket to one private IP.

    Return (dial_ip, port, tls_hostname). The URL hostname remains the TLS
    server_hostname, preserving SNI and certificate validation while preventing
    a second DNS lookup between the allowlist check and connect.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError("AMUSANT_ACP_URL must include a host")
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
            timeout=remaining,
        )
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for info in infos:
            resolved = ipaddress.ip_address(info[4][0])
            if not _is_allowed_destination(resolved):
                raise ValueError("AMUSANT_ACP_URL DNS resolved outside loopback/RFC1918")
            if resolved not in addresses:
                addresses.append(resolved)
        if not addresses:
            raise ValueError("AMUSANT_ACP_URL host has no loopback/RFC1918 addresses")
        # Prefer IPv4 for ECS Cloud Map A records; all returned answers must pass.
        selected = next((address for address in addresses if address.version == 4), addresses[0])
        return selected.compressed, port, host
    if not _is_allowed_destination(address):
        raise ValueError("AMUSANT_ACP_URL may target only loopback or an RFC1918 address")
    return address.compressed, port, host


def _require_private_ws_url(url: str, *, allow_plaintext: bool) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("AMUSANT_ACP_URL must be a ws:// or wss:// /acp URL")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"ws", "wss"} or parsed.path != "/acp" or parsed.query or parsed.fragment:
        raise ValueError("AMUSANT_ACP_URL must be ws:// or wss:// host/acp with no query")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("AMUSANT_ACP_URL must use a valid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("AMUSANT_ACP_URL must use a valid port")
    if parsed.scheme == "ws" and not allow_plaintext:
        host = parsed.hostname or ""
        try:
            address = ipaddress.ip_address(host) if host not in {"localhost"} else ipaddress.ip_address("127.0.0.1")
        except ValueError:
            address = None
        if address is None or not address.is_loopback:
            raise ValueError("plaintext ws:// to a non-loopback host requires AMUSANT_ACP_ALLOW_PLAINTEXT=1")
    if not parsed.hostname:
        raise ValueError("AMUSANT_ACP_URL host must be localhost, loopback, private IP, or private DNS")
    _require_private_host(parsed.hostname)
    return url.strip()


def _require_remote_cwd(cwd: str) -> str:
    """cwd is sent to the ACP runtime; do not require it to exist on the Relay."""
    if not isinstance(cwd, str):
        raise ValueError("AMUSANT_ACP_CWD must be an absolute POSIX path on the ACP runtime")
    raw = cwd.strip()
    if not raw.startswith("/") or raw.startswith("//") or "\x00" in raw or len(raw) > 256:
        raise ValueError("AMUSANT_ACP_CWD must be an absolute POSIX path on the ACP runtime")
    if any(part == ".." for part in raw.split("/")):
        raise ValueError("AMUSANT_ACP_CWD must not contain ..")
    return raw


def normalize_agent_text(text: str) -> BoundedTaskResult:
    if not isinstance(text, str):
        raise ValueError("acp_empty_result")
    cleaned = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32).strip()
    if not cleaned:
        raise ValueError("acp_empty_result")
    speak = " ".join(cleaned.split())[:MAX_SPEAK_CHARS].strip()
    lines = [line.strip()[:120] for line in cleaned.splitlines() if line.strip()][:MAX_CARD_LINES]
    title = (lines[0] if lines else "Task result")[:MAX_CARD_TITLE]
    return BoundedTaskResult(
        speak=speak,
        card={"title": title, "lines": lines},
        details_md=cleaned[:MAX_DETAILS_CHARS],
    )


def _prompt_blocks(task: ClaimedTask) -> list[dict[str, str]]:
    body = f"Task kind: {task.task_kind}\n\n{task.task}".strip()
    if len(body) > MAX_PROMPT_CHARS:
        body = body[:MAX_PROMPT_CHARS]
    return [{"type": "text", "text": body}]


class OpenAbAcpExecutor:
    """One ACP connection per claimed task; text-only, bounded, fail-closed."""

    def __init__(
        self,
        *,
        url: str,
        auth_key: str,
        cwd: str,
        timeout_seconds: float = DEFAULT_ACP_TIMEOUT_SECONDS,
        allow_plaintext: bool = False,
        ca_pem: str | None = None,
        connect_factory=connect,
    ) -> None:
        if not isinstance(auth_key, str) or len(auth_key) < 12:
            raise ValueError("AMUSANT_ACP_AUTH_KEY must contain at least 12 characters")
        self._url = _require_private_ws_url(url, allow_plaintext=allow_plaintext)
        if ca_pem is not None:
            if not isinstance(ca_pem, str) or len(ca_pem) > MAX_ACP_CA_PEM_CHARS:
                raise ValueError("AMUSANT_ACP_CA_PEM must be a bounded PEM certificate bundle")
            ca_pem = ca_pem.strip() or None
            if ca_pem is not None and "-----BEGIN CERTIFICATE-----" not in ca_pem:
                raise ValueError("AMUSANT_ACP_CA_PEM must contain PEM certificate data")
            if ca_pem is not None and urlparse(self._url).scheme != "wss":
                raise ValueError("AMUSANT_ACP_CA_PEM requires a wss:// ACP URL")
        self._ca_pem = ca_pem
        self._auth_key = auth_key
        self._cwd = _require_remote_cwd(cwd)
        self._timeout_seconds = _require_timeout(timeout_seconds)
        self._connect = connect_factory
        self._deadline: float | None = None

    async def execute(self, task: ClaimedTask) -> ExecutionOutcome:
        prompt_sent = False
        loop = asyncio.get_running_loop()
        self._deadline = loop.time() + self._timeout_seconds
        try:
            dial_host, dial_port, tls_hostname = await _resolve_private_target(self._url, self._deadline)
            remaining = max(0.01, self._deadline - loop.time())
            connect_kwargs: dict[str, Any] = {
                "additional_headers": {"Authorization": f"Bearer {self._auth_key}"},
                "open_timeout": min(5.0, remaining),
                "max_size": 64 * 1024,
                "logger": _ACP_WEBSOCKETS_LOGGER,
                # Pin the TCP dial to the address that passed the private-IP check.
                "host": dial_host,
                "port": dial_port,
                "proxy": None,
            }
            if urlparse(self._url).scheme == "wss":
                tls = ssl.create_default_context()
                if self._ca_pem is not None:
                    tls.load_verify_locations(cadata=self._ca_pem)
                if tls.verify_mode != ssl.CERT_REQUIRED or not tls.check_hostname:
                    raise ValueError("ACP wss:// must verify TLS certificates and hostnames")
                connect_kwargs["ssl"] = tls
                connect_kwargs["server_hostname"] = tls_hostname
            async with self._connect(self._url, **connect_kwargs) as ws:
                await self._rpc(ws, 1, "initialize", {"protocolVersion": ACP_PROTOCOL_VERSION})
                session = await self._rpc(ws, 2, "session/new", {"cwd": self._cwd, "mcpServers": []})
                session_id = session.get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    return ExecutionOutcome(status="failed", code="acp_session")
                prompt_sent = True
                chunks: list[str] = []
                result = await self._rpc(
                    ws,
                    3,
                    "session/prompt",
                    {"sessionId": session_id, "prompt": _prompt_blocks(task)},
                    on_update=lambda params: _collect_chunk(chunks, params),
                )
        except AcpExecutorFailed as error:
            return ExecutionOutcome(status="failed", code=error.code)
        except (ConnectionClosed, OSError, TimeoutError, asyncio.TimeoutError):
            if prompt_sent:
                raise AcpUncertainError("acp_result_unconfirmed") from None
            return ExecutionOutcome(status="failed", code="acp_connect")
        except AcpUncertainError:
            raise
        except Exception:
            if prompt_sent:
                raise AcpUncertainError("acp_result_unconfirmed") from None
            return ExecutionOutcome(status="failed", code="acp_connect")

        stop = result.get("stopReason") if isinstance(result, dict) else None
        if stop == "cancelled":
            return ExecutionOutcome(status="failed", code="acp_cancelled")
        if stop != "end_turn":
            raise AcpUncertainError("acp_result_unconfirmed")
        try:
            bounded = normalize_agent_text("".join(chunks))
        except ValueError:
            return ExecutionOutcome(status="failed", code="acp_empty_result")
        return ExecutionOutcome(status="completed", code="ok", result=bounded)

    async def _rpc(
        self,
        ws,
        request_id: int,
        method: str,
        params: dict[str, Any],
        *,
        on_update=None,
    ) -> dict[str, Any]:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
        deadline = self._deadline
        if deadline is None:
            raise asyncio.TimeoutError
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            if not isinstance(raw, str):
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                continue
            if message.get("method") == "session/update":
                if on_update is not None:
                    on_update(message.get("params"))
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error") if isinstance(message.get("error"), dict) else {}
                code = error.get("code")
                if code == -32000:
                    raise AcpExecutorFailed("acp_overloaded")
                if method == "session/prompt":
                    raise AcpUncertainError("acp_result_unconfirmed")
                if method == "initialize":
                    raise AcpExecutorFailed("acp_initialize")
                raise AcpExecutorFailed("acp_session")
            result = message.get("result")
            if not isinstance(result, dict):
                raise AcpExecutorFailed("acp_rpc_error")
            return result


class AcpExecutorFailed(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _collect_chunk(chunks: list[str], params: object) -> None:
    if not isinstance(params, dict):
        return
    update = params.get("update")
    if not isinstance(update, dict) or update.get("sessionUpdate") != "agent_message_chunk":
        return
    content = update.get("content")
    if not isinstance(content, dict) or content.get("type") != "text":
        return
    text = content.get("text")
    if isinstance(text, str) and text:
        chunks.append(text)


def load_acp_executor(environ) -> OpenAbAcpExecutor | None:
    url = environ.get("AMUSANT_ACP_URL")
    key = environ.get("AMUSANT_ACP_AUTH_KEY")
    cwd = environ.get("AMUSANT_ACP_CWD")
    present = [value for value in (url, key, cwd) if isinstance(value, str) and value.strip()]
    if not present:
        return None
    if len(present) != 3:
        raise ValueError("ACP executor requires AMUSANT_ACP_URL, AMUSANT_ACP_AUTH_KEY, and AMUSANT_ACP_CWD")
    timeout_raw = environ.get("AMUSANT_ACP_TIMEOUT_SECONDS", "")
    timeout = DEFAULT_ACP_TIMEOUT_SECONDS
    if isinstance(timeout_raw, str) and timeout_raw.strip():
        timeout = float(timeout_raw)
    allow_plaintext = environ.get("AMUSANT_ACP_ALLOW_PLAINTEXT", "") == "1"
    ca_pem = environ.get("AMUSANT_ACP_CA_PEM")
    return OpenAbAcpExecutor(
        url=url.strip(),
        auth_key=key.strip(),
        cwd=cwd.strip(),
        timeout_seconds=timeout,
        allow_plaintext=allow_plaintext,
        ca_pem=ca_pem,
    )
