Warning: truncated output (original token count: 27765)
Total output lines: 2154

import asyncio
import json
import secrets
import signal
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

from fast_voice.contracts import AudioFrame, ProviderCapabilities, ProviderError, ToolCallEvent, TranscriptEvent, TurnCompleteEvent
from relay.application import (
    build_relay_handler,
    build_supported_relay_handler,
    build_supported_relay_runtime,
    load_relay_process_config,
)
from relay.dispatcher import AsyncDispatcher, ExecutionOutcome
from relay.device_opus import DeviceProtocolError
from relay.device_protocol import DeviceWebSocketAdapter, adapt_device_handler
from relay.live_bridge import (
    MIN_UTTERANCE_BYTES,
    install_shutdown_handlers,
    make_handler,
    remove_shutdown_handlers,
    run as run_live_bridge,
)
from relay.slow_path import CANCEL_TOOL_NAME, INBOX_TOOL_NAME, TOOL_NAME, SlowPathToolRouter
from relay.task_store import BoundedTaskResult, SQLiteTaskStore, TaskAdmissionRequest

TOKEN = secrets.token_urlsafe(24)


class FakeProvider:
    provider_id = "gemini"
    capabilities = ProviderCapabilities(True, True, True, True, False, True)

    def __init__(self, audio_outputs=None, transcripts=None, auto_complete=True):
        self.audio_outputs = list(audio_outputs or [bytes([1, 0]) * 6000])
        self.transcripts = list(transcripts or ["Ready."])
        self.auto_complete = auto_complete
        self.events = []
        self.callbacks = {}
        self.connected = False
        self.closed = False
        self.close_calls = 0
        self.connect_calls = 0
        self.end_turn_calls = 0
        self.audio_input = bytearray()
        self.tool_results = []
        self.injected_text = []

    def on_audio(self, callback): self.callbacks["audio"] = callback
    def on_transcript(self, callback): self.callbacks["transcript"] = callback
    def on_tool_call(self, callback): self.callbacks["tool"] = callback
    def on_tool_calls_complete(self, callback): self.callbacks["tool_batch_complete"] = callback
    def on_interrupted(self, callback): self.callbacks["interrupted"] = callback
    def on_turn_complete(self, callback): self.callbacks["complete"] = callback
    def on_error(self, callback): self.callbacks["error"] = callback

    async def connect(self):
        self.connected = True
        self.closed = False
        self.connect_calls += 1
        self.events.append("connect")

    async def close(self):
        self.connected = False
        self.closed = True
        self.close_calls += 1
        self.events.append("close")

    async def start_turn(self): self.events.append("start_turn")

    async def send_audio(self, frame: AudioFrame):
        self.events.append(("send_audio", frame.sample_rate_hz))
        self.audio_input.extend(frame.data)

    async def end_turn(self):
        self.events.append("end_turn")
        self.end_turn_calls += 1
        if self.auto_complete:
            await self.emit_response()

    async def emit_response(self, callbacks=None, index=None):
        callbacks = callbacks or self.callbacks
        index = self.end_turn_calls - 1 if index is None else index
        audio = self.audio_outputs[min(index, len(self.audio_outputs) - 1)]
        transcript = self.transcripts[min(index, len(self.transcripts) - 1)]
        await callbacks["audio"](AudioFrame(audio, 24000))
        await callbacks["transcript"](TranscriptEvent(transcript, "output", True))
        await callbacks["complete"](TurnCompleteEvent(cancelled=False))

    async def send_tool_result(self, call_id, result): self.tool_results.append((call_id, dict(result)))
    async def cancel_response(self): raise NotImplementedError
    async def inject_text(self, text): self.injected_text.append(text)


class FakeSchemaTypes:
    class Behavior:
        BLOCKING = "BLOCKING"
    class Schema:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class FunctionDeclaration:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Tool:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)


class BufferingFakeDeviceCodec:
    """Retain a partial encoded frame until terminal flush, without PyAV."""
    def __init__(self): self.pending = bytearray()
    def decode_uplink(self, message):
        if message != b"OPUS-IN": raise DeviceProtocolError("invalid_opus_packet")
        return bytes(MIN_UTTERANCE_BYTES)
    def encode_downlink(self, pcm):
        self.pending.extend(pcm)
        return []
    def finish_downlink(self):
        if not self.pending: return []
        self.pending.clear()
        return [b"OPUS-FINAL"]
    def discard_downlink(self): self.pending.clear()
    def reset_uplink(self): pass
    def reset_turn(self): self.pending.clear()


