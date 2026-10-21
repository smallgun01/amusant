"""Offline fixtures only: no real package manager, daemon, SSH or network calls."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy/self-hosted/bootstrap_host.py"
spec = importlib.util.spec_from_file_location("bootstrap_host", SCRIPT)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
REAL_COMMAND = b.command
UUID = "11111111-2222-3333-4444-555555555555"


def inputs(data="/mnt/amusant-data"):
    return {"data_dir": str(data), "expected_uuid": UUID, "env_file": "",
            "packages": {p: "1.2.3-offline-fixture" for p in b.PACKAGES}}


def host(prepared=False, data="/mnt/amusant-data"):
    example = b.load_preflight().read_env_file(b.EXAMPLE)
    return {"os": {"ID": "ubuntu", "VERSION_ID": "24.04", "VERSION_CODENAME": "noble", "arch": "aarch64"},
            "mount": {"target": str(data), "fstype": "ext4", "uuid": UUID, "options": "rw,relatime", "maj:min": "8:16"},
            "root_mount": {"target": "/", "fstype": "ext4", "uuid": "boot", "options": "rw", "maj:min": "8:1"},
            "root_disks": ["8:0"], "data_disks": ["8:16"],
            "data_root": {"uid": 0, "gid": 0, "mode": 0o755, "device": 100, "inode": 1},
            "directories": {rel: {"uid": uid, "gid": gid, "mode": mode, "device": 100, "inode": i+2}
                            if prepared else None for i, (rel, uid, gid, mode) in enumerate(b.DIRECTORIES)},
            "packages": {p: "1.2.3-offline-fixture" for p in b.PACKAGES} if prepared else {},
            "docker": {"cli": prepared, "active": prepared, "enabled": prepared,
                       "compose": "2.40.0" if prepared else "", "server": "28.5.0" if prepared else "",
                       "networks": [], "containers": []},
            "networks": {k: example[k] for k in b.load_preflight().NETWORK_NAMES},
            "addresses": [{"name": "enp0s3", "ips": ["10.0.0.10"]}],
            "routes": [{"dst": "10.0.0.0/24", "dev": "enp0s3", "gateway": "", "table": "", "type": ""}],
            "apt_files": {str(b.KEY): None, str(b.SOURCE): None}, "apt_status": "offline-state",
             "dpkg_audit_clean": True,
             "docker_startup": {"configs": {}, "state_dirs": {str(p): None for p in b.DOCKER_STATE_DIRS}},
            "extra_docker_sources": [], "source_valid": True, "free_mib": 4096,
            "static_status": "DEFERRED_B3_INPUTS_INCOMPLETE", "env_digest": b.digest(b.EXAMPLE)}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.commands = self.stack.enter_context(patch.object(b, "command", side_effect=AssertionError("Unexpected host command")))
        self.network = self.stack.enter_context(patch.object(b.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network call")))
        self.output = io.StringIO()
        self.stack.enter_context(redirect_stdout(self.output))
        self.stack.enter_context(redirect_stderr(self.output))

    def authorize_fixture(self):
        # Mocks emulate the privileged runner; no real sudo/UID escalation happens.
        self.stack.enter_context(patch.object(b.os, "geteuid", return_value=0))
        flags = {name: getattr(sys.flags, name) for name in dir(sys.flags)
                 if not name.startswith("_") and isinstance(getattr(sys.flags, name), int)}
        flags["isolated"] = 1
        self.stack.enter_context(patch.object(b.sys, "flags", SimpleNamespace(**flags)))
        self.stack.enter_context(patch.object(b, "trusted"))

    def test_clean_host_has_bounded_install_enable_and_four_directories(self):
        result = b.plan(inputs(), host())
        self.assertEqual(result["blocked"], [])
        self.assertEqual([a["op"] for a in result["actions"]], ["install_docker", "enable_docker"] + ["mkdir"] * 4)
        self.commands.assert_not_called()
        self.network.assert_not_called()

    def test_prepared_host_is_noop(self):
        self.assertEqual(b.plan(inputs(), host(True))["actions"], [])

    def test_missing_package_pins_are_blocked_not_latest(self):
        config = inputs(); config["packages"] = {}
        result = b.plan(config, host())
        self.assertIn("EXPLICIT_MISSING_PACKAGE_VERSIONS_REQUIRED", result["blocked"])
        self.assertNotIn("install_docker", [a["op"] for a in result["actions"]])

    def test_only_missing_compose_is_installed(self):
        facts = host(True); facts["packages"].pop("docker-compose-plugin"); facts["docker"]["compose"] = ""
        action = b.plan(inputs(), facts)["actions"][0]
        self.assertEqual(action["packages"], {"docker-compose-plugin": "1.2.3-offline-fixture"})

    def test_usable_existing_non_ce_install_is_preserved(self):
        facts = host(True); facts["packages"] = {"docker.io": "existing"}
        self.assertEqual(b.plan(inputs(), facts)["actions"], [])
        self.assertEqual(b.plan(inputs(), facts)["blocked"], [])

    def test_supported_os_and_wrong_arch_fixtures(self):
        for os_id, version, codename, arch, valid in (
                ("ubuntu", "22.04", "jammy", "aarch64", True), ("ubuntu", "24.04", "noble", "aarch64", True),
                ("ubuntu", "24.04", "noble", "x86_64", False), ("debian", "12", "bookworm", "aarch64", False),
                ("ubuntu", "26.04", "unknown", "aarch64", False)):
            with self.subTest(version=version, arch=arch):
                facts = host(); facts["os"] = dict(ID=os_id, VERSION_ID=version, VERSION_CODENAME=codename, arch=arch)
                self.assertEqual("UNREVIEWED_OS_OR_ARCH" not in b.plan(inputs(), facts)["blocked"], valid)

    def test_wrong_disk_uuid_filesystem_readonly_and_boot_fallback(self):
        changes = ({"uuid": "wrong"}, {"uuid": None}, {"fstype": "xfs"}, {"options": "ro,relatime"}, {"target": "/"})
        for change in changes:
            with self.subTest(change=change):
                facts = host(); facts["mount"].update(change)
                self.assertIn("WRONG_OR_NONPERSISTENT_DATA_MOUNT", b.plan(inputs(), facts)["blocked"])
        for disks in ([], ["8:0"]):
            facts = host(); facts["data_disks"] = disks
            self.assertIn("WRONG_OR_NONPERSISTENT_DATA_MOUNT", b.plan(inputs(), facts)["blocked"])

    def test_uuid_survives_device_renumbering(self):
        facts = host(); facts["data_disks"] = ["8:32"]; facts["mount"]["maj:min"] = "8:32"
        self.assertEqual(b.plan(inputs(), facts)["blocked"], [])

    def test_low_disk_space_and_unsafe_root(self):
        facts = host(); facts["free_mib"] = 0; facts["data_root"]["mode"] = 0o777
        result = b.plan(inputs(), facts)
        self.assertIn("INSUFFICIENT_BOOTSTRAP_DISK_SPACE", result["blocked"])
        self.assertIn("DATA_ROOT_MUST_BE_ROOT_OWNED_NOT_WRITABLE", result["blocked"])

    def test_existing_wrong_directory_metadata_never_gets_repaired(self):
        for field, value in (("uid", 123), ("gid", 123), ("mode", 0o777), ("device", 101)):
            with self.subTest(field=field):
                facts = host(True); facts["directories"]["hermes"][field] = value
                result = b.plan(inputs(), facts)
                self.assertIn("EXISTING_DIRECTORY_METADATA_MISMATCH:hermes", result["blocked"])
                self.assertEqual(result["actions"], [])

    def test_symlink_or_non_directory_is_rejected(self):
        (self.temp / "real").mkdir(); (self.temp / "link").symlink_to(self.temp / "real")
        with self.assertRaisesRegex(b.Blocked, "SYMLINK"):
            b.dir_metadata(self.temp / "link" / "child")
        (self.temp / "file").write_text("preserve")
        with self.assertRaisesRegex(b.Blocked, "NOT_DIRECTORY"):
            b.dir_metadata(self.temp / "file")
        with self.assertRaisesRegex(b.Blocked, "UNSAFE_PATH"):
            b.no_symlinks(Path("/mnt/../etc"))

    def test_package_source_and_running_container_conflicts(self):
        for key, value, expected in (("extra_docker_sources", ["custom.list"], "EXISTING_PACKAGE_OR_APT_SOURCE_CONFLICT"),
                                     ("source_valid", False, "EXISTING_PACKAGE_OR_APT_SOURCE_CONFLICT")):
            facts = host(); facts[key] = value
            self.assertIn(expected, b.plan(inputs(), facts)["blocked"])
        facts = host(); facts["packages"]["docker.io"] = "existing"
        self.assertIn("EXISTING_PACKAGE_OR_APT_SOURCE_CONFLICT", b.plan(inputs(), facts)["blocked"])
        facts = host(); facts["docker"]["containers"] = ["foreign-container"]
        self.assertIn("PACKAGE_CHANGE_WITH_EXISTING_CONTAINERS_REQUIRES_REVIEW", b.plan(inputs(), facts)["blocked"])

    def test_stopped_docker_with_custom_root_config_or_existing_state_is_blocked(self):
        facts = host()
        facts["docker_startup"]["configs"]["/etc/docker/daemon.json"] = "daemon-config-hash"
        self.assertIn("DOCKER_STARTUP_CONFIGURATION_REQUIRES_REVIEW", b.plan(inputs(), facts)["blocked"])
        facts = host()
        facts["docker_startup"]["configs"]["/etc/systemd/system/docker.service.d/override.conf"] = "override-hash"
        self.assertIn("DOCKER_STARTUP_CONFIGURATION_REQUIRES_REVIEW", b.plan(inputs(), facts)["blocked"])
        facts = host()
        facts["docker_startup"]["state_dirs"][str(b.DOCKER_STATE_DIRS[0])] = {"nonempty": True}
        self.assertIn("DOCKER_STARTUP_STATE_REQUIRES_REVIEW", b.plan(inputs(), facts)["blocked"])

    def test_docker_startup_state_reads_configs_overrides_and_storage_without_starting_daemon(self):
        config = self.temp / "daemon.json"
        unit = self.temp / "docker.service"
        docker_unit = self.temp / "docker.service.d"; docker_unit.mkdir()
        override = docker_unit / "10-storage.conf"; override.write_text("[Service]\\nExecStart=custom\\n")
        docker_data = self.temp / "docker-data"; docker_data.mkdir(); (docker_data / "containers").mkdir()
        containerd_data = self.temp / "containerd-data"; containerd_data.mkdir()
        real_is_file = Path.is_file
        with patch.object(b, "DOCKER_CONFIG_FILES", (config, unit)), \
                patch.object(b, "DOCKER_OVERRIDE_DIRS", (docker_unit,)), \
                patch.object(b, "DOCKER_STATE_DIRS", (docker_data, containerd_data)), \
                patch.object(Path, "is_file", lambda path: False if str(path) == "/usr/bin/docker" else real_is_file(path)):
            config.write_text('{"data-root":"/srv/docker"}')
            unit.write_text("[Service]\nExecStart=/usr/bin/dockerd --data-root=/srv/docker\n")
            state = b.docker_startup_state()
        self.assertEqual(set(state["configs"]), {str(config), str(unit), str(override)})
        self.assertTrue(state["state_dirs"][str(docker_data)]["nonempty"])
        self.assertFalse(state["state_dirs"][str(containerd_data)]["nonempty"])

    def test_unverified_docker_startup_state_blocks_apply_before_any_mutation(self):
        self.authorize_fixture(); facts = host()
        facts["docker_startup"]["configs"]["/etc/systemd/system/docker.service.d/override.conf"] = "override"
        reviewed = b.plan(inputs(), facts)
        with patch.object(b, "observe", return_value=facts), self.assertRaisesRegex(b.Blocked, "PLAN_HAS_BLOCKERS"):
            b.apply(reviewed, self.temp / "plan")
        self.commands.assert_not_called(); self.network.assert_not_called()

    def test_dpkg_audit_blocks_package_plan_and_stale_startup_facts(self):
        facts = host(); facts["dpkg_audit_clean"] = False
        self.assertIn("DPKG_HAS_UNFINISHED_CONFIGURATION", b.plan(inputs(), facts)["blocked"])
        self.authorize_fixture()
        reviewed = b.plan(inputs(), host())
        changed = deepcopy(reviewed)
        changed["facts"]["docker_startup"]["configs"]["/etc/systemd/system/docker.service"] = "new-config"
        with patch.object(b, "observe", return_value=host()), self.assertRaisesRegex(b.Blocked, "STALE_OR_TAMPERED"):
            b.apply(changed, self.temp / "plan")
        self.commands.assert_not_called(); self.network.assert_not_called()

    def test_network_conflicts_and_own_bridge_identity(self):
        facts = host(True)
        net = {"id": "123456789abc" + "0" * 52, "driver": "bridge", "options": {},
               "labels": {"com.docker.compose.project": b.PROJECT, "com.docker.compose.network": "relay_api"},
               "subnets": ["172.30.62.0/29"]}
        facts["docker"]["networks"] = [net]
        facts["routes"].extend([{"dst": "172.30.62.0/29", "dev": "br-123456789abc"},
                                {"dst": "172.30.62.1", "dev": "br-123456789abc", "table": "local"}])
        self.assertEqual(b.plan(inputs(), facts)["blocked"], [])
        bad = deepcopy(facts); bad["docker"]["networks"][0]["labels"] = {}
        self.assertIn("FOREIGN_DOCKER_NETWORK_CONFLICT", b.plan(inputs(), bad)["blocked"])
        bad = deepcopy(facts); bad["routes"][1]["dev"] = "foreign0"
        self.assertIn("HOST_ROUTE_CONFLICT", b.plan(inputs(), bad)["blocked"])
        bad = host(); bad["routes"].append({"dst": "172.30.0.0/16", "dev": "vpn0"})
        self.assertIn("HOST_ROUTE_CONFLICT", b.plan(inputs(), bad)["blocked"])

    def test_default_route_not_conflict_but_docker_only_address_not_host(self):
        facts = host(True); facts["routes"].append({"dst": "default", "dev": "enp0s3"})
        self.assertEqual(b.plan(inputs(), facts)["blocked"], [])
        for name, ip in (("docker0", "172.17.0.1"), ("lo", "127.0.0.1"), ("eth0", "192.0.2.1")):
            facts["addresses"] = [{"name": name, "ips": [ip]}]
            self.assertIn("PRIVATE_HOST_ADDRESS_UNAVAILABLE", b.plan(inputs(), facts)["blocked"])

    def test_invalid_package_injection_is_rejected(self):
        for name, version in (("bash", "1.0"), ("docker-ce", "1.0;id"), ("docker-ce", "$(id)"), ("docker-ce", "latest")):
            config = inputs(); config["packages"] = {name: version}
            with self.assertRaisesRegex(b.Blocked, "INVALID_PACKAGE"):
                b.plan(config, host())

    def test_privileged_apply_cannot_use_agent_writable_plan(self):
        path = self.temp / "plan.json"; path.write_text("{}"); path.chmod(0o666)
        with self.assertRaisesRegex(b.Blocked, "ROOT_OWNED"):
            b.trusted(path, private=True)

    def test_unprivileged_apply_blocks_before_read_or_mutation(self):
        with patch.object(b.os, "geteuid", return_value=1000), patch.object(b, "observe") as observe:
            self.assertEqual(b.main(["--apply", "--plan-file", "/no-private-input-read"]), 2)
            observe.assert_not_called()
            self.assertIn("EXPLICIT_PRIVILEGED_EXECUTION_REQUIRED", self.output.getvalue())
        self.commands.assert_not_called()
        self.network.assert_not_called()

    def test_python_isolation_is_required(self):
        self.authorize_fixture(); b.sys.flags.isolated = 0
        with self.assertRaisesRegex(b.Blocked, "ISOLATED_PYTHON"):
            b.apply({}, self.temp / "plan")

    def test_stale_or_tampered_plan_never_executes(self):
        self.authorize_fixture()
        reviewed = b.plan(inputs(), host())
        for variant in ("facts", "actions", "code"):
            changed = deepcopy(reviewed)
            if variant == "facts": changed["facts"]["apt_status"] = "outdated"
            if variant == "actions": changed["actions"].append({"op": "shell", "command": "id"})
            if variant == "code": changed["code"]["bootstrap"] = "changed"
            with patch.object(b, "observe", return_value=host()), self.assertRaisesRegex(b.Blocked, "STALE_OR_TAMPERED"):
                b.apply(changed, self.temp / "plan")
        self.commands.assert_not_called(); self.network.assert_not_called()

    def test_blocked_plan_never_executes(self):
        self.authorize_fixture(); facts = host(); facts["mount"]["uuid"] = "wrong"
        with patch.object(b, "observe", return_value=facts), self.assertRaisesRegex(b.Blocked, "PLAN_HAS_BLOCKERS"):
            b.apply(b.plan(inputs(), facts), self.temp / "plan")
        self.commands.assert_not_called()

    def test_mount_disappearing_between_actions_stops(self):
        self.authorize_fixture(); facts = host()
        with patch.object(b, "observe", return_value=facts), patch.object(b, "mounted", return_value={}), \
                self.assertRaisesRegex(b.Blocked, "MOUNT_CHANGED"):
            b.apply(b.plan(inputs(), facts), self.temp / "plan")
        self.commands.assert_not_called(); self.network.assert_not_called()

    def test_clean_apply_and_second_run_preserve_all_sentinels(self):
        self.authorize_fixture()
        data = self.temp / "data"; data.mkdir()
        config, before, after = inputs(data), host(data=data), host(True, data=data)
        with patch.object(b, "observe", side_effect=[before, after]), patch.object(b, "mounted", return_value=before["mount"]), \
                patch.object(b, "install_docker") as install, patch.object(b.os, "fchown") as chown:
            self.commands.side_effect = None
            b.apply(b.plan(config, before), self.temp / "plan")
            install.assert_called_once(); self.assertEqual(chown.call_count, 4)
        sentinels = [data / "relay/task-ledger.sqlite3", data / "hermes/MEMORY.md", data / "hermes/USER.md",
                     data / "hermes/SOUL.md", data / "hermes/sessions/saved.json",
                     data / "hermes/skills/existing/SKILL.md", data / "results/facade/artifacts/report.md",
                     self.temp / "config.yaml", self.temp / ".env", self.temp / "secret"]
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"PRIVATE-EXISTING-STATE"); path.chmod(0o600)
        snapshot = {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_uid, p.stat().st_gid, p.stat().st_mtime_ns) for p in sentinels}
        self.commands.reset_mock(); self.commands.side_effect = AssertionError("No commands on repeat")
        with patch.object(b, "observe", return_value=after), patch.object(b, "make_directory") as mkdir:
            b.apply(b.plan(config, after), self.temp / "plan")
            mkdir.assert_not_called()
        self.assertEqual(snapshot, {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_uid, p.stat().st_gid, p.stat().st_mtime_ns) for p in sentinels})
        self.commands.assert_not_called(); self.network.assert_not_called()
        self.assertNotIn("PRIVATE-EXISTING-STATE", self.output.getvalue())

    def test_package_failure_keeps_state_and_is_not_ready(self):
        self.authorize_fixture(); facts = host()
        with patch.object(b, "observe", return_value=facts), patch.object(b, "mounted", return_value=facts["mount"]), \
                patch.object(b, "install_docker", side_effect=b.Blocked("APT_FAILED")), \
                patch.object(b, "make_directory") as mkdir, self.assertRaisesRegex(b.Blocked, "APT_FAILED"):
            b.apply(b.plan(inputs(), facts), self.temp / "plan")
        mkdir.assert_not_called(); self.commands.assert_not_called()
        self.assertNotIn("HOST_PREPARED", self.output.getvalue())
        self.assertIn("Completed host actions: 0", self.output.getvalue())

    def test_postcheck_failure_never_prints_ready(self):
        self.authorize_fixture(); before = host(True); after = deepcopy(before); after["docker"]["server"] = ""
        with patch.object(b, "observe", side_effect=[before, after]), self.assertRaisesRegex(b.Blocked, "POSTCHECK"):
            b.apply(b.plan(inputs(), before), self.temp / "plan")
        self.assertNotIn("HOST_PREPARED", self.output.getvalue())

    def test_installed_package_version_must_match_reviewed_pin(self):
        self.authorize_fixture(); before, after = host(), host(True)
        after["packages"]["docker-ce"] = "different-version"
        with patch.object(b, "observe", side_effect=[before, after]), patch.object(b, "mounted", return_value=before["mount"]), \
                patch.object(b, "install_docker"), patch.object(b, "make_directory"), \
                self.assertRaisesRegex(b.Blocked, "INSTALLED_PACKAGE_VERSION_MISMATCH"):
            self.commands.side_effect = None
            b.apply(b.plan(inputs(), before), self.temp / "plan")
        self.assertNotIn("HOST_PREPARED", self.output.getvalue())

    def test_cold_preflight_import_creates_no_pycache(self):
        cold = self.temp / "preflight.py"; cold.write_bytes(b.PREFLIGHT.read_bytes())
        with patch.object(b, "PREFLIGHT", cold):
            b.load_preflight()
        self.assertFalse((self.temp / "__pycache__").exists())

    def test_free_space_change_above_floor_does_not_make_plan_stale(self):
        before = host(True); after = deepcopy(before); after["free_mib"] -= 1
        self.assertEqual(b.plan(inputs(), before), b.plan(inputs(), after))

    def test_private_plan_is_atomic_0600_and_no_clobber(self):
        path = self.temp / "plan.json"
        b.create_file(path, b"first", 0o600)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError): b.create_file(path, b"second", 0o600)
        self.assertEqual(path.read_bytes(), b"first")
        self.assertEqual(list(self.temp.glob(".amusant-bootstrap-*")), [])

    def test_default_cli_is_read_only_plan_and_redacts_host_details(self):
        facts = host(); facts["apt_status"] = "PRIVATE-PATH-OR-CREDENTIAL"
        with patch.object(b, "observe", return_value=facts):
            args = ["--data-dir", "/mnt/amusant-data", "--expected-fs-uuid", UUID, "--plan-out", str(self.temp / "plan")]
            for name, version in inputs()["packages"].items(): args += ["--package", f"{name}={version}"]
            self.assertEqual(b.main(args), 0)
        self.assertIn("PLAN_READY_FOR_REVIEW", self.output.getvalue())
        self.assertNotIn("HOST_PREPARED", self.output.getvalue())
        self.assertNotIn("PRIVATE-PATH-OR-CREDENTIAL", self.output.getvalue())
        self.assertNotIn(UUID, self.output.getvalue())
        self.commands.assert_not_called(); self.network.assert_not_called()

    def test_unexpected_failure_and_env_error_do_not_echo_secrets(self):
        for error in (PermissionError("secret-token"), b.load_preflight().PreflightError("PRIVATE-CONFIG")):
            self.output.seek(0); self.output.truncate()
            with patch.object(b, "observe", side_effect=error):
                self.assertEqual(b.main(["--data-dir", "/mnt/data", "--expected-fs-uuid", UUID]), 2)
            self.assertNotIn("secret-token", self.output.getvalue()); self.assertNotIn("PRIVATE-CONFIG", self.output.getvalue())

    def test_existing_preflight_is_reused_without_weakening_digest_or_network_checks(self):
        pf = b.load_preflight(); values = pf.read_env_file(b.EXAMPLE)
        for i, name in enumerate(pf.IMAGE_NAMES): values[name] = "registry.invalid/image@sha256:" + str(i+1) * 64
        pf.validate(values)
        for changes in ({"RELAY_IMAGE": "image:latest"}, {"HERMES_API_CIDR": values["RELAY_API_CIDR"]}, {"RELAY_HOST_PRIVATE_IP": "0.0.0.0"}):
            with self.assertRaises(pf.PreflightError): pf.validate({**values, **changes})
        self.assertTrue(sys.dont_write_bytecode)

    def test_disk_ancestry_distinguishes_two_partitions_on_boot_disk(self):
        tree = [{"type": "disk", "maj:min": "8:0", "children": [{"type": "part", "maj:min": "8:1"}, {"type": "part", "maj:min": "8:2"}]},
                {"type": "disk", "maj:min": "8:16"}]
        self.assertEqual(b.backing_disks(tree, "8:2"), {"8:0"})
        self.assertEqual(b.backing_disks(tree, "8:16"), {"8:16"})

    def observation_fixture(self, *, prepared=True, deny_daemon=False, missing_mount=False):
        facts = host(prepared)
        real_read = Path.read_text
        real_exists, real_is_file = Path.exists, Path.is_file
        real_digest, real_dir_metadata = b.digest, b.dir_metadata
        self.stack.enter_context(patch.object(b, "KEY", self.temp / "docker.asc"))
        self.stack.enter_context(patch.object(b, "SOURCE", self.temp / "docker.sources"))
        self.stack.enter_context(patch.object(b, "DOCKER_CONFIG_FILES", (self.temp / "host/docker.json",)))
        self.stack.enter_context(patch.object(b, "DOCKER_OVERRIDE_DIRS", (self.temp / "host/systemd",)))
        self.stack.enter_context(patch.object(b, "DOCKER_STATE_DIRS", (self.temp / "host/docker", self.temp / "host/containerd")))
        self.stack.enter_context(patch.object(Path, "read_text", lambda path, *a, **kw:
            'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n' if str(path) == "/etc/os-release" else real_read(path, *a, **kw)))
        self.stack.enter_context(patch.object(Path, "exists", lambda path:
            False if str(path) in ("/var/lib/docker", "/var/lib/containerd") else real_exists(path)))
        self.stack.enter_context(patch.object(Path, "is_file", lambda path:
            prepared if str(path) == "/usr/bin/docker" else real_is_file(path)))
        self.stack.enter_context(patch.object(Path, "glob", return_value=[]))
        self.stack.enter_context(patch.object(b, "digest", side_effect=lambda path:
            "offline-apt-state" if str(path) == "/var/lib/dpkg/status" else real_digest(path)))
        self.stack.enter_context(patch.object(b, "dir_metadata", side_effect=lambda path:
            facts["data_root"] if str(path) == inputs()["data_dir"] else real_dir_metadata(path)))
        self.stack.enter_context(patch.object(b.os, "statvfs", return_value=SimpleNamespace(f_bavail=1024*1024, f_frsize=4096)))
        def fake_command(*args, **kwargs):
            if args == ("uname", "-m"): return "aarch64\n"
            if args == ("dpkg", "--audit"): return ""
            if args[0] == "dpkg-query": return "".join(f"{p}\tinstalled\t1.2.3\n" for p in b.PACKAGES) if prepared else ""
            if args[0] == "findmnt":
                if "--mountpoint" in args and missing_mount: raise b.Blocked("DENIED")
                return json.dumps({"filesystems": [facts["mount"] if "--mountpoint" in args else facts["root_mount"]]})
            if args[0] == "lsblk": return json.dumps({"blockdevices": [
                {"type": "disk", "maj:min": "8:0", "children": [{"type": "part", "maj:min": "8:1"}]},
                {"type": "disk", "maj:min": "8:16"}]})
            if args[:2] == ("systemctl", "show"): return ""
            if args[:2] == ("systemctl", "is-active"): return "active\n"
            if args[:2] == ("systemctl", "is-enabled"): return "enabled\n"
            if args[0] == "ip":
                if "address" in args: return json.dumps([{"ifname": "enp0s3", "addr_info": [{"local": "10.0.0.10"}]}])
                return json.dumps(facts["routes"])
            if args[:3] == b.DOCKER:
                tail = args[3:]
                if tail == ("--version",): return "Docker version fixture"
                if tail == ("compose", "version", "--short"): return "2.40.0"
                if tail[:1] == ("info",):
                    if deny_daemon: raise b.Blocked("PRIVATE-DOCKER-SOCKET-DETAIL")
                    return json.dumps("28.5.0")
                if tail[:2] == ("network", "ls"): return "fake-network\n"
                if tail[:2] == ("network", "inspect"): return json.dumps([
                    {"Id": "f" * 64, "Driver": "host", "Labels": None, "Options": None, "IPAM": {"Config": None}}])
                if tail[:2] == ("container", "ls"): return ""
            raise AssertionError("Unapproved fixture command: " + str(args))
        self.commands.side_effect = fake_command

    def test_observe_clean_host_uses_real_parsers_without_mutating_commands(self):
        self.observation_fixture(prepared=False)
        result = b.observe(inputs())
        self.assertFalse(result["docker"]["cli"])
        self.assertEqual(result["mount"]["uuid"], UUID)
        self.assertEqual(result["data_disks"], ["8:16"])
        self.assertEqual(result["static_status"], "DEFERRED_B3_INPUTS_INCOMPLETE")
        self.assertFalse(any(c.args[0] in ("apt-get", "sudo", "ssh") for c in self.commands.call_args_list))
        self.network.assert_not_called()

    def test_observe_existing_daemon_accepts_null_ipam_config(self):
        self.observation_fixture()
        result = b.observe(inputs())
        self.assertEqual(result["docker"]["server"], "28.5.0")
        self.assertEqual(result["docker"]["networks"][0]["subnets"], [])
        self.assertFalse(any(c.args[:2] == ("systemctl", "enable") for c in self.commands.call_args_list))

    def test_observe_denied_daemon_is_not_missing_docker(self):
        self.observation_fixture(deny_daemon=True)
        with self.assertRaisesRegex(b.Blocked, "DOCKER_DAEMON_PERMISSION_OR_HEALTH_UNVERIFIED"):
            b.observe(inputs())
        self.assertNotIn("PRIVATE-DOCKER-SOCKET-DETAIL", self.output.getvalue())

    def test_observe_missing_mount_has_specific_blocker(self):
        self.observation_fixture(missing_mount=True)
        with self.assertRaisesRegex(b.Blocked, "EXPECTED_DATA_MOUNT_UNAVAILABLE"):
            b.observe(inputs())

    def test_command_runner_drops_inherited_docker_and_python_environment(self):
        result = subprocess.CompletedProcess(["uname"], 0, "aarch64\n", "")
        with patch.object(b.subprocess, "run", return_value=result) as run:
            self.assertEqual(REAL_COMMAND("uname", "-m"), "aarch64\n")
        kwargs = run.call_args.kwargs
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("DOCKER_HOST", kwargs["env"])
        self.assertNotIn("PYTHONPATH", kwargs["env"])
        self.assertTrue(run.call_args.args[0][0].startswith("/"))

    def test_command_timeout_does_not_expose_output(self):
        with patch.object(b.subprocess, "run", side_effect=subprocess.TimeoutExpired("uname", 1, output="SECRET")), \
                self.assertRaisesRegex(b.Blocked, "UNAVAILABLE_OR_TIMEOUT") as raised:
            REAL_COMMAND("uname", "-m")
        self.assertNotIn("SECRET", str(raised.exception))

    def install_fixture(self, simulation="Inst docker-ce (1.2.3 Docker:stable [arm64])\n"):
        self.stack.enter_context(patch.object(b, "KEY", self.temp / "keys/docker.asc"))
        self.stack.enter_context(patch.object(b, "SOURCE", self.temp / "sources/docker.sources"))
        b.SOURCE.parent.mkdir(); b.KEY.parent.mkdir()
        self.stack.enter_context(patch.object(b, "trusted"))
        response = Mock(); response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = b.KEY_URL
        response.read.return_value = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nOFFLINE FIXTURE\n-----END PGP PUBLIC KEY BLOCK-----\n"
        self.network.side_effect = None; self.network.return_value = response
        self.stack.enter_context(patch.object(b, "docker_startup_state", return_value={
            "configs": {}, "state_dirs": {str(p): None for p in b.DOCKER_STATE_DIRS}}))
        self.commands.side_effect = lambda *args, **kwargs: (
            "" if args == ("dpkg", "--audit") else simulation if "--simulate" in args else "")
        return response

    def test_official_apt_install_pins_versions_and_never_runs_hello_world(self):
        self.install_fixture()
        b.install_docker(inputs()["packages"], "noble")
        calls = [c.args for c in self.commands.call_args_list]
        self.assertEqual(calls[0], ("dpkg", "--audit"))
        self.assertEqual(calls[1], ("apt-get", "update"))
        self.assertIn("--simulate", calls[2]); self.assertIn("--no-remove", calls[4]); self.assertIn("--no-upgrade", calls[4])
        self.assertTrue(all("=1.2.3-offline-fixture" in value for value in calls[4][-5:]))
        self.assertNotIn("docker", [c[0] for c in calls]); self.assertNotIn("sudo", [c[0] for c in calls])
        self.assertEqual(stat.S_IMODE(b.KEY.stat().st_mode), 0o644)

    def test_apt_simulation_rejects_remove_or_upgrade(self):
        self.install_fixture()
        for simulation in ("Remv docker.io [old]\n", "Inst libc6 [old] (new Ubuntu)\n"):
            self.commands.reset_mock()
            self.commands.side_effect = lambda *args, **kwargs: simulation if "--simulate" in args else ""
            with self.assertRaisesRegex(b.Blocked, "APT_WOULD_REMOVE_OR_UPGRADE"):
                b.install_docker(inputs()["packages"], "noble")
            self.assertFalse(any("--yes" in c.args for c in self.commands.call_args_list))

    def test_apt_simulation_rejects_unapproved_conf_and_allows_new_dependency_conf(self):
        self.install_fixture()
        self.commands.side_effect = lambda *args, **kwargs: (
            "" if args == ("dpkg", "--audit") else
            "Inst docker-ce (1.2.3 Docker:stable [arm64])\nConf unrelated-daemon (9.9 Ubuntu [arm64])\n"
            if "--simulate" in args else "")
        with self.assertRaisesRegex(b.Blocked, "APT_WOULD_CONFIGURE_UNAPPROVED_PACKAGE"):
            b.install_docker({"docker-ce": "1.2.3"}, "noble")
        self.assertFalse(any("--yes" in c.args for c in self.commands.call_args_list))

        self.commands.reset_mock()
        self.commands.side_effect = lambda *args, **kwargs: (
            "" if args == ("dpkg", "--audit") else
            "Inst docker-ce (1.2.3 Docker:stable [arm64])\n"
            "Inst libnew-dependency (2.0 Ubuntu [arm64])\n"
            "Conf docker-ce (1.2.3 Docker:stable [arm64])\n"
            "Conf libnew-dependency (2.0 Ubuntu [arm64])\n"
            if "--simulate" in args else "")
        b.install_docker({"docker-ce": "1.2.3"}, "noble")
        self.assertTrue(any("--yes" in c.args for c in self.commands.call_args_list))

    def test_unfinished_dpkg_state_stops_before_apt_or_source_mutation(self):
        self.stack.enter_context(patch.object(b, "KEY", self.temp / "keys/docker.asc"))
        self.stack.enter_context(patch.object(b, "SOURCE", self.temp / "sources/docker.sources"))
        self.commands.side_effect = lambda *args, **kwargs: "unconfigured packages remain" if args == ("dpkg", "--audit") else ""
        self.stack.enter_context(patch.object(b, "docker_startup_state", return_value={
            "configs": {}, "state_dirs": {str(p): None for p in b.DOCKER_STATE_DIRS}}))
        with self.assertRaisesRegex(b.Blocked, "DPKG_HAS_UNFINISHED_CONFIGURATION"):
            b.install_docker({"docker-ce": "1.2.3"}, "noble")
        self.assertEqual([call.args for call in self.commands.call_args_list], [("dpkg", "--audit")])
        self.network.assert_not_called()
        self.assertFalse(b.KEY.exists()); self.assertFalse(b.SOURCE.exists())

    def test_partial_install_retry_preserves_signing_key_and_source(self):
        self.install_fixture(); b.install_docker(inputs()["packages"], "noble")
        original = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (b.KEY, b.SOURCE)}
        self.network.reset_mock(); b.install_docker(inputs()["packages"], "noble")
        self.network.assert_not_called()
        self.assertEqual(original, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in original})

    def test_signing_key_redirect_or_oversize_is_rejected(self):
        response = self.install_fixture()
        response.geturl.return_value = "https://unapproved.invalid/gpg"
        with self.assertRaisesRegex(b.Blocked, "SIGNING_KEY"):
            b.install_docker(inputs()["packages"], "noble")
        self.assertEqual([c.args for c in self.commands.call_args_list], [("dpkg", "--audit")])
        self.assertFalse(b.KEY.exists())
        response.geturl.return_value = b.KEY_URL; response.read.return_value = b"x" * 65537
        with self.assertRaisesRegex(b.Blocked, "SIGNING_KEY"):
            b.install_docker(inputs()["packages"], "noble")


if __name__ == "__main__":
    unittest.main()
