#!/usr/bin/env python3
"""Offline ARM64 Relay -> Facade -> pinned Hermes integration proof."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.client
import json
import os
import socket
import ssl
import struct
import sys
import threading
import traceback
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from fast_voice.contracts import (
    AudioFrame,
    ProviderCapabilities,
    ToolCallEvent,
    TranscriptEvent,
    TurnCompleteEvent,
)
from google.genai import types as sdk_types
from relay.application import build_supported_relay_runtime
from relay.device_protocol import adapt_device_handler
from relay.device_tls_psk import PSK_CIPHERS, build_server_context
from relay.hermes_runs_executor import HermesRequestError, HermesRunsExecutor
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from stop_classification import classify_stop_outcome


TOKEN = "offline-b2-device-token-synthetic"
CLIENT_ID = "b2-offline-owner"
DEVICE_IDENTITY = "b2-test-device"
DEVICE_PSK = bytes(range(32))
FACADE_KEY = os.environ["POC_FACADE_KEY"]
RUN_TAG = os.environ["POC_RUN_TAG"]
EVIDENCE_PATH = Path(os.environ.get("E2E_EVIDENCE_PATH", "/evidence/relay-e2e.json"))
MIN_PCM_BYTES = 12_000
STOP_POLL_CYCLE_WAIT_SECONDS = 16.0
STOP_DISPATCH_TIMEOUT_SECONDS = 20.0
HERMES_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted", "expired"}

CHECKS: dict[str, str] = {}
WIRE: list[str | bytes] = []
PHASE = "initializing"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def diagnostic_token(value: object) -> str:
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and all(character.isascii() and (character.isalnum() or character in "_-") for character in value)
    ):
        return value
    return "unknown"


def diagnostic_error_category(payload: dict[str, object]) -> str:
    error = payload.get("error")
    parts = [error, payload.get("turn_exit_reason")]
    if isinstance(error, dict):
        parts.extend(error.get(key) for key in ("code", "type", "message"))
    message = " ".join(value for value in parts if isinstance(value, str)).lower()
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if "interrupt" in message or "cancel" in message:
        return "interrupt"
    if "auth" in message or "credential" in message or "401" in message:
        return "auth_error"
    if "provider" in message or "model" in message or "api" in message:
        return "model_api_error"
    return "hermes_error" if message else "unknown"


def diagnostic_exit_category(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    reason = value.lower()
    if "interrupt" in reason or "cancel" in reason:
        return "interrupt"
    if "timeout" in reason or "timed_out" in reason:
        return "timeout"
    if "max_iterations" in reason or "budget_exhausted" in reason:
        return "iteration_limit"
    if "pending_tool" in reason:
        return "pending_tool_result"
    if "persistence" in reason or "storage" in reason:
        return "persistence_error"
    if "provider" in reason or "model" in reason or "api" in reason:
        return "model_api_error"
    return "other"


class FakeGeminiLive:
    """Fake Gemini Live provider used only by this test container."""

    provider_id = "gemini"
    capabilities = ProviderCapabilities(True, True, True, True, False, True)

    def __init__(self, *, auto_complete: bool = False):
        self.auto_complete = auto_complete
        self.callbacks = {}
        self.connected = asyncio.Event()
        self.tool_results = []
        self.audio_input = bytearray()

    def on_audio(self, callback): self.callbacks["audio"] = callback
    def on_transcript(self, callback): self.callbacks["transcript"] = callback
    def on_tool_call(self, callback): self.callbacks["tool"] = callback
    def on_tool_calls_complete(self, callback): self.callbacks["tool_batch_complete"] = callback
    def on_interrupted(self, callback): self.callbacks["interrupted"] = callback
    def on_turn_complete(self, callback): self.callbacks["complete"] = callback
    def on_error(self, callback): self.callbacks["error"] = callback

    async def connect(self):
        self.connected.set()

    async def close(self):
        return None

    async def start_turn(self):
        return None

    async def send_audio(self, frame):
        self.audio_input.extend(frame.data)

    async def end_turn(self):
        if self.auto_complete:
            await self.emit_response()

    async def send_tool_result(self, call_id, result):
        self.tool_results.append((call_id, dict(result)))

    async def cancel_response(self):
        return None

    async def inject_text(self, text):
        return None

    async def emit_response(self):
        await self.callbacks["audio"](AudioFrame(b"\x00\x00" * 12_000, 24_000))
        await self.callbacks["transcript"](
            TranscriptEvent("Offline fake Gemini response.", "output", True)
        )
        await self.callbacks["complete"](TurnCompleteEvent(cancelled=False))


class HttpAudit:
    def __init__(self, original):
        self.original = original
        self.lock = threading.Lock()
        self.relay_submit_calls: dict[str, int] = {}
        self.get_calls = 0
        self.stop_calls = 0
        self.stop_targeted_posts = 0
        self.stop_unexpected_posts = 0
        self.stop_accepted_posts = 0
        self.stop_remote_terminal_observed = False
        self.stop_remote_terminal_after_accepted_stop = False
        self.stop_run_id: str | None = None
        self.stop_post_accepted = False
        self.stop_post_completed = False
        self.stop_post_http_status: int | None = None
        self.stop_provider_release_signaled = False
        self.stop_remote_status = "unknown"
        self.stop_remote_error_category = "unknown"
        self.stop_remote_exit_category = "unknown"
        self.stop_remote_completed = "unknown"
        self.stop_remote_interrupted = "unknown"
        self.stop_remote_partial = "unknown"
        self.duplicate_verified = False
        self.ambiguous_task_id: str | None = None
        self.restart_task_id: str | None = None
        self.restart_run_id: str | None = None
        self.drop_complete_once = False
        self.drop_event = threading.Event()

    def request(self, executor, method, path, body, headers):
        method = method.upper()
        task_id = headers.get("Idempotency-Key", "")
        task_text = body.get("input", "") if isinstance(body, dict) else ""
        if method == "POST" and path == "/v1/runs":
            if "[POC_CASE=restart_10k]" in task_text:
                with self.lock:
                    count = self.relay_submit_calls.get(task_id, 0)
                    self.relay_submit_calls[task_id] = count + 1
                    self.restart_task_id = task_id
                first = self.original(executor, method, path, body, headers)
                if count == 0:
                    with self.lock:
                        self.relay_submit_calls[task_id] += 1
                    duplicate = self.original(executor, method, path, body, headers)
                    require(first[0] < 300 and duplicate[0] < 300, "duplicate facade submit did not succeed")
                    require(first[1].get("run_id") == duplicate[1].get("run_id"), "duplicate submit changed Run ID")
                    with self.lock:
                        self.duplicate_verified = True
                        self.restart_run_id = first[1].get("run_id")
                return first
            if "[POC_CASE=ambiguous_10k]" in task_text:
                with self.lock:
                    self.relay_submit_calls[task_id] = self.relay_submit_calls.get(task_id, 0) + 1
                    self.ambiguous_task_id = task_id
                status, payload = self.original(executor, method, path, body, headers)
                if status < 300 and isinstance(payload.get("run_id"), str):
                    raise HermesRequestError("synthetic response loss after facade acceptance")
                return status, payload
            if "[POC_CASE=stop_10k]" in task_text:
                with self.lock:
                    self.relay_submit_calls[task_id] = self.relay_submit_calls.get(task_id, 0) + 1
                status, payload = self.original(executor, method, path, body, headers)
                if status < 300 and isinstance(payload.get("run_id"), str):
                    with self.lock:
                        self.stop_run_id = payload["run_id"]
                return status, payload
            with self.lock:
                self.relay_submit_calls[task_id] = self.relay_submit_calls.get(task_id, 0) + 1
            return self.original(executor, method, path, body, headers)

        if method == "GET" and path.startswith("/v1/runs/"):
            with self.lock:
                self.get_calls += 1
            status, payload = self.original(executor, method, path, body, headers)
            if path == "/v1/runs/" + str(self.stop_run_id):
                release_provider = False
                with self.lock:
                    self.stop_remote_status = diagnostic_token(payload.get("status"))
                    self.stop_remote_completed = diagnostic_token(
                        str(payload.get("completed")).lower()
                        if isinstance(payload.get("completed"), bool) else None
                    )
                    self.stop_remote_interrupted = diagnostic_token(
                        str(payload.get("interrupted")).lower()
                        if isinstance(payload.get("interrupted"), bool) else None
                    )
                    self.stop_remote_partial = diagnostic_token(
                        str(payload.get("partial")).lower()
                        if isinstance(payload.get("partial"), bool) else None
                    )
                    self.stop_remote_exit_category = diagnostic_exit_category(
                        payload.get("turn_exit_reason")
                    )
                    if self.stop_remote_status == "failed":
                        self.stop_remote_error_category = diagnostic_error_category(payload)
                    if status == 200 and self.stop_remote_status in HERMES_TERMINAL_STATUSES:
                        self.stop_remote_terminal_observed = True
                        if self.stop_post_accepted:
                            self.stop_remote_terminal_after_accepted_stop = True
                    release_provider = self.stop_post_accepted and self.stop_remote_status in {
                        "stopping", "cancelled", "interrupted"
                    }
                if release_provider and not self.stop_provider_release_signaled:
                    # This marker only releases the deterministic fake fixture;
                    # it is not evidence that external model-provider work stopped.
                    (EVIDENCE_PATH.parent / "stop_10k.release").write_text("accepted", encoding="ascii")
                    with self.lock:
                        self.stop_provider_release_signaled = True
            if (
                status == 200
                and payload.get("status") == "completed"
                and self.restart_run_id is not None
                and path == "/v1/runs/" + self.restart_run_id
                and not self.drop_complete_once
            ):
                with self.lock:
                    self.drop_complete_once = True
                self.drop_event.set()
                return 503, {"error": {"code": "synthetic_dropped_envelope"}}
            return status, payload

        if method == "POST" and path.endswith("/stop"):
            with self.lock:
                self.stop_calls += 1
                expected_path = f"/v1/runs/{self.stop_run_id}/stop" if self.stop_run_id else None
                targeted = path == expected_path
                if targeted:
                    self.stop_targeted_posts += 1
                else:
                    self.stop_unexpected_posts += 1
            status, payload = self.original(executor, method, path, body, headers)
            if targeted:
                with self.lock:
                    self.stop_post_completed = True
                    self.stop_post_http_status = status
                    self.stop_post_accepted = 200 <= status < 300
                    if self.stop_post_accepted:
                        self.stop_accepted_posts += 1
            return status, payload
        return self.original(executor, method, path, body, headers)


def make_client_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("ECDHE-PSK-AES128-CBC-SHA256")
    context.set_ecdh_curve("X25519")
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_psk_client_callback(lambda _hint: (DEVICE_IDENTITY, DEVICE_PSK))
    return context


def runtime_environment() -> dict[str, str]:
    return {
        "AMUSANT_CLIENT_ID": CLIENT_ID,
        "AMUSANT_BACKEND_ID": "offline-hermes",
        "AMUSANT_TASK_STORE": "sqlite",
        "AMUSANT_TASK_DB": "/var/lib/amusant/task-ledger.sqlite3",
        "AMUSANT_TASK_DB_DIR": "/var/lib/amusant",
        "AMUSANT_DEFAULT_TASK_KIND": "research",
        "AMUSANT_MAX_TASK_TTL_SECONDS": "600",
        "AMUSANT_MAX_PENDING_TASKS": "16",
        "AMUSANT_ACCEPTANCE_TIMEOUT_SECONDS": "2",
        "AMUSANT_DISPATCHER_POLL_INTERVAL_SECONDS": "0.05",
        "AMUSANT_SLOW_EXECUTOR": "hermes_runs",
        "AMUSANT_HERMES_RUNS_URL": "http://facade:8643",
        "AMUSANT_FACADE_API_KEY": FACADE_KEY,
        "AMUSANT_HERMES_ALLOW_PLAINTEXT": "1",
        "AMUSANT_HERMES_RUN_DEADLINE_SECONDS": "120",
        "AMUSANT_HERMES_POLL_CYCLE_SECONDS": "15",
        "AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS": "3",
        "AMUSANT_HERMES_POLL_INTERVAL_SECONDS": "0.5",
        "AMUSANT_HERMES_STOP_GRACE_SECONDS": "30",
        "AMUSANT_HERMES_STOP_MAX_ATTEMPTS": "3",
        "AMUSANT_HERMES_STOP_RETRY_BASE_SECONDS": "0.1",
        "AMUSANT_HERMES_STOP_RETRY_MAX_SECONDS": "0.5",
        "AMUSANT_LIVE_PROVIDER": "gemini",
        "GEMINI_API_KEY": "offline-fake-gemini-key-never-used",
    }


async def eventually(predicate, *, timeout=20.0, interval=0.025, description="condition"):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError("timed out waiting for " + description)


async def wait_row(store, task_id, predicate, *, timeout=90.0, description="task state"):
    return await eventually(
        lambda: (lambda row: row if row and predicate(row) else None)(store.get_by_task_id(task_id)),
        timeout=timeout,
        description=description,
    )


async def wait_state(websocket, target, *, timeout=6.0):
    async def receive():
        while True:
            message = await websocket.recv()
            WIRE.append(message)
            if isinstance(message, str):
                value = json.loads(message)
                if value.get("type") == "state" and value.get("state") == target:
                    return value
    return await asyncio.wait_for(receive(), timeout=timeout)


async def wait_turn_complete(websocket, *, timeout=8.0):
    async def receive():
        while True:
            message = await websocket.recv()
            WIRE.append(message)
            if isinstance(message, str):
                value = json.loads(message)
                if value.get("type") == "turn_complete":
                    return value
    return await asyncio.wait_for(receive(), timeout=timeout)


async def open_turn(server, providers):
    before = len(providers)
    port = server.sockets[0].getsockname()[1]
    websocket = await connect(
        f"wss://127.0.0.1:{port}/ws",
        ssl=make_client_context(),
        additional_headers={"Authorization": "Bearer " + TOKEN},
        max_size=64 * 1024,
        ping_interval=None,
    )
    await websocket.send(json.dumps({
        "type": "hello", "device": "untrusted-synthetic-label", "codecs": ["pcm"]
    }))
    await websocket.send(json.dumps({"type": "ptt_start"}))
    await websocket.send(b"\x00" * MIN_PCM_BYTES)
    await websocket.send(json.dumps({"type": "ptt_end"}))
    provider = await eventually(
        lambda: providers[before] if len(providers) > before else None,
        description="fake Live provider creation",
    )
    await asyncio.wait_for(provider.connected.wait(), timeout=4.0)
    if provider.auto_complete:
        await wait_turn_complete(websocket)
    else:
        await wait_state(websocket, "thinking")
    return websocket, provider


async def invoke_tool(provider, call_id, name, arguments):
    await provider.callbacks["tool"](ToolCallEvent(call_id, name, arguments))
    await eventually(
        lambda: provider.tool_results[-1] if provider.tool_results and provider.tool_results[-1][0] == call_id else None,
        timeout=5.0,
        description="fake Live tool result",
    )
    return provider.tool_results[-1][1]


async def finish_synthetic_turn(websocket, provider):
    await provider.emit_response()
    await wait_turn_complete(websocket)


def start_runtime(environ, providers, conversation_counter):
    from relay.application import build_supported_relay_runtime

    return build_supported_relay_runtime(
        expected_device_token=TOKEN,
        environ=environ,
        sdk_types=sdk_types,
        conversation_id_factory=lambda: f"b2-conversation-{next(conversation_counter)}",
        post_tool_ack_timeout_seconds=0.5,
        post_tool_ack_max_duration_seconds=2.0,
    )


def probe_routing() -> dict[str, bool]:
    try:
        socket.getaddrinfo("hermes", 8642, type=socket.SOCK_STREAM)
        dns_denied = False
    except socket.gaierror:
        dns_denied = True

    raw_hermes_port_denied = _raw_hermes_port_denied()
    raw_hermes_get_denied = _raw_hermes_http_denied("/health")
    raw_hermes_sse_denied = _raw_hermes_http_denied(
        "/v1/runs/run_synthetic_probe/events",
        accept="text/event-stream",
    )

    owner_key = hmac.new(
        FACADE_KEY.encode("utf-8"), CLIENT_ID.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    unauthorized = http.client.HTTPConnection("facade", 8643, timeout=2)
    try:
        unauthorized.request(
            "GET", "/v1/runs/run_synthetic_probe",
            headers={
                "Authorization": "Bearer invalid-synthetic-facade-key",
                "X-Hermes-Session-Key": owner_key,
            },
        )
        unauthorized_status = unauthorized.getresponse().status
    finally:
        unauthorized.close()

    connection = http.client.HTTPConnection("facade", 8643, timeout=2)
    try:
        connection.request(
            "GET", "/v1/runs/run_synthetic_probe/events",
            headers={
                "Authorization": "Bearer " + FACADE_KEY,
                "X-Hermes-Session-Key": owner_key,
            },
        )
        facade_sse_status = connection.getresponse().status
    finally:
        connection.close()
    return {
        "raw_hermes_dns_denied": dns_denied,
        "raw_hermes_port_denied": raw_hermes_port_denied,
        "raw_hermes_get_denied": raw_hermes_get_denied,
        "raw_hermes_sse_denied": raw_hermes_sse_denied,
        "facade_rejects_invalid_key": unauthorized_status == 401,
        "facade_sse_route_closed": facade_sse_status == 404,
    }


def _raw_hermes_port_denied() -> bool:
    try:
        connection = socket.create_connection(("172.30.64.3", 8642), timeout=0.4)
    except OSError:
        return True
    connection.close()
    return False


def _raw_hermes_http_denied(path: str, *, accept: str | None = None) -> bool:
    connection = http.client.HTTPConnection("172.30.64.3", 8642, timeout=0.4)
    headers = {"Accept": accept} if accept else {}
    try:
        connection.request("GET", path, headers=headers)
        connection.getresponse()
        return False
    except (OSError, http.client.HTTPException):
        return True
    finally:
        connection.close()


def probe_wrong_owner_run_access(run_id: str) -> bool:
    connection = http.client.HTTPConnection("facade", 8643, timeout=2)
    try:
        connection.request(
            "GET", "/v1/runs/" + quote(run_id, safe=""),
            headers={
                "Authorization": "Bearer " + FACADE_KEY,
                "X-Hermes-Session-Key": "different-synthetic-owner-session-key",
            },
        )
        response = connection.getresponse()
        response.read(512)
        return response.status == 404
    finally:
        connection.close()


def result_is_bounded(store, task_id):
    result = store.get_result(task_id)
    require(isinstance(result, dict), "bounded task result missing")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    require(len(encoded) <= 3000, "Relay task result exceeded the bounded envelope")
    require(result.get("details_md") == result.get("speak"), "legacy details_md slot was not reduced to short speech")
    marker = f"POC_REPORT_SECRET_{RUN_TAG}_"
    require(marker not in encoded.decode("utf-8"), "full report marker reached Relay task result")
    require(marker not in str(result.get("card")), "full report marker reached Relay card")
    return len(encoded)


async def run():
    global PHASE
    providers = type("ProviderPool", (), {})()
    providers.instances = []
    providers.auto_modes = [True]
    conversation_counter = iter(range(1, 10_000))
    environ = runtime_environment()
    route = probe_routing()
    require(all(route.values()), "Relay container reached raw Hermes or facade SSE route")
    CHECKS.update(route)

    original_request = HermesRunsExecutor._http_request
    audit = HttpAudit(original_request)

    def audited_request(executor, method, path, body, headers):
        return audit.request(executor, method, path, body, headers)

    phase_patcher = patch("relay.application.build_live_provider", side_effect=lambda *a, **kw: _current_live_factory(providers, *a, **kw))
    request_patcher = patch.object(HermesRunsExecutor, "_http_request", audited_request)
    phase_patcher.start()
    request_patcher.start()
    handler = dispatcher = store = None
    server = None
    dispatcher2 = server2 = None
    store2 = None
    dispatcher3 = server3 = None
    try:
        PHASE = "runtime-start"
        handler, dispatcher, store = start_runtime(environ, providers, conversation_counter)
        await dispatcher.start()
        server = await serve(
            adapt_device_handler(handler, hardware_mode=True),
            "127.0.0.1",
            0,
            ssl=build_server_context({DEVICE_IDENTITY: DEVICE_PSK}),
            max_size=64 * 1024,
            ping_interval=None,
        )

        PHASE = "fast-fake-live"
        fast_ws, fast_provider = await open_turn(server, providers.instances)
        require(fast_provider.provider_id == "gemini", "FAST path did not use the fake Gemini Live provider")
        require(fast_provider.audio_input, "FAST path did not carry PCM audio through Relay")
        await fast_ws.close()
        CHECKS["fast_fake_gemini_live"] = "PASS"

        PHASE = "slow-restart-admission"
        slow_ws, slow_provider = await open_turn(server, providers.instances)
        accepted = await invoke_tool(
            slow_provider, "b2-restart-delegate", "delegate_task",
            {"task": "[POC_CASE=restart_10k] Produce a bounded synthetic report.", "task_kind": "research"},
        )
        require(accepted.get("status") == "accepted", "SLOW task was not durably admitted")
        restart_task_id = accepted["task_id"]
        await slow_ws.close()
        CHECKS["slow_relay_dispatcher_sqlite_admission"] = "PASS"

        # One slow-path action is allowed per Live turn; use a new session for
        # the same owner to exercise the durable single-flight admission guard.
        busy_ws, busy_provider = await open_turn(server, providers.instances)
        busy = await invoke_tool(
            busy_provider, "b2-restart-busy", "delegate_task",
            {"task": "A second task must remain single-flight.", "task_kind": "research"},
        )
        require(busy.get("status") == "busy" and busy.get("code") == "slow_busy", "single-flight did not return slow_busy")
        await finish_synthetic_turn(busy_ws, busy_provider)
        await busy_ws.close()
        CHECKS["single_flight_slow_busy"] = "PASS"

        PHASE = "drop-completed-envelope"
        await asyncio.wait_for(asyncio.to_thread(audit.drop_event.wait, 75.0), timeout=80.0)
        require(audit.drop_complete_once, "completed Relay envelope was not dropped")
        row = await wait_row(
            store, restart_task_id,
            lambda value: bool(value.get("backend_run_id")) and value.get("poll_attempts", 0) >= 1,
            timeout=10.0, description="persisted Run ID after dropped envelope",
        )
        require(row.get("status") == "running", "task did not remain recoverable after the dropped envelope")
        CHECKS["dropped_completed_envelope"] = "PASS"

        PHASE = "relay-restart"
        get_count_before_restart = audit.get_calls
        await dispatcher.stop()
        dispatcher = None
        server.close()
        await server.wait_closed()
        server = None
        providers.auto_modes = []
        handler2, dispatcher2, store2 = start_runtime(environ, providers, conversation_counter)
        await dispatcher2.start()
        server2 = await serve(
            adapt_device_handler(handler2, hardware_mode=True),
            "127.0.0.1",
            0,
            ssl=build_server_context({DEVICE_IDENTITY: DEVICE_PSK}),
            max_size=64 * 1024,
            ping_interval=None,
        )
        completed = await wait_row(
            store2, restart_task_id,
            lambda value: value.get("status") == "completed",
            timeout=60.0, description="GET-only completion after Relay restart",
        )
        require(completed.get("backend_run_id"), "restart lost the persisted Hermes Run ID")
        require(audit.get_calls > get_count_before_restart, "restart did not resume with a GET")
        require(
            probe_wrong_owner_run_access(completed["backend_run_id"]),
            "Facade exposed a Run to a different owner scope",
        )
        require(audit.relay_submit_calls.get(restart_task_id) == 2, "duplicate facade submission count was unexpected")
        require(audit.duplicate_verified, "Facade did not return the same Run ID for duplicate submit")
        CHECKS["persisted_run_get_only_restart"] = "PASS"
        CHECKS["facade_run_owner_scope_isolated"] = "PASS"
        CHECKS["duplicate_submit_same_run_no_second_upstream_post"] = "PASS"

        PHASE = "inbox-readback"
        inbox_ws, inbox_provider = await open_turn(server2, providers.instances)
        inbox = await invoke_tool(
            inbox_provider, "b2-inbox-readback", "check_task_status",
            {"task_id": restart_task_id, "speak": False},
        )
        require(inbox.get("status") == "completed", "Relay Inbox did not read the completed task")
        require(isinstance(inbox.get("speak"), str) and isinstance(inbox.get("card"), dict), "Inbox omitted bounded result fields")
        require(f"POC_REPORT_SECRET_{RUN_TAG}_" not in json.dumps(inbox), "Inbox exposed the full report")
        await finish_synthetic_turn(inbox_ws, inbox_provider)
        await inbox_ws.close()
        restart_result_bytes = result_is_bounded(store2, restart_task_id)
        CHECKS["inbox_readback_bounded_result"] = "PASS"

        PHASE = "thirty-k-report"
        report_ws, report_provider = await open_turn(server2, providers.instances)
        report_accept = await invoke_tool(
            report_provider, "b2-report-30k", "delegate_task",
            {"task": "[POC_CASE=success_30k] Produce a 30,000-byte synthetic report.", "task_kind": "research"},
        )
        require(report_accept.get("status") == "accepted", "30K report task was not accepted")
        report_task_id = report_accept["task_id"]
        await finish_synthetic_turn(report_ws, report_provider)
        await report_ws.close()
        await wait_row(store2, report_task_id, lambda value: value.get("status") == "completed",
                       timeout=60.0, description="30K report bounded result")
        report_result_bytes = result_is_bounded(store2, report_task_id)
        CHECKS["thirty_k_report_bounded_relay_result"] = "PASS"

        PHASE = "stop-run"
        stop_ws, stop_provider = await open_turn(server2, providers.instances)
        stop_accept = await invoke_tool(
            stop_provider, "b2-stop-delegate", "delegate_task",
            {"task": "[POC_CASE=stop_10k] Start a synthetic Run that can be stopped.", "task_kind": "research"},
        )
        require(stop_accept.get("status") == "accepted", "stop scenario was not admitted")
        stop_task_id = stop_accept["task_id"]
        await wait_row(store2, stop_task_id,
                       lambda value: bool(value.get("backend_run_id")) and value.get("status") == "running",
                       timeout=20.0, description="Run ID before stop request")
        await stop_ws.close()
        stop_request_ws, stop_request_provider = await open_turn(server2, providers.instances)
        await eventually(
            lambda: (EVIDENCE_PATH.parent / "stop_10k.active").exists(),
            timeout=20.0,
            description="fake model request active before cancellation",
        )
        # Let the current executor poll cycle yield before testing persisted stop intent.
        await asyncio.sleep(STOP_POLL_CYCLE_WAIT_SECONDS)
        require(
            (EVIDENCE_PATH.parent / "stop_10k.active").exists(),
            "fake model request completed before cancellation was requested",
        )
        CHECKS["stop_fake_model_request_active"] = "PASS"
        pre_cancel_row = store2.get_by_task_id(stop_task_id)
        CHECKS["stop_pre_cancel_status"] = diagnostic_token(
            pre_cancel_row.get("status") if pre_cancel_row else None
        )
        CHECKS["stop_pre_cancel_dispatch_state"] = diagnostic_token(
            pre_cancel_row.get("dispatch_state") if pre_cancel_row else None
        )
        require(
            pre_cancel_row is not None
            and bool(pre_cancel_row.get("backend_run_id"))
            and pre_cancel_row.get("status") == "running",
            "stop test Run was not active before cancellation: " + CHECKS["stop_pre_cancel_status"],
        )
        stop_receipt = await invoke_tool(
            stop_request_provider, "b2-stop-request", "cancel_task", {"task_id": stop_task_id}
        )
        CHECKS["stop_cancel_receipt_status"] = diagnostic_token(stop_receipt.get("status"))
        CHECKS["stop_cancel_receipt_code"] = diagnostic_token(stop_receipt.get("code"))
        require(
            stop_receipt.get("status") == "stopping",
            "Relay did not record stop intent for a known Run ID (receipt "
            + CHECKS["stop_cancel_receipt_status"]
            + ", code " + CHECKS["stop_cancel_receipt_code"] + ")",
        )
        await finish_synthetic_turn(stop_request_ws, stop_request_provider)
        await stop_request_ws.close()

        def stop_dispatch_or_terminal():
            if audit.stop_calls >= 1:
                return "stop_post"
            row = store2.get_by_task_id(stop_task_id)
            if row and row.get("status") in {"completed", "failed", "uncertain"}:
                CHECKS["stop_terminal_status"] = diagnostic_token(row.get("status"))
                CHECKS["stop_terminal_error_code"] = diagnostic_token(row.get("last_error"))
                CHECKS["stop_terminal_dispatch_state"] = diagnostic_token(row.get("dispatch_state"))
                CHECKS["stop_terminal_stop_attempts"] = diagnostic_token(str(row.get("stop_attempts")))
                CHECKS["stop_terminal_poll_attempts"] = diagnostic_token(str(row.get("poll_attempts")))
                return CHECKS["stop_terminal_status"]
            return None

        stop_dispatch_observation = await eventually(
            stop_dispatch_or_terminal,
            timeout=STOP_DISPATCH_TIMEOUT_SECONDS,
            description="Hermes stop POST or terminal state",
        )
        require(
            audit.stop_calls >= 1,
            "Relay did not send a Hermes stop POST before terminal state "
            + diagnostic_token(stop_dispatch_observation)
            + " (cancel code " + CHECKS["stop_cancel_receipt_code"] + ")",
        )
        await eventually(
            lambda: audit.stop_post_completed,
            timeout=10.0,
            description="Hermes stop POST response",
        )
        CHECKS["stop_hermes_stop_http_status"] = diagnostic_token(
            str(audit.stop_post_http_status)
        )
        require(audit.stop_post_accepted, "Hermes did not accept the stop POST")
        stop_row = await wait_row(
            store2, stop_task_id,
            lambda value: value.get("status") in {"completed", "failed", "uncertain"},
            timeout=30.0, description="stop terminal or uncertain state",
        )
        stop_terminal_status = stop_row.get("status")
        stop_terminal_error_code = stop_row.get("last_error")
        stop_visible = store2.lookup_inbox(client_id=CLIENT_ID, task_id=stop_task_id)
        stop_visible_status = stop_visible.get("status") if stop_visible else None
        stop_visible_code = stop_visible.get("code") if stop_visible else None
        CHECKS["stop_terminal_status"] = diagnostic_token(stop_terminal_status)
        CHECKS["stop_terminal_error_code"] = diagnostic_token(stop_terminal_error_code)
        CHECKS["stop_hermes_status"] = diagnostic_token(audit.stop_remote_status)
        CHECKS["stop_hermes_error_category"] = diagnostic_token(audit.stop_remote_error_category)
        CHECKS["stop_hermes_exit_category"] = diagnostic_token(audit.stop_remote_exit_category)
        CHECKS["stop_hermes_completed"] = diagnostic_token(audit.stop_remote_completed)
        CHECKS["stop_hermes_interrupted"] = diagnostic_token(audit.stop_remote_interrupted)
        CHECKS["stop_hermes_partial"] = diagnostic_token(audit.stop_remote_partial)
        CHECKS["stop_remote_terminal_after_accepted_stop"] = (
            "PASS" if audit.stop_remote_terminal_after_accepted_stop else "FAIL"
        )
        require(
            audit.stop_remote_terminal_after_accepted_stop,
            "a terminal Hermes status was not observed after the accepted stop request",
        )
        CHECKS["stop_fixture_release_signaled_after_stopping_observed"] = (
            "PASS" if audit.stop_provider_release_signaled else "FAIL"
        )
        require(
            audit.stop_provider_release_signaled,
            "Hermes status never confirmed stopping/cancellation before the fake-fixture deadline",
        )
        CHECKS["stop_visible_status"] = diagnostic_token(stop_visible_status)
        CHECKS["stop_visible_code"] = diagnostic_token(stop_visible_code)
        CHECKS["stop_hermes_stop_post_count"] = str(audit.stop_calls)
        CHECKS["stop_targeted_hermes_stop_post_count"] = str(audit.stop_targeted_posts)
        CHECKS["stop_unexpected_hermes_stop_post_count"] = str(audit.stop_unexpected_posts)
        require(
            audit.stop_calls == 1
            and audit.stop_targeted_posts == 1
            and audit.stop_unexpected_posts == 0
            and audit.stop_accepted_posts == 1,
            "Relay did not issue exactly one accepted STOP request for the expected Run",
        )
        stop_submit_attempts = audit.relay_submit_calls.get(stop_task_id, 0)
        CHECKS["stop_relay_submit_attempts"] = str(stop_submit_attempts)
        require(stop_submit_attempts == 1, "stop task was replayed or submitted more than once")
        stop_outcome = classify_stop_outcome(
            remote_status=audit.stop_remote_status,
            remote_error_category=audit.stop_remote_error_category,
            remote_interrupted=audit.stop_remote_interrupted,
            ledger_status=stop_terminal_status,
            ledger_code=stop_terminal_error_code,
            visible_status=stop_visible_status,
            visible_code=stop_visible_code,
        )
        CHECKS["stop_terminal_outcome"] = stop_outcome or "unclassified"
        CHECKS["stop_cancel_confirmed"] = "true" if stop_outcome == "cancelled" else "false"
        require(
            stop_outcome is not None,
            "Hermes stop result was incomplete, unrelated, or not honestly represented by Relay "
            "(remote " + CHECKS["stop_hermes_status"] + "/" + CHECKS["stop_hermes_error_category"]
            + ", Relay " + CHECKS["stop_terminal_status"] + "/" + CHECKS["stop_terminal_error_code"]
            + ", owner " + CHECKS["stop_visible_status"] + "/" + CHECKS["stop_visible_code"] + ")",
        )
        CHECKS["stop_handling"] = "PASS"

        PHASE = "ambiguous-submit"
        ambiguous_ws, ambiguous_provider = await open_turn(server2, providers.instances)
        ambiguous_accept = await invoke_tool(
            ambiguous_provider, "b2-ambiguous-delegate", "delegate_task",
            {"task": "[POC_CASE=ambiguous_10k] Simulate accepted POST with response loss.", "task_kind": "research"},
        )
        require(ambiguous_accept.get("status") == "accepted", "ambiguous scenario was not admitted")
        ambiguous_task_id = ambiguous_accept["task_id"]
        await finish_synthetic_turn(ambiguous_ws, ambiguous_provider)
        await ambiguous_ws.close()
        uncertain = await wait_row(
            store2, ambiguous_task_id,
            lambda value: value.get("status") == "uncertain",
            timeout=20.0, description="uncertain ambiguous submission",
        )
        require(uncertain.get("backend_run_id") is None, "ambiguous submission invented a Run ID")
        require(audit.ambiguous_task_id == ambiguous_task_id, "response loss did not target the ambiguous task")
        attempts_before_restart = audit.relay_submit_calls.get(ambiguous_task_id, 0)
        require(attempts_before_restart == 1, "ambiguous submission was retried before restart")
        await dispatcher2.stop()
        dispatcher2 = None
        server2.close()
        await server2.wait_closed()
        server2 = None
        handler3, dispatcher3, store3 = start_runtime(environ, providers, conversation_counter)
        await dispatcher3.start()
        await asyncio.sleep(0.5)
        recovered_uncertain = store3.get_by_task_id(ambiguous_task_id)
        require(recovered_uncertain and recovered_uncertain.get("status") == "uncertain",
                "restart changed the ambiguous submission state")
        require(audit.relay_submit_calls.get(ambiguous_task_id, 0) == attempts_before_restart,
                "Relay replayed an uncertain submission after restart")
        CHECKS["ambiguous_run_stays_uncertain_without_replay"] = "PASS"

        PHASE = "final-evidence"
        markers = (
            f"POC_REPORT_SECRET_{RUN_TAG}_",
            f"POC_FINAL_SECRET_{RUN_TAG}_",
            f"POC_TOOL_SECRET_{RUN_TAG}_",
        )
        wire_text = b"".join(
            item if isinstance(item, bytes) else item.encode("utf-8")
            for item in WIRE
        )
        require(not any(marker.encode("utf-8") in wire_text for marker in markers),
                "synthetic full-report marker reached the Relay-facing WSS wire")
        require(audit.relay_submit_calls.get(ambiguous_task_id, 0) == attempts_before_restart,
                "uncertain task was replayed")
        CHECKS["full_report_markers_absent_from_relay_wire"] = "PASS"
        CHECKS["restart_task_status"] = "PASS" if completed.get("status") == "completed" else "FAIL"
        CHECKS["stop_state"] = "PASS" if CHECKS.get("stop_handling") == "PASS" else "FAIL"
        CHECKS["ambiguous_state"] = "PASS" if uncertain.get("status") == "uncertain" else "FAIL"

        evidence = {
            "schema": "amusant.issue-85.pr-b2.relay-e2e.v1",
            "status": "PASS",
            "hermes_image": "docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7",
            "hermes_platform": "linux/arm64",
            "checks": CHECKS,
            "metrics": {
                "relay_facade_submit_calls": sum(audit.relay_submit_calls.values()),
                "restart_duplicate_relay_submit_calls": audit.relay_submit_calls.get(restart_task_id, 0),
                "relay_get_calls": audit.get_calls,
                "relay_stop_calls": audit.stop_calls,
                "restart_bounded_result_bytes": restart_result_bytes,
                "thirty_k_bounded_result_bytes": report_result_bytes,
            },
        }
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    finally:
        if dispatcher3 is not None:
            await dispatcher3.stop()
        if server3 is not None:
            server3.close()
            await server3.wait_closed()
        if dispatcher2 is not None:
            await dispatcher2.stop()
        if server2 is not None:
            server2.close()
            await server2.wait_closed()
        if dispatcher is not None:
            await dispatcher.stop()
        if server is not None:
            server.close()
            await server.wait_closed()
        request_patcher.stop()
        phase_patcher.stop()


def _current_live_factory(pool, *_args, **_kwargs):
    auto = pool.auto_modes.pop(0) if pool.auto_modes else False
    provider = FakeGeminiLive(auto_complete=auto)
    pool.instances.append(provider)
    return provider


def main() -> int:
    try:
        asyncio.run(run())
        return 0
    except Exception as exc:
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema": "amusant.issue-85.pr-b2.relay-e2e.v1",
            "status": "FAIL",
            "phase": PHASE,
            "failure_type": type(exc).__name__,
            "checks": CHECKS,
        }
        frames = traceback.extract_tb(exc.__traceback__)
        if frames and Path(frames[-1].filename).resolve() == Path(__file__).resolve():
            failure["failure_location"] = {
                "function": frames[-1].name,
                "line": frames[-1].lineno,
            }
            if isinstance(exc, AssertionError):
                # All assertions in this synthetic runner use fixed, non-payload messages.
                failure["failure_reason"] = str(exc)[:160]
        EVIDENCE_PATH.write_text(json.dumps(failure, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
