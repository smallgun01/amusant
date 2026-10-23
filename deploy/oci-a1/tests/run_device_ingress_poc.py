"""Run the isolated Docker-network + systemd TLS-PSK ingress PoC on Linux CI only."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "oci-a1"
FIXTURES = PACKAGE / "tests" / "device_ingress_poc"
COMPOSE_FILE = PACKAGE / "tests" / "device_ingress_poc.yaml"
IMAGE = "amusant-relay:g1-device-ingress-poc"
SOCKET_NAME = "amusant-device-ingress.socket"
SERVICE_NAME = "amusant-device-ingress.service"
RELAY_IP = "172.30.62.2"


def command(args: list[str], *, env: dict[str, str] | None = None, capture: bool = False,
            check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=check,
        timeout=timeout,
    )


def docker_host_gateway() -> str:
    result = command(["docker", "network", "inspect", "bridge"], capture=True)
    info = json.loads(result.stdout)[0]
    gateway = info["IPAM"]["Config"][0]["Gateway"]
    address = ipaddress.ip_address(gateway)
    if address.version != 4 or not address.is_private:
        raise RuntimeError("offline test requires a private IPv4 Docker host gateway")
    return gateway


def assert_test_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, port))
        except OSError:
            raise RuntimeError("offline runner TCP/443 is already occupied") from None


def wait_for_relay(project_args: list[str], env: dict[str, str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = command(
            project_args + [
                "exec", "-T", "private_client", "/app/.venv/bin/python",
                "/repo/deploy/oci-a1/tests/device_ingress_poc/network_probe.py",
                "--allow", f"{RELAY_IP}:8443",
            ],
            env=env,
            capture=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("synthetic Relay readiness timed out")


def run_client(project_args: list[str], suite: str, host: str, port: int,
               env: dict[str, str]) -> None:
    client_service = "private_client" if suite == "private" else "external_client"
    args = project_args + [
        "exec", "-T", client_service,
        "/app/.venv/bin/python", "/repo/deploy/oci-a1/tests/device_ingress_poc/client.py",
        "--host", host, "--port", str(port), "--suite", suite,
    ]
    result = command(args, env=env, capture=True, check=False, timeout=60)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode == 0:
        return
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if suite == "core":
        for diagnostic in (
            ["systemctl", "show", SERVICE_NAME, "--property=ExecStart", "--property=MainPID", "--property=ActiveState"],
            ["systemctl", "status", SERVICE_NAME, "--no-pager", "--full"],
            ["journalctl", "-u", SERVICE_NAME, "--no-pager", "-n", "80"],
            project_args + ["exec", "-T", "relay", "cat", "/tmp/g1-active-connections"],
            project_args + ["logs", "--no-color", "--tail", "80", "relay"],
        ):
            output = command(diagnostic, env=env, capture=True, check=False, timeout=30)
            if output.stdout:
                print(output.stdout, end="")
            if output.stderr:
                print(output.stderr, end="", file=sys.stderr)
    raise subprocess.CalledProcessError(result.returncode, args)


def wait_for_service_state(active: bool, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = command(["systemctl", "is-active", SERVICE_NAME], capture=True, check=False)
        state = result.stdout.strip()
        if (state == "active") is active:
            return
        time.sleep(0.2)
    wanted = "active" if active else "inactive"
    raise RuntimeError(f"systemd proxy did not reach {wanted} state")


def write_synthetic_env(path: Path, gateway: str) -> None:
    digest = "a" * 64
    values = {
        "RELAY_IMAGE": f"registry.invalid/relay@sha256:{digest}",
        "FACADE_IMAGE": f"registry.invalid/facade@sha256:{digest}",
        "HERMES_IMAGE": f"registry.invalid/hermes@sha256:{digest}",
        "EGRESS_PROXY_IMAGE": f"registry.invalid/proxy@sha256:{digest}",
        "RELAY_API_CIDR": "172.30.62.0/29",
        "RELAY_BIND_IP": RELAY_IP,
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
        "RELAY_HOST_PRIVATE_IP": gateway,
        "RELAY_PUBLISH_PORT": "8443",
    }
    path.write_text("".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    if sys.platform != "linux":
        raise RuntimeError("this test must run on an isolated Linux CI host")
    if os.geteuid() != 0:
        raise RuntimeError("run this disposable test harness as root on CI")
    for executable in ("docker", "systemctl", "systemd-analyze"):
        if not shutil.which(executable):
            raise RuntimeError(f"required offline test tool is unavailable: {executable}")
    if not Path("/usr/lib/systemd/systemd-socket-proxyd").is_file():
        raise RuntimeError("systemd-socket-proxyd is unavailable at its Ubuntu system path")
    if not Path("/run/systemd/system").is_dir():
        raise RuntimeError("systemd system manager is unavailable")
    command(["systemctl", "show", "--property=Version", "--value"], capture=True)

    project = f"g1-ingress-{uuid.uuid4().hex[:8]}"
    project_args = ["docker", "compose", "--project-name", project, "-f", str(COMPOSE_FILE)]
    systemd_dir = Path("/run/systemd/system")
    socket_path = systemd_dir / SOCKET_NAME
    service_path = systemd_dir / SERVICE_NAME
    if socket_path.exists() or service_path.exists():
        raise RuntimeError("test systemd unit name is already in use")

    evidence_path = Path(os.environ.get("G1_EVIDENCE_PATH", tempfile.gettempdir() + "/issue-27-g1-evidence.json"))
    evidence = {
        "suite": "issue-27-g1-offline-systemd-socket-proxyd",
        "status": "running",
        "oci_accessed": False,
        "provider_calls": 0,
        "device_flashed": False,
        "results": {},
    }
    project_env = os.environ.copy()
    project_env["G1_TEST_IMAGE"] = IMAGE
    project_env["G1_REPO_ROOT"] = str(ROOT)

    try:
        with tempfile.TemporaryDirectory(prefix="issue-27-g1-") as temp_name:
            temp = Path(temp_name)
            env_file = temp / "synthetic.env"
            gateway = docker_host_gateway()
            assert_test_port_available(gateway, 443)
            write_synthetic_env(env_file, gateway)
            renderer_dir = temp / "units"
            render_args = [
                sys.executable,
                str(PACKAGE / "render_device_ingress.py"),
                "--env-file", str(env_file),
                "--output-dir", str(renderer_dir),
                "--max-connections", "3",
                "--exit-idle-seconds", "2",
            ]
            command(render_args)
            socket_text = (renderer_dir / SOCKET_NAME).read_text(encoding="utf-8")
            service_text = (renderer_dir / SERVICE_NAME).read_text(encoding="utf-8")
            command(["systemd-analyze", "verify", str(renderer_dir / SOCKET_NAME), str(renderer_dir / SERVICE_NAME)])

            started = False
            try:
                command([
                    "docker", "buildx", "build", "--platform", "linux/amd64", "--load",
                    "-f", "deploy/oci-a1/Dockerfile.relay", "-t", IMAGE, str(ROOT),
                ], timeout=1200)
                started = True
                command(project_args + ["--env-file", str(env_file), "up", "--detach", "--no-build"], env=project_env)
                wait_for_relay(project_args, project_env)

                socket_path.write_text(socket_text, encoding="utf-8")
                service_path.write_text(service_text, encoding="utf-8")
                command(["systemctl", "daemon-reload"])
                command(["systemctl", "start", SOCKET_NAME])
                command(["systemctl", "is-active", SOCKET_NAME], capture=True)

                run_client(project_args, "core", "host.docker.internal", 443, project_env)
                metrics = command(
                    project_args + ["exec", "-T", "relay", "cat", "/tmp/g1-metrics"],
                    env=project_env,
                    capture=True,
                ).stdout.strip()
                if metrics != "synthetic_provider_activations=1":
                    raise RuntimeError("invalid PSK/Bearer/plaintext/oversized test activated the fake provider")
                evidence["provider_calls"] = 0
                evidence["results"].update({
                    "external_test_client_to_host_tcp_443": "PASS",
                    "tls12_ecdhe_psk_bearer_synthetic_ptt": "PASS",
                    "wrong_psk_wrong_bearer_missing_bearer_plaintext": "PASS",
                    "oversized_frame_and_connection_limit": "PASS",
                    "invalid_inputs_before_fake_provider": "PASS",
                })

                command(project_args + [
                    "exec", "-T", "external_client", "/app/.venv/bin/python",
                    "/repo/deploy/oci-a1/tests/device_ingress_poc/network_probe.py",
                    "--deny", "172.30.62.3:8643", "--deny", "172.30.64.3:8642",
                ], env=project_env)
                command(project_args + [
                    "exec", "-T", "relay", "/app/.venv/bin/python",
                    "/repo/deploy/oci-a1/tests/device_ingress_poc/network_probe.py",
                    "--allow", "172.30.63.3:3128", "--deny", "172.30.67.3:9443",
                ], env=project_env)
                command(project_args + [
                    "exec", "-T", "hermes", "/app/.venv/bin/python",
                    "/repo/deploy/oci-a1/tests/device_ingress_poc/network_probe.py",
                    "--allow", "172.30.65.3:3128", "--deny", "172.30.67.3:9443",
                ], env=project_env)
                command(project_args + [
                    "exec", "-T", "egress_proxy", "/app/.venv/bin/python",
                    "/repo/deploy/oci-a1/tests/device_ingress_poc/network_probe.py",
                    "--allow", "172.30.67.3:9443",
                ], env=project_env)
                evidence["results"].update({
                    "ingress_client_to_facade_hermes_denied": "PASS",
                    "relay_hermes_direct_egress_denied": "PASS",
                    "egress_proxy_route_available": "PASS",
                })

                run_client(project_args, "private", "172.30.62.2", 8443, project_env)
                evidence["results"]["existing_private_relay_path"] = "PASS"

                wait_for_service_state(active=False)
                run_client(project_args, "basic", "host.docker.internal", 443, project_env)
                evidence["results"]["idle_exit_and_socket_reactivation"] = "PASS"

                command(["systemctl", "restart", SOCKET_NAME])
                run_client(project_args, "basic", "host.docker.internal", 443, project_env)
                evidence["results"]["socket_restart_and_reconnect"] = "PASS"

                command(project_args + ["stop", "relay"], env=project_env)
                run_client(project_args, "expect-failure", "host.docker.internal", 443, project_env)
                command(project_args + ["start", "relay"], env=project_env)
                wait_for_relay(project_args, project_env)
                run_client(project_args, "basic", "host.docker.internal", 443, project_env)
                evidence["results"]["upstream_failure_restart_recovery"] = "PASS"
                evidence["status"] = "PASS"
            finally:
                command(["systemctl", "stop", SOCKET_NAME], check=False, capture=True)
                command(["systemctl", "stop", SERVICE_NAME], check=False, capture=True)
                command(["systemctl", "reset-failed", SOCKET_NAME, SERVICE_NAME], check=False, capture=True)
                socket_path.unlink(missing_ok=True)
                service_path.unlink(missing_ok=True)
                command(["systemctl", "daemon-reload"], check=False, capture=True)
                if started:
                    command(project_args + ["down", "--volumes", "--remove-orphans"], env=project_env,
                            check=False, capture=True, timeout=120)

    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["failure_type"] = type(exc).__name__
        raise
    finally:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print("ISSUE_27_G1_OFFLINE_POC_" + evidence["status"])
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
