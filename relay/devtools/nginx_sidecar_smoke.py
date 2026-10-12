"""Exercise the built NGINX sidecar against a local fake ACP server; no AWS/model calls."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from websockets.asyncio.server import serve

from relay.acp_client import load_acp_executor
from relay.tests.test_wss_proxy import FakeAcpServer, TOKEN, WssProxyTests, _task

RESULT_SENTINEL = "Local verified WSS result."


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_listener(process: subprocess.Popen, port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("NGINX container exited before the local listener became ready")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            await asyncio.sleep(0.1)
    raise TimeoutError("NGINX sidecar did not bind the loopback test port in time")


def _executor(url: str, ca_pem: str, *, token: str = TOKEN):
    executor = load_acp_executor({
        "AMUSANT_ACP_URL": url,
        "AMUSANT_ACP_AUTH_KEY": token,
        "AMUSANT_ACP_CWD": "/openab/work",
        "AMUSANT_ACP_TIMEOUT_SECONDS": "5",
        "AMUSANT_ACP_CA_PEM": ca_pem,
    })
    if executor is None:
        raise RuntimeError("ACP executor failed to load")
    return executor


async def run_smoke(image: str, *, repo_root: Path) -> dict[str, object]:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("this offline smoke requires Linux Docker host networking")
    if shutil.which("docker") is None:
        raise RuntimeError("docker is required; build the sidecar image first")
    subprocess.run(["docker", "image", "inspect", image], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    conf_path = repo_root / "deploy" / "acp-wss-proxy" / "acp.conf"
    conf = conf_path.read_text(encoding="utf-8")
    listen_port = _free_loopback_port()
    agent = FakeAcpServer()
    fake_logger = logging.getLogger("amusant.nginx.smoke.fake-acp")
    fake_logger.setLevel(logging.INFO)
    server = await serve(agent.handler, "127.0.0.1", 0, max_size=64 * 1024, logger=fake_logger)
    upstream_port = int(server.sockets[0].getsockname()[1])

    try:
        with tempfile.TemporaryDirectory(prefix="amusant-nginx-sidecar-") as directory:
            temp = Path(directory)
            ca_cert, ca_key = WssProxyTests._make_ca(temp, "nginx-smoke-ca")
            leaf_cert, leaf_key = WssProxyTests._make_server_cert(
                temp, "nginx-smoke-leaf", "IP:127.0.0.1", ca_cert, ca_key
            )
            wrong_ca, _ = WssProxyTests._make_ca(temp, "nginx-smoke-wrong-ca")
            ca_pem = Path(ca_cert).read_text(encoding="ascii")
            cert_pem = Path(leaf_cert).read_text(encoding="ascii")
            key_pem = Path(leaf_key).read_text(encoding="ascii")

            test_conf = conf.replace(
                "listen 8443 ssl default_server;",
                f"listen 127.0.0.1:{listen_port} ssl default_server;",
                1,
            ).replace(
                "proxy_pass http://127.0.0.1:8080/acp;",
                f"proxy_pass http://127.0.0.1:{upstream_port}/acp;",
                1,
            )
            if test_conf == conf:
                raise RuntimeError("sidecar test config substitutions were not applied")
            test_conf_path = temp / "acp.conf"
            test_conf_path.write_text(test_conf, encoding="utf-8")
            log_path = temp / "nginx.log"
            container_name = "amusant-nginx-smoke-" + uuid.uuid4().hex[:12]
            docker_env = os.environ.copy()
            docker_env["TLS_CERT_PEM"] = cert_pem
            docker_env["TLS_KEY_PEM"] = key_pem
            capture = io.StringIO()
            root_logger = logging.getLogger()
            previous_level = root_logger.level
            capture_handler = logging.StreamHandler(capture)
            process = None
            try:
                with log_path.open("wb") as logs:
                    process = subprocess.Popen(
                        [
                            "docker", "run", "--rm", "--network", "host", "--name", container_name,
                            "--mount", f"type=bind,src={test_conf_path},dst=/etc/nginx/conf.d/acp.conf,readonly",
                            "--env", "TLS_CERT_PEM", "--env", "TLS_KEY_PEM", image,
                        ],
                        env=docker_env,
                        stdout=logs,
                        stderr=subprocess.STDOUT,
                    )
                    root_logger.addHandler(capture_handler)
                    root_logger.setLevel(logging.DEBUG)
                    await _wait_for_listener(process, listen_port)
                    url = f"wss://127.0.0.1:{listen_port}/acp"
                    outcome = await _executor(url, ca_pem).execute(_task())
                    if outcome.status != "completed" or outcome.code != "ok":
                        raise RuntimeError("fake ACP exchange through NGINX did not complete")
                    if agent.prompt_count != 1 or agent.methods != ["initialize", "session/new", "session/prompt"]:
                        raise RuntimeError("fake ACP exchange did not execute exactly one prompt")

                    rejected = await _executor(
                        url, Path(wrong_ca).read_text(encoding="ascii")
                    ).execute(_task())
                    if (rejected.status, rejected.code) != ("failed", "acp_connect"):
                        raise RuntimeError("NGINX WSS did not fail closed for an untrusted CA")
                    if agent.prompt_count != 1:
                        raise RuntimeError("untrusted CA reached the fake ACP backend")
            finally:
                root_logger.removeHandler(capture_handler)
                root_logger.setLevel(previous_level)
                capture_handler.close()
                if process is not None and process.poll() is None:
                    subprocess.run(
                        ["docker", "stop", "--time", "2", container_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)

            nginx_logs = log_path.read_text(encoding="utf-8", errors="replace")
            app_logs = capture.getvalue()
            for sentinel in (TOKEN, _task().task, RESULT_SENTINEL):
                if sentinel in nginx_logs or sentinel in app_logs:
                    raise RuntimeError("a credential or ACP-content sentinel appeared in captured logs")

            return {
                "status": "PASS",
                "transport": "local WSS through pinned NGINX container",
                "acp_methods": agent.methods,
                "prompt_count": agent.prompt_count,
                "untrusted_ca": rejected.code,
                "secret_content_in_logs": False,
                "live_jellii_or_aws": False,
            }
    finally:
        server.close()
        await server.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=os.environ.get("AMUSANT_NGINX_SIDECAR_IMAGE", "amusant-acp-nginx-sidecar:offline-test"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        result = asyncio.run(run_smoke(args.image, repo_root=root))
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
