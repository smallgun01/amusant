#!/usr/bin/env python3
"""Private-LAN Kai-sim PTT bridge to the Gemini Live adapter.
Audio buffers exist only in RAM for the current utterance; never persist PCM or credentials.
"""
from __future__ import annotations
import argparse, asyncio, hmac, json, logging, os
from collections.abc import Callable
from typing import Any
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from fast_voice import AudioFrame, LiveProvider, ProviderError, build_live_provider
from relay.mock_relay import INPUT_RATE, MAX_FRAME_BYTES, OUTPUT_CHUNK_BYTES, OUTPUT_RATE, validate_bind_host

DEVICE_TOKEN_ENV = "AMUSANT_DEVICE_TOKEN"
MAX_UTTERANCE_BYTES = 1_000_000
MIN_UTTERANCE_BYTES = INPUT_RATE * 2 * 300 // 1000
LOG = logging.getLogger("amusant.p2.live_bridge")
ProviderFactory = Callable[[], LiveProvider]

def _safe_device(value: Any) -> str:
    cleaned = "".join(ch for ch in str(value or "kai-sim") if ch.isalnum() or ch in "-_ ")[:32]
    return cleaned or "kai-sim"

def make_handler(expected_token: str, provider_factory: ProviderFactory):
    """Build a Kai-compatible /ws handler with one Live session per client."""
    async def handler(ws: ServerConnection) -> None:
        path = ws.request.path.split("?", 1)[0]
        if path != "/ws":
            await ws.close(code=1008, reason="unknown path")
            return
        supplied = ws.request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {expected_token}"):
            await ws.close(code=1008, reason="unauthorized client")
            return
        send_lock = asyncio.Lock()
        async def send_control(payload: dict[str, Any]) -> None:
            async with send_lock:
                await ws.send(json.dumps(payload))
        try:
            provider = provider_factory()
        except Exception as exc:
            LOG.warning("provider setup failed error_type=%s", type(exc).__name__)
            await send_control({"type": "card", "title": "Live unavailable", "lines": ["Provider setup failed."]})
            await send_control({"type": "state", "state": "idle"})
            return
        device = "kai-sim"
        ptt_open = False
        activity_started = False
        overflow = False
        pending_audio = bytearray()
        turn_input_bytes = 0
        turn_output_bytes = 0
        async def send_audio(frame: AudioFrame) -> None:
            nonlocal turn_output_bytes
            if frame.sample_rate_hz != OUTPUT_RATE or len(frame.data) % 2:
                raise ProviderError("gemini", "invalid_audio_frame", "Live output PCM format mismatch")
            for start in range(0, len(frame.data), OUTPUT_CHUNK_BYTES):
                chunk = frame.data[start : start + OUTPUT_CHUNK_BYTES]
                async with send_lock:
                    await ws.send(chunk)
                turn_output_bytes += len(chunk)
        async def on_transcript(event) -> None:
            if event.direction == "output" and event.text:
                await send_control({"type": "caption", "delta": event.text})
        async def on_interrupted(_event) -> None:
            await send_control({"type": "interrupted"})
        async def on_turn_complete(_event) -> None:
            await send_control({"type": "turn_complete"})
            LOG.info("Live turn complete device=%s input_bytes=%d output_bytes=%d; audio not persisted", device, turn_input_bytes, turn_output_bytes)
        async def on_error(error: ProviderError) -> None:
            await send_control({"type": "card", "title": "Live unavailable", "lines": [f"Provider error ({error.code}).", "No audio was saved."], "icon": "mic"})
            await send_control({"type": "state", "state": "idle"})
        provider.on_audio(send_audio)
        provider.on_transcript(on_transcript)
        provider.on_interrupted(on_interrupted)
        provider.on_turn_complete(on_turn_complete)
        provider.on_error(on_error)
        async def fail_safe(code: str) -> None:
            try:
                await send_control({"type": "card", "title": "Live unavailable", "lines": [f"Provider error ({code}).", "No audio was saved."], "icon": "mic"})
                await send_control({"type": "state", "state": "idle"})
            except ConnectionClosed:
                pass
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    if not ptt_open or overflow:
                        continue
                    if len(message) % 2:
                        overflow = True
                        pending_audio.clear()
                        LOG.warning("rejected odd PCM frame device=%s", device)
                        continue
                    if turn_input_bytes + len(message) > MAX_UTTERANCE_BYTES:
                        overflow = True
                        pending_audio.clear()
                        LOG.warning("PTT size limit exceeded device=%s", device)
                        continue
                    turn_input_bytes += len(message)
                    if not activity_started:
                        pending_audio.extend(message)
                        if len(pending_audio) < MIN_UTTERANCE_BYTES:
                            continue
                        await provider.connect()
                        await provider.start_turn()
                        activity_started = True
                        buffered = bytes(pending_audio)
                        pending_audio.clear()
                        await provider.send_audio(AudioFrame(buffered, INPUT_RATE))
                    else:
                        await provider.send_audio(AudioFrame(message, INPUT_RATE))
                    continue
                try:
                    control = json.loads(message)
                except (TypeError, json.JSONDecodeError):
                    LOG.warning("ignored malformed control frame device=%s", device)
                    continue
                if not isinstance(control, dict):
                    continue
                kind = control.get("type")
                if kind == "hello":
                    device = _safe_device(control.get("device"))
                elif kind == "ptt_start":
                    if ptt_open:
                        continue
                    ptt_open = True
                    activity_started = False
                    overflow = False
                    turn_input_bytes = 0
                    turn_output_bytes = 0
                    pending_audio.clear()
                elif kind == "ptt_end":
                    if not ptt_open:
                        continue
                    ptt_open = False
                    pending_audio.clear()
                    if overflow:
                        await send_control({"type": "card", "title": "Audio too long", "lines": ["Utterance exceeded the local limit.", "No audio was saved."], "icon": "mic"})
                        if activity_started:
                            activity_started = False
                            await provider.end_turn()
                        else:
                            await send_control({"type": "turn_complete"})
                    elif activity_started:
                        activity_started = False
                        await send_control({"type": "state", "state": "thinking"})
                        await provider.end_turn()
                    else:
                        await send_control({"type": "state", "state": "idle"})
        except ConnectionClosed:
            pass
        except ProviderError as error:
            await fail_safe(error.code)
        except Exception as exc:
            LOG.warning("client session ended device=%s error_type=%s", device, type(exc).__name__)
            await fail_safe("runtime_error")
        finally:
            pending_audio.clear()
            try:
                await provider.close()
            except Exception as exc:
                LOG.warning("provider cleanup failed device=%s error_type=%s", device, type(exc).__name__)
    return handler

async def run(host: str, port: int, token: str) -> None:
    validate_bind_host(host)
    if len(token) < 12:
        raise ValueError("AMUSANT_DEVICE_TOKEN must contain at least 12 characters")
    if os.environ.get("AMUSANT_LIVE_PROVIDER") != "gemini" or not os.environ.get("GEMINI_API_KEY"):
        raise ValueError("Set AMUSANT_LIVE_PROVIDER=gemini and inject GEMINI_API_KEY at runtime")
    LOG.info("starting private Live bridge host=%s port=%d", host, port)
    async with serve(make_handler(token, lambda: build_live_provider(os.environ)), host, port, max_size=MAX_FRAME_BYTES, ping_interval=20, ping_timeout=20) as server:
        await server.serve_forever()

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="loopback or explicit private LAN IP only")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.environ.get(DEVICE_TOKEN_ENV, "")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(args.host, args.port, token))
    except KeyboardInterrupt:
        LOG.info("Live bridge stopped")
    except (OSError, ValueError) as exc:
        parser.error(f"cannot start Live bridge: {type(exc).__name__}")

if __name__ == "__main__":
    main()
