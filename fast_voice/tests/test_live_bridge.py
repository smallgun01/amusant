import asyncio
import json
import secrets
import unittest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError
from fast_voice.contracts import AudioFrame, ProviderCapabilities, TranscriptEvent, TurnCompleteEvent
from relay.live_bridge import MIN_UTTERANCE_BYTES, make_handler
TOKEN = secrets.token_urlsafe(24)

class FakeProvider:
    provider_id = "gemini"
    capabilities = ProviderCapabilities(True, True, True, True, False, True)
    def __init__(self):
        self.audio_output = bytes([1, 0]) * 6000
        self.events = []
        self.callbacks = {}
        self.connected = False
        self.closed = False
        self.audio_input = bytearray()
    def on_audio(self, callback): self.callbacks["audio"] = callback
    def on_transcript(self, callback): self.callbacks["transcript"] = callback
    def on_tool_call(self, callback): self.callbacks["tool"] = callback
    def on_interrupted(self, callback): self.callbacks["interrupted"] = callback
    def on_turn_complete(self, callback): self.callbacks["complete"] = callback
    def on_error(self, callback): self.callbacks["error"] = callback
    async def connect(self):
        self.connected = True
        self.events.append("connect")
    async def close(self): self.closed = True
    async def start_turn(self): self.events.append("start_turn")
    async def send_audio(self, frame: AudioFrame):
        self.events.append(("send_audio", frame.sample_rate_hz))
        self.audio_input.extend(frame.data)
    async def end_turn(self):
        self.events.append("end_turn")
        await self.callbacks["audio"](AudioFrame(self.audio_output, 24000))
        await self.callbacks["transcript"](TranscriptEvent("Ready.", "output", True))
        await self.callbacks["complete"](TurnCompleteEvent(cancelled=False))
    async def send_tool_result(self, call_id, result): pass
    async def cancel_response(self): raise NotImplementedError
    async def inject_text(self, text): pass

class LiveBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ptt_pcm_input_streams_to_fake_live_and_audio_returns(self):
        fake = FakeProvider()
        async with serve(make_handler(TOKEN, lambda: fake), "127.0.0.1", 0, max_size=64 * 1024) as server:
            port = server.sockets[0].getsockname()[1]
            headers = {"Authorization": f"Bearer {TOKEN}"}
            async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers=headers) as ws:
                await ws.send(json.dumps({"type": "hello", "device": "test-sim"}))
                await ws.send(json.dumps({"type": "ptt_start"}))
                source = bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2)
                await ws.send(source)
                await ws.send(json.dumps({"type": "ptt_end"}))
                controls = []
                returned_audio_bytes = 0
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2)
                    if isinstance(msg, bytes):
                        returned_audio_bytes += len(msg)
                        continue
                    control = json.loads(msg)
                    controls.append(control)
                    if control.get("type") == "turn_complete":
                        break
        self.assertTrue(fake.connected)
        self.assertTrue(fake.closed)
        self.assertEqual(bytes(fake.audio_input), source)
        self.assertEqual(fake.events[0], "connect")
        self.assertIn("start_turn", fake.events)
        self.assertIn("end_turn", fake.events)
        self.assertEqual(returned_audio_bytes, 12000)
        self.assertTrue(any(item.get("type") == "caption" and item.get("delta") == "Ready." for item in controls))

    async def test_short_ptt_tap_does_not_open_live_session(self):
        fake = FakeProvider()
        async with serve(make_handler(TOKEN, lambda: fake), "127.0.0.1", 0, max_size=64 * 1024) as server:
            port = server.sockets[0].getsockname()[1]
            headers = {"Authorization": f"Bearer {TOKEN}"}
            async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers=headers) as ws:
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([1, 0]) * 100)
                await ws.send(json.dumps({"type": "ptt_end"}))
                state = json.loads(await ws.recv())
        self.assertEqual(state, {"type": "state", "state": "idle"})
        self.assertFalse(fake.connected)

    async def test_oversize_ptt_turn_is_rejected_and_completes_once(self):
        fake = FakeProvider()
        async with serve(make_handler(TOKEN, lambda: fake), "127.0.0.1", 0, max_size=64 * 1024) as server:
            port = server.sockets[0].getsockname()[1]
            headers = {"Authorization": f"Bearer {TOKEN}"}
            async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers=headers) as ws:
                await ws.send(json.dumps({"type": "ptt_start"}))
                frame = bytes([1, 0]) * 30_000
                for _ in range(17):
                    await ws.send(frame)
                await ws.send(json.dumps({"type": "ptt_end"}))
                controls = []
                returned_bytes = 0
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3)
                    if isinstance(msg, bytes):
                        returned_bytes += len(msg)
                    else:
                        item = json.loads(msg)
                        controls.append(item)
                        if item.get("type") == "turn_complete":
                            break
        self.assertTrue(fake.connected)
        self.assertEqual(returned_bytes, 12000)
        self.assertEqual(sum(item.get("type") == "turn_complete" for item in controls), 1)
        self.assertTrue(any(item.get("title") == "Audio too long" for item in controls))

    async def test_wrong_device_token_is_rejected_before_provider_creation(self):
        created = []
        async with serve(make_handler(TOKEN, lambda: created.append(True) or FakeProvider()), "127.0.0.1", 0, max_size=64 * 1024) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/ws", additional_headers={"Authorization": "Bearer wrong-token"}) as ws:
                with self.assertRaises(ConnectionClosedError):
                    await asyncio.wait_for(ws.recv(), timeout=2)
        self.assertEqual(created, [])

if __name__ == "__main__":
    unittest.main()
