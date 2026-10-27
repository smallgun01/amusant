"""Source-only Grok profile tests; all endpoints and secrets are fixtures."""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "oci-a1"


def load_module(name, filename):
    spec = spec_from_file_location(name, PACKAGE / filename)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GrokProfileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / f".grok-runtime-{uuid4().hex}"
        self.root.mkdir()
        self.addCleanup(self.cleanup_root)
        (self.root / "relay").mkdir()
        self.secrets = {}
        for name in ("GEMINI_API_KEY_FILE", "DEVICE_TOKEN_FILE", "DEVICE_PSKS_FILE", "GROK_ROUTINE_TOKEN_FILE"):
            path = self.root / name.lower()
            path.write_text("offline-fixture-secret\n", encoding="utf-8")
            path.chmod(0o400)
            self.secrets[name] = str(path)
        self.values = {
            "RELAY_IMAGE": "registry.invalid/relay@sha256:" + "a" * 64,
            "EGRESS_PROXY_IMAGE": "registry.invalid/proxy@sha256:" + "b" * 64,
            "RELAY_API_CIDR": "172.30.62.0/29", "RELAY_BIND_IP": "172.30.62.2",
            "CALLBACK_API_CIDR": "172.30.64.0/29", "RELAY_CALLBACK_BIND_IP": "172.30.64.2",
            "RELAY_EGRESS_CIDR": "172.30.63.0/29", "RELAY_EGRESS_IP": "172.30.63.2",
            "EGRESS_PROXY_RELAY_BIND_IP": "172.30.63.3", "RELAY_HOST_PRIVATE_IP": "10.0.0.10",
            "AMUSANT_CLIENT_ID": "offline-owner", "AMUSANT_GROK_ROUTINE_URL": "https://routine.invalid/webhook",
            "AMUSANT_GROK_CALLBACK_URL": "https://callback.invalid/callback/results",
            "AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS": "callback.invalid",
            "AMUSANT_GROK_CALLBACK_SERVICE_PORT": "443", "OCI_DATA_DIR": str(self.root),
            "RELAY_STOP_GRACE_PERIOD": "30s", "AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS": "10",
            "RELAY_CPU_LIMIT": "0.5", "RELAY_MEMORY_LIMIT": "512m",
            "PROXY_CPU_LIMIT": "0.1", "PROXY_MEMORY_LIMIT": "128m", **self.secrets,
        }
        self.preflight = load_module("grok_preflight", "preflight.py")

    def cleanup_root(self):
        for path in self.root.iterdir():
            if path.is_file():
                path.chmod(0o600)
                path.unlink()
            else:
                path.rmdir()
        self.root.rmdir()

    def test_preflight_rejects_unsafe_network_urls_and_missing_secrets(self):
        self.preflight.validate_grok(self.values)
        changes = (
            {"RELAY_IMAGE": "latest"},
            {"RELAY_CALLBACK_BIND_IP": "0.0.0.0"},
            {"CALLBACK_API_CIDR": "172.30.62.0/29"},
            {"AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS": "attacker.invalid"},
            {"AMUSANT_GROK_CALLBACK_URL": "https://callback.invalid/callback/results?token=secret"},
            {"AMUSANT_GROK_ROUTINE_URL": "https://127.0.0.1/webhook"},
            {"AMUSANT_GROK_ROUTINE_URL": "https://localhost/webhook"},
            {"GROK_ROUTINE_TOKEN_FILE": str(self.root / "missing")},
            {"AMUSANT_GROK_ROUTINE_TOKEN": "must-not-be-inline"},
            {"RELAY_STOP_GRACE_PERIOD": "11s"},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(self.preflight.PreflightError):
                self.preflight.validate_grok({**self.values, **change})

    def test_grok_secret_file_is_loaded_without_facade_secret(self):
        entrypoint = load_module("grok_relay_entrypoint", "relay_entrypoint.py")
        env = {
            "AMUSANT_SLOW_EXECUTOR": "grok_callback", "GEMINI_API_KEY_FILE": self.secrets["GEMINI_API_KEY_FILE"],
            "AMUSANT_DEVICE_TOKEN_FILE": self.secrets["DEVICE_TOKEN_FILE"],
            "AMUSANT_DEVICE_PSKS_FILE": self.secrets["DEVICE_PSKS_FILE"],
            "AMUSANT_GROK_ROUTINE_TOKEN_FILE": self.secrets["GROK_ROUTINE_TOKEN_FILE"],
        }
        entrypoint.load_secrets(env)
        self.assertEqual(env["AMUSANT_GROK_ROUTINE_TOKEN"], "offline-fixture-secret")
        self.assertNotIn("AMUSANT_GROK_ROUTINE_TOKEN_FILE", env)
        self.assertNotIn("AMUSANT_FACADE_API_KEY", env)
        for bad in ({"AMUSANT_GROK_ROUTINE_TOKEN_FILE": ""}, {"AMUSANT_GROK_ROUTINE_TOKEN": "inline"}):
            original = {
                "AMUSANT_SLOW_EXECUTOR": "grok_callback", "GEMINI_API_KEY_FILE": self.secrets["GEMINI_API_KEY_FILE"],
                "AMUSANT_DEVICE_TOKEN_FILE": self.secrets["DEVICE_TOKEN_FILE"],
                "AMUSANT_DEVICE_PSKS_FILE": self.secrets["DEVICE_PSKS_FILE"],
                "AMUSANT_GROK_ROUTINE_TOKEN_FILE": self.secrets["GROK_ROUTINE_TOKEN_FILE"], **bad,
            }
            with self.subTest(bad=bad), self.assertRaises(entrypoint.SecretConfigurationError):
                entrypoint.load_secrets(original)

    def test_grok_preflight_cli_reports_missing_file_without_secret_values(self):
        env_file = self.root / "preflight.env"
        env_file.write_text("".join(f"{key}={value}\n" for key, value in self.values.items()), encoding="utf-8")
        command = [sys.executable, str(PACKAGE / "preflight.py"), "--profile", "grok", "--env-file", str(env_file)]
        success = subprocess.run(command, text=True, capture_output=True, timeout=10)
        self.assertEqual(success.returncode, 0, success.stderr)
        bad = dict(self.values)
        bad["GROK_ROUTINE_TOKEN_FILE"] = str(self.root / "missing")
        env_file.write_text("".join(f"{key}={value}\n" for key, value in bad.items()), encoding="utf-8")
        failure = subprocess.run(command, text=True, capture_output=True, timeout=10)
        self.assertEqual(failure.returncode, 2)
        self.assertNotIn("offline-fixture-secret", failure.stderr)

    def test_compose_has_only_two_services_and_no_published_ports(self):
        document = yaml.safe_load((PACKAGE / "compose.grok.yaml").read_text(encoding="utf-8"))
        self.assertEqual(set(document["services"]), {"relay", "egress_proxy"})
        for service in document["services"].values():
            self.assertNotIn("ports", service)
            self.assertTrue(service["read_only"])
            self.assertIn("ALL", service["cap_drop"])
        relay = document["services"]["relay"]
        self.assertEqual(set(relay["depends_on"]), {"egress_proxy"})
        self.assertEqual(relay["environment"]["AMUSANT_SLOW_EXECUTOR"], "grok_callback")
        self.assertEqual(relay["environment"]["AMUSANT_GROK_CALLBACK_MAX_INFLIGHT"], "1")
        self.assertNotIn("AMUSANT_GROK_ROUTINE_TOKEN", relay["environment"])
        self.assertIn("AMUSANT_GROK_ROUTINE_TOKEN_FILE", relay["environment"])
        self.assertTrue(document["networks"]["callback_api"]["internal"])

    def test_health_requires_both_wss_and_callback_listener(self):
        health = load_module("grok_healthcheck", "healthcheck.py")
        env = {
            "RELAY_BIND_IP": "172.30.62.2", "RELAY_LISTEN_PORT": "8443",
            "RELAY_CALLBACK_BIND_IP": "172.30.64.2", "RELAY_CALLBACK_LISTEN_PORT": "8645",
            "AMUSANT_SLOW_EXECUTOR": "grok_callback",
            "AMUSANT_GROK_CALLBACK_URL": "https://callback.invalid/callback/results",
        }
        with (patch.dict(os.environ, env, clear=True),
              patch.object(health.socket, "create_connection") as wss,
              patch.object(health.http.client, "HTTPConnection") as http):
            http.return_value.getresponse.return_value.status = 405
            self.assertEqual(health.main(), 0)
            wss.assert_called_once_with(("172.30.62.2", 8443), timeout=2)
            http.return_value.putheader.assert_called_once_with("Host", "callback.invalid")
            http.return_value.getresponse.return_value.status = 500
            self.assertEqual(health.main(), 1)

    def test_docker_compose_config_is_static_and_exposes_no_ports(self):
        if not shutil.which("docker"):
            if os.environ.get("GROK_REQUIRE_COMPOSE") == "1":
                self.fail("docker compose CLI is required for this regression")
            self.skipTest("docker compose CLI unavailable")
        env_file = self.root / "compose.env"
        env_file.write_text("".join(f"{key}={value}\n" for key, value in self.values.items()), encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "DOCKER_CONFIG": str(self.root)}
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(env_file), "-f", str(PACKAGE / "compose.grok.yaml"), "config", "--format", "json"],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(set(rendered["services"]), {"relay", "egress_proxy"})
        for service in rendered["services"].values():
            self.assertFalse(service.get("ports"))
        self.assertEqual(rendered["services"]["relay"]["environment"]["AMUSANT_GROK_CALLBACK_MAX_INFLIGHT"], "1")
        self.assertNotIn("offline-fixture-secret", result.stdout)


class GrokProxyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.proxy = load_module("grok_egress_proxy", "egress_proxy.py")

    async def test_single_peer_mode_rejects_bad_configuration_and_unauthorized_client(self):
        env = {"EGRESS_PROXY_MODE": "grok", "EGRESS_PROXY_BIND_IPS": "172.30.63.3", "EGRESS_PROXY_ALLOWED_CLIENTS": "172.30.63.2"}
        binds, peers = self.proxy.proxy_config(env)
        self.assertEqual(len(binds), 1)
        self.assertEqual(len(peers), 1)
        for change in (
            {"EGRESS_PROXY_BIND_IPS": "0.0.0.0"},
            {"EGRESS_PROXY_ALLOWED_CLIENTS": "172.30.63.2,172.30.63.4"},
            {"EGRESS_PROXY_ALLOWED_CLIENTS": "172.30.63.3"},
            {"EGRESS_PROXY_MODE": "unknown"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.proxy.proxy_config({**env, **change})
        async def handle(reader, writer):
            await self.proxy.handle_connect(reader, writer, allowed_peers=peers)
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
            response = await asyncio.wait_for(reader.read(), 2)
            self.assertIn(b" 403 ", response.split(b"\r\n", 1)[0])
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
