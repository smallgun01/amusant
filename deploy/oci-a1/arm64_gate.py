#!/usr/bin/env python3
"""Native, offline-only ARM64 go/no-go gate for issue #82."""
from __future__ import annotations

import importlib
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OCI_DIR = ROOT / "deploy" / "oci-a1"
EVIDENCE_FILE = OCI_DIR / "evidence" / "arm64-gate.json"
OPTION_D_RUNNER = ROOT / "spikes" / "004-relay-facade-hermes-e2e" / "run_e2e.py"

HERMES_IMAGE = (
    "docker.io/nousresearch/hermes-agent:v2026.9.24"
    "@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7"
)
HERMES_SOURCE_COMMIT = "f97608f178d1ffeca59860195ab7da295f7c8e5f"
RELAY_IMAGE = "amusant-relay:issue-82-arm64-gate"
RELAY_BASE_IMAGE = (
    "python:3.13.15-slim-bookworm"
    "@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26"
)
PLATFORM = "linux/arm64"
ELF_EM_AARCH64 = 183
ARM_MACHINES = {"aarch64", "arm64"}

class GateError(RuntimeError):
    def __init__(self, status: str, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def run(argv: list[str], *, timeout: int = 120, env=None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise GateError("UNKNOWN", "command_timeout") from None
    except OSError:
        raise GateError("UNKNOWN", "command_unavailable") from None


def record(evidence: dict, name: str, status: str, **details) -> None:
    evidence["checks"][name] = {"status": status, **details}


def failure_status(result: subprocess.CompletedProcess) -> str:
    diagnostic = (result.stderr + "\n" + result.stdout).lower()
    if any(
        marker in diagnostic
        for marker in (
            "toomanyrequests", "rate limit", "temporary failure",
            "connection reset", "connection refused", "i/o timeout",
            "context deadline exceeded", "service unavailable",
            "tls handshake timeout", "no such host",
        )
    ):
        return "UNKNOWN"
    return "FAIL"


def docker_server_arch() -> str | None:
    result = run(["docker", "info", "--format", "{{.Architecture}}"], timeout=30)
    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    return value or None


def set_unknown_if_missing(evidence: dict, names: list[str], reason: str) -> None:
    for name in names:
        evidence["checks"].setdefault(name, {"status": "UNKNOWN", "reason": reason})


def registry_manifest_check(evidence: dict) -> bool:
    result = run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", HERMES_IMAGE],
        timeout=180,
    )
    if result.returncode != 0:
        record(
            evidence, "hermes_registry_manifest", "UNKNOWN",
            reason="registry_inspection_unavailable", return_code=result.returncode,
        )
        return True
    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError:
        record(evidence, "hermes_registry_manifest", "UNKNOWN", reason="manifest_not_json")
        return True

    entries = manifest.get("manifests")
    if not isinstance(entries, list):
        evidence["hermes_manifest_kind"] = "single_manifest"
        record(
            evidence, "hermes_registry_manifest", "UNKNOWN",
            reason="single_manifest_platform_requires_pulled_config",
        )
        return True

    platforms = []
    arm_digest = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item = entry.get("platform") or {}
        os_name = item.get("os")
        arch = item.get("architecture")
        if isinstance(os_name, str) and isinstance(arch, str):
            platform_name = os_name + "/" + arch
            variant = item.get("variant")
            if isinstance(variant, str) and variant:
                platform_name += "/" + variant
            platforms.append(platform_name)
            if os_name == "linux" and arch in ARM_MACHINES:
                arm_digest = entry.get("digest")
    platforms = sorted(set(platforms))
    evidence["hermes_manifest_kind"] = "index"
    evidence["hermes_manifest_platforms"] = platforms
    if arm_digest:
        evidence["hermes_linux_arm64_child_digest"] = arm_digest
        record(evidence, "hermes_registry_manifest", "PASS", arm64_manifest_present=True)
        return True
    record(
        evidence, "hermes_registry_manifest", "FAIL",
        arm64_manifest_present=False, platforms=platforms,
    )
    return False


HERMES_PROBE = r"""
import importlib
import importlib.metadata
import json
import platform
import struct
import sys
from pathlib import Path

required = [
    ("aiohttp", "aiohttp", False),
    ("cryptography", "cryptography", False),
    ("cryptography.hazmat.bindings._rust", "cryptography", True),
    ("nacl", "PyNaCl", False),
    ("nacl._sodium", "PyNaCl", True),
    ("pydantic_core", "pydantic-core", False),
    ("pydantic_core._pydantic_core", "pydantic-core", True),
    ("websockets", "websockets", False),
    ("yaml", "PyYAML", False),
]
rows = []
ok = platform.machine().lower() in {"aarch64", "arm64"}
for module_name, distribution, native in required:
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__ or "")
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        version = None
    row = {"module": module_name, "version": version, "native": native}
    if native:
        data = module_path.read_bytes()
        if len(data) < 20 or data[:4] != b"\x7fELF" or data[5] != 1:
            machine = None
        else:
            machine = struct.unpack("<H", data[18:20])[0]
        row["file"] = module_path.name
        row["elf_machine"] = machine
        ok = ok and machine == 183
    rows.append(row)
print(json.dumps({
    "python": sys.version.split()[0],
    "executable": sys.executable,
    "architecture": platform.machine(),
    "modules": rows,
    "pass": ok,
}, separators=(",", ":")))
"""

RELAY_PROBE = r"""
import importlib
import json
import platform
import ssl
import struct
import sys
from pathlib import Path
import av
import numpy
from relay.device_opus import DeviceOpusCodec
from relay.device_tls_psk import server_context_from_environ

psk = "a" * 64
context = server_context_from_environ({"AMUSANT_DEVICE_PSKS": "offline-ci:" + psk})
DeviceOpusCodec()
assert callable(getattr(ssl.SSLContext, "set_psk_server_callback", None))
try:
    numpy_native = importlib.import_module("numpy._core._multiarray_umath")
except ModuleNotFoundError:
    numpy_native = importlib.import_module("numpy.core._multiarray_umath")
native = []
for name, module in (
    ("av._core", importlib.import_module("av._core")),
    ("numpy._multiarray_umath", numpy_native),
):
    path = Path(module.__file__ or "")
    data = path.read_bytes()
    machine = struct.unpack("<H", data[18:20])[0] if len(data) >= 20 and data[:4] == b"\x7fELF" and data[5] == 1 else None
    native.append({"module": name, "file": path.name, "elf_machine": machine})
print(json.dumps({
    "python": sys.version.split()[0],
    "executable": sys.executable,
    "python_architecture": platform.machine(),
    "av": av.__version__,
    "numpy": numpy.__version__,
    "native_modules": native,
    "openssl": ssl.OPENSSL_VERSION,
    "tls_psk_callbacks": True,
    "tls_psk_context": context.protocol == ssl.PROTOCOL_TLS_SERVER,
    "opus_codec_constructed": True,
    "pass": all(item["elf_machine"] == 183 for item in native),
}, separators=(",", ":")))
"""


def container_json(image: str, entrypoint: str, code: str) -> tuple[subprocess.CompletedProcess, dict | None]:
    result = run(
        [
            "docker", "run", "--rm", "--platform", PLATFORM,
            "--entrypoint", entrypoint, image, "-c", code,
        ],
        timeout=180,
    )
    if result.returncode != 0:
        return result, None
    for line in reversed(result.stdout.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return result, parsed
    return result, None


def hermes_checks(evidence: dict, manifest_has_arm64: bool) -> bool:
    if not manifest_has_arm64:
        set_unknown_if_missing(
            evidence,
            ["hermes_exact_digest_pull", "hermes_image_architecture", "hermes_python_native_modules", "hermes_cli", "option_d_v2_integrated_fake_provider"],
            "not_run_because_exact_digest_has_no_arm64_manifest",
        )
        return False

    pull = run(["docker", "pull", "--platform", PLATFORM, HERMES_IMAGE], timeout=900)
    if pull.returncode != 0:
        record(
            evidence, "hermes_exact_digest_pull", failure_status(pull),
            reason="platform_pull_failed", return_code=pull.returncode,
        )
        set_unknown_if_missing(
            evidence,
            ["hermes_image_architecture", "hermes_python_native_modules", "hermes_cli", "option_d_v2_integrated_fake_provider"],
            "exact_digest_pull_not_available",
        )
        return False

    inspect = run(
        ["docker", "image", "inspect", "--format", "{{.Os}}|{{.Architecture}}|{{.Id}}", HERMES_IMAGE],
        timeout=30,
    )
    if inspect.returncode != 0:
        record(evidence, "hermes_exact_digest_pull", "UNKNOWN", reason="image_inspection_failed")
        set_unknown_if_missing(
            evidence,
            ["hermes_image_architecture", "hermes_python_native_modules", "hermes_cli", "option_d_v2_integrated_fake_provider"],
            "pulled_image_metadata_unavailable",
        )
        return False
    parts = inspect.stdout.strip().split("|")
    if len(parts) != 3:
        record(evidence, "hermes_image_architecture", "UNKNOWN", reason="image_metadata_unparseable")
        return False
    os_arch, config_id = parts[0] + "/" + parts[1].lower(), parts[2]
    evidence["hermes_local_config_id"] = config_id
    if os_arch != PLATFORM:
        record(
            evidence, "hermes_exact_digest_pull", "PASS", requested_platform=PLATFORM,
        )
        record(evidence, "hermes_image_architecture", "FAIL", image_platform=os_arch)
        set_unknown_if_missing(
            evidence,
            ["hermes_python_native_modules", "hermes_cli", "option_d_v2_integrated_fake_provider"],
            "pulled_image_architecture_mismatch",
        )
        return False
    record(evidence, "hermes_exact_digest_pull", "PASS", requested_platform=PLATFORM)
    record(evidence, "hermes_image_architecture", "PASS", image_platform=os_arch)
    if evidence["checks"].get("hermes_registry_manifest", {}).get("status") == "UNKNOWN":
        evidence["checks"]["hermes_registry_manifest"] = {
            "status": "PASS",
            "reason": "pulled_exact_image_config_confirms_linux_arm64",
            "manifest_kind": evidence.get("hermes_manifest_kind", "single_manifest"),
        }

    probe, payload = container_json(
        HERMES_IMAGE, "/opt/hermes/.venv/bin/python", HERMES_PROBE,
    )
    if payload is None:
        record(
            evidence, "hermes_python_native_modules", failure_status(probe),
            reason="interpreter_import_or_elf_probe_failed", return_code=probe.returncode,
        )
        set_unknown_if_missing(
            evidence, ["hermes_cli", "option_d_v2_integrated_fake_provider"],
            "native_module_probe_not_passed",
        )
        return False
    evidence["hermes_python"] = {
        "version": payload.get("python"),
        "executable": payload.get("executable"),
        "architecture": payload.get("architecture"),
    }
    evidence["hermes_native_modules"] = payload.get("modules", [])
    modules_pass = (
        payload.get("pass") is True
        and str(payload.get("architecture", "")).lower() in ARM_MACHINES
        and all(
            item.get("elf_machine") == ELF_EM_AARCH64
            for item in payload.get("modules", [])
            if item.get("native") is True
        )
        and sum(1 for item in payload.get("modules", []) if item.get("native") is True) >= 3
    )
    record(
        evidence, "hermes_python_native_modules",
        "PASS" if modules_pass else "FAIL",
        architecture=payload.get("architecture"),
        native_module_count=sum(1 for item in payload.get("modules", []) if item.get("native") is True),
        elf_machine="EM_AARCH64" if modules_pass else "unexpected_or_missing",
    )
    if not modules_pass:
        set_unknown_if_missing(evidence, ["hermes_cli", "option_d_v2_integrated_fake_provider"], "native_module_probe_failed")
        return False

    outputs = {}
    cli_ok = True
    for label, args in (
        ("help", ["--help"]),
        ("version", ["--version"]),
    ):
        result = run(
            ["docker", "run", "--rm", "--platform", PLATFORM,
             "--entrypoint", "hermes", HERMES_IMAGE, *args],
            timeout=120,
        )
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        version_match = re.search(r"(?i)\bv?\d{4}\.\d{1,2}\.\d{1,2}\b", text)
        outputs[label] = {
            "status": "PASS" if result.returncode == 0 else failure_status(result),
            "return_code": result.returncode,
        }
        if label == "version" and version_match:
            outputs["reported_release"] = version_match.group(0)
        cli_ok = cli_ok and result.returncode == 0
    evidence["hermes_cli"] = outputs
    record(
        evidence, "hermes_cli",
        "PASS" if cli_ok else "FAIL",
        help=outputs["help"]["status"], version=outputs["version"]["status"],
    )
    return cli_ok


def relay_checks(evidence: dict) -> bool:
    build = run(
        [
            "docker", "buildx", "build", "--platform", PLATFORM, "--load",
            "--file", "deploy/oci-a1/Dockerfile.relay",
            "--tag", RELAY_IMAGE, ".",
        ],
        timeout=1800,
    )
    if build.returncode != 0:
        status = failure_status(build)
        record(evidence, "relay_native_build", status, return_code=build.returncode)
        set_unknown_if_missing(
            evidence,
            ["relay_runtime_imports", "relay_opus_tests", "relay_tls_psk_tests", "relay_persistent_entrypoint"],
            "relay_image_build_not_available",
        )
        return False

    inspect = run(
        ["docker", "image", "inspect", "--format", "{{.Id}}|{{.Os}}/{{.Architecture}}", RELAY_IMAGE],
        timeout=30,
    )
    image_id = None
    image_platform = None
    if inspect.returncode == 0:
        parts = inspect.stdout.strip().split("|", 1)
        if len(parts) == 2:
            image_id, image_platform = parts
            evidence["relay_local_config_id"] = image_id
    if image_platform != PLATFORM:
        record(
            evidence, "relay_native_build", "FAIL",
            reason="built_image_platform_mismatch", image_platform=image_platform,
        )
        return False
    record(
        evidence, "relay_native_build", "PASS",
        image_platform=image_platform, local_config_id=image_id,
        base_image=RELAY_BASE_IMAGE,
    )

    probe, payload = container_json(
        RELAY_IMAGE, "/app/.venv/bin/python", RELAY_PROBE,
    )
    if payload is None:
        record(
            evidence, "relay_runtime_imports", failure_status(probe),
            reason="runtime_import_or_context_probe_failed", return_code=probe.returncode,
        )
        set_unknown_if_missing(
            evidence,
            ["relay_opus_tests", "relay_tls_psk_tests", "relay_persistent_entrypoint"],
            "relay_runtime_probe_not_passed",
        )
        return False
    evidence["relay_runtime"] = payload
    runtime_ok = (
        str(payload.get("python_architecture", "")).lower() in ARM_MACHINES
        and payload.get("tls_psk_callbacks") is True
        and payload.get("tls_psk_context") is True
        and payload.get("opus_codec_constructed") is True
        and payload.get("pass") is True
    )
    record(
        evidence, "relay_runtime_imports",
        "PASS" if runtime_ok else "FAIL",
        python=payload.get("python"),
        executable=payload.get("executable"),
        python_architecture=payload.get("python_architecture"),
        av=payload.get("av"), numpy=payload.get("numpy"),
        native_modules=payload.get("native_modules", []),
        tls_psk_context=payload.get("tls_psk_context") is True,
        opus_codec_constructed=payload.get("opus_codec_constructed") is True,
    )
    if not runtime_ok:
        return False

    tests = [
        ("relay_opus_tests", "test_device_opus.py"),
        ("relay_tls_psk_tests", "test_device_tls_psk.py"),
    ]
    tests_ok = True
    for name, pattern in tests:
        result = run(
            [
                "docker", "run", "--rm", "--platform", PLATFORM,
                "--entrypoint", "/app/.venv/bin/python", RELAY_IMAGE,
                "-m", "unittest", "discover", "-s", "relay/tests",
                "-p", pattern, "-v",
            ],
            timeout=180,
        )
        status = "PASS" if result.returncode == 0 else failure_status(result)
        record(evidence, name, status, return_code=result.returncode)
        tests_ok = tests_ok and result.returncode == 0
    if not tests_ok:
        record(evidence, "relay_persistent_entrypoint", "UNKNOWN", reason="runtime_tests_failed")
        return False

    startup = relay_entrypoint_probe()
    record(evidence, "relay_persistent_entrypoint", startup["status"], **startup["details"])
    return startup["status"] == "PASS"


def relay_entrypoint_probe() -> dict:
    suffix = secrets.token_hex(6)
    network = "issue82-relay-" + suffix
    volume = "issue82-secrets-" + suffix
    container = "issue82-relay-runtime-" + suffix
    bind_ip = "172.30.82.2"
    fake_values = [
        "offline-test-gemini-key",
        "offline-test-device-token-84c912",
        "offline-ci:" + "a" * 64,
        "offline-test-hermes-key",
    ]
    cleanup = []
    status = "UNKNOWN"
    details: dict = {
        "network": "internal_docker_bridge",
        "health_check": "listener_tcp_only",
        "provider_calls": False,
    }
    try:
        created_network = run(
            ["docker", "network", "create", "--driver", "bridge", "--internal",
             "--subnet", "172.30.82.0/29", network],
            timeout=30,
        )
        if created_network.returncode != 0:
            raise GateError(failure_status(created_network), "relay_private_network_create_failed")
        cleanup.append(("docker", "network", "rm", network))

        created_volume = run(["docker", "volume", "create", volume], timeout=30)
        if created_volume.returncode != 0:
            raise GateError(failure_status(created_volume), "relay_secret_volume_create_failed")
        cleanup.append(("docker", "volume", "rm", volume))

        seed_script = (
            "from pathlib import Path; import os; "
            "root=Path('/run/secrets'); "
            "items={"
            "'gemini_api_key':(" + repr(fake_values[0]) + ",10001,10001,0o400),"
            "'device_token':(" + repr(fake_values[1]) + ",10001,10001,0o400),"
            "'device_psks':(" + repr(fake_values[2]) + ",10001,10001,0o400),"
            "'hermes_api_key':(" + repr(fake_values[3]) + ",0,20000,0o440)}; "
            "[(root.joinpath(name).write_text(value+'\\n',encoding='utf-8'),"
            "os.chown(root/name,uid,gid),os.chmod(root/name,mode)) "
            "for name,(value,uid,gid,mode) in items.items()]"
        )
        seeded = run(
            [
                "docker", "run", "--rm", "--network", "none",
                "--volume", volume + ":/run/secrets",
                "--user", "0:0", "--entrypoint", "/usr/local/bin/python",
                RELAY_IMAGE, "-c", seed_script,
            ],
            timeout=60,
        )
        if seeded.returncode != 0:
            raise GateError(failure_status(seeded), "relay_synthetic_secret_setup_failed")

        cleanup.append(("docker", "rm", "--force", container))
        started = run(
            [
                "docker", "run", "--detach", "--name", container,
                "--network", network, "--ip", bind_ip, "--group-add", "20000",
                "--mount", "type=volume,src=" + volume + ",dst=/run/secrets,readonly",
                "--health-cmd", "exec /usr/local/bin/python /opt/amusant/healthcheck.py",
                "--health-interval", "2s", "--health-timeout", "3s",
                "--health-retries", "8", "--health-start-period", "5s",
                "-e", "GEMINI_API_KEY_FILE=/run/secrets/gemini_api_key",
                "-e", "AMUSANT_DEVICE_TOKEN_FILE=/run/secrets/device_token",
                "-e", "AMUSANT_DEVICE_PSKS_FILE=/run/secrets/device_psks",
                "-e", "AMUSANT_HERMES_API_KEY_FILE=/run/secrets/hermes_api_key",
                "-e", "AMUSANT_LIVE_PROVIDER=gemini",
                "-e", "RELAY_BIND_IP=" + bind_ip,
                "-e", "RELAY_LISTEN_PORT=8443",
                "-e", "RELAY_DEVICE_MODE=sticks3",
                "-e", "AMUSANT_CLIENT_ID=offline-ci-owner",
                "-e", "AMUSANT_BACKEND_ID=issue82-arm64",
                "-e", "AMUSANT_TASK_DB=/var/lib/amusant/issue82-gate.sqlite3",
                "-e", "AMUSANT_SLOW_EXECUTOR=openab_acp",
                "-e", "AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS=2",
                "--platform", PLATFORM, RELAY_IMAGE,
            ],
            timeout=60,
        )
        if started.returncode != 0:
            raise GateError(failure_status(started), "relay_container_start_failed")
        healthy_samples = 0
        for _ in range(40):
            inspected = run(
                ["docker", "inspect", "--format", "{{json .State}}", container],
                timeout=15,
            )
            if inspected.returncode != 0:
                break
            try:
                state = json.loads(inspected.stdout)
            except json.JSONDecodeError:
                break
            if state.get("Running") and (state.get("Health") or {}).get("Status") == "healthy":
                healthy_samples += 1
                if healthy_samples >= 3:
                    break
            else:
                healthy_samples = 0
                if not state.get("Running"):
                    break
            time.sleep(2)
        if healthy_samples < 3:
            raise GateError("FAIL", "relay_listener_did_not_remain_healthy")

        log_result = run(["docker", "logs", container], timeout=20)
        if log_result.returncode != 0:
            raise GateError(failure_status(log_result), "relay_log_probe_failed")
        combined_logs = log_result.stdout + "\n" + log_result.stderr
        marker_absent = all(value not in combined_logs for value in fake_values)
        if not marker_absent:
            raise GateError("FAIL", "synthetic_secret_value_logged")

        stopped = run(["docker", "stop", "--time", "20", container], timeout=35)
        if stopped.returncode != 0:
            raise GateError(failure_status(stopped), "relay_sigterm_shutdown_failed")
        final_state = run(
            ["docker", "inspect", "--format", "{{json .State}}", container],
            timeout=15,
        )
        state = json.loads(final_state.stdout) if final_state.returncode == 0 else {}
        if state.get("Running") is not False or state.get("ExitCode") not in {0, None}:
            raise GateError("FAIL", "relay_container_did_not_stop_cleanly")
        details.update({
            "healthy_samples": healthy_samples,
            "synthetic_secrets_absent_from_logs": marker_absent,
            "sigterm_shutdown": "PASS",
            "bind_mode": "explicit_private_ip",
        })
        status = "PASS"
    except GateError as exc:
        status = exc.status
        details["reason"] = exc.code
    except Exception as exc:
        status = "UNKNOWN"
        details["reason"] = "probe_" + type(exc).__name__
    finally:
        cleanup_failed = []
        for argv in reversed(cleanup):
            result = run(list(argv), timeout=30)
            if result.returncode != 0:
                cleanup_failed.append(argv[1])
        details["cleanup_complete"] = not cleanup_failed
        if cleanup_failed:
            details["cleanup_failures"] = cleanup_failed
            if status == "PASS":
                status = "UNKNOWN"
                details["reason"] = "disposable_resource_cleanup_failed"
    return {"status": status, "details": details}


def option_d_check(evidence: dict) -> bool:
    env = os.environ.copy()
    env["POC_HERMES_PLATFORM"] = PLATFORM
    env["DOCKER_DEFAULT_PLATFORM"] = PLATFORM
    env["PYTHONUNBUFFERED"] = "1"
    result = run([sys.executable, str(OPTION_D_RUNNER)], timeout=1800, env=env)
    e2e_path = ROOT / "spikes" / "004-relay-facade-hermes-e2e" / "evidence" / "phase1b-e1.json"
    e2e = None
    try:
        e2e = json.loads(e2e_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    verdict = e2e.get("status") if isinstance(e2e, dict) else None
    if result.returncode == 0 and verdict == "PASS":
        record(
            evidence, "option_d_v2_integrated_fake_provider",
            "PASS", platform=PLATFORM, proof_status=verdict,
            evidence_file="spikes/004-relay-facade-hermes-e2e/evidence/phase1b-e1.json",
        )
        return True
    if verdict == "FAIL":
        status = "FAIL"
    elif verdict == "UNKNOWN" or result.returncode != 0:
        status = "UNKNOWN"
    else:
        status = "UNKNOWN"
    record(
        evidence, "option_d_v2_integrated_fake_provider", status,
        reason="integrated_v2_runner_did_not_pass",
        runner_return_code=result.returncode,
        proof_status=verdict,
        failure_stage=e2e.get("failure_stage") if isinstance(e2e, dict) else None,
        failure_type=e2e.get("failure_type") if isinstance(e2e, dict) else None,
    )
    return False


def main() -> int:
    evidence = {
        "schema_version": 1,
        "issue": 82,
        "verdict": "UNKNOWN",
        "platform": PLATFORM,
        "hermes": {"image": HERMES_IMAGE, "source_commit": HERMES_SOURCE_COMMIT},
        "relay": {
            "dockerfile": "deploy/oci-a1/Dockerfile.relay",
            "base_image": RELAY_BASE_IMAGE,
            "image_tag": RELAY_IMAGE,
        },
        "source_commit_under_test": os.environ.get("ARM64_SOURCE_COMMIT") or os.environ.get("GITHUB_SHA"),
        "runner": {
            "os": platform.system(),
            "machine": platform.machine(),
            "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
        },
        "checks": {},
        "limitations": [
            "No OCI host or cloud infrastructure was contacted or changed.",
            "Hermes integration uses only the local deterministic fake provider.",
            "Relay startup uses synthetic secret files and an internal Docker network; no provider request is made.",
            "This gate does not measure OCI A1 performance, device ingress, persistent-volume restore, or production readiness.",
        ],
    }
    native_machine = platform.machine().lower()
    server_arch = docker_server_arch()
    evidence["runner"]["docker_server_architecture"] = server_arch
    native = platform.system() == "Linux" and native_machine in ARM_MACHINES and server_arch in ARM_MACHINES
    if native:
        record(evidence, "native_arm64_runner", "PASS", docker_server_architecture=server_arch)
    else:
        record(
            evidence, "native_arm64_runner", "UNKNOWN",
            reason="native_linux_arm64_runner_not_verified",
            docker_server_architecture=server_arch,
        )
        set_unknown_if_missing(
            evidence,
            [
                "hermes_registry_manifest", "hermes_exact_digest_pull",
                "hermes_image_architecture", "hermes_python_native_modules",
                "hermes_cli", "relay_native_build", "relay_runtime_imports",
                "relay_opus_tests", "relay_tls_psk_tests",
                "relay_persistent_entrypoint", "option_d_v2_integrated_fake_provider",
            ],
            "no_native_arm64_execution",
        )
    if native:
        try:
            manifest_has_arm64 = registry_manifest_check(evidence)
            hermes_ok = hermes_checks(evidence, manifest_has_arm64)
        except GateError as exc:
            record(evidence, "hermes_gate_internal", exc.status, reason=exc.code)
            hermes_ok = False
        except Exception as exc:
            record(evidence, "hermes_gate_internal", "UNKNOWN", reason="probe_" + type(exc).__name__)
            hermes_ok = False
        try:
            relay_ok = relay_checks(evidence)
        except GateError as exc:
            record(evidence, "relay_gate_internal", exc.status, reason=exc.code)
            relay_ok = False
        except Exception as exc:
            record(evidence, "relay_gate_internal", "UNKNOWN", reason="probe_" + type(exc).__name__)
            relay_ok = False
        if hermes_ok and relay_ok:
            try:
                optiond_ok = option_d_check(evidence)
            except GateError as exc:
                record(evidence, "option_d_v2_integrated_fake_provider", exc.status, reason=exc.code)
                optiond_ok = False
            except Exception as exc:
                record(evidence, "option_d_v2_integrated_fake_provider", "UNKNOWN", reason="probe_" + type(exc).__name__)
                optiond_ok = False
        else:
            record(
                evidence, "option_d_v2_integrated_fake_provider", "UNKNOWN",
                reason="hermes_or_relay_prerequisite_not_passed",
            )
            optiond_ok = False

    statuses = [item.get("status") for item in evidence["checks"].values()]
    if "FAIL" in statuses:
        evidence["verdict"] = "NO-GO"
    elif statuses and all(status == "PASS" for status in statuses):
        evidence["verdict"] = "GO"
    else:
        evidence["verdict"] = "UNKNOWN"

    evidence["next_gate"] = (
        "A GO records native ARM64 v2 integrated fake-provider evidence only; OCI deployment, live-provider use, and operational cutover require separate review."
        if evidence["verdict"] == "GO"
        else "Resolve failed checks or obtain complete native ARM64 evidence before using this offline gate as evidence."
    )
    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": evidence["verdict"],
        "checks": {name: item.get("status") for name, item in evidence["checks"].items()},
        "evidence_file": str(EVIDENCE_FILE.relative_to(ROOT)),
    }, separators=(",", ":")))
    return 0 if evidence["verdict"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
