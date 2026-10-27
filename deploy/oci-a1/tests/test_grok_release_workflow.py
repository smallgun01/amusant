"""Static safety checks for the Owner-triggered Grok image release workflow."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "grok-arm64-image-release.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "grok-release-workflow-tests.yml"
STAGING_ENV = ROOT / "deploy" / "oci-a1" / "grok-staging.env.example"


class GrokImageReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        cls.test_workflow = yaml.load(TEST_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

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
        guard = jobs["guard"]["steps"][0]
        guard_script = guard["run"]
        self.assertEqual(guard["env"]["DISPATCH_ACTOR"], "${{ github.actor }}")
        self.assertEqual(guard["env"]["DISPATCH_TRIGGERING_ACTOR"], "${{ github.triggering_actor }}")
        self.assertIn('"$DISPATCH_ACTOR" != "smallgun01"', guard_script)
        self.assertIn('"$DISPATCH_TRIGGERING_ACTOR" != "smallgun01"', guard_script)
        self.assertIn('"$DISPATCH_REF" != "refs/heads/main"', guard_script)
        self.assertIn('"$PUBLISH_CONFIRMATION" != "PUBLISH"', guard_script)
        publish_if = jobs["publish"]["if"]
        self.assertIn("needs.guard.outputs.publish == 'true'", publish_if)
        self.assertIn("github.actor == 'smallgun01'", publish_if)
        self.assertIn("github.triggering_actor == 'smallgun01'", publish_if)
        self.assertIn("github.ref == 'refs/heads/main'", publish_if)
        self.assertIn("inputs.publish_images", publish_if)
        self.assertIn("inputs.publish_confirmation == 'PUBLISH'", publish_if)

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

    def test_digest_summary_printf_renders_literal_markdown_without_substitution(self):
        ref_step = next(
            step for step in self.workflow["jobs"]["publish"]["steps"] if step.get("id") == "references"
        )
        script = ref_step["run"]
        self.assertIn("printf 'Source revision: `%s`\\n' \"$GITHUB_SHA\"", script)
        self.assertIn("printf -- '- Relay: `%s`\\n' \"$relay_image\"", script)
        self.assertIn("printf -- '- Egress Proxy: `%s`\\n' \"$egress_proxy_image\"", script)
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is unavailable for executing the summary step")

        relay_digest = "sha256:" + "a" * 64
        egress_digest = "sha256:" + "b" * 64
        source_revision = "1234567890abcdef" * 2 + "12345678"
        env = {
            **os.environ,
            "RELAY_DIGEST": relay_digest,
            "EGRESS_PROXY_DIGEST": egress_digest,
            "GITHUB_SHA": source_revision,
            "GITHUB_OUTPUT": os.devnull,
            "GITHUB_STEP_SUMMARY": "/dev/stdout",
        }
        result = subprocess.run([bash, "-c", script], text=True, capture_output=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Source revision: `{source_revision}`", result.stdout)
        self.assertIn(f"- Relay: `ghcr.io/smallgun01/amusant-relay@{relay_digest}`", result.stdout)
        self.assertIn(f"- Egress Proxy: `ghcr.io/smallgun01/amusant-egress-proxy@{egress_digest}`", result.stdout)
        self.assertNotIn("command not found", result.stderr)

    def test_focused_safety_test_runs_in_lightweight_pull_request_ci(self):
        workflow = self.test_workflow
        self.assertEqual(set(workflow["on"]), {"pull_request"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(set(workflow["jobs"]), {"static-release-safety"})
        job = workflow["jobs"]["static-release-safety"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertNotIn("packages", job.get("permissions", {}))
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        self.assertIn("test_grok_release_workflow.py", commands)
        self.assertNotIn("docker/build-push-action", "\n".join(str(step) for step in job["steps"]))

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
