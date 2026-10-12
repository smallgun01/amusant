"""Exercise the built NGINX sidecar against a local fake ACP server; no AWS/model calls."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import re
import ssl
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus

from relay.acp_client import load_acp_executor
from relay.tests.test_wss_proxy import FakeAcpServer, TOKEN, WssProxyTests, _task

RESULT_SENTINEL = "Local verified WSS result."
_RAW_CLIENT_LOGGER = logging.getLogger("amusant.nginx.smoke.raw-client")
_RAW_CLIENT_LOGGER.setLevel(logging.INFO)
WRONG_TOKEN = "invalid-sidecar-bearer-12"


def _verify_running_nginx_config(container_name: str) -> None:
    result = subprocess.run(
        ["docker", "exec", container_name, "nginx", "-T"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    config = result.stdout + result.stderr
    for directive in (
        "pid",
        "client_body_temp_path",
        "proxy_temp_path",
        "fastcgi_temp_path",
        "uwsgi_temp_path",
        "scgi_temp_path",
    ):
        if not re.search(rf"(?m)^\s*{directive}\s+/tmp(?:/[^;\s]+)?;", config):
            raise RuntimeError("running NGINX config lacks an expected /tmp runtime path")
    listens = re.findall(r"(?m)^\s*listen\s+([^;]+);", config)
    if any(re.search(r"(?:^|[^0-9])8080(?:$|[^0-9])", listener) for listener in listens):
        raise RuntimeError("running NGINX config still has a listener on port 8080")
    if "location = /acp" not in config:
        raise RuntimeError("running NGINX config lacks the exact ACP route")


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

                    _verify_running_nginx_config(container_name)

                    bad_bearer = await _executor(url, ca_pem, token=WRONG_TOKEN).execute(_task())
                    if (bad_bearer.status, bad_bearer.code) != ("failed", "acp_connect"):
                        raise RuntimeError("NGINX WSS did not fail closed for a bad Bearer")
                    if agent.prompt_count != 1:
                        raise RuntimeError("bad Bearer reached the fake ACP prompt")

                    tls_context = ssl.create_default_context(cafile=ca_cert)
                    try:
                        async with connect(
                            url, ssl=tls_context, open_timeout=3, logger=_RAW_CLIENT_LOGGER
                        ) as websocket:
                            try:
                                await asyncio.wait_for(websocket.recv(), timeout=3)
                            except ConnectionClosed:
                                pass
                            else:
                                raise RuntimeError("fake ACP did not close a missing-Bearer connection")
                    except ConnectionClosed:
                        pass
                    if agent.prompt_count != 1:
                        raise RuntimeError("missing Bearer reached the fake ACP prompt")

                    wrong_path = url.rsplit("/", 1)[0] + "/not-acp"
                    try:
                        async with connect(
                            wrong_path,
                            ssl=ssl.create_default_context(cafile=ca_cert),
                            open_timeout=3,
                            logger=_RAW_CLIENT_LOGGER,
                        ):
                            raise RuntimeError("NGINX accepted a non-/acp path")
                    except InvalidStatus as error:
                        if error.response.status_code != 404:
                            raise RuntimeError("NGINX returned an unexpected status for non-/acp path") from None
                    if agent.prompt_count != 1:
                        raise RuntimeError("non-/acp path reached the fake ACP prompt")

                    dns_url = f"wss://jellii-acp.internal:{listen_port}/acp"
                    loop = asyncio.get_running_loop()
                    system_getaddrinfo = loop.getaddrinfo

                    async def test_dns_to_proxy(host, port, **kwargs):
                        if host == "jellii-acp.internal":
                            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]
                        return await system_getaddrinfo(host, port, **kwargs)

                    with patch.object(loop, "getaddrinfo", new=test_dns_to_proxy):
                        wrong_hostname = await _executor(dns_url, ca_pem).execute(_task())
                    if (wrong_hostname.status, wrong_hostname.code) != ("failed", "acp_connect"):
                        raise RuntimeError("NGINX WSS did not reject a wrong leaf SAN/hostname")
                    if agent.prompt_count != 1:
                        raise RuntimeError("wrong leaf SAN/hostname reached the fake ACP prompt")

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
            leaks = {
                "bearer": TOKEN in nginx_logs or TOKEN in app_logs,
                "bad_bearer": WRONG_TOKEN in nginx_logs or WRONG_TOKEN in app_logs,
                "prompt": _task().task in nginx_logs or _task().task in app_logs,
                "result": RESULT_SENTINEL in nginx_logs or RESULT_SENTINEL in app_logs,
            }
            if any(leaks.values()):
                names = ",".join(name for name, present in leaks.items() if present)
                raise RuntimeError("captured logs contain sentinel categories: " + names)

            return {
                "status": "PASS",
                "transport": "local WSS through pinned NGINX container",
                "acp_methods": agent.methods,
                "prompt_count": agent.prompt_count,
                "security_probes": {
                    "bad_bearer": bad_bearer.code,
                    "missing_bearer": "rejected",
                    "non_acp_path": "http_404",
                    "wrong_hostname": wrong_hostname.code,
                    "untrusted_ca": rejected.code,
                },
                "nginx_config_verified": {"tmp_paths": True, "no_8080_listener": True},
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
