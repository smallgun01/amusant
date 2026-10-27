"""Static safety checks for the Owner-triggered Grok image release workflow."""
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "grok-arm64-image-release.yml"
STAGING_ENV = ROOT / "deploy" / "oci-a1" / "grok-staging.env.example"


class GrokImageReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    def test_release_is_manual_and_off_by_default(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["publish_images"]["default"], "false")
        self.assertEqual(inputs["publish_confirmation"]["default"], "NO")
        self.assertEqual(set(inputs["publish_confirmation"]["options"]), {"NO", "PUBLISH"})

    def test_registry_write_is_scoped_to_the_guarded_publish_job(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        jobs = self.workflow["jobs"]
        self.assertEqual(jobs["guard"]["permissions"], {})
        self.assertEqual(jobs["publish"]["permissions"], {"contents": "read", "packages": "write"})
        guard_script = jobs["guard"]["steps"][0]["run"]
        self.assertIn('"$DISPATCH_ACTOR" != "smallgun01"', guard_script)
        self.assertIn('"$DISPATCH_REF" != "refs/heads/main"', guard_script)
        self.assertIn('"$PUBLISH_CONFIRMATION" != "PUBLISH"', guard_script)
        self.assertIn("needs.guard.outputs.publish == 'true'", jobs["publish"]["if"])

    def test_only_the_two_grok_images_are_built_for_arm64(self):
        steps = self.workflow["jobs"]["publish"]["steps"]
        builds = [step for step in steps if step.get("uses") == "docker/build-push-action@v6"]
        self.assertEqual(len(builds), 2)
        self.assertEqual(
            {step["with"]["file"] for step in builds},
            {"deploy/oci-a1/Dockerfile.relay", "deploy/oci-a1/Dockerfile.egress-proxy"},
        )
        self.assertTrue(all(step["with"]["platforms"] == "linux/arm64" for step in builds))
        self.assertTrue(all(step["with"]["push"] == "true" for step in builds))
        self.assertEqual(self.workflow["jobs"]["publish"]["runs-on"], "ubuntu-24.04-arm")

    def test_digest_references_are_validated_and_reported(self):
        jobs = self.workflow["jobs"]
        ref_step = next(step for step in jobs["publish"]["steps"] if step.get("id") == "references")
        self.assertIn("^sha256:[0-9a-f]{64}$", ref_step["run"])
        self.assertIn("ghcr.io/smallgun01/amusant-relay@$RELAY_DIGEST", ref_step["run"])
        self.assertIn("ghcr.io/smallgun01/amusant-egress-proxy@$EGRESS_PROXY_DIGEST", ref_step["run"])
        self.assertEqual(
            set(jobs["publish"]["outputs"]),
            {"relay_image", "egress_proxy_image"},
        )

    def test_no_cloud_or_provider_secret_is_referenced(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        secret_references = re.findall(r"\$\{\{\s*(secrets\.[A-Za-z0-9_]+)\s*\}\}", source)
        self.assertEqual(secret_references, ["secrets.GITHUB_TOKEN"])
        self.assertNotIn("GEMINI_API_KEY", source)
        self.assertNotIn("CLOUDFLARE", source)
        self.assertNotIn("OCI_", source)

    def test_staging_template_is_grok_only_and_uses_a_fresh_ledger_path(self):
        values = {
            line.partition("=")[0]: line.partition("=")[2]
            for line in STAGING_ENV.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        required = {
            "RELAY_IMAGE", "EGRESS_PROXY_IMAGE", "RELAY_API_CIDR", "CALLBACK_API_CIDR",
            "RELAY_EGRESS_CIDR", "RELAY_BIND_IP", "RELAY_CALLBACK_BIND_IP", "RELAY_EGRESS_IP",
            "EGRESS_PROXY_RELAY_BIND_IP", "RELAY_HOST_PRIVATE_IP", "AMUSANT_CLIENT_ID",
            "AMUSANT_GROK_ROUTINE_URL", "AMUSANT_GROK_CALLBACK_URL",
            "AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS", "OCI_DATA_DIR", "RELAY_STOP_GRACE_PERIOD",
            "AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS", "RELAY_CPU_LIMIT", "RELAY_MEMORY_LIMIT",
            "PROXY_CPU_LIMIT", "PROXY_MEMORY_LIMIT", "GEMINI_API_KEY_FILE", "DEVICE_TOKEN_FILE",
            "DEVICE_PSKS_FILE", "GROK_ROUTINE_TOKEN_FILE",
        }
        self.assertTrue(required.issubset(values))
        self.assertEqual(
            {name for name in values if name.endswith("_IMAGE")},
            {"RELAY_IMAGE", "EGRESS_PROXY_IMAGE"},
        )
        self.assertEqual(values["OCI_DATA_DIR"], "/mnt/amusant-data/grok")
        self.assertEqual(values["AMUSANT_GROK_CALLBACK_URL"], "https://callback.example.invalid/callback/results")
        self.assertTrue(values["RELAY_IMAGE"].endswith("REPLACE_WITH_RELEASE_DIGEST"))
        self.assertTrue(values["EGRESS_PROXY_IMAGE"].endswith("REPLACE_WITH_RELEASE_DIGEST"))
        self.assertNotEqual(values["OCI_DATA_DIR"], "/mnt/amusant-data/relay")
        self.assertTrue(all(values[name].startswith("/mnt/amusant-secrets/grok/") for name in (
            "GEMINI_API_KEY_FILE", "DEVICE_TOKEN_FILE", "DEVICE_PSKS_FILE", "GROK_ROUTINE_TOKEN_FILE",
        )))
        self.assertFalse({"GEMINI_API_KEY", "AMUSANT_DEVICE_TOKEN", "AMUSANT_DEVICE_PSKS", "GROK_ROUTINE_TOKEN"} & values.keys())


if __name__ == "__main__":
    unittest.main()