class FailingFlushFakeDeviceCodec(BufferingFakeDeviceCodec):
    def finish_downlink(self):
        raise DeviceProtocolError("opus_encoder_flush_failed")


class ImmediateFakeDeviceCodec(BufferingFakeDeviceCodec):
    def __init__(self):
        super().__init__()
        self.generation = 0
    def reset_uplink(self): pass
    def encode_downlink(self, _pcm): return [f"PACKET-{self.generation}".encode()]
    def finish_downlink(self): return []
    def discard_downlink(self):
        super().discard_downlink()
        self.generation += 1


class LiveBridgeTests(unittest.IsolatedAsyncioTestCase):
    def test_shutdown_handlers_close_listener_and_are_removed(self):
        class FakeEvent:
            def __init__(self):
                self.set_calls = 0

            def set(self):
                self.set_calls += 1

        class FakeLoop:
            def __init__(self):
                self.handlers = {}
                self.removed = []

            def add_signal_handler(self, signum, callback):
                self.handlers[signum] = callback

            def remove_signal_handler(self, signum):
                self.removed.append(signum)
                return self.handlers.pop(signum, None) is not None

        stop_event = FakeEvent()
        loop = FakeLoop()
        installed = install_shutdown_handlers(stop_event, loop)
        self.assertEqual(set(installed), {signal.SIGINT, signal.SIGTERM})
        loop.handlers[signal.SIGTERM]()
        self.assertEqual(stop_event.set_calls, 1)
        remove_shutdown_handlers(loop, installed)
        self.assertEqual(set(loop.removed), {signal.SIGINT, signal.SIGTERM})

    async def _open(self, factory, max_size=64 * 1024, **handler_kwargs):
        server = await serve(make_handler(TOKEN, factory, **handler_kwargs), "127.0.0.1", 0, max_size=max_size)
        port = server.sockets[0].getsockname()[1]
        ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
        return server, ws

    async def _controls_until(self, ws, done):
        controls = []
        audio_bytes = 0
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            if isinstance(msg, bytes):
                audio_bytes += len(msg)
                continue
            item = json.loads(msg)
            controls.append(item)
            if done(item, controls):
                return controls, audio_bytes

    async def _send_turn(self, ws, audio=None):
        await ws.send(json.dumps({"type": "ptt_start"}))
        if audio is None:
            audio = bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2)
        await ws.send(audio)
        await ws.send(json.dumps({"type": "ptt_end"}))

    async def test_ptt_pcm_input_streams_to_fake_live_and_audio_returns(self):
        fake = FakeProvider()
        server, ws = await self._open(lambda: fake)
        try:
            await ws.send(json.dumps({"type": "hello", "device": "test-sim"}))
            await self._send_turn(ws)
            controls, returned_audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
            self.assertTrue(fake.connected)
            self.assertEqual(len(fake.audio_input), MIN_UTTERANCE_BYTES)
            self.assertIn("start_turn", fake.events)
            self.assertIn("end_turn", fake.events)
            self.assertEqual(returned_audio_bytes, 12000)
            self.assertTrue(any(item.get("type") == "caption" and item.get("delta") == "Ready." for item in controls))
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertTrue(fake.closed)

    async def test_live_tool_call_commits_durable_admission_before_same_call_id_receipt(self):
        fake = FakeProvider(auto_complete=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteTaskStore(database_path=Path(temp_dir) / "tasks.sqlite3", max_pending_tasks=8)
            router = SlowPathToolRouter(
                store=store,
                client_id="authenticated-device-1",
                conversation_id="kai-session-1",
                backend_id="openab",
                default_task_kind="research",
                max_task_ttl_seconds=600,
                acceptance_timeout_seconds=2,
                new_task_id=lambda: "4db5aabc-284a-4a01-8a6d-5cddcc7934f1",
            )
            server = await serve(
                make_handler(TOKEN, lambda: fake, slow_path_tool_handler=router),
                "127.0.0.1", 0, max_size=64 * 1024,
            )
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            try:
                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
                event = ToolCallEvent("call-slow-1", "delegate_task", {"task": "Compare current options", "task_kind": "compare"})
                await fake.callbacks["tool"](event)
                # Gemini may finish the function-call phase in a separate event.
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(), timeout=0.05)
                await fake.emit_response()
                controls, returned_audio_bytes = await self._controls_until(
                    ws, lambda item, _items: item.get("type") == "turn_complete"
                )
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()
            self.assertEqual(fake.tool_results, [("call-slow-1", {"status": "accepted", "task_id": "4db5aabc-284a-4a01-8a6d-5cddcc7934f1", "duplicate": False})])
            row = store.get_by_call(client_id="authenticated-device-1", conversation_id="kai-session-1", turn_id=1, call_id="call-slow-1")
            self.assertEqual(row["task_kind"], "compare")
            self.assertEqual(row["backend"], "openab")
            self.assertEqual(row["dispatch_state"], "pending")
            self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)
            self.assertEqual(returned_audio_bytes, 12_000)
            self.assertTrue(any(item.get("type") == "caption" for item in controls))

    async def test_busy_and_cancel_status_cross_real_fast_voice_tool_boundary(self):
        fake = FakeProvider(
            auto_complete=False,
            transcripts=[
                "I am still working on the previous task. I can check it or cancel it before starting another.",
                "Cancellation was requested. I will wait for Hermes to confirm it stopped.",
                "The task is still stopping; it remains busy until confirmation.",
            ],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteTaskStore(database_path=Path(temp_dir) / "single-flight.sqlite3", max_pending_tasks=8)
            owner = "authenticated-device-1"
            conversation = "kai-session-single-flight"
            created = datetime.now(timezone.utc)
            active_id = "4db5aabc-284a-4a01-8a6d-5cddcc7934f1"
            store.admit(TaskAdmissionRequest(
                task_id=active_id,
                client_id=owner,
                conversation_id=conversation,
                turn_id=1,
                call_id="existing-active-call",
                task="Existing research request",
                task_kind="research",
                backend="server-selected",
                created_at=created,
                expires_at=created + timedelta(minutes=10),
                executor_kind="hermes_runs",
            ))
            store.claim_due(now=created, lease_seconds=30, limit=1)
            store.mark_dispatch_started(active_id, now=created)
            store.save_backend_run_id(
                active_id,
                "run-active-voice-boundary",
                run_started_at=created,
                run_deadline_at=created + timedelta(minutes=5),
                next_poll_at=created + timedelta(seconds=5),
            )
            router = SlowPathToolRouter(
                store=store,
                client_id=owner,
                conversation_id=conversation,
                backend_id="server-selected",
                default_task_kind="research",
                max_task_ttl_seconds=600,
                acceptance_timeout_seconds=2,
                new_task_id=lambda: "f8f3c5a2-4f97-4c91-ae62-6b0d3f84ea28",
            )
            server = await serve(
                make_handler(TOKEN, lambda: fake, slow_path_tool_handler=router),
                "127.0.0.1", 0, max_size=64 * 1024,
            )
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            try:
                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
                await fake.callbacks["tool"](ToolCallEvent("busy-call", TOOL_NAME, {"task": "New research request"}))
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                busy_result = fake.tool_results[-1][1]
                self.assertEqual(busy_result["status"], "busy")
                self.assertEqual(busy_result["code"], "slow_busy")
                self.assertEqual(busy_result["active_task_id"], active_id)
                await fake.emit_response()
                controls, audio = await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
                self.assertEqual(audio, 12_000)
                self.assertTrue(any(item.get("type") == "caption" and "previous task" in item.get("delta", "") for item in controls))
                self.assertIsNone(store.get_by_call(client_id=owner, conversation_id=conversation, turn_id=2, call_id="busy-call"))

                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
                await fake.callbacks["tool"](ToolCallEvent("cancel-call", CANCEL_TOOL_NAME, {"task_id": active_id}))
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                self.assertEqual(fake.tool_results[-1][1]["status"], "stopping")
                await fake.emit_response()
                await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
                self.assertEqual(store.lookup_inbox(client_id=owner, task_id=active_id)["status"], "stopping")

                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
                await fake.callbacks["tool"](ToolCallEvent("lookup-call", INBOX_TOOL_NAME, {"task_id": active_id}))
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                self.assertEqual(fake.tool_results[-1][1]["status"], "stopping")
                await fake.emit_response()
                controls, audio = await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
                self.assertEqual(audio, 12_000)
                self.assertTrue(any(item.get("type") == "caption" and "still stopping" in item.get("delta", "") for item in controls))
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()

    async def test_empty_post_tool_followup_uses_bounded_honest_ack_card(self):
        fake = FakeProvider(auto_complete=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteTaskStore(database_path=Path(temp_dir) / "empty-followup.sqlite3", max_pending_tasks=8)
            router = SlowPathToolRouter(
                store=store,
                client_id="authenticated-device-1",
                conversation_id="kai-session-empty-followup",
                backend_id="openab",
                default_task_kind="research",
                max_task_ttl_seconds=600,
                acceptance_timeout_seconds=2,
            )
            server = await serve(
                make_handler(TOKEN, lambda: fake, slow_path_tool_handler=router, post_tool_ack_timeout_seconds=0.05),
                "127.0.0.1", 0, max_size=64 * 1024,
            )
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            try:
                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
                await fake.callbacks["tool"](ToolCallEvent("call-empty-1", "delegate_task", {"task": "Research this"}))
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                controls, audio_bytes = await self._controls_until(
                    ws, lambda item, _items: item.get("type") == "turn_complete"
                )
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()
            self.assertEqual(fake.tool_results[0][0], "call-empty-1")
            self.assertEqual(fake.tool_results[0][1]["status"], "accepted")
            self.assertEqual(audio_bytes, 0)
            cards = [item for item in controls if item.get("type") == "card"]
            self.assertEqual(cards[0]["title"], "Task accepted")
            self.assertIn("durably queued", " ".join(cards[0]["lines"]))
            self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)

    async def test_partial_post_tool_audio_has_bounded_completion(self):
        fake = FakeProvider(auto_complete=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteTaskStore(database_path=Path(temp_dir) / "partial-followup.sqlite3", max_pending_tasks=8)
            router = SlowPathToolRouter(
                store=store,
                client_id="authenticated-device-1",
                conversation_id="kai-session-partial-followup",
                backend_id="openab",
                default_task_kind="research",
                max_task_ttl_seconds=600,
                acceptance_timeout_seconds=2,
            )
            server = await serve(
                make_handler(TOKEN, lambda: fake, slow_path_tool_handler=router, post_tool_ack_timeout_seconds=0.05),
                "127.0.0.1", 0, max_size=64 * 1024,
            )
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            try:
                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
                await fake.callbacks["tool"](ToolCallEvent("call-partial-1", "delegate_task", {"task": "Research this"}))
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                await fake.callbacks["audio"](AudioFrame(bytes([1, 0]) * 6000, 24000))
                await fake.callbacks["transcript"](TranscriptEvent("Task accepted.", "output", True))
                controls, audio_bytes = await self._controls_until(
                    ws, lambda item, _items: item.get("type") == "turn_complete"
                )
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()
            row = store.get_by_call(
                client_id="authenticated-device-1",
                conversation_id="kai-session-partial-followup",
                turn_id=1,
                call_id="call-partial-1",
            )
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "queued")
            self.assertEqual(audio_bytes, 12_000)
            self.assertTrue(any(item.get("type") == "caption" for item in controls))
            cards = [item for item in controls if item.get("type") == "card"]
            self.assertEqual(cards[0]["title"], "Voice response incomplete")
            self.assertNotIn("accepted", " ".join(cards[0]["lines"]).lower())

    async def test_opus_adapter_flushes_before_local_post_tool_timeout_marker(self):
        fake = FakeProvider(auto_complete=False)
        dispatches = []

        async def slow_handler(_event, _turn_id):
            dispatches.append("accepted")
            return {"status": "accepted", "task_id": "offline-task"}

        base = make_handler(
            TOKEN,
            lambda: fake,
            slow_path_tool_handler=slow_handler,
            post_tool_ack_timeout_seconds=0.05,
            post_tool_ack_max_duration_seconds=0.2,
        )
        handler = adapt_device_handler(base, codec_factory=BufferingFakeDeviceCodec)
        server = await serve(handler, "127.0.0.1", 0, max_size=64 * 1024)
        port = server.sockets[0].getsockname()[1]
        ws = await connect(
            f"ws://127.0.0.1:{port}/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        )
        try:
            await ws.send(json.dumps({"type": "hello", "codecs": ["opus"]}))
            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(b"OPUS-IN")
            await ws.send(json.dumps({"type": "ptt_end"}))
            await self._controls_until(
                ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking"
            )
            await fake.callbacks["tool"](
                ToolCallEvent("call-opus-timeout", "delegate_task", {"task": "Research this"})
            )
            # This provider turn_complete is internal to the tool phase, not the device terminal marker.
            await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
            await fake.callbacks["audio…15765 tokens truncated…ask_id):
                        spoken_calls.append(task_id)
                        return True
                tool_provider = FakeProvider(auto_complete=False)
                spoken_provider = FakeProvider(auto_complete=False)
                server, ws = await self._open(
                    lambda: tool_provider,
                    slow_path_tool_handler=Handler(),
                    spoken_provider_factory=lambda: spoken_provider,
                    post_tool_ack_timeout_seconds=0.05,
                    post_tool_ack_max_duration_seconds=0.05,
                )
                try:
                    await self._send_turn(ws)
                    await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
                    await tool_provider.callbacks["tool"](
                        ToolCallEvent("lookup-stall", INBOX_TOOL_NAME, {"speak": True})
                    )
                    await tool_provider.callbacks["tool_batch_complete"]()
                    if emit_partial_audio:
                        await spoken_provider.callbacks["audio"](
                            AudioFrame(bytes([1, 0]) * 3000, 24000)
                        )
                    else:
                        await spoken_provider.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                    controls, _audio_bytes = await self._controls_until(
                        ws, lambda item, _items: item.get("type") == "turn_complete"
                    )
                    self.assertTrue(any(item.get("title") == "Saved result available" for item in controls))
                    self.assertEqual(spoken_calls, [])
                finally:
                    await ws.close()
                    server.close()
                    await server.wait_closed()


    async def test_supported_application_creates_tool_free_spoken_provider(self):
        task_id = "4db5aabc-284a-4a01-8a6d-5cddcc7934f1"
        tool_provider = FakeProvider(auto_complete=False)
        spoken_provider = FakeProvider(auto_complete=False)
        provider_kwargs = []
        providers = [tool_provider, spoken_provider]
        class Store:
            def __init__(self): self.marked = []
            def lookup_inbox(self, *, client_id, task_id=None, speak=False, now=None):
                return {
                    "status": "completed", "task_id": task_id or "4db5aabc-284a-4a01-8a6d-5cddcc7934f1",
                    "task_kind": "research", "delivery_state": "read",
                    "speak": "Saved answer.", "card": {}, "details_md": "detail",
                    "speak_now": speak,
                }
            def mark_inbox_spoken(self, *, client_id, task_id, now=None):
                self.marked.append((client_id, task_id))
                return True
        store = Store()
        def fake_provider_factory(_environ, **kwargs):
            provider_kwargs.append(kwargs)
            return providers.pop(0)
        with patch("relay.application.build_live_provider", side_effect=fake_provider_factory):
            handler = build_relay_handler(
                expected_device_token=TOKEN,
                authenticated_client_id="authenticated-device-1",
                task_store=store,
                provider_environ={
                    "AMUSANT_LIVE_PROVIDER": "gemini",
                    "GEMINI_API_KEY": "offline-test-key",
                    "AMUSANT_PERSONA_PROFILE": "jellii",
                    "AMUSANT_GEMINI_VOICE": "Zephyr",
                    "AMUSANT_GEMINI_GOOGLE_SEARCH": "1",
                },
                backend_id="openab",
                default_task_kind="research",
                max_task_ttl_seconds=600,
                acceptance_timeout_seconds=1,
                sdk_types=FakeSchemaTypes,
                conversation_id_factory=lambda: "app-inbox-session",
            )
            server = await serve(handler, "127.0.0.1", 0, max_size=64 * 1024)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            try:
                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
                await tool_provider.callbacks["tool"](
                    ToolCallEvent("lookup-app", INBOX_TOOL_NAME, {"task_id": task_id, "speak": True})
                )
                await tool_provider.callbacks["tool_batch_complete"]()
                self.assertIn("tool_declarations", provider_kwargs[0])
                self.assertNotIn("tool_declarations", provider_kwargs[1])
                self.assertIs(provider_kwargs[1]["allow_google_search"], False)
                # Jellii is consistent across the tool-enabled and tool-free sessions.
                self.assertIn("Jellii", provider_kwargs[0]["system_instruction"])
                self.assertEqual(
                    provider_kwargs[0]["system_instruction"],
                    provider_kwargs[1]["system_instruction"],
                )
                # A teardown/late error from the retired session cannot fail the new epoch.
                await tool_provider.callbacks["error"](
                    ProviderError("gemini", "late_old_session_error", "sanitized")
                )
                await spoken_provider.emit_response()
                await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
                self.assertEqual(store.marked, [("authenticated-device-1", task_id)])
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()


    async def test_lookup_exception_is_unknown_lookup_not_admission(self):
        fake = FakeProvider(auto_complete=False)
        async def broken_handler(_event, _turn_id):
            raise RuntimeError("sensitive lookup diagnostic")
        server, ws = await self._open(
            lambda: fake,
            slow_path_tool_handler=broken_handler,
            post_tool_ack_timeout_seconds=0.05,
        )
        try:
            await self._send_turn(ws)
            await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
            await fake.callbacks["tool"](
                ToolCallEvent("lookup-exception", INBOX_TOOL_NAME, {"speak": False})
            )
            await fake.callbacks["tool_batch_complete"]()
            await fake.emit_response()
            controls, audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(fake.tool_results, [("lookup-exception", {"status": "unknown", "code": "lookup_unconfirmed"})])
        self.assertGreater(audio_bytes, 0)
        self.assertFalse(any("Admission could not be confirmed" in str(item) for item in controls))
        self.assertNotIn("sensitive lookup diagnostic", json.dumps(fake.tool_results))


    async def test_old_provider_turn_complete_cannot_cancel_new_epoch_timeout(self):
        spoken_calls = []
        class Handler:
            async def __call__(self, _event, _turn_id):
                return {"status": "completed", "task_id": "4db5aabc-284a-4a01-8a6d-5cddcc7934f1", "speak": "Saved.", "speak_now": True}
            async def mark_spoken(self, task_id):
                spoken_calls.append(task_id)
                return True
        tool_provider = FakeProvider(auto_complete=False)
        spoken_provider = FakeProvider(auto_complete=False)
        server, ws = await self._open(
            lambda: tool_provider,
            slow_path_tool_handler=Handler(),
            spoken_provider_factory=lambda: spoken_provider,
            post_tool_ack_timeout_seconds=0.05,
            post_tool_ack_max_duration_seconds=0.1,
        )
        try:
            await self._send_turn(ws)
            await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
            await tool_provider.callbacks["tool"](ToolCallEvent("lookup-old-complete", INBOX_TOOL_NAME, {"speak": True}))
            await tool_provider.callbacks["tool_batch_complete"]()
            await tool_provider.callbacks["complete"](TurnCompleteEvent(cancelled=False))
            controls, _audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
            self.assertTrue(any(item.get("title") == "Saved result available" for item in controls))
            self.assertEqual(spoken_calls, [])
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()


    async def test_malformed_lookup_status_is_bounded_unknown(self):
        fake = FakeProvider(auto_complete=False)
        async def malformed(_event, _turn_id):
            return {"status": ["completed"], "speak": "untrusted"}
        server, ws = await self._open(lambda: fake, slow_path_tool_handler=malformed)
        try:
            await self._send_turn(ws)
            await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
            await fake.callbacks["tool"](ToolCallEvent("lookup-bad-status", INBOX_TOOL_NAME, {}))
            await fake.callbacks["tool_batch_complete"]()
            await fake.emit_response()
            controls, audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(fake.tool_results, [("lookup-bad-status", {"status": "unknown", "code": "invalid_tool_result"})])
        self.assertGreater(audio_bytes, 0)
        self.assertFalse(any(item.get("title") == "Task not accepted" for item in controls))


    async def test_late_retired_transcript_cannot_reset_tool_free_timeout(self):
        spoken_calls = []
        class Handler:
            async def __call__(self, _event, _turn_id):
                return {"status": "completed", "task_id": "4db5aabc-284a-4a01-8a6d-5cddcc7934f1", "speak": "Saved.", "speak_now": True}
            async def mark_spoken(self, task_id):
                spoken_calls.append(task_id)
                return True
        tool_provider = FakeProvider(auto_complete=False)
        spoken_provider = FakeProvider(auto_complete=False)
        server, ws = await self._open(
            lambda: tool_provider,
            slow_path_tool_handler=Handler(),
            spoken_provider_factory=lambda: spoken_provider,
            post_tool_ack_timeout_seconds=0.05,
            post_tool_ack_max_duration_seconds=0.1,
        )
        try:
            await self._send_turn(ws)
            await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
            await tool_provider.callbacks["tool"](
                ToolCallEvent("lookup-stale-caption", INBOX_TOOL_NAME, {"speak": True})
            )
            await tool_provider.callbacks["tool_batch_complete"]()
            # Simulate a transcript callback already queued by the retired epoch.
            await tool_provider.callbacks["transcript"](
                TranscriptEvent("late old-session caption", "output", True)
            )
            controls, _audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
            self.assertTrue(any(item.get("title") == "Saved result available" for item in controls))
            self.assertEqual(spoken_calls, [])
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()

    async def test_new_ptt_during_old_provider_close_uses_tool_enabled_provider(self):
        close_entered = asyncio.Event()
        release_close = asyncio.Event()
        routed = []
        class SignalingProvider(FakeProvider):
            def __init__(self):
                super().__init__(auto_complete=False)
                self.connected_event = asyncio.Event()
            async def connect(self):
                await super().connect()
                self.connected_event.set()
        tool_provider = FakeProvider(auto_complete=False)
        normal_provider = SignalingProvider()
        spoken_provider = FakeProvider(auto_complete=False)
        async def blocked_close():
            tool_provider.connected = False
            tool_provider.closed = True
            tool_provider.close_calls += 1
            close_entered.set()
            await release_close.wait()
        tool_provider.close = blocked_close
        provider_queue = [tool_provider, normal_provider]
        def next_tool_enabled_provider():
            return provider_queue.pop(0)
        async def route(event, _turn_id):
            routed.append(event.name)
            if event.name == INBOX_TOOL_NAME:
                return {"status": "completed", "task_id": "4db5aabc-284a-4a01-8a6d-5cddcc7934f1", "speak": "Saved.", "speak_now": True}
            return {"status": "accepted", "task_id": "new-task", "duplicate": False}
        server, ws = await self._open(
            next_tool_enabled_provider,
            slow_path_tool_handler=route,
            spoken_provider_factory=lambda: spoken_provider,
        )
        handoff = None
        try:
            await self._send_turn(ws)
            await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
            await tool_provider.callbacks["tool"](
                ToolCallEvent("lookup-close-race", INBOX_TOOL_NAME, {"speak": True})
            )
            handoff = asyncio.create_task(tool_provider.callbacks["tool_batch_complete"]())
            await asyncio.wait_for(close_entered.wait(), timeout=1)

            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
            await asyncio.wait_for(normal_provider.connected_event.wait(), timeout=1)
            await ws.send(json.dumps({"type": "ptt_end"}))

            release_close.set()
            await asyncio.wait_for(handoff, timeout=1)
            self.assertEqual(spoken_provider.connect_calls, 0)
            self.assertEqual(normal_provider.injected_text, [])
            await normal_provider.callbacks["tool"](
                ToolCallEvent("delegate-new-turn", "delegate_task", {"task": "new work"})
            )
            self.assertEqual(routed, [INBOX_TOOL_NAME, "delegate_task"])
            await normal_provider.emit_response()
            await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
        finally:
            release_close.set()
            if handoff is not None and not handoff.done():
                await asyncio.gather(handoff, return_exceptions=True)
            await ws.close()
            server.close()
            await server.wait_closed()

    async def test_new_ptt_during_spoken_provider_connect_discards_stale_session(self):
        connect_entered = asyncio.Event()
        release_connect = asyncio.Event()
        routed = []
        class SignalingProvider(FakeProvider):
            def __init__(self):
                super().__init__(auto_complete=False)
                self.connected_event = asyncio.Event()
            async def connect(self):
                await super().connect()
                self.connected_event.set()
        tool_provider = FakeProvider(auto_complete=False)
        normal_provider = SignalingProvider()
        spoken_provider = FakeProvider(auto_complete=False)
        async def blocked_spoken_connect():
            spoken_provider.connect_calls += 1
            connect_entered.set()
            await release_connect.wait()
            spoken_provider.connected = True
            spoken_provider.events.append("connect")
        spoken_provider.connect = blocked_spoken_connect
        provider_queue = [tool_provider, normal_provider]
        def next_tool_enabled_provider():
            return provider_queue.pop(0)
        async def route(event, _turn_id):
            routed.append(event.name)
            if event.name == INBOX_TOOL_NAME:
                return {"status": "completed", "task_id": "4db5aabc-284a-4a01-8a6d-5cddcc7934f1", "speak": "Saved.", "speak_now": True}
            return {"status": "accepted", "task_id": "new-task", "duplicate": False}
        server, ws = await self._open(
            next_tool_enabled_provider,
            slow_path_tool_handler=route,
            spoken_provider_factory=lambda: spoken_provider,
        )
        handoff = None
        try:
            await self._send_turn(ws)
            await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
            await tool_provider.callbacks["tool"](
                ToolCallEvent("lookup-connect-race", INBOX_TOOL_NAME, {"speak": True})
            )
            handoff = asyncio.create_task(tool_provider.callbacks["tool_batch_complete"]())
            await asyncio.wait_for(connect_entered.wait(), timeout=1)

            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
            await asyncio.wait_for(normal_provider.connected_event.wait(), timeout=1)
            await ws.send(json.dumps({"type": "ptt_end"}))

            release_connect.set()
            await asyncio.wait_for(handoff, timeout=1)
            self.assertFalse(spoken_provider.connected)
            self.assertEqual(spoken_provider.injected_text, [])
            self.assertEqual(normal_provider.injected_text, [])
            await normal_provider.callbacks["tool"](
                ToolCallEvent("delegate-after-connect-race", "delegate_task", {"task": "new work"})
            )
            self.assertEqual(routed, [INBOX_TOOL_NAME, "delegate_task"])
            await normal_provider.emit_response()
            await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
        finally:
            release_connect.set()
            if handoff is not None and not handoff.done():
                await asyncio.gather(handoff, return_exceptions=True)
            await ws.close()
            server.close()
            await server.wait_closed()


    async def test_supported_runtime_wakes_idle_dispatcher_after_durable_admission(self):
        fake = FakeProvider(auto_complete=False)
        class ObservedExecutor:
            def __init__(self):
                self.calls = []
                self.started = asyncio.Event()
            async def execute(self, task):
                self.calls.append(task.task)
                self.started.set()
                return ExecutionOutcome(status="completed")
        executor = ObservedExecutor()
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "relay.application.load_acp_executor", return_value=executor
        ), patch("relay.application.build_live_provider", return_value=fake):
            env = {
                "AMUSANT_LIVE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "offline-test-key",
                "AMUSANT_CLIENT_ID": "authenticated-device-1",
                "AMUSANT_BACKEND_ID": "openab",
                "AMUSANT_SLOW_EXECUTOR": "openab_acp",  # explicit legacy fixture
                "AMUSANT_TASK_DB": str(Path(temp_dir) / "dispatcher-wakeup.sqlite3"),
                "AMUSANT_DISPATCHER_POLL_INTERVAL_SECONDS": "30",
            }
            handler, dispatcher, store = build_supported_relay_runtime(
                expected_device_token=TOKEN,
                environ=env,
                sdk_types=FakeSchemaTypes,
                conversation_id_factory=lambda: "dispatcher-wakeup-session",
            )
            self.assertIsNotNone(dispatcher)
            self.assertEqual(dispatcher._poll_interval_seconds, 30.0)
            await dispatcher.start()
            server = await serve(handler, "127.0.0.1", 0, max_size=64 * 1024)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(
                f"ws://127.0.0.1:{port}/ws",
                additional_headers={"Authorization": f"Bearer {TOKEN}"},
            )
            try:
                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("state") == "thinking")
                await fake.callbacks["tool"](
                    ToolCallEvent("call-wakeup", "delegate_task", {"task": "Compare current options"})
                )
                await fake.callbacks["tool_batch_complete"]()
                # 30s fallback is configured; prompt wakeup must execute immediately.
                await asyncio.wait_for(executor.started.wait(), timeout=0.5)
                await fake.emit_response()
                await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
                for _ in range(50):
                    row = store.get_by_call(
                        client_id="authenticated-device-1",
                        conversation_id="dispatcher-wakeup-session",
                        turn_id=1,
                        call_id="call-wakeup",
                    )
                    if row is not None and row["status"] == "completed":
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(executor.calls, ["Compare current options"])
                self.assertEqual(row["status"], "completed")
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()
                await dispatcher.stop()


    def test_dispatcher_poll_config_has_backend_defaults_and_bounded_override(self):
        sqlite_env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "offline-test-key",
            "AMUSANT_CLIENT_ID": "authenticated-device-1",
            "AMUSANT_BACKEND_ID": "openab",
            "AMUSANT_TASK_DB": "/tmp/tasks.sqlite3",
        }
        sqlite_config = load_relay_process_config(sqlite_env, expected_device_token="device-token-12")
        self.assertEqual(sqlite_config.dispatcher_poll_interval_seconds, 1.0)
        dynamo_env = {
            **sqlite_env,
            "AMUSANT_TASK_STORE": "dynamodb",
            "AMUSANT_DDB_TABLE": "offline-table-name",
        }
        dynamo_env.pop("AMUSANT_TASK_DB")
        dynamo_config = load_relay_process_config(dynamo_env, expected_device_token="device-token-12")
        self.assertEqual(dynamo_config.dispatcher_poll_interval_seconds, 15.0)
        override = load_relay_process_config(
            {**dynamo_env, "AMUSANT_DISPATCHER_POLL_INTERVAL_SECONDS": "2.5"},
            expected_device_token="device-token-12",
        )
        self.assertEqual(override.dispatcher_poll_interval_seconds, 2.5)
        for raw in ("0", "-1", "NaN", "Infinity", "60.1", "True"):
            with self.subTest(value=raw), self.assertRaises(ValueError):
                load_relay_process_config(
                    {**dynamo_env, "AMUSANT_DISPATCHER_POLL_INTERVAL_SECONDS": raw},
                    expected_device_token="device-token-12",
                )


if __name__ == "__main__":
    unittest.main()

