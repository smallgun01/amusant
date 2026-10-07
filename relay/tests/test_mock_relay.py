import array
import asyncio
import json
import secrets
import unittest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

from mock_relay import make_handler, resample_pcm16


TOKEN = secrets.token_urlsafe(24)  # generated per test run; never matches a live relay token


class AudioTests(unittest.TestCase):
    def test_resample_16k_to_24k_and_keep_pcm16(self):
        samples = array.array("h", [1000] * 160)
        raw = samples.tobytes()
        output = resample_pcm16(raw)
        self.assertEqual(len(output), 240 * 2)
        decoded = array.array("h")
        decoded.frombytes(output)
        self.assertTrue(all(640 <= sample <= 660 for sample in decoded))

    def test_empty_audio_is_empty(self):
        self.assertEqual(resample_pcm16(b""), b"")


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_ptt_round_trip_returns_text_and_audio_without_external_calls(self):
        async with serve(make_handler(TOKEN), "127.0.0.1", 0, max_size=64 * 1024) as server:
            port = server.sockets[0].getsockname()[1]
            uri = f"ws://127.0.0.1:{port}/ws"
            headers = {"Authorization": f"Bearer {TOKEN}"}
            async with connect(uri, additional_headers=headers) as ws:
                await ws.send(json.dumps({"type": "hello", "device": "p1-test"}))
                await ws.send(json.dumps({"type": "ptt_start"}))
                state = json.loads(await ws.recv())
                self.assertEqual(state["type"], "state")
                self.assertEqual(state["state"], "thinking")

                source = array.array("h", [1200] * 160).tobytes()
                await ws.send(source)
                await ws.send(json.dumps({"type": "ptt_end"}))

                controls = []
                returned_audio_bytes = 0
                while True:
                    message = await asyncio.wait_for(ws.recv(), timeout=2)
                    if isinstance(message, bytes):
                        returned_audio_bytes += len(message)
                        continue
                    control = json.loads(message)
                    controls.append(control)
                    if control.get("type") == "turn_complete":
                        break

                self.assertTrue(any(item.get("type") == "card" for item in controls))
                self.assertTrue(any(item.get("type") == "caption" for item in controls))
                self.assertGreater(returned_audio_bytes, 0)

    async def test_wrong_test_token_is_rejected(self):
        async with serve(make_handler(TOKEN), "127.0.0.1", 0, max_size=64 * 1024) as server:
            port = server.sockets[0].getsockname()[1]
            uri = f"ws://127.0.0.1:{port}/ws"
            async with connect(uri, additional_headers={"Authorization": "Bearer wrong-token"}) as ws:
                with self.assertRaises(ConnectionClosedError):
                    await asyncio.wait_for(ws.recv(), timeout=2)


if __name__ == "__main__":
    unittest.main()
