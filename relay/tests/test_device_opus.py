from __future__ import annotations

import math
import struct
import unittest
from importlib.util import find_spec

from relay.device_opus import (
    DeviceOpusCodec,
    DeviceProtocolError,
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_PACKETS,
    MAX_OPUS_PACKET_BYTES,
    pack_opus_bundles,
    unpack_opus_bundle,
)


class OpusBundleTests(unittest.TestCase):
    def test_multiple_packets_round_trip_and_size_bounds(self):
        packets = [bytes([index % 251 + 1]) * 40 for index in range(128)]
        bundles = pack_opus_bundles(packets)
        self.assertGreater(len(bundles), 1)
        self.assertTrue(all(len(bundle) <= MAX_BUNDLE_BYTES for bundle in bundles))
        self.assertEqual([packet for bundle in bundles for packet in unpack_opus_bundle(bundle)], packets)

    def test_rejects_wrong_kind_empty_truncated_and_oversized_packets(self):
        for message in (b"", b"\x00\x01\x00\x01", b"\x02\x01"):
            with self.subTest(message=message), self.assertRaises(DeviceProtocolError):
                unpack_opus_bundle(message)
        oversized = b"\x02" + struct.pack("<H", MAX_OPUS_PACKET_BYTES + 1) + bytes(MAX_OPUS_PACKET_BYTES + 1)
        with self.assertRaises(DeviceProtocolError):
            unpack_opus_bundle(oversized)

    def test_rejects_bundle_packet_count_and_trailing_length(self):
        too_many = b"\x02" + (struct.pack("<H", 1) + b"x") * (MAX_BUNDLE_PACKETS + 1)
        with self.assertRaises(DeviceProtocolError):
            unpack_opus_bundle(too_many)
        with self.assertRaises(DeviceProtocolError):
            unpack_opus_bundle(b"\x02\x01\x00x\x02")
        with self.assertRaises(DeviceProtocolError):
            unpack_opus_bundle(bytes(MAX_BUNDLE_BYTES + 1))

    def test_rejects_oversized_encoded_packet(self):
        with self.assertRaises(DeviceProtocolError):
            pack_opus_bundles([bytes(MAX_OPUS_PACKET_BYTES + 1)])


@unittest.skipUnless(
    find_spec("av") is not None and find_spec("numpy") is not None,
    "run with the optional hardware-opus extra; 3.13 integration is a separate mandatory command",
)
class OpusCodecTests(unittest.TestCase):
    @staticmethod
    def _pcm16(rate: int, duration_ms: int) -> bytes:
        samples = rate * duration_ms // 1000
        return b"".join(
            struct.pack("<h", int(7000 * math.sin(2 * math.pi * 440 * index / rate)))
            for index in range(samples)
        )

    def test_16khz_codec_round_trip_and_final_partial_flush(self):
        source = self._pcm16(16_000, 503)
        encoder = DeviceOpusCodec(uplink_rate=16_000, downlink_rate=16_000)
        bundles = encoder.encode_downlink(source[:-38])
        bundles += encoder.encode_downlink(source[-38:])
        bundles += encoder.finish_downlink()
        self.assertTrue(bundles)
        self.assertTrue(all(len(bundle) <= MAX_BUNDLE_BYTES for bundle in bundles))

        decoder = DeviceOpusCodec(uplink_rate=16_000, downlink_rate=24_000)
        decoded = b"".join(decoder.decode_uplink(bundle) for bundle in bundles)
        self.assertGreater(len(decoded), 0)
        source_ms = len(source) / (16_000 * 2) * 1000
        decoded_ms = len(decoded) / (16_000 * 2) * 1000
        self.assertGreaterEqual(decoded_ms, source_ms - 20)
        self.assertLessEqual(decoded_ms, source_ms + 60)

    def test_24khz_output_encoder_discards_partial_tail_on_supersede(self):
        codec = DeviceOpusCodec()
        self.assertEqual(codec.encode_downlink(bytes(100)), [])
        codec.discard_downlink()
        self.assertEqual(codec.finish_downlink(), [])

    def test_rejects_odd_pcm(self):
        codec = DeviceOpusCodec()
        with self.assertRaises(DeviceProtocolError):
            codec.encode_downlink(b"\x00")


if __name__ == "__main__":
    unittest.main()
