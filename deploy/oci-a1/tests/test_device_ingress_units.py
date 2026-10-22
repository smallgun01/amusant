from __future__ import annotations

import ast
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "oci-a1"
sys.path.insert(0, str(PACKAGE))

from render_device_ingress import render  # noqa: E402


def valid_values():
    digest = "a" * 64
    return {
        "RELAY_IMAGE": f"registry.invalid/relay@sha256:{digest}",
        "FACADE_IMAGE": f"registry.invalid/facade@sha256:{digest}",
        "HERMES_IMAGE": f"registry.invalid/hermes@sha256:{digest}",
        "EGRESS_PROXY_IMAGE": f"registry.invalid/proxy@sha256:{digest}",
        "RELAY_API_CIDR": "172.30.62.0/29",
        "RELAY_BIND_IP": "172.30.62.2",
        "FACADE_RELAY_BIND_IP": "172.30.62.3",
        "HERMES_API_CIDR": "172.30.64.0/29",
        "FACADE_HERMES_BIND_IP": "172.30.64.2",
        "HERMES_API_BIND_IP": "172.30.64.3",
        "RELAY_EGRESS_CIDR": "172.30.63.0/29",
        "RELAY_EGRESS_IP": "172.30.63.2",
        "EGRESS_PROXY_RELAY_BIND_IP": "172.30.63.3",
        "HERMES_EGRESS_CIDR": "172.30.65.0/29",
        "HERMES_EGRESS_IP": "172.30.65.2",
        "EGRESS_PROXY_HERMES_BIND_IP": "172.30.65.3",
        "RELAY_HOST_PRIVATE_IP": "10.0.0.10",
        "RELAY_PUBLISH_PORT": "443",
    }


class DeviceIngressUnitTests(unittest.TestCase):
    def test_renderer_binds_private_host_ip_and_fixed_relay_listener(self):
        units = render(valid_values())
        socket_unit = units["amusant-device-ingress.socket"]
        service_unit = units["amusant-device-ingress.service"]
        self.assertIn("ListenStream=10.0.0.10:443", socket_unit)
        self.assertIn("Accept=no", socket_unit)
        self.assertIn("systemd-socket-proxyd --connections-max=31", service_unit)
        self.assertIn("--exit-idle-time=120s 172.30.62.2:8443", service_unit)
        self.assertIn("DynamicUser=yes", service_unit)
        self.assertIn("User=amusant-device-ingress", service_unit)
        self.assertIn("NoNewPrivileges=yes", service_unit)
        self.assertIn("CapabilityBoundingSet=", service_unit)
        self.assertNotIn("0.0.0.0", socket_unit + service_unit)
        self.assertNotIn("@RELAY_", socket_unit + service_unit)

    def test_renderer_never_copies_environment_secrets_to_unit_files(self):
        values = valid_values()
        values.update({
            "GEMINI_API_KEY": "synthetic-secret-marker",
            "AMUSANT_DEVICE_TOKEN": "another-synthetic-marker",
        })
        rendered = "\n".join(render(values).values())
        self.assertNotIn("synthetic-secret-marker", rendered)
        self.assertNotIn("another-synthetic-marker", rendered)

    def test_only_raw_tls_psk_port_443_is_accepted(self):
        values = valid_values()
        values["RELAY_PUBLISH_PORT"] = "8443"
        with self.assertRaisesRegex(ValueError, "must be 443"):
            render(values)

    def test_upstream_must_be_the_relay_address_on_the_relay_api_network(self):
        values = valid_values()
        values["RELAY_BIND_IP"] = "172.30.64.2"
        with self.assertRaisesRegex(ValueError, "usable host"):
            render(values)

    def test_test_mode_limits_are_bounded(self):
        values = valid_values()
        units = render(values, max_connections=3, exit_idle_seconds=2)
        self.assertIn("--connections-max=2 --exit-idle-time=2s", units["amusant-device-ingress.service"])
        with self.assertRaisesRegex(ValueError, "between 2 and 256"):
            render(values, max_connections=1)
        with self.assertRaisesRegex(ValueError, "connection limit"):
            render(values, max_connections=257)
        with self.assertRaisesRegex(ValueError, "idle exit"):
            render(values, exit_idle_seconds=3601)

    def test_private_test_client_has_an_ip_reserved_away_from_relay(self):
        fixture = (PACKAGE / "tests" / "device_ingress_poc.yaml").read_text(encoding="utf-8")
        client = fixture.split("  private_client:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        self.assertIn("ipv4_address: 172.30.62.4", client)

    def test_synthetic_websocket_client_never_uses_an_environment_proxy(self):
        source = (PACKAGE / "tests" / "device_ingress_poc" / "client.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "connect"
        ]
        self.assertGreaterEqual(len(calls), 5)
        for call in calls:
            proxy = next((kw.value for kw in call.keywords if kw.arg == "proxy"), None)
            self.assertIsInstance(proxy, ast.Constant)
            self.assertIsNone(proxy.value)


if __name__ == "__main__":
    unittest.main()

