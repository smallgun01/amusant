#!/usr/bin/env python3
"""LAN-only Amusant P1 mock. No model calls, tools, disk audio, or persistence."""

from __future__ import annotations

import argparse
import array
import asyncio
import hmac
import ipaddress
import json
import logging
import math
import os
import sys
import time
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

INPUT_RATE = 16_000
OUTPUT_RATE = 24_000
MAX_UTTERANCE_BYTES = 1_000_000  # about 31 seconds of mono PCM16 at 16 kHz
MAX_FRAME_BYTES = 64 * 1024
OUTPUT_CHUNK_BYTES = 4 * 1024
MOCK_TOKEN_ENV = "AMUSANT_MOCK_TOKEN"
LOG = logging.getLogger("amusant.p1.mock")


def validate_bind_host(host: str) -> None:
    """Permit loopback or a private LAN address; never bind a public address."""
    if host in {"localhost", "127.0.0.1", "::1"}:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Use localhost, loopback, or an explicit private IP address") from exc
    if address.is_unspecified or not address.is_private:
        raise ValueError("Mock relay may bind only to loopback or a private LAN IP")


def resample_pcm16(data: bytes, source_rate: int = INPUT_RATE, target_rate: int = OUTPUT_RATE) -> bytes:
    """Small linear PCM16 mono resampler; keeps P1 independent of audio/model APIs."""
    if len(data) % 2:
        data = data[:-1]
    source = array.array("h")
    source.frombytes(data)
    if sys.byteorder != "little":
        source.byteswap()
    if not source:
        return b""

    out_count = (len(source) * target_rate) // source_rate
    output = array.array("h")
    for index in range(out_count):
        position = index * source_rate / target_rate
        left = min(int(position), len(source) - 1)
        right = min(left + 1, len(source) - 1)
        fraction = position - left
        sample = source[left] + (source[right] - source[left]) * fraction
        sample = max(-32768, min(32767, round(sample * 0.65)))
        output.append(sample)
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def make_test_tone(rate: int = OUTPUT_RATE, duration_s: float = 0.12, frequency_hz: float = 660.0) -> bytes:
    """A quiet marker confirms the return-audio path even if the mic input is silent."""
    count = int(rate * duration_s)
    output = array.array("h")
    for index in range(count):
        envelope = min(1.0, index / max(1, int(rate * 0.01)))
        sample = int(1800 * envelope * math.sin(2 * math.pi * frequency_hz * index / rate))
        output.append(sample)
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def pcm_chunks(data: bytes):
    chunk_size = OUTPUT_CHUNK_BYTES
    for start in range(0, len(data), chunk_size):
        yield data[start : start + chunk_size]


def make_handler(expected_token: str):
    async def handler(ws: ServerConnection) -> None:
        path = ws.request.path.split("?", 1)[0]
        if path != "/ws":
            await ws.close(code=1008, reason="unknown path")
            return
        supplied = ws.request.headers.get("Authorization", "")
        expected = f"Bearer {expected_token}"
        if not hmac.compare_digest(supplied, expected):
            await ws.close(code=1008, reason="unauthorized test client")
            return

        device = "kai-sim"
        ptt_open = False
        overflow = False
        audio = bytearray()
        ptt_started = 0.0

        try:
            async for message in ws:
                if isinstance(message, bytes):
                    if not ptt_open:
                        continue
                    if len(audio) + len(message) > MAX_UTTERANCE_BYTES:
                        overflow = True
                        audio.clear()
                    elif not overflow:
                        audio.extend(message)
                    continue

                try:
                    control: Any = json.loads(message)
                except (TypeError, json.JSONDecodeError):
                    LOG.warning("ignored malformed control frame")
                    continue
                if not isinstance(control, dict):
                    continue

                kind = control.get("type")
                if kind == "hello":
                    raw_device = str(control.get("device") or "kai-sim")
                    device = "".join(ch for ch in raw_device if ch.isalnum() or ch in "-_ ")[:32] or "kai-sim"
                    LOG.info("client connected device=%s", device)
                elif kind == "ptt_start":
                    ptt_open = True
                    overflow = False
                    audio.clear()
                    ptt_started = time.monotonic()
                    await ws.send(json.dumps({"type": "state", "state": "thinking"}))
                elif kind == "ptt_end":
                    ptt_open = False
                    capture_ms = int((time.monotonic() - ptt_started) * 1000) if ptt_started else 0
                    captured = bytes(audio)
                    audio.clear()
                    if overflow:
                        await ws.send(json.dumps({
                            "type": "card",
                            "title": "Amusant P1 mock",
                            "lines": ["Input exceeded the 30-second test limit.", "No audio was saved."],
                            "icon": "mic",
                        }))
                        await ws.send(json.dumps({"type": "turn_complete"}))
                        LOG.info("PTT rejected device=%s reason=utterance_limit", device)
                        continue

                    duration_s = len(captured) / (INPUT_RATE * 2)
                    response_audio = make_test_tone() + resample_pcm16(captured)
                    await ws.send(json.dumps({
                        "type": "card",
                        "title": "Amusant P1 mock",
                        "lines": [
                            f"Captured {duration_s:.2f}s PCM16 audio.",
                            "Local tone/echo only; no model or API call.",
                        ],
                        "icon": "mic",
                    }))
                    await ws.send(json.dumps({
                        "type": "caption",
                        "delta": "P1 mock received audio; playing a short tone and local echo.",
                    }))
                    for chunk in pcm_chunks(response_audio):
                        await ws.send(chunk)
                    await ws.send(json.dumps({"type": "turn_complete"}))
                    LOG.info(
                        "PTT complete device=%s capture_ms=%d input_bytes=%d output_bytes=%d; audio not persisted",
                        device,
                        capture_ms,
                        len(captured),
                        len(response_audio),
                    )
        except Exception as exc:
            # Avoid dumping frames, audio, or authorization headers to logs.
            LOG.warning("client session ended device=%s error_type=%s", device, type(exc).__name__)

    return handler


async def run(host: str, port: int, token: str) -> None:
    validate_bind_host(host)
    LOG.info("starting P1 mock on ws://%s:%d/ws; no external model, no audio persistence", host, port)
    async with serve(
        make_handler(token),
        host,
        port,
        max_size=MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
    ) as server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="loopback or a private LAN IP only")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    token = os.environ.get(MOCK_TOKEN_ENV, "")
    if len(token) < 12:
        parser.error(f"set {MOCK_TOKEN_ENV} to a test-only value of at least 12 characters")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(args.host, args.port, token))
    except KeyboardInterrupt:
        LOG.info("mock relay stopped")
    except OSError as exc:
        parser.error(f"cannot bind {args.host}:{args.port}: {type(exc).__name__}")


if __name__ == "__main__":
    main()
