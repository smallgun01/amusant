from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "oci-a1"


def write_temp_file(test_case: unittest.TestCase, contents: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=Path(__file__).parent, prefix=".offline-env-", delete=False
    )
    path = Path(handle.name)
    test_case.addCleanup(path.unlink, missing_ok=True)
    with handle:
        handle.write(contents)
    return path


def load_preflight():
    spec = importlib.util.spec_from_file_location("oci_a1_preflight", PACKAGE / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class OfflinePackageTests(unittest.TestCase):
    def test_compose_keeps_api_and_outbound_networks_separate(self):
        compose = (PACKAGE / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("relay_api:\n    internal: true", compose)
        self.assertIn("controlled_egress:\n    internal: true", compose)
        self.assertIn("API_SERVER_HOST: ${HERMES_API_BIND_IP", compose)
        self.assertNotIn('"0.0.0.0"', compose)
        self.assertIn("host_ip: ${RELAY_HOST_PRIVATE_IP", compose)
        self.assertNotIn("published: 8642", compose)
        self.assertIn("wss_proxy: http://${EGRESS_PROXY_BIND_IP", compose)
        self.assertIn("cap_drop: [ALL]", compose)
        self.assertIn("read_only: true", compose)

    def test_preflight_requires_exact_digests_and_private_networks(self):
        preflight = load_preflight()
        digest = "a" * 64
        values = {
            "RELAY_IMAGE": f"registry.invalid/relay@sha256:{digest}",
            "HERMES_IMAGE": f"registry.invalid/hermes@sha256:{digest}",
            "EGRESS_PROXY_IMAGE": f"registry.invalid/proxy@sha256:{digest}",
            "RELAY_API_CIDR": "172.30.62.0/29",
            "RELAY_BIND_IP": "172.30.62.2",
            "HERMES_API_BIND_IP": "172.30.62.3",
            "CONTROLLED_EGRESS_CIDR": "172.30.63.0/29",
            "HERMES_EGRESS_IP": "172.30.63.2",
            "RELAY_EGRESS_IP": "172.30.63.3",
            "EGRESS_PROXY_BIND_IP": "172.30.63.4",
            "RELAY_HOST_PRIVATE_IP": "10.0.0.10",
            "RELAY_PUBLISH_PORT": "8443",
        }
        preflight.validate(values)
        with self.assertRaisesRegex(preflight.PreflightError, "sha256"):
            preflight.validate({**values, "HERMES_IMAGE": "nousresearch/hermes-agent:latest"})
        with self.assertRaisesRegex(preflight.PreflightError, "RFC1918"):
            preflight.validate({**values, "RELAY_HOST_PRIVATE_IP": "169.254.169.254"})
        with self.assertRaisesRegex(preflight.PreflightError, "overlap"):
            preflight.validate({**values, "CONTROLLED_EGRESS_CIDR": "172.30.62.0/29"})
        with self.assertRaisesRegex(preflight.PreflightError, "RFC1918 IPv4"):
            preflight.validate(
                {
                    **values,
                    "RELAY_API_CIDR": "8.8.8.0/29",
                    "RELAY_BIND_IP": "8.8.8.2",
                    "HERMES_API_BIND_IP": "8.8.8.3",
                }
            )
        with self.assertRaisesRegex(preflight.PreflightError, "usable host"):
            preflight.validate({**values, "RELAY_BIND_IP": "172.30.62.1"})
        with self.assertRaisesRegex(preflight.PreflightError, "room for the Docker gateway"):
            preflight.validate(
                {
                    **values,
                    "RELAY_API_CIDR": "172.30.62.0/30",
                    "RELAY_BIND_IP": "172.30.62.2",
                    "HERMES_API_BIND_IP": "172.30.62.3",
                }
            )

    def test_env_file_parser_rejects_duplicate_keys_without_echoing_values(self):
        preflight = load_preflight()
        path = write_temp_file(self, "RELAY_IMAGE=first\nRELAY_IMAGE=second-secret\n")
        with self.assertRaises(preflight.PreflightError) as raised:
            preflight.read_env_file(path)
        self.assertNotIn("second-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
