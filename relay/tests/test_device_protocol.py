import asyncio
import json
import unittest
from types import SimpleNamespace

from relay.device_opus import DeviceProtocolError
from relay.device_protocol import DeviceWebSocketAdapter, adapt_device_handler


class FakeCodec:
    def __init__(self):
        self.pending = bytearray()
        self.reset_calls = 0
        self.discard_calls = 0
        self.finish_calls = 0

    def decode_uplink(self, message):
        if message != b"OPUS-IN":
            raise DeviceProtocolError("invalid_opus_packet")
        return bytes(9600)  # exactly the existing 300 ms decoded-PCM gate

    def encode_downlink(self, pcm):
        self.pending.extend(pcm)
        return []  # emulate a partial 20 ms frame retained in the encoder

    def finish_downlink(self):
        self.finish_calls += 1
        if not self.pending:
            return []
        self.pending.clear()
        return [b"OPUS-FINAL"]

    def discard_downlink(self):
        self.discard_calls += 1
        self.pending.clear()

    def reset_turn(self):
        self.reset_calls += 1
        self.pending.clear()


class FakeWebSocket:
    def __init__(self, tls=False):
        self.request = SimpleNamespace(path="/ws", headers={})
        self.transport = SimpleNamespace(get_extra_info=lambda key: object() if tls and key == "ssl_object" else None)
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.close_args = None

    async def recv(self):
        return await self.incoming.get()

    async def send(self, message):
        self.sent.append(message)

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_args = (code, reason)


class DeviceProtocolAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_hello_negotiates_opus_without_emitting_an_idle_reply(self):
        raw = FakeWebSocket()
        fake_codec = FakeCodec()
        adapter = DeviceWebSocketAdapter(raw, codec_factory=lambda: fake_codec)
        hello = json.dumps({"type": "hello", "device": "kai-sim", "codecs": ["opus"]})
        await raw.incoming.put(hello)
        await raw.incoming.put(json.dumps({"type": "ptt_start"}))
        await raw.incoming.put(b"OPUS-IN")
        self.assertEqual(await adapter.__anext__(), hello)
        self.assertEqual(json.loads(await adapter.__anext__())["type"], "ptt_start")
        self.assertEqual(len(await adapter.__anext__()), 9600)
        self.assertEqual(fake_codec.reset_calls, 1)
        self.assertEqual(raw.sent, [])  # hello must not trigger premature state:idle

    async def test_unadvertised_codec_stays_pcm_and_hardware_mode_rejects_downgrade(self):
        raw = FakeWebSocket()
        adapter = DeviceWebSocketAdapter(raw, codec_factory=FakeCodec)
        hello = json.dumps({"type": "hello", "device": "sim"})
        await raw.incoming.put(hello)
        self.assertEqual(await adapter.__anext__(), hello)
        pcm = bytes([1, 0]) * 400
        await raw.incoming.put(pcm)
        self.assertEqual(await adapter.__anext__(), pcm)

        hardware_raw = FakeWebSocket(tls=True)
        hardware = DeviceWebSocketAdapter(
            hardware_raw,
            require_hello=True,
            require_opus=True,
            codec_factory=FakeCodec,
        )
        await hardware_raw.incoming.put(json.dumps({"type": "hello", "codecs": []}))
        with self.assertRaises(StopAsyncIteration):
            await hardware.__anext__()
        self.assertTrue(hardware_raw.closed)

    async def test_every_turn_complete_flushes_before_marker(self):
        for label in ("provider-terminal", "local-timeout-fallback"):
            with self.subTest(path=label):
                raw = FakeWebSocket()
                codec = FakeCodec()
                adapter = DeviceWebSocketAdapter(raw, codec_factory=lambda: codec)
                await raw.incoming.put(json.dumps({"type": "hello", "codecs": ["opus"]}))
                await adapter.__anext__()
                await adapter.send(bytes(321))
                await adapter.send(json.dumps({"type": "turn_complete"}))
                self.assertEqual(raw.sent, [b"OPUS-FINAL", json.dumps({"type": "turn_complete"})])
                self.assertEqual(codec.finish_calls, 1)

    async def test_interruption_failure_and_supersede_discard_partial_audio(self):
        for control in (
            {"type": "interrupted"},
            {"type": "state", "state": "idle"},
        ):
            with self.subTest(control=control):
                raw = FakeWebSocket()
                codec = FakeCodec()
                adapter = DeviceWebSocketAdapter(raw, codec_factory=lambda: codec)
                await raw.incoming.put(json.dumps({"type": "hello", "codecs": ["opus"]}))
                await adapter.__anext__()
                await adapter.send(bytes(101))
                await adapter.send(json.dumps(control))
                self.assertEqual(codec.pending, bytearray())
                self.assertEqual(codec.discard_calls, 1)
                self.assertEqual(raw.sent, [json.dumps(control)])

    async def test_new_ptt_turn_resets_codec_and_sessions_do_not_share_state(self):
        created = []

        def codec_factory():
            codec = FakeCodec()
            created.append(codec)
            return codec

        adapters = [
            DeviceWebSocketAdapter(FakeWebSocket(), codec_factory=codec_factory),
            DeviceWebSocketAdapter(FakeWebSocket(), codec_factory=codec_factory),
        ]
        hello = json.dumps({"type": "hello", "codecs": ["opus"]})
        for adapter in adapters:
            await adapter._websocket.incoming.put(hello)
            await adapter.__anext__()
        self.assertIsNot(created[0], created[1])

        first_raw = adapters[0]._websocket
        await first_raw.incoming.put(json.dumps({"type": "ptt_start"}))
        await adapters[0].__anext__()
        await adapters[0].send(bytes(101))
        self.assertTrue(created[0].pending)
        await first_raw.incoming.put(json.dumps({"type": "ptt_end"}))
        await adapters[0].__anext__()
        await first_raw.incoming.put(json.dumps({"type": "ptt_start"}))
        await adapters[0].__anext__()
        self.assertEqual(created[0].reset_calls, 2)
        self.assertEqual(created[0].pending, bytearray())
        self.assertEqual(created[1].pending, bytearray())

    async def test_hardware_handler_rejects_plaintext_before_application_handler(self):
        raw = FakeWebSocket(tls=False)
        called = []

        async def handler(_ws):
            called.append(True)

        await adapt_device_handler(handler, hardware_mode=True)(raw)
        self.assertEqual(called, [])
        self.assertTrue(raw.closed)
        self.assertEqual(raw.close_args[0], 1008)


if __name__ == "__main__":
    unittest.main()
