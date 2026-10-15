"""Opus framing and codecs for the unmodified Kai StickS3 edge link.

The framing/codec implementation is adapted from Kai v0.12.0
relay/kai_relay/opus.py (MIT; upstream LICENSE notice reproduced below).
PyAV is imported only when an Opus connection is selected, leaving the
existing PCM-only Python 3.11/3.12 development path usable without the extra.

MIT License
Copyright (c) 2026 zbruceli

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import fractions
import struct
from typing import Iterable

KIND_OPUS = 0x02
FRAME_MS = 20
MAX_BUNDLE_BYTES = 4096
MAX_BUNDLE_PACKETS = 128
MAX_OPUS_PACKET_BYTES = 1276
UPLINK_RATE = 16_000
DOWNLINK_RATE = 24_000
DOWNLINK_BITRATE = 32_000
MAX_OPUS_PACKET_DURATION_MS = 120
MAX_DECODED_PCM_BYTES_PER_BUNDLE = (
    UPLINK_RATE * MAX_OPUS_PACKET_DURATION_MS // 1000 * MAX_BUNDLE_PACKETS * 2
)


class DeviceProtocolError(ValueError):
    """A bounded, sanitized edge-protocol or codec error."""


def unpack_opus_bundle(message: bytes) -> tuple[bytes, ...]:
    """Parse one strict Kai 0x02 bundle; reject malformed tails, never truncate silently."""
    if not isinstance(message, bytes) or not 1 < len(message) <= MAX_BUNDLE_BYTES:
        raise DeviceProtocolError("invalid_opus_bundle_size")
    if message[0] != KIND_OPUS:
        raise DeviceProtocolError("invalid_opus_bundle_kind")
    packets: list[bytes] = []
    offset = 1
    while offset < len(message):
        if len(packets) >= MAX_BUNDLE_PACKETS or offset + 2 > len(message):
            raise DeviceProtocolError("invalid_opus_bundle_length")
        packet_size = struct.unpack_from("<H", message, offset)[0]
        offset += 2
        if packet_size == 0 or packet_size > MAX_OPUS_PACKET_BYTES or offset + packet_size > len(message):
            raise DeviceProtocolError("invalid_opus_packet_length")
        packets.append(message[offset : offset + packet_size])
        offset += packet_size
    if not packets:
        raise DeviceProtocolError("empty_opus_bundle")
    return tuple(packets)


def pack_opus_bundles(packets: Iterable[bytes]) -> list[bytes]:
    """Pack 20 ms Opus packets into ordered Kai messages, each at most 4 KiB."""
    messages: list[bytes] = []
    current = bytearray([KIND_OPUS])
    packet_count = 0
    for packet in packets:
        if not isinstance(packet, bytes) or not packet or len(packet) > MAX_OPUS_PACKET_BYTES:
            raise DeviceProtocolError("invalid_encoded_opus_packet")
        entry = struct.pack("<H", len(packet)) + packet
        if packet_count and (
            len(current) + len(entry) > MAX_BUNDLE_BYTES or packet_count >= MAX_BUNDLE_PACKETS
        ):
            messages.append(bytes(current))
            current = bytearray([KIND_OPUS])
            packet_count = 0
        if len(current) + len(entry) > MAX_BUNDLE_BYTES:
            raise DeviceProtocolError("opus_packet_exceeds_bundle_limit")
        current.extend(entry)
        packet_count += 1
    if packet_count:
        messages.append(bytes(current))
    return messages


class DeviceOpusCodec:
    """Per-WebSocket Opus decoder/encoder; caller serializes access and owns turn resets."""

    def __init__(self, uplink_rate: int = UPLINK_RATE, downlink_rate: int = DOWNLINK_RATE):
        try:
            import av
            import numpy as np
        except ImportError:
            raise DeviceProtocolError("opus_extra_not_installed") from None
        self._av = av
        self._np = np
        self.uplink_rate = uplink_rate
        self.downlink_rate = downlink_rate
        self._create_decoder()
        self._create_encoder()

    def _create_decoder(self) -> None:
        try:
            self._decoder = self._av.CodecContext.create("libopus", "r")
            self._decoder.sample_rate = self.uplink_rate
            self._decoder.layout = "mono"
            self._resampler = self._av.AudioResampler(format="s16", layout="mono", rate=self.uplink_rate)
        except Exception:
            raise DeviceProtocolError("opus_decoder_unavailable") from None

    def _create_encoder(self) -> None:
        try:
            encoder = self._av.CodecContext.create("libopus", "w")
            encoder.sample_rate = self.downlink_rate
            encoder.layout = "mono"
            encoder.format = "s16"
            encoder.bit_rate = DOWNLINK_BITRATE
            encoder.time_base = fractions.Fraction(1, self.downlink_rate)
            encoder.options = {"application": "voip", "frame_duration": str(FRAME_MS)}
            encoder.open()
        except Exception:
            raise DeviceProtocolError("opus_encoder_unavailable") from None
        self._encoder = encoder
        self._encoder_pts = 0
        self._encoder_finalized = False
        self._pending_pcm = bytearray()
        self._frame_bytes = self.downlink_rate * FRAME_MS // 1000 * 2

    def reset_uplink(self) -> None:
        """Reset mic decoder eagerly when a new device PTT begins."""
        self._create_decoder()

    def reset_turn(self) -> None:
        """Reset both directions when the owning bridge confirms a new turn."""
        self.reset_uplink()
        self._create_encoder()

    def discard_downlink(self) -> None:
        """Discard a partial response on interrupt, cancellation, failure, or supersede."""
        self._create_encoder()

    def decode_uplink(self, message: bytes) -> bytes:
        """Decode one bounded bundle into PCM16LE mono at 16 kHz."""
        packets = unpack_opus_bundle(message)
        pcm = bytearray()
        for packet in packets:
            try:
                frames = self._decoder.decode(self._av.Packet(packet))
                for frame in frames:
                    for converted in self._resampler.resample(frame):
                        samples = converted.to_ndarray().astype(self._np.dtype("<i2"), copy=False)
                        pcm.extend(samples.tobytes())
                        if len(pcm) > MAX_DECODED_PCM_BYTES_PER_BUNDLE:
                            raise DeviceProtocolError("opus_bundle_decoded_audio_too_large")
            except DeviceProtocolError:
                raise
            except Exception:
                raise DeviceProtocolError("invalid_opus_packet") from None
        if len(pcm) % 2:
            raise DeviceProtocolError("invalid_decoded_pcm")
        return bytes(pcm)

    def _encode_frame(self, pcm: bytes) -> list[bytes]:
        samples_per_frame = self.downlink_rate * FRAME_MS // 1000
        try:
            samples = self._np.frombuffer(pcm, dtype="<i2").reshape(1, samples_per_frame)
            frame = self._av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate = self.downlink_rate
            frame.pts = self._encoder_pts
            self._encoder_pts += samples_per_frame
            return [bytes(packet) for packet in self._encoder.encode(frame)]
        except Exception:
            raise DeviceProtocolError("opus_encode_failed") from None

    def encode_downlink(self, pcm: bytes) -> list[bytes]:
        """Encode PCM16LE mono at 24 kHz; retain a partial 20 ms frame in bounded memory."""
        if not isinstance(pcm, bytes) or len(pcm) % 2 or self._encoder_finalized:
            raise DeviceProtocolError("invalid_downlink_pcm")
        self._pending_pcm.extend(pcm)
        packets: list[bytes] = []
        while len(self._pending_pcm) >= self._frame_bytes:
            frame = bytes(self._pending_pcm[: self._frame_bytes])
            del self._pending_pcm[: self._frame_bytes]
            packets.extend(self._encode_frame(frame))
        return pack_opus_bundles(packets)

    def finish_downlink(self) -> list[bytes]:
        """Pad/encode the final partial 20 ms frame and drain before a terminal marker."""
        if self._encoder_finalized:
            return []
        packets: list[bytes] = []
        if self._pending_pcm:
            frame = bytes(self._pending_pcm).ljust(self._frame_bytes, b"\0")
            self._pending_pcm.clear()
            packets.extend(self._encode_frame(frame))
        try:
            packets.extend(bytes(packet) for packet in self._encoder.encode(None))
        except Exception:
            raise DeviceProtocolError("opus_encoder_flush_failed") from None
        self._encoder_finalized = True
        return pack_opus_bundles(packets)
