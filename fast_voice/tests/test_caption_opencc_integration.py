"""Offline FAST caption normalization through the actual WebSocket handler."""
import asyncio
import json
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from fast_voice.tests.test_live_bridge import FakeProvider
from relay.live_bridge import MIN_UTTERANCE_BYTES, make_handler


class LiveCaptionOpenCCIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_display_only_conversion_does_not_change_audio_or_controls(self):
        provider = FakeProvider(transcripts=["没问题，我会回复你的。"])
        with patch.dict("os.environ", {"AMUSANT_CAPTION_SCRIPT": "zh-TW"}):
            handler = make_handler("offline-token", lambda: provider)
        server = await serve(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            async with connect(
                f"ws://127.0.0.1:{port}/ws",
                additional_headers={"Authorization": "Bearer offline-token"},
            ) as ws:
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                messages = []
                while True:
                    frame = await asyncio.wait_for(ws.recv(), 2)
                    messages.append(frame if isinstance(frame, bytes) else json.loads(frame))
                    if isinstance(messages[-1], dict) and messages[-1].get("type") == "turn_complete":
                        break
                captions = [msg["delta"] for msg in messages if isinstance(msg, dict) and msg.get("type") == "caption"]
                audio = b"".join(msg for msg in messages if isinstance(msg, bytes))
                self.assertEqual(captions, ["沒問題，我會回覆你的。"])
                self.assertEqual(audio, bytes([1, 0]) * 6000)
                self.assertEqual(provider.transcripts, ["没问题，我会回复你的。"])
                self.assertEqual(sum(isinstance(msg, dict) and msg.get("type") == "turn_complete" for msg in messages), 1)
        finally:
            server.close()
            await server.wait_closed()

    async def test_converter_failure_keeps_live_audio_and_turn_completion(self):
        transcript = "没问题，我会回复你的。"
        expected_audio = bytes([1, 0]) * 6000
        private_detail = "private converter diagnostic"

        class BrokenConverter:
            def convert(self, _text):
                raise RuntimeError(private_detail)

        provider = FakeProvider(audio_outputs=[expected_audio], transcripts=[transcript])
        with patch.dict("os.environ", {"AMUSANT_CAPTION_SCRIPT": "zh-TW"}):
            with patch("relay.caption_script._taiwan_converter", return_value=BrokenConverter()):
                handler = make_handler("offline-token", lambda: provider)
        server = await serve(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            async with connect(
                f"ws://127.0.0.1:{port}/ws",
                additional_headers={"Authorization": "Bearer offline-token"},
            ) as ws:
                with self.assertLogs("relay.caption_script", level="WARNING") as captured:
                    await ws.send(json.dumps({"type": "ptt_start"}))
                    await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                    await ws.send(json.dumps({"type": "ptt_end"}))
                    messages = []
                    while True:
                        frame = await asyncio.wait_for(ws.recv(), 2)
                        messages.append(frame if isinstance(frame, bytes) else json.loads(frame))
                        if isinstance(messages[-1], dict) and messages[-1].get("type") == "turn_complete":
                            break
                captions = [
                    msg["delta"] for msg in messages
                    if isinstance(msg, dict) and msg.get("type") == "caption"
                ]
                audio = b"".join(msg for msg in messages if isinstance(msg, bytes))
                self.assertEqual(captions, [transcript])
                self.assertEqual(audio, expected_audio)
                self.assertEqual(provider.transcripts, [transcript])
                self.assertEqual(
                    sum(isinstance(msg, dict) and msg.get("type") == "turn_complete" for msg in messages),
                    1,
                )
                self.assertEqual(len(captured.records), 1)
                self.assertNotIn(transcript, captured.output[0])
                self.assertNotIn(private_detail, captured.output[0])
        finally:
            server.close()
            await server.wait_closed()

    async def test_raw_mode_keeps_provider_caption(self):
        provider = FakeProvider(transcripts=["我会回复你的"])
        with patch.dict("os.environ", {"AMUSANT_CAPTION_SCRIPT": "raw"}):
            handler = make_handler("offline-token", lambda: provider)
        server = await serve(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            async with connect(
                f"ws://127.0.0.1:{port}/ws",
                additional_headers={"Authorization": "Bearer offline-token"},
            ) as ws:
                await ws.send(json.dumps({"type": "ptt_start"}))
                await ws.send(bytes([132, 3]) * (MIN_UTTERANCE_BYTES // 2))
                await ws.send(json.dumps({"type": "ptt_end"}))
                while True:
                    frame = await asyncio.wait_for(ws.recv(), 2)
                    if isinstance(frame, str):
                        msg = json.loads(frame)
                        if msg.get("type") == "caption":
                            self.assertEqual(msg["delta"], "我会回复你的")
                        if msg.get("type") == "turn_complete":
                            break
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
