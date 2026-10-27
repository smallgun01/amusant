"""Run explicitly with Python 3.13 and the hardware-opus extra; never skipped."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import ssl
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

if sys.version_info < (3, 13):
    raise RuntimeError("Run this mandatory TLS-PSK integration with Python 3.13+")

from fast_voice.contracts import AudioFrame, ProviderCapabilities, ToolCallEvent, TranscriptEvent, TurnCompleteEvent
from relay.application import build_supported_relay_runtime
from relay.device_opus import DeviceOpusCodec, unpack_opus_bundle
from relay.device_protocol import adapt_device_handler
from relay.device_tls_psk import PSK_CIPHERS, build_server_context
from relay.live_bridge import MIN_UTTERANCE_BYTES, make_handler

TOKEN = "offline-test-device-token-keep-local"
IDENTITY = "h03-test-stick"
PSK = bytes(range(32))


class FakeProvider:
    provider_id = "gemini"
    capabilities = ProviderCapabilities(True, True, True, True, False, True)

    def __init__(self, auto_complete=True):
        self.callbacks = {}
        self.auto_complete = auto_complete
        self.connected = False
        self.connect_calls = 0
        self.end_turn_calls = 0
        self.audio_input = bytearray()
        self.tool_results = []

    def on_audio(self, callback): self.callbacks["audio"] = callback
    def on_transcript(self, callback): self.callbacks["transcript"] = callback
    def on_tool_call(self, callback): self.callbacks["tool"] = callback
    def on_tool_calls_complete(self, callback): self.callbacks["tool_batch_complete"] = callback
    def on_interrupted(self, callback): self.callbacks["interrupted"] = callback
    def on_turn_complete(self, callback): self.callbacks["complete"] = callback
    def on_error(self, callback): self.callbacks["error"] = callback

    async def connect(self):
        self.connected = True
        self.connect_calls += 1

    async def close(self):
        self.connected = False

    async def start_turn(self): pass

    async def send_audio(self, frame: AudioFrame):
        self.audio_input.extend(frame.data)

    async def end_turn(self):
        self.end_turn_calls += 1
        if self.auto_complete:
            await self.emit_response()

    async def emit_response(self):
        await self.callbacks["audio"](AudioFrame(bytes([0x34, 0x12]) * 12_000, 24_000))
        await self.callbacks["transcript"](TranscriptEvent("Offline test response", "output", True))
        await self.callbacks["complete"](TurnCompleteEvent(cancelled=False))

    async def send_tool_result(self, call_id, result):
        self.tool_results.append((call_id, dict(result)))
    async def cancel_response(self): pass
    async def inject_text(self, text): pass


def make_client_context(
    identity: str | None,
    key: bytes | None,
    ciphers: str = PSK_CIPHERS,
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(ciphers)
    context.set_ecdh_curve("X25519")
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # PSK authenticates this no-certificate test link.
    if identity is not None and key is not None:
        context.set_psk_client_callback(lambda _hint: (identity, key))
    return context


def make_pcm16(rate: int, duration_ms: int) -> bytes:
    count = rate * duration_ms // 1000
    return b"".join(
        struct.pack("<h", int(7000 * math.sin(2 * math.pi * 440 * index / rate)))
        for index in range(count)
    )


class PskWssIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._loop = asyncio.get_running_loop()
        self._previous_loop_debug = self._loop.get_debug()
        self._loop.set_debug(False)
        self.provider = FakeProvider()
        self.provider_factory_calls = 0
        self.application_handler_calls = 0

        def factory():
            self.provider_factory_calls += 1
            return self.provider

        base = make_handler(TOKEN, factory)

        async def counted_handler(websocket):
            self.application_handler_calls += 1
            await base(websocket)

        handler = adapt_device_handler(counted_handler, hardware_mode=True)
        self.server = await serve(
            handler,
            "127.0.0.1",
            0,
            ssl=build_server_context({IDENTITY: PSK}),
            max_size=64 * 1024,
            ping_interval=None,
        )
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"wss://127.0.0.1:{self.port}/ws"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.assertFalse(self.server.is_serving())
        self._loop.set_debug(self._previous_loop_debug)

    async def assert_tls_handshake_rejected(self, connection):
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        previous_debug = loop.get_debug()
        previous_disable = logging.root.manager.disable

        def suppress_expected_tls_reset(loop, context):
            exception = context.get("exception")
            if isinstance(exception, (ConnectionResetError, ssl.SSLError)):
                return
            if previous_handler is not None:
                previous_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(suppress_expected_tls_reset)
        loop.set_debug(False)
        logging.disable(logging.CRITICAL)
        try:
            with self.assertRaises(Exception):
                await connection
            # Let the server finish reporting the expected failed handshake while the
            # scoped, sanitized exception handler is still installed.
            await asyncio.sleep(0.1)
        finally:
            logging.disable(previous_disable)
            loop.set_debug(previous_debug)
            loop.set_exception_handler(previous_handler)

    async def test_valid_psk_bearer_opus_wss_round_trip_and_turn_order(self):
        # The Stick's mbedTLS offers the AES ECDHE-PSK suite; force it in this client.
        context = make_client_context(IDENTITY, PSK, "ECDHE-PSK-AES128-CBC-SHA256")
        client_codec = DeviceOpusCodec(uplink_rate=24_000, downlink_rate=16_000)
        source = make_pcm16(16_000, 400)
        uplink_bundles = []
        for start in range(0, len(source), 640):
            uplink_bundles.extend(client_codec.encode_downlink(source[start : start + 640]))
        uplink_bundles.extend(client_codec.finish_downlink())
        self.assertTrue(uplink_bundles)

        received_controls = []
        downlink_bundles = []
        with self.assertLogs("amusant.p2.live_bridge", level="INFO") as captured:
            async with connect(
                self.url,
                ssl=context,
                additional_headers={"Authorization": f"Bearer {TOKEN}"},
                max_size=64 * 1024,
            ) as websocket:
                ssl_object = websocket.transport.get_extra_info("ssl_object")
                self.assertIsNotNone(ssl_object)
                self.assertEqual(ssl_object.cipher()[0], "ECDHE-PSK-AES128-CBC-SHA256")
                await websocket.send(json.dumps({
                    "type": "hello",
                    "device": "kai-private-mac-suffix",
                    "fw": "0.12.0",
                    "battery": 80,
                    "codecs": ["opus"],
                    "wake": "button",
                }))
                await websocket.send(json.dumps({"type": "ptt_start"}))
                for bundle in uplink_bundles:
                    self.assertLessEqual(len(bundle), 4096)
                    await websocket.send(bundle)
                await websocket.send(json.dumps({"type": "ptt_end"}))
                while True:
                    message = await asyncio.wait_for(websocket.recv(), timeout=3)
                    if isinstance(message, bytes):
                        downlink_bundles.append(message)
                    else:
                        control = json.loads(message)
                        received_controls.append(control)
                        if control.get("type") == "turn_complete":
                            break

        self.assertEqual(self.provider.connect_calls, 1)
        self.assertEqual(self.provider.end_turn_calls, 1)
        self.assertGreaterEqual(len(self.provider.audio_input), MIN_UTTERANCE_BYTES)
        self.assertTrue(downlink_bundles)
        self.assertTrue(all(bundle[0] == 0x02 and len(bundle) <= 4096 for bundle in downlink_bundles))
        decoded = b"".join(client_codec.decode_uplink(bundle) for bundle in downlink_bundles)
        self.assertGreater(len(decoded), 0)
        self.assertEqual(sum(item.get("type") == "turn_complete" for item in received_controls), 1)
        self.assertFalse(any(item.get("type") == "state" and item.get("state") == "idle" for item in received_controls))
        self.assertTrue(any(item.get("type") == "caption" for item in received_controls))
        joined_logs = "\n".join(captured.output)
        for marker in (
            "event=ptt_start",
            "event=decoded_threshold",
            "event=ptt_release",
            "event=provider_connect_start",
            "event=first_outgoing_audio",
        ):
            self.assertIn(marker, joined_logs)
        self.assertNotIn(PSK.hex(), joined_logs)
        self.assertNotIn(TOKEN, joined_logs)
        self.assertNotIn("kai-private-mac-suffix", joined_logs)
        self.assertNotIn("Offline test response", joined_logs)

    async def test_short_opus_tap_does_not_create_or_connect_live_provider(self):
        context = make_client_context(IDENTITY, PSK)
        client_codec = DeviceOpusCodec(uplink_rate=24_000, downlink_rate=16_000)
        source = make_pcm16(16_000, 200)
        uplink_bundles = []
        for start in range(0, len(source), 640):
            uplink_bundles.extend(client_codec.encode_downlink(source[start : start + 640]))
        uplink_bundles.extend(client_codec.finish_downlink())
        self.assertTrue(uplink_bundles)

        async with connect(
            self.url,
            ssl=context,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as websocket:
            await websocket.send(json.dumps({"type": "hello", "codecs": ["opus"], "wake": "button"}))
            await websocket.send(json.dumps({"type": "ptt_start"}))
            for bundle in uplink_bundles:
                await websocket.send(bundle)
            await websocket.send(json.dumps({"type": "ptt_end"}))
            controls = []
            while not any(item.get("type") == "state" and item.get("state") == "idle" for item in controls):
                message = await asyncio.wait_for(websocket.recv(), timeout=2)
                self.assertIsInstance(message, str)
                controls.append(json.loads(message))

        self.assertEqual(self.provider_factory_calls, 0)
        self.assertEqual(self.provider.connect_calls, 0)
        self.assertEqual(self.provider.end_turn_calls, 0)
        self.assertFalse(any(item.get("type") == "turn_complete" for item in controls))

    async def test_legacy_pcm_codec_is_negotiated_only_inside_psk_wss(self):
        source = make_pcm16(16_000, 400)
        output_pcm = bytes([0x34, 0x12]) * 12_000
        received_controls = []
        received_audio = bytearray()
        async with connect(
            self.url,
            ssl=make_client_context(IDENTITY, PSK),
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as websocket:
            await websocket.send(json.dumps({"type": "hello", "fw": "older", "wake": "button"}))
            await websocket.send(json.dumps({"type": "ptt_start"}))
            for start in range(0, len(source), 2048):
                await websocket.send(source[start : start + 2048])
            await websocket.send(json.dumps({"type": "ptt_end"}))
            while True:
                message = await asyncio.wait_for(websocket.recv(), timeout=3)
                if isinstance(message, bytes):
                    received_audio.extend(message)
                else:
                    control = json.loads(message)
                    received_controls.append(control)
                    if control.get("type") == "turn_complete":
                        break

        self.assertEqual(bytes(self.provider.audio_input), source)
        self.assertEqual(bytes(received_audio), output_pcm)
        self.assertEqual(self.provider.connect_calls, 1)
        self.assertTrue(any(item.get("type") == "state" and item.get("state") == "thinking" for item in received_controls))

    async def _read_controls_until(self, websocket, collected, state):
        while True:
            message = await asyncio.wait_for(websocket.recv(), timeout=2)
            self.assertIsInstance(message, str)
            control = json.loads(message)
            collected.append(control)
            if control.get("type") == "state" and control.get("state") == state:
                return

    async def test_supported_runtime_wss_admits_one_server_owned_slow_task(self):
        from google.genai import types as sdk_types

        with tempfile.TemporaryDirectory() as temp_dir:
            environ = {
                "AMUSANT_CLIENT_ID": "h03-owner",
                "AMUSANT_BACKEND_ID": "openab",
                "AMUSANT_SLOW_EXECUTOR": "openab_acp",  # explicit configured legacy fixture
                "AMUSANT_ACP_URL": "ws://127.0.0.1:9/acp",
                "AMUSANT_ACP_AUTH_KEY": "offline-test-acp-key-123",
                "AMUSANT_ACP_CWD": "/workspace",
                "AMUSANT_TASK_DB": f"{temp_dir}/task-ledger.sqlite3",
                "AMUSANT_TASK_STORE": "sqlite",
                "AMUSANT_DEFAULT_TASK_KIND": "research",
                "AMUSANT_MAX_TASK_TTL_SECONDS": "600",
                "AMUSANT_MAX_PENDING_TASKS": "8",
                "AMUSANT_ACCEPTANCE_TIMEOUT_SECONDS": "1",
                "AMUSANT_LIVE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "offline-test-key",
            }
            fake = FakeProvider(auto_complete=False)
            with patch("relay.application.build_live_provider", return_value=fake):
                handler, dispatcher, store = build_supported_relay_runtime(
                    expected_device_token=TOKEN,
                    environ=environ,
                    sdk_types=sdk_types,
                    conversation_id_factory=lambda: "h03-server-conversation",
                    post_tool_ack_timeout_seconds=0.2,
                    post_tool_ack_max_duration_seconds=1.0,
                )
                self.assertIsNotNone(dispatcher)  # configured, but not started; no ACP/AWS call.
                server = await serve(
                    adapt_device_handler(handler, hardware_mode=True),
                    "127.0.0.1",
                    0,
                    ssl=build_server_context({IDENTITY: PSK}),
                    max_size=64 * 1024,
                    ping_interval=None,
                )
                port = server.sockets[0].getsockname()[1]
                client_codec = DeviceOpusCodec(uplink_rate=24_000, downlink_rate=16_000)
                source = make_pcm16(16_000, 400)
                uplink_bundles = []
                for start in range(0, len(source), 640):
                    uplink_bundles.extend(client_codec.encode_downlink(source[start : start + 640]))
                uplink_bundles.extend(client_codec.finish_downlink())
                controls = []
                downlink_bundles = []
                try:
                    async with connect(
                        f"wss://127.0.0.1:{port}/ws",
                        ssl=make_client_context(IDENTITY, PSK, "ECDHE-PSK-AES128-CBC-SHA256"),
                        additional_headers={"Authorization": f"Bearer {TOKEN}"},
                    ) as websocket:
                        await websocket.send(json.dumps({
                            "type": "hello",
                            "device": "untrusted-device-label",
                            "codecs": ["opus"],
                        }))
                        await websocket.send(json.dumps({"type": "ptt_start"}))
                        for bundle in uplink_bundles:
                            await websocket.send(bundle)
                        await websocket.send(json.dumps({"type": "ptt_end"}))
                        await self._read_controls_until(websocket, controls, "thinking")

                        await fake.callbacks["tool"](ToolCallEvent(
                            "call-h03-slow",
                            "delegate_task",
                            {"task": "Research a bounded topic", "task_kind": "research"},
                        ))
                        await fake.callbacks["complete"](TurnCompleteEvent(cancelled=False))
                        with self.assertRaises(asyncio.TimeoutError):
                            await asyncio.wait_for(websocket.recv(), timeout=0.05)
                        await fake.emit_response()

                        while True:
                            message = await asyncio.wait_for(websocket.recv(), timeout=3)
                            if isinstance(message, bytes):
                                downlink_bundles.append(message)
                            else:
                                control = json.loads(message)
                                controls.append(control)
                                if control.get("type") == "turn_complete":
                                    break
                finally:
                    server.close()
                    await server.wait_closed()

            self.assertEqual(len(fake.tool_results), 1)
            call_id, receipt = fake.tool_results[0]
            row = store.get_by_call(
                client_id="h03-owner",
                conversation_id="h03-server-conversation",
                turn_id=1,
                call_id="call-h03-slow",
            )
            self.assertEqual(call_id, "call-h03-slow")
            self.assertEqual(receipt["status"], "accepted")
            self.assertIsNotNone(row)
            self.assertEqual(receipt["task_id"], row["task_id"])
            self.assertEqual(row["status"], "queued")
            self.assertEqual(row["task_kind"], "research")
            self.assertEqual(row["backend"], "openab")
            self.assertEqual(row["dispatch_state"], "pending")
            self.assertIsNone(store.get_by_call(
                client_id="untrusted-device-label",
                conversation_id="h03-server-conversation",
                turn_id=1,
                call_id="call-h03-slow",
            ))
            self.assertEqual(fake.connect_calls, 1)
            self.assertEqual(fake.end_turn_calls, 1)
            self.assertTrue(downlink_bundles)
            self.assertTrue(all(bundle[0] == 0x02 and len(bundle) <= 4096 for bundle in downlink_bundles))
            self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)

    async def test_wrong_identity_or_psk_fails_before_websocket_handler(self):
        for identity, key in ((IDENTITY, bytes([99]) * 32), ("unknown-stick", PSK)):
            with self.subTest(identity=identity):
                before = self.application_handler_calls
                await self.assert_tls_handshake_rejected(
                    connect(self.url, ssl=make_client_context(identity, key), open_timeout=1)
                )
                await asyncio.sleep(0)
                self.assertEqual(self.application_handler_calls, before)
                self.assertEqual(self.provider_factory_calls, 0)

    async def test_missing_psk_fails_tls_handshake(self):
        context = make_client_context(None, None)
        await self.assert_tls_handshake_rejected(
            connect(self.url, ssl=context, open_timeout=1)
        )
        self.assertEqual(self.application_handler_calls, 0)

    async def test_unsupported_psk_cipher_fails_context_construction(self):
        with patch("relay.device_tls_psk.PSK_CIPHERS", "not-a-supported-cipher"):
            with self.assertRaisesRegex(ValueError, "cannot provide the required TLS-PSK"):
                build_server_context({IDENTITY: PSK})

    async def test_valid_psk_with_wrong_bearer_is_still_rejected(self):
        async with connect(
            self.url,
            ssl=make_client_context(IDENTITY, PSK),
            additional_headers={"Authorization": "Bearer wrong-offline-token"},
            open_timeout=2,
        ) as websocket:
            with self.assertRaises(ConnectionClosed):
                await asyncio.wait_for(websocket.recv(), timeout=2)
        self.assertEqual(self.application_handler_calls, 1)
        self.assertEqual(self.provider_factory_calls, 0)

    async def test_valid_psk_without_bearer_is_rejected(self):
        async with connect(self.url, ssl=make_client_context(IDENTITY, PSK), open_timeout=2) as websocket:
            with self.assertRaises(ConnectionClosed):
                await asyncio.wait_for(websocket.recv(), timeout=2)
        self.assertEqual(self.application_handler_calls, 1)
        self.assertEqual(self.provider_factory_calls, 0)

    async def test_plaintext_fallback_to_tls_listener_is_rejected(self):
        await self.assert_tls_handshake_rejected(
            connect(
                self.url.replace("wss://", "ws://"),
                additional_headers={"Authorization": f"Bearer {TOKEN}"},
                open_timeout=1,
            )
        )
        self.assertEqual(self.application_handler_calls, 0)
        self.assertEqual(self.provider_factory_calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
