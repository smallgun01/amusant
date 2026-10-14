import unittest
from types import SimpleNamespace
from unittest.mock import patch

from relay.device_tls_psk import (
    PSK_BYTES,
    build_server_context,
    parse_psks,
    psk_callbacks_supported,
    validate_hardware_bind_host,
)


class TlsPskConfigTests(unittest.TestCase):
    def test_parses_unique_32_byte_keys(self):
        first = bytes(range(PSK_BYTES))
        second = bytes(range(PSK_BYTES, PSK_BYTES * 2))
        parsed = parse_psks(f"stick-a:{first.hex()},stick_b:{second.hex()}")
        self.assertEqual(parsed, {"stick-a": first, "stick_b": second})

    def test_rejects_missing_malformed_duplicate_and_reused_keys(self):
        valid_key = bytes(range(PSK_BYTES)).hex()
        cases = [
            None,
            "",
            "bad identity:" + valid_key,
            "stick-a:abcd",
            "stick-a:" + "z" * (PSK_BYTES * 2),
            f"stick-a:{valid_key},stick-a:{bytes(reversed(range(PSK_BYTES))).hex()}",
            f"stick-a:{valid_key},stick-b:{valid_key}",
        ]
        for value in cases:
            with self.subTest(value="<redacted>"), self.assertRaises(ValueError) as raised:
                parse_psks(value)
            self.assertNotIn(valid_key, str(raised.exception))

    def test_tls_psk_unsupported_runtime_fails_closed(self):
        with patch("relay.device_tls_psk.psk_callbacks_supported", return_value=False):
            with self.assertRaisesRegex(ValueError, "Python 3.13"):
                build_server_context({"stick-a": bytes(range(PSK_BYTES))})

    def test_actual_python_build_psk_capability_matches_fail_closed_guard(self):
        keys = {"stick-a": bytes(range(PSK_BYTES))}
        if not psk_callbacks_supported():
            with self.assertRaisesRegex(ValueError, "Python 3.13"):
                build_server_context(keys)
            return
        context = build_server_context(keys)
        self.assertEqual(context.minimum_version, context.maximum_version)
        self.assertEqual(context.minimum_version.name, "TLSv1_2")

    def test_hardware_listener_requires_explicit_private_lan_ip(self):
        private_address = SimpleNamespace(
            is_private=True,
            is_loopback=False,
            is_unspecified=False,
            is_link_local=False,
        )
        with patch("relay.device_tls_psk.ipaddress.ip_address", return_value=private_address):
            validate_hardware_bind_host("private-host")
        for host in ("127.0.0.1", "0.0.0.0", "8.8.8.8", "router.local"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_hardware_bind_host(host)


if __name__ == "__main__":
    unittest.main()
