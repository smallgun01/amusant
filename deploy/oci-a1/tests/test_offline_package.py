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
        "RELAY_PUBLISH_PORT": "8443",
    }


class OfflinePackageTests(unittest.TestCase):
    def test_compose_isolates_relay_facade_and_hermes_networks(self):
        compose = (PACKAGE / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("relay_api:\n    internal: true", compose)
        self.assertIn("hermes_api:\n    internal: true", compose)
        self.assertIn("relay_egress:\n    internal: true", compose)
        self.assertIn("hermes_egress:\n    internal: true", compose)
        relay = compose.split("  relay:\n", 1)[1].split("\n  facade:\n", 1)[0]
        facade = compose.split("  facade:\n", 1)[1].split("\n  hermes:\n", 1)[0]
        hermes = compose.split("  hermes:\n", 1)[1].split("\n  egress_proxy:\n", 1)[0]
        proxy = compose.split("  egress_proxy:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        self.assertIn("AMUSANT_HERMES_RUNS_URL: http://facade:8643", relay)
        self.assertNotIn("HERMES_API_BIND_IP", relay)
        self.assertNotIn("hermes_api:", relay)
        self.assertNotIn("hermes_egress:", relay)
        self.assertNotIn("hermes_api_key", relay)
        self.assertIn("facade_api_key", relay)
        self.assertIn("relay_api:", facade)
        self.assertIn("hermes_api:", facade)
        self.assertNotIn("relay_egress:", facade)
        self.assertNotIn("proxy_outbound", facade)
        self.assertIn("facade_api_key", facade)
        self.assertIn("hermes_api_key", facade)
        self.assertIn("hermes_api:", hermes)
        self.assertIn("hermes_egress:", hermes)
        self.assertNotIn("relay_api:", hermes)
        self.assertNotIn("relay_egress:", hermes)
        self.assertNotIn("ports:", hermes)
        self.assertNotIn("facade_api_key", hermes)
        self.assertIn("API_SERVER_HOST: ${HERMES_API_BIND_IP", hermes)
        self.assertIn("EGRESS_PROXY_BIND_IPS:", proxy)
        self.assertIn("relay_egress:", proxy)
        self.assertIn("hermes_egress:", proxy)
        self.assertIn("proxy_outbound:", proxy)
        self.assertNotIn('"0.0.0.0"', compose)
        self.assertIn("host_ip: ${RELAY_HOST_PRIVATE_IP", compose)
        self.assertNotIn("published: 8642", compose)
        self.assertIn("cap_drop: [ALL]", compose)
        self.assertIn("read_only: true", compose)

    def test_runtime_state_and_legacy_detail_boundary_are_separate(self):
        compose = (PACKAGE / "compose.yaml").read_text(encoding="utf-8")
        relay = compose.split("  relay:\n", 1)[1].split("\n  facade:\n", 1)[0]
        facade = (PACKAGE / "facade.py").read_text(encoding="utf-8")
        plugin = (PACKAGE / "hermes_plugin" / "__init__.py").read_text(encoding="utf-8")
        projection = (PACKAGE / "hermes_plugin" / "reprojection.py").read_text(encoding="utf-8")
        relay_parser = (ROOT / "relay" / "hermes_result.py").read_text(encoding="utf-8")
        slow_path = (ROOT / "relay" / "slow_path.py").read_text(encoding="utf-8")
        self.assertNotIn("result_state:", relay)
        self.assertNotIn("POC_STATE_DIR", facade + plugin + projection)
        self.assertNotIn("POC_FACADE_KEY", facade)
        self.assertIn("credentials must be different", facade)
        self.assertIn("facade-ledger.sqlite3", facade)
        self.assertIn('"details_md"', plugin)
        self.assertNotIn('"details_md": value["details_md"]', projection)
        self.assertNotIn('"details_md"', relay_parser.split("expected = {", 1)[1].split("}", 1)[0])
        self.assertNotIn('payload["details_md"]', slow_path)

    def test_preflight_requires_pinned_images_and_four_non_overlapping_networks(self):
        preflight = load_preflight()
        values = valid_values()
        preflight.validate(values)
        with self.assertRaisesRegex(preflight.PreflightError, "sha256"):
            preflight.validate({**values, "FACADE_IMAGE": "facade:latest"})
        with self.assertRaisesRegex(preflight.PreflightError, "RFC1918 IPv4"):
            preflight.validate({**values, "RELAY_HOST_PRIVATE_IP": "169.254.169.254"})
        with self.assertRaisesRegex(preflight.PreflightError, "must not overlap"):
            preflight.validate({**values, "HERMES_API_CIDR": "172.30.62.0/29"})
        with self.assertRaisesRegex(preflight.PreflightError, "RFC1918 IPv4 subnet"):
            preflight.validate({
                **values,
                "RELAY_API_CIDR": "8.8.8.0/29",
                "RELAY_BIND_IP": "8.8.8.2",
                "FACADE_RELAY_BIND_IP": "8.8.8.3",
            })
        with self.assertRaisesRegex(preflight.PreflightError, "usable host"):
            preflight.validate({**values, "RELAY_BIND_IP": "172.30.62.1"})
        with self.assertRaisesRegex(preflight.PreflightError, "room for the Docker gateway"):
            preflight.validate({
                **values,
                "RELAY_API_CIDR": "172.30.62.0/30",
                "RELAY_BIND_IP": "172.30.62.2",
                "FACADE_RELAY_BIND_IP": "172.30.62.3",
            })

    def test_env_file_parser_rejects_duplicate_keys_without_echoing_values(self):
        preflight = load_preflight()
        path = write_temp_file(self, "RELAY_IMAGE=first\nRELAY_IMAGE=second-secret\n")
        with self.assertRaises(preflight.PreflightError) as raised:
            preflight.read_env_file(path)
        self.assertNotIn("second-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
