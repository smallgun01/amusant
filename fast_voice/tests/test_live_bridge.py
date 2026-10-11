import asyncio
import json
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

from fast_voice.contracts import AudioFrame, ProviderCapabilities, ToolCallEvent, TranscriptEvent, TurnCompleteEvent
from relay.application import build_relay_handler
from relay.live_bridge import MIN_UTTERANCE_BYTES, make_handler
from relay.slow_path import SlowPathToolRouter
from relay.task_store import SQLiteTaskStore

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

    def on_audio(self, callback): self.callbacks["audio"] = callback
    def on_transcript(self, callback): self.callbacks["transcript"] = callback
    def on_tool_call(self, callback): self.callbacks["tool"] = callback
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
    async def inject_text(self, text): pass


class FakeSchemaTypes:
    class Schema:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class FunctionDeclaration:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Tool:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)


class LiveBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def _open(self, factory, max_size=64 * 1024):
        server = await serve(make_handler(TOKEN, factory), "127.0.0.1", 0, max_size=max_size)
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
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                controls, _ = await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
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

    async def test_fast_turn_does_not_create_a_slow_task(self):
        fake = FakeProvider()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteTaskStore(database_path=Path(temp_dir) / "fast.sqlite3", max_pending_tasks=8)
            router = SlowPathToolRouter(
                store=store,
                client_id="authenticated-device-1",
                conversation_id="kai-session-fast",
                backend_id="openab",
                default_task_kind="research",
                max_task_ttl_seconds=600,
                acceptance_timeout_seconds=2,
            )
            server = await serve(make_handler(TOKEN, lambda: fake, slow_path_tool_handler=router), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            try:
                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()
            self.assertEqual(store.pending_dispatch_count(), 0)

    async def test_unknown_tool_is_rejected_with_correlated_call_id(self):
        fake = FakeProvider(auto_complete=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteTaskStore(database_path=Path(temp_dir) / "unknown.sqlite3", max_pending_tasks=8)
            router = SlowPathToolRouter(
                store=store,
                client_id="authenticated-device-1",
                conversation_id="kai-session-unknown",
                backend_id="openab",
                default_task_kind="research",
                max_task_ttl_seconds=600,
                acceptance_timeout_seconds=2,
            )
            server = await serve(make_handler(TOKEN, lambda: fake, slow_path_tool_handler=router), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
            try:
                await self._send_turn(ws)
                await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
                await fake.callbacks["tool"](ToolCallEvent("call-unknown-1", "run_shell", {"command": "id"}))
                await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
            finally:
                await ws.close()
                server.close()
                await server.wait_closed()
            self.assertEqual(fake.tool_results, [("call-unknown-1", {"status": "rejected", "code": "unsupported_tool"})])
            self.assertEqual(store.pending_dispatch_count(), 0)

    async def test_dispatch_handler_exception_returns_correlated_unknown(self):
        fake = FakeProvider(auto_complete=False)

        async def broken_handler(_event, _turn_id):
            raise RuntimeError("sensitive diagnostic must not be forwarded")

        server = await serve(make_handler(TOKEN, lambda: fake, slow_path_tool_handler=broken_handler), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
        try:
            await self._send_turn(ws)
            await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
            await fake.callbacks["tool"](ToolCallEvent("call-exception", "delegate_task", {"task": "Do work"}))
            await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
            await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(fake.tool_results, [("call-exception", {"status": "unknown", "code": "acceptance_unconfirmed"})])
        self.assertNotIn("sensitive diagnostic", json.dumps(fake.tool_results))

    async def test_oversized_dispatch_receipt_is_replaced_with_bounded_error(self):
        fake = FakeProvider(auto_complete=False)

        async def slow_path(_event, _turn_id):
            return {"status": "accepted", "detail": "x" * 9000}

        server = await serve(
            make_handler(TOKEN, lambda: fake, slow_path_tool_handler=slow_path),
            "127.0.0.1", 0, max_size=64 * 1024,
        )
        port = server.sockets[0].getsockname()[1]
        ws = await connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": f"Bearer {TOKEN}"})
        try:
            await self._send_turn(ws)
            await self._controls_until(ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking")
            await fake.callbacks["tool"](ToolCallEvent("call-large", "delegate_task", {"task": "Do work"}))
            await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
            await self._controls_until(ws, lambda item, _items: item.get("type") == "turn_complete")
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(fake.tool_results, [("call-large", {"status": "unknown", "code": "invalid_tool_result"})])

    async def test_short_ptt_tap_does_not_create_or_open_live_session(self):
        created = []
        server, ws = await self._open(lambda: created.append(FakeProvider()) or created[-1])
        try:
            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(bytes([1, 0]) * 100)
            await ws.send(json.dumps({"type": "ptt_end"}))
            state = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(state, {"type": "state", "state": "idle"})
        self.assertEqual(created, [])

    async def test_oversize_before_live_activation_rejects_without_creating_provider(self):
        created = []
        server, ws = await self._open(lambda: created.append(FakeProvider()) or created[-1])
        try:
            with patch("relay.live_bridge.MAX_UTTERANCE_BYTES", MIN_UTTERANCE_BYTES - 2):
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([1, 0]) * (MIN_UTTERANCE_BYTES // 2))
                controls, audio_bytes = await self._controls_until(
                    ws, lambda item, items: item.get("type") == "state" and item.get("state") == "idle"
                )
            await ws.send(json.dumps({"type": "ptt_end"}))
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(created, [])
        self.assertEqual(audio_bytes, 0)
        self.assertEqual([item.get("title") for item in controls if item.get("type") == "card"], ["Audio too long"])
        self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 0)

    async def test_odd_pcm_before_activation_rejects_without_provider(self):
        created = []
        server, ws = await self._open(lambda: created.append(FakeProvider()) or created[-1])
        try:
            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(b"\x01")
            controls, audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "idle"
            )
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(created, [])
        self.assertEqual(audio_bytes, 0)
        self.assertEqual([item.get("title") for item in controls if item.get("type") == "card"], ["Invalid audio"])

    async def test_odd_pcm_after_activation_suppresses_output_and_tears_down(self):
        fake = FakeProvider(auto_complete=False)
        server, ws = await self._open(lambda: fake)
        try:
            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
            await ws.send(b"\x01")
            controls, audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "idle"
            )
            await ws.send(json.dumps({"type": "ptt_end"}))
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(audio_bytes, 0)
        self.assertEqual([item.get("title") for item in controls if item.get("type") == "card"], ["Invalid audio"])
        self.assertEqual(fake.end_turn_calls, 0)
        self.assertTrue(fake.closed)
        self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 0)

    async def test_oversize_after_activation_suppresses_output_and_next_turn_recovers(self):
        first = FakeProvider(auto_complete=False)
        second = FakeProvider(audio_outputs=[bytes([2, 0]) * 3000], transcripts=["Next turn."], auto_complete=False)
        providers = iter([first, second])
        server, ws = await self._open(lambda: next(providers))
        try:
            await ws.send(json.dumps({"type": "ptt_start"}))
            frame = bytes([1, 0]) * 30_000
            for _ in range(17):
                await ws.send(frame)
            await ws.send(json.dumps({"type": "ptt_end"}))
            rejected_controls, rejected_audio = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "idle"
            )
            self.assertEqual(rejected_audio, 0)
            self.assertEqual([item.get("title") for item in rejected_controls if item.get("type") == "card"], ["Audio too long"])
            self.assertEqual(sum(item.get("type") == "turn_complete" for item in rejected_controls), 0)
            self.assertTrue(first.closed)
            self.assertEqual(first.end_turn_calls, 0)
            old_callbacks = dict(first.callbacks)

            await self._send_turn(ws)
            pre_controls, pre_audio = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking"
            )
            self.assertEqual(pre_audio, 0)
            await old_callbacks["audio"](AudioFrame(bytes([9, 0]) * 1000, 24000))
            await old_callbacks["transcript"](TranscriptEvent("STALE", "output", True))
            await old_callbacks["complete"](TurnCompleteEvent(cancelled=False))
            second_callbacks = dict(second.callbacks)
            await second.emit_response(second_callbacks)
            controls, audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
            self.assertEqual(audio_bytes, 6000)
            self.assertTrue(any(item.get("type") == "caption" and item.get("delta") == "Next turn." for item in controls))
            self.assertFalse(any(item.get("type") == "caption" and item.get("delta") == "STALE" for item in controls))
            self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)
            self.assertTrue(second.connected)
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertTrue(second.closed)

    async def test_barge_in_fences_delayed_events_from_previous_provider_epoch(self):
        first = FakeProvider(auto_complete=False)
        second = FakeProvider(audio_outputs=[bytes([2, 0]) * 2000], transcripts=["New response."], auto_complete=False)
        providers = iter([first, second])
        server, ws = await self._open(lambda: next(providers))
        try:
            await self._send_turn(ws)
            first_controls, first_audio = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking"
            )
            self.assertEqual(first_audio, 0)
            old_callbacks = dict(first.callbacks)
            self.assertEqual(first.end_turn_calls, 1)

            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
            await ws.send(json.dumps({"type": "ptt_end"}))
            pre_controls, pre_audio = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking"
            )
            self.assertEqual(pre_audio, 0)
            self.assertTrue(first.closed)
            self.assertEqual(second.end_turn_calls, 1)

            await old_callbacks["audio"](AudioFrame(bytes([9, 0]) * 1000, 24000))
            await old_callbacks["transcript"](TranscriptEvent("STALE", "output", True))
            await old_callbacks["complete"](TurnCompleteEvent(cancelled=False))
            await second.emit_response(dict(second.callbacks))

            controls, audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
            self.assertEqual(audio_bytes, 4000)
            self.assertTrue(any(item.get("type") == "caption" and item.get("delta") == "New response." for item in controls))
            self.assertFalse(any(item.get("type") == "caption" and item.get("delta") == "STALE" for item in controls))
            self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)
            self.assertTrue(any(item.get("type") == "state" and item.get("state") == "idle" for item in pre_controls + controls))
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()

    async def test_sequential_turns_have_disjoint_audio_and_counters(self):
        fake = FakeProvider(
            audio_outputs=[bytes([1, 0]) * 6000, bytes([2, 0]) * 2000],
            transcripts=["First.", "Second."],
        )
        server, ws = await self._open(lambda: fake)
        try:
            with self.assertLogs("amusant.p2.live_bridge", level="INFO") as captured:
                await self._send_turn(ws, bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await self._send_turn(ws, bytes([133, 3]) * (MIN_UTTERANCE_BYTES // 2 + 100))
                controls = []
                segment_bytes = []
                current_audio = 0
                while len(segment_bytes) < 2:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2)
                    if isinstance(msg, bytes):
                        current_audio += len(msg)
                    else:
                        item = json.loads(msg)
                        controls.append(item)
                        if item.get("type") == "turn_complete":
                            segment_bytes.append(current_audio)
                            current_audio = 0
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(segment_bytes, [12000, 4000])
        self.assertTrue(any(item.get("delta") == "First." for item in controls))
        self.assertTrue(any(item.get("delta") == "Second." for item in controls))
        joined = "\n".join(captured.output)
        self.assertIn("turn_id=1", joined)
        self.assertIn("input_bytes=9600 output_bytes=12000", joined)
        self.assertIn("turn_id=2", joined)
        self.assertIn("output_bytes=4000", joined)
        self.assertEqual(fake.end_turn_calls, 2)

    async def test_malformed_ptt_order_does_not_duplicate_a_turn(self):
        fake = FakeProvider()
        server, ws = await self._open(lambda: fake)
        try:
            await ws.send(json.dumps({"type": "ptt_end"}))
            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(json.dumps({"type": "ptt_start"}))
            await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
            await ws.send(json.dumps({"type": "ptt_end"}))
            controls, audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(fake.events.count("start_turn"), 1)
        self.assertEqual(fake.end_turn_calls, 1)
        self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)
        self.assertEqual(audio_bytes, 12000)

    async def test_provider_interruption_suppresses_later_media_until_completion(self):
        fake = FakeProvider(auto_complete=False)
        server, ws = await self._open(lambda: fake)
        try:
            await self._send_turn(ws)
            await self._controls_until(
                ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking"
            )
            callbacks = dict(fake.callbacks)
            await callbacks["interrupted"](object())
            await callbacks["audio"](AudioFrame(bytes([9, 0]) * 1000, 24000))
            await callbacks["transcript"](TranscriptEvent("STALE AFTER INTERRUPT", "output", True))
            await callbacks["complete"](TurnCompleteEvent(cancelled=True))
            controls, audio_bytes = await self._controls_until(
                ws, lambda item, _items: item.get("type") == "turn_complete"
            )
        finally:
            await ws.close()
            server.close()
            await server.wait_closed()
        self.assertEqual(audio_bytes, 0)
        self.assertEqual(sum(item.get("type") == "interrupted" for item in controls), 1)
        self.assertFalse(any(item.get("delta") == "STALE AFTER INTERRUPT" for item in controls))
        self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)

    async def test_disconnect_fences_late_provider_callbacks(self):
        fake = FakeProvider(auto_complete=False)
        server, ws = await self._open(lambda: fake)
        await self._send_turn(ws)
        await self._controls_until(
            ws, lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking"
        )
        callbacks = dict(fake.callbacks)
        await ws.close()
        server.close()
        await server.wait_closed()
        await callbacks["audio"](AudioFrame(bytes([9, 0]) * 1000, 24000))
        await callbacks["transcript"](TranscriptEvent("LATE", "output", True))
        await callbacks["complete"](TurnCompleteEvent(cancelled=False))
        self.assertTrue(fake.closed)

    async def test_wrong_device_token_is_rejected_before_provider_creation(self):
        created = []
        server = await serve(make_handler(TOKEN, lambda: created.append(True) or FakeProvider()), "127.0.0.1", 0, max_size=64 * 1024)
        port = server.sockets[0].getsockname()[1]
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": "Bearer wrong"}) as ws:
                with self.assertRaises(ConnectionClosedError):
                    await asyncio.wait_for(ws.recv(), timeout=2)
        finally:
            server.close()
            await server.wait_closed()
        self.assertEqual(created, [])

    async def test_relay_application_composes_server_identity_schema_and_admission(self):
        fake = FakeProvider(auto_complete=False)
        captured = {}

        def provider_builder(environ, **kwargs):
            captured["environ"] = dict(environ)
            captured["tool_declarations"] = kwargs["tool_declarations"]
            return fake

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteTaskStore(database_path=Path(temp_dir) / "relay-app.sqlite3", max_pending_tasks=8)
            with patch("relay.application.build_live_provider", side_effect=provider_builder):
                handler = build_relay_handler(
                    expected_device_token=TOKEN,
                    authenticated_client_id="trusted-owner",
                    task_store=store,
                    provider_environ={
                        "AMUSANT_LIVE_PROVIDER": "gemini",
                        "GEMINI_API_KEY": "offline-test-key",
                    },
                    backend_id="openab",
                    default_task_kind="research",
                    max_task_ttl_seconds=600,
                    acceptance_timeout_seconds=2,
                    sdk_types=FakeSchemaTypes,
                    conversation_id_factory=lambda: "server-conversation-1",
                )
                server = await serve(handler, "127.0.0.1", 0, max_size=64 * 1024)
                port = server.sockets[0].getsockname()[1]
                ws = await connect(
                    f"ws://127.0.0.1:{port}/ws",
                    additional_headers={"Authorization": f"Bearer {TOKEN}"},
                )
                try:
                    await ws.send(json.dumps({"type": "hello", "device": "untrusted-device-label"}))
                    await self._send_turn(ws)
                    await self._controls_until(
                        ws,
                        lambda item, _items: item.get("type") == "state" and item.get("state") == "thinking",
                    )
                    await fake.callbacks["tool"](
                        ToolCallEvent(
                            "call-app-1",
                            "delegate_task",
                            {"task": "Research a bounded topic", "task_kind": "research"},
                        )
                    )
                    await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                    controls, _ = await self._controls_until(
                        ws, lambda item, _items: item.get("type") == "turn_complete"
                    )
                finally:
                    await ws.close()
                    server.close()
                    await server.wait_closed()

            declaration = captured["tool_declarations"][0].function_declarations[0]
            self.assertEqual(declaration.name, "delegate_task")
            self.assertEqual(declaration.parameters.required, ["task"])
            self.assertEqual(captured["environ"]["AMUSANT_LIVE_PROVIDER"], "gemini")
            self.assertEqual(len(fake.tool_results), 1)
            call_id, receipt = fake.tool_results[0]
            self.assertEqual(call_id, "call-app-1")
            self.assertEqual(receipt["status"], "accepted")
            self.assertFalse(receipt["duplicate"])
            row = store.get_by_call(
                client_id="trusted-owner",
                conversation_id="server-conversation-1",
                turn_id=1,
                call_id="call-app-1",
            )
            self.assertIsNotNone(row)
            self.assertEqual(row["task_id"], receipt["task_id"])
            self.assertEqual(row["backend"], "openab")
            self.assertEqual(row["dispatch_state"], "pending")
            self.assertIsNone(store.get_by_call(
                client_id="untrusted-device-label",
                conversation_id="server-conversation-1",
                turn_id=1,
                call_id="call-app-1",
            ))
            self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)


if __name__ == "__main__":
    unittest.main()
