from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "oci-a1"


def write_temp_file(test_case: unittest.TestCase, contents: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=Path(__file__).parent, prefix=".offline-secret-", delete=False
    )
    path = Path(handle.name)
    test_case.addCleanup(path.unlink, missing_ok=True)
    with handle:
        handle.write(contents)
    if os.name == "posix":
        path.chmod(0o400)
    return path


def load_module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class SecretLoaderTests(unittest.TestCase):
    def test_relay_reads_all_required_files_and_removes_file_paths(self):
        relay_entrypoint = load_module("oci_a1_relay_entrypoint", PACKAGE / "relay_entrypoint.py")
        values = {
            "GEMINI_API_KEY_FILE": "gemini",
            "AMUSANT_DEVICE_TOKEN_FILE": "token",
            "AMUSANT_DEVICE_PSKS_FILE": "psks",
            "AMUSANT_HERMES_API_KEY_FILE": "hermes",
        }
        expected = {
            "GEMINI_API_KEY": "offline-gemini-secret",
            "AMUSANT_DEVICE_TOKEN": "offline-device-secret",
            "AMUSANT_DEVICE_PSKS": "stick-a:" + "ab" * 32,
            "AMUSANT_HERMES_API_KEY": "offline-hermes-secret",
        }
        for name in values:
            target = name.removesuffix("_FILE")
            path = write_temp_file(self, expected[target] + "\n")
            values[name] = str(path)
        relay_entrypoint.load_secrets(values)
        for target, secret in expected.items():
            self.assertEqual(values[target], secret)
        self.assertFalse(any(name.endswith("_FILE") for name in values))

    def test_relay_rejects_direct_secrets_without_echoing_them(self):
        relay_entrypoint = load_module("oci_a1_relay_entrypoint_direct", PACKAGE / "relay_entrypoint.py")
        with self.assertRaises(relay_entrypoint.SecretConfigurationError) as raised:
            relay_entrypoint.load_secrets({"GEMINI_API_KEY": "never-print-this"})
        self.assertNotIn("never-print-this", str(raised.exception))

    def test_hermes_secret_wrapper_injects_key_only_into_process_environment(self):
        hermes_entrypoint = load_module("oci_a1_hermes_entrypoint", PACKAGE / "hermes_entrypoint.py")
        path = write_temp_file(self, "offline-hermes-key\n")
        with (
            patch.dict("os.environ", {"API_SERVER_KEY_FILE": str(path)}, clear=True),
            patch.object(hermes_entrypoint.os, "execvp") as execvp,
            patch.object(hermes_entrypoint.os, "umask"),
        ):
            self.assertEqual(hermes_entrypoint.main(), 0)
            execvp.assert_called_once_with("hermes", ["hermes", "gateway", "run"])
            self.assertEqual(hermes_entrypoint.os.environ["API_SERVER_KEY"], "offline-hermes-key")
            self.assertNotIn("API_SERVER_KEY_FILE", hermes_entrypoint.os.environ)


class EgressProxyTests(unittest.TestCase):
    def setUp(self):
        self.proxy = load_module("oci_a1_egress_proxy", PACKAGE / "egress_proxy.py")

    def test_accepts_https_connect_and_rejects_non_https_targets(self):
        self.assertEqual(
            self.proxy.parse_connect_target(b"CONNECT api.example.com:443 HTTP/1.1"),
            ("api.example.com", 443),
        )
        for request in (
            b"GET https://api.example.com/ HTTP/1.1",
            b"CONNECT 169.254.169.254:443 HTTP/1.1",
            b"CONNECT 10.0.0.8:443 HTTP/1.1",
            b"CONNECT api.example.com:8642 HTTP/1.1",
            b"CONNECT user@api.example.com:443 HTTP/1.1",
        ):
            with self.subTest(request=request), self.assertRaises(self.proxy.ProxyRequestError):
                self.proxy.parse_connect_target(request)

    def test_dns_results_must_all_be_public_and_use_supported_socket_family(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443))]
        with patch.object(self.proxy.asyncio, "to_thread", new=AsyncMock(return_value=public)):
            addresses = asyncio.run(self.proxy.resolve_public_addresses("api.example.com", 443))
        self.assertEqual(addresses, (self.proxy.ipaddress.ip_address("142.250.72.14"),))

        mixed = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        ]
        with patch.object(self.proxy.asyncio, "to_thread", new=AsyncMock(return_value=mixed)):
            with self.assertRaisesRegex(self.proxy.ProxyRequestError, "not public"):
                asyncio.run(self.proxy.resolve_public_addresses("rebound.example.com", 443))


if __name__ == "__main__":
    unittest.main()
