"""Translate the authenticated device WebSocket edge to/from Amusant's PCM contract."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from relay.device_opus import DeviceOpusCodec, DeviceProtocolError

LOG = logging.getLogger("amusant.p2.live_bridge")
MIN_DECODED_PTT_BYTES = 16_000 * 2 * 300 // 1000


class DeviceWebSocketAdapter:
    """Thin per-connection codec boundary; underlying handler keeps its PCM API."""

    def __init__(
        self,
        websocket: Any,
        *,
        require_hello: bool = False,
        require_opus: bool = False,
        redact_device_label: bool = False,
        hardware_mode: bool = False,
        codec_factory: Callable[[], DeviceOpusCodec] = DeviceOpusCodec,
    ):
        self._websocket = websocket
        self.request = websocket.request
        self._require_hello = require_hello
        self._require_opus = require_opus
        self._redact_device_label = redact_device_label
        self._hardware_mode = hardware_mode
        self._codec_factory = codec_factory
        self._hello_seen = False
        self._codec_mode = "pcm"
        self._codec: DeviceOpusCodec | None = None
        self._codec_lock = asyncio.Lock()
        self._turn_id = 0
        self._active_output_turn_id = 0
        self._ptt_open = False
        self._ptt_started_at: float | None = None
        self._ptt_released_at: float | None = None
        self._turn_started_at: dict[int, float] = {}
        self._turn_released_at: dict[int, float] = {}
        self._decoded_input_bytes = 0
        self._threshold_logged = False
        self._first_wire_audio_turns: set[int] = set()

    def _note_decoded_input(self, byte_count: int) -> None:
        if not self._hardware_mode or not self._ptt_open:
            return
        self._decoded_input_bytes += byte_count
        if not self._threshold_logged and self._decoded_input_bytes >= MIN_DECODED_PTT_BYTES:
            self._threshold_logged = True
            assert self._ptt_started_at is not None
            LOG.info(
                "device latency turn_id=%d event=decoded_threshold ptt_to_threshold_ms=%d decoded_pcm_bytes=%d",
                self._turn_id,
                round((time.monotonic() - self._ptt_started_at) * 1000),
                self._decoded_input_bytes,
            )

    def _note_wire_audio(self) -> None:
        turn_id = self._active_output_turn_id
        if not self._hardware_mode or turn_id < 1 or turn_id in self._first_wire_audio_turns:
            return
        self._first_wire_audio_turns.add(turn_id)
        now = time.monotonic()
        release_at = self._turn_released_at.get(turn_id)
        start_at = self._turn_started_at.get(turn_id)
        release_ms = None if release_at is None else round((now - release_at) * 1000)
        ptt_ms = None if start_at is None else round((now - start_at) * 1000)
        LOG.info(
            "device latency turn_id=%d event=first_outgoing_audio ptt_to_first_audio_ms=%s release_to_first_audio_ms=%s",
            turn_id,
            ptt_ms,
            release_ms,
        )

    async def begin_device_turn(self, turn_id: int) -> None:
        """Switch/reset downlink state only after live_bridge has fenced the old turn."""
        if not isinstance(turn_id, int) or turn_id < 1:
            raise DeviceProtocolError("invalid_bridge_turn_id")
        async with self._codec_lock:
            self._active_output_turn_id = turn_id
            self._first_wire_audio_turns.discard(turn_id)
            if self._codec is not None:
                self._codec.discard_downlink()

    def __aiter__(self):
        return self

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._websocket.close(code=code, reason=reason)

    async def _reject_connection(self, reason: str) -> None:
        await self._websocket.close(code=1008, reason=reason)
        raise StopAsyncIteration

    @staticmethod
    def _select_codec(hello: dict[str, Any]) -> str:
        advertised = hello.get("codecs")
        if advertised is None or advertised == []:
            return "pcm"
        if not isinstance(advertised, list) or any(not isinstance(item, str) for item in advertised):
            raise DeviceProtocolError("invalid_codec_declaration")
        if any(item not in {"opus", "pcm"} for item in advertised):
            raise DeviceProtocolError("unsupported_device_codec")
        return "opus" if "opus" in advertised else "pcm"

    async def __anext__(self):
        while True:
            message = await self._websocket.recv()
            if isinstance(message, bytes):
                if self._require_hello and not self._hello_seen:
                    await self._reject_connection("hello required before device media")
                if self._codec_mode == "pcm":
                    self._note_decoded_input(len(message))
                    return message
                if self._codec is None:
                    raise DeviceProtocolError("opus_codec_not_initialized")
                async with self._codec_lock:
                    decoded = self._codec.decode_uplink(message)
                if not decoded:
                    continue
                self._note_decoded_input(len(decoded))
                return decoded

            try:
                control = json.loads(message)
            except (TypeError, json.JSONDecodeError):
                return message
            if not isinstance(control, dict):
                return message
            kind = control.get("type")
            if self._require_hello and not self._hello_seen and kind != "hello":
                await self._reject_connection("hello required before device control")
            if kind == "hello":
                if self._hello_seen:
                    await self._reject_connection("duplicate hello")
                selected = self._select_codec(control)
                if self._require_opus and selected != "opus":
                    await self._reject_connection("Opus is required for StickS3 mode")
                self._codec_mode = selected
                if selected == "opus":
                    self._codec = self._codec_factory()
                self._hello_seen = True
                if self._redact_device_label:
                    # Firmware hello.device is telemetry (currently MAC-derived), never identity.
                    control = dict(control)
                    control["device"] = "sticks3"
                    message = json.dumps(control, separators=(",", ":"), ensure_ascii=False)
            elif kind == "ptt_start":
                if self._require_hello and not self._hello_seen:
                    await self._reject_connection("hello required before PTT")
                if not self._ptt_open:
                    self._ptt_open = True
                    self._turn_id += 1
                    self._ptt_started_at = time.monotonic()
                    self._turn_started_at[self._turn_id] = self._ptt_started_at
                    self._ptt_released_at = None
                    self._decoded_input_bytes = 0
                    self._threshold_logged = False
                    if self._hardware_mode:
                        LOG.info("device latency turn_id=%d event=ptt_start", self._turn_id)
                    if self._codec is not None:
                        async with self._codec_lock:
                            self._codec.reset_uplink()
            elif kind == "ptt_end" and self._ptt_open:
                self._ptt_open = False
                self._ptt_released_at = time.monotonic()
                self._turn_released_at[self._turn_id] = self._ptt_released_at
                if self._hardware_mode and self._ptt_started_at is not None:
                    LOG.info(
                        "device latency turn_id=%d event=ptt_release ptt_hold_ms=%d decoded_pcm_bytes=%d",
                        self._turn_id,
                        round((self._ptt_released_at - self._ptt_started_at) * 1000),
                        self._decoded_input_bytes,
                    )
            return message

    async def send(self, message: Any) -> None:
        if isinstance(message, bytes) and self._codec_mode == "opus":
            if self._codec is None:
                raise DeviceProtocolError("opus_codec_not_initialized")
            async with self._codec_lock:
                try:
                    for bundle in self._codec.encode_downlink(message):
                        await self._websocket.send(bundle)
                        self._note_wire_audio()
                except DeviceProtocolError:
                    self._codec.discard_downlink()
                    raise
            return

        control = None
        if isinstance(message, str) and self._codec_mode == "opus":
            try:
                parsed = json.loads(message)
                if isinstance(parsed, dict):
                    control = parsed
            except json.JSONDecodeError:
                pass

        if self._codec is not None and control is not None:
            async with self._codec_lock:
                kind = control.get("type")
                if kind == "turn_complete":
                    # This interception covers provider completion and local post-tool timeout fallback.
                    try:
                        for bundle in self._codec.finish_downlink():
                            await self._websocket.send(bundle)
                            self._note_wire_audio()
                    except DeviceProtocolError:
                        self._codec.discard_downlink()
                        raise
                elif kind == "interrupted" or (
                    kind == "state" and control.get("state") == "idle"
                ):
                    # Cancel/failure/supersede is not a successful media terminal: discard the tail.
                    self._codec.discard_downlink()
                await self._websocket.send(message)
            return
        await self._websocket.send(message)


def adapt_device_handler(
    handler: Callable[[Any], Awaitable[None]],
    *,
    hardware_mode: bool = False,
    codec_factory: Callable[[], DeviceOpusCodec] = DeviceOpusCodec,
) -> Callable[[Any], Awaitable[None]]:
    """Wrap the existing supported Fast+Slow handler without changing its PCM contract."""
    async def adapted(websocket: Any) -> None:
        if hardware_mode:
            transport = getattr(websocket, "transport", None)
            if transport is None or transport.get_extra_info("ssl_object") is None:
                await websocket.close(code=1008, reason="TLS-PSK required")
                return
        adapter = DeviceWebSocketAdapter(
            websocket,
            require_hello=hardware_mode,
            # Transport stays TLS-PSK in hardware mode; codec is still negotiated per hello.
            # The pinned Stick advertises Opus, while older authenticated firmware may omit it for PCM.
            require_opus=False,
            redact_device_label=hardware_mode,
            hardware_mode=hardware_mode,
            codec_factory=codec_factory,
        )
        try:
            await handler(adapter)
        finally:
            # Ending the inner handler must not leave an unread device connection open.
            await websocket.close()

    return adapted
