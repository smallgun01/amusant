from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "deploy" / "self-hosted"))
import toolkit  # noqa: E402

PREFLIGHT = REPO / "deploy" / "oci-a1" / "preflight.py"
PROFILE_SOURCE = REPO / "deploy" / "oci-a1" / "self-hosted-profile.json"


def valid_values(data_root: Path) -> dict[str, str]:
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
        "OCI_DATA_DIR": str(data_root),
        "OCI_EXPECTED_DATA_SOURCE": "/dev/fake-volume",
        "OCI_VCN_CIDRS": "10.20.0.0/16",
    }


class ToolkitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "deploy" / "oci-a1").mkdir(parents=True)
        (self.root / "deploy" / "self-hosted").mkdir(parents=True)
        self.data_root = self.root / "mounted-data"
        self.data_root.mkdir()
        self.env_file = self.root / "deploy" / "oci-a1" / ".env"
        self.template = self.root / "deploy" / "oci-a1" / ".env.example"
        self.compose = self.root / "deploy" / "oci-a1" / "compose.yaml"
        self.compose.write_text(
            "services:" + chr(10) + "  relay:" + chr(10) + "    image: " + chr(36) + "{RELAY_IMAGE:?required}" + chr(10),
            encoding="utf-8",
        )
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.profile = json.loads(PROFILE_SOURCE.read_text(encoding="utf-8"))
        self.profile["preflight_file"] = str(PREFLIGHT)
        self.profile["env_file"] = "deploy/oci-a1/.env"
        self.profile["env_example"] = "deploy/oci-a1/.env.example"
        self.profile["compose_file"] = "deploy/oci-a1/compose.yaml"
        for entry in self.profile["data_directories"]:
            entry["uid"] = self.uid
            entry["gid"] = self.gid
        for entry in self.profile["secret_files"]:
            entry["consumer_uids"] = [self.uid]
            entry["shared_gid"] = self.gid
        self.profile["config_files"][0]["reader_uid"] = self.uid
        self.profile["config_files"][0]["reader_gids"] = [self.gid]
        secret_dir = self.root / "secret-files"
        secret_dir.mkdir()
        self.values = valid_values(self.data_root)
        for entry in self.profile["secret_files"]:
            secret = secret_dir / entry["env"]
            secret.write_text("FAKE_OFFLINE_SECRET_SENTINEL", encoding="utf-8")
            secret.chmod(0o400)
            self.values[entry["env"]] = str(secret)
        config = self.root / "hermes-config.yaml"
        config.write_text("fake-test-config" + chr(10), encoding="utf-8")
        config.chmod(0o400)
        self.values["HERMES_CONFIG_FILE"] = str(config)
        self.write_env(self.env_file, self.values)
        template_values = dict(self.values)
        for name in self.profile["secret_template_envs"]:
            template_values[name] = ""
        template_values["HERMES_CONFIG_FILE"] = ""
        template_values["OCI_EXPECTED_DATA_SOURCE"] = ""
        template_values["OCI_VCN_CIDRS"] = ""
        self.write_env(self.template, template_values)
        self.profile_path = self.root / "deploy" / "self-hosted" / "profile.json"
        self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        self.profile_rel = Path("deploy/self-hosted/profile.json")
        self.mount_source = "/dev/fake-volume"
        self.command_calls = []

    def write_env(self, path: Path, values: dict[str, str]):
        path.write_text("".join(f"{name}={value}" + chr(10) for name, value in values.items()), encoding="utf-8")
        path.chmod(0o600)

    def create_data_directories(self):
        for entry in self.profile["data_directories"]:
            path = self.data_root / entry["path"]
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(int(entry["mode"], 8))

    def fake_command(self, argv, *, cwd=None):
        self.command_calls.append(list(argv))
        if argv[:3] == ["ip", "-j", "address"]:
            return 0, json.dumps([{"addr_info": [{"local": "10.0.0.10", "prefixlen": 24}]}]), ""
        if argv[:3] == ["ip", "-j", "route"]:
            return 0, json.dumps([{"dst": "10.0.0.0/24"}, {"dst": "default"}]), ""
        if argv and argv[0] == "docker":
            if argv[1:] == ["--version"]:
                return 0, "Docker version fake" + chr(10), ""
            if argv[1:3] == ["compose", "version"]:
                return 0, "Docker Compose version fake" + chr(10), ""
            if len(argv) > 1 and argv[1] == "ps":
                return 0, "running" + chr(10) + "exited" + chr(10), ""
            if "config" in argv and "--quiet" in argv:
                return 0, "", ""
        return 127, "", ""

    def patches(self, stack: ExitStack, *, docker=None, source=None, root_device=1, data_device=2):
        source = source or self.mount_source
        stack.enter_context(patch.object(toolkit, "_load_mount", return_value={
            "source": source, "target": str(self.data_root), "fstype": "ext4"
        }))
        stack.enter_context(patch.object(toolkit, "_device_id", side_effect=lambda path: root_device if Path(path) == Path("/") else data_device))
        stack.enter_context(patch.object(toolkit, "run_command", side_effect=self.fake_command))
        stack.enter_context(patch.object(toolkit.shutil, "which", return_value=docker))
        stack.enter_context(patch.object(toolkit.platform, "system", return_value="Linux"))
        stack.enter_context(patch.object(toolkit.platform, "machine", return_value="aarch64"))

    def inspect_fixture(self, stack: ExitStack, **kwargs):
        self.patches(stack, **kwargs)
        return toolkit.inspect(self.profile_rel, root=self.root)

    @staticmethod
    def check(context, name):
        return next(item for item in context["checks"] if item["name"] == name)

    def test_doctor_is_read_only_redacted_and_not_deployment_go(self):
        self.create_data_directories()
        before_env = self.env_file.read_bytes()
        secret = Path(self.values["GEMINI_API_KEY_FILE"])
        before_secret = secret.read_bytes()
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
            out = io.StringIO()
            with redirect_stdout(out):
                result = toolkit.render_doctor(context)
        text = out.getvalue()
        self.assertEqual(self.check(context, "persistent data mount")["status"], "PASS")
        self.assertEqual(self.check(context, "image provenance and platform")["status"], "UNKNOWN")
        self.assertIn("NOT deployment GO", text)
        self.assertNotIn("registry.invalid", text)
        self.assertNotIn("10.0.0.10", text)
        self.assertNotIn("FAKE_OFFLINE_SECRET_SENTINEL", text)
        self.assertEqual(self.env_file.read_bytes(), before_env)
        self.assertEqual(secret.read_bytes(), before_secret)
        self.assertEqual(result, 1)  # Docker is unavailable and live provenance remains UNKNOWN.

    def test_partial_env_reports_missing_names_without_echoing_values(self):
        values = dict(self.values)
        values.pop("FACADE_IMAGE")
        values["UNUSED_SENTINEL"] = "DO_NOT_ECHO_THIS_VALUE"
        self.write_env(self.env_file, values)
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        completeness = self.check(context, "environment completeness")
        self.assertEqual(completeness["status"], "BLOCKED")
        self.assertIn("FACADE_IMAGE", completeness["detail"])
        self.assertNotIn("DO_NOT_ECHO_THIS_VALUE", str(context["checks"]))

    def test_empty_directory_and_wrong_source_do_not_pass_persistence_check(self):
        with ExitStack() as stack:
            context = self.inspect_fixture(stack, source="/dev/wrong-volume")
        self.assertEqual(self.check(context, "persistent data mount")["status"], "BLOCKED")
        with ExitStack() as stack:
            context = self.inspect_fixture(stack, source="/dev/fake-volume", root_device=1, data_device=1)
        self.assertEqual(self.check(context, "persistent data mount")["status"], "BLOCKED")

    def test_vcn_overlap_blocks_and_missing_vcn_is_unknown(self):
        values = dict(self.values)
        values["OCI_VCN_CIDRS"] = "172.30.62.0/24"
        self.write_env(self.env_file, values)
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        self.assertEqual(self.check(context, "external network overlap")["status"], "BLOCKED")
        values["OCI_VCN_CIDRS"] = ""
        self.write_env(self.env_file, values)
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        self.assertEqual(self.check(context, "external network overlap")["status"], "UNKNOWN")

    def test_symlinked_secret_is_blocked_without_reading_target(self):
        target = Path(self.values["DEVICE_TOKEN_FILE"])
        link = self.root / "secret-files" / "device-token-link"
        link.symlink_to(target)
        values = dict(self.values)
        values["DEVICE_TOKEN_FILE"] = str(link)
        self.write_env(self.env_file, values)
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        check = self.check(context, "secret-file metadata DEVICE_TOKEN_FILE")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertNotIn("FAKE_OFFLINE_SECRET_SENTINEL", str(context["checks"]))
        self.assertNotIn(str(target), str(context["checks"]))

    def test_compose_config_is_static_and_never_starts_services(self):
        self.create_data_directories()
        with ExitStack() as stack:
            context = self.inspect_fixture(stack, docker="docker")
        calls = self.command_calls
        self.assertTrue(any(call[:3] == ["docker", "compose", "version"] for call in calls))
        self.assertTrue(any("config" in call and "--quiet" in call for call in calls))
        self.assertTrue(any(call[:2] == ["docker", "ps"] for call in calls))
        self.assertFalse(any("up" in call or "run" in call or "build" in call for call in calls))
        self.assertIn("no services were started", self.check(context, "static Compose config")["detail"])

    def test_setup_plan_is_dry_run_and_never_touches_existing_env(self):
        before = self.env_file.read_bytes()
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        operations, blockers = toolkit.setup_plan(context)
        self.assertFalse(blockers)
        self.assertTrue(any(item["kind"] == "create_dir" for item in operations))
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertFalse((self.data_root / "relay").exists())

    def test_apply_confirmation_and_idempotency(self):
        with ExitStack() as stack:
            self.patches(stack)
            with patch.object(toolkit, "ROOT", self.root), patch.object(toolkit.sys, "stdin", Mock(isatty=Mock(return_value=True))), patch("builtins.input", return_value="APPLY SAFE"):
                result = toolkit.main(["--profile", str(self.profile_rel), "setup", "--apply"])
        self.assertEqual(result, 0)
        for entry in self.profile["data_directories"]:
            path = self.data_root / entry["path"]
            self.assertTrue(path.is_dir())
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, int(entry["mode"], 8))
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        operations, blockers = toolkit.setup_plan(context)
        self.assertFalse(blockers)
        self.assertFalse(any(item["kind"] == "create_dir" for item in operations))

    def test_setup_can_copy_only_nonsecret_template_when_mount_is_unverified(self):
        self.env_file.unlink()
        with ExitStack() as stack:
            context = self.inspect_fixture(stack, source="/dev/wrong-volume")
        operations, blockers = toolkit.setup_plan(context)
        self.assertTrue(blockers)
        self.assertEqual([item["kind"] for item in operations], ["copy_env"])
        self.assertFalse(self.env_file.exists())
        toolkit._apply_operations(context, operations)
        self.assertTrue(self.env_file.exists())
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        text = self.env_file.read_text(encoding="utf-8")
        self.assertNotIn("FAKE_OFFLINE_SECRET_SENTINEL", text)

    def test_wrong_data_directory_mode_is_blocked_and_no_other_dir_is_created(self):
        relay = self.data_root / "relay"
        relay.mkdir()
        relay.chmod(0o777)
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        operations, blockers = toolkit.setup_plan(context)
        self.assertTrue(blockers)
        self.assertFalse(any(item["kind"] == "create_dir" for item in operations))
        self.assertFalse((self.data_root / "hermes").exists())
        self.assertEqual(stat.S_IMODE(relay.stat().st_mode), 0o777)


    def test_bad_digest_and_unsupported_architecture_are_blocked(self):
        values = dict(self.values)
        values["RELAY_IMAGE"] = "registry.invalid/relay:latest"
        self.write_env(self.env_file, values)
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        self.assertEqual(self.check(context, "image digest syntax RELAY_IMAGE")["status"], "BLOCKED")
        with ExitStack() as stack:
            self.patches(stack)
            with patch.object(toolkit.platform, "machine", return_value="x86_64"):
                context = toolkit.inspect(self.profile_rel, root=self.root)
        self.assertEqual(self.check(context, "host architecture")["status"], "BLOCKED")

    def test_wrong_private_bind_address_is_blocked_and_redacted(self):
        values = dict(self.values)
        values["RELAY_HOST_PRIVATE_IP"] = "10.99.0.9"
        self.write_env(self.env_file, values)
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
            out = io.StringIO()
            with redirect_stdout(out):
                toolkit.render_doctor(context)
        self.assertEqual(self.check(context, "private Relay bind address")["status"], "BLOCKED")
        self.assertNotIn("10.99.0.9", out.getvalue())

    def test_secret_mode_is_checked_without_reading_file_content(self):
        secret = Path(self.values["DEVICE_TOKEN_FILE"])
        secret.chmod(0o644)
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        self.assertEqual(self.check(context, "secret-file metadata DEVICE_TOKEN_FILE")["status"], "BLOCKED")
        self.assertEqual(secret.read_text(encoding="utf-8"), "FAKE_OFFLINE_SECRET_SENTINEL")
        self.assertNotIn("FAKE_OFFLINE_SECRET_SENTINEL", str(context["checks"]))

    def test_wrong_data_owner_blocks_all_new_data_directory_operations(self):
        with ExitStack() as stack:
            context = self.inspect_fixture(stack)
        original = toolkit._inspect_dir
        def wrong_owner(path):
            if path.name == "relay":
                return "directory", type("Metadata", (), {
                    "st_mode": stat.S_IFDIR | 0o700,
                    "st_uid": self.uid + 1,
                    "st_gid": self.gid,
                })()
            return original(path)
        with patch.object(toolkit, "_inspect_dir", side_effect=wrong_owner):
            operations, blockers = toolkit.setup_plan(context)
        self.assertTrue(any("ownership/mode" in blocker for blocker in blockers))
        self.assertFalse(any(item["kind"] == "create_dir" for item in operations))
        self.assertFalse((self.data_root / "hermes").exists())

if __name__ == "__main__":
    unittest.main()
