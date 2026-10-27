"""Private embedded ASGI listener for the existing Grok Callback app."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
import ipaddress
import socket
from urllib.parse import urlsplit

from .grok_callback import _normalize_host
from .grok_callback_executor import _https_url

_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


def callback_listener_config(environ, *, device_host: str) -> tuple[str, int]:
    """Require a distinct RFC1918 bind and one canonical public Host mapping."""
    host = environ.get("RELAY_CALLBACK_BIND_IP", "")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("Callback requires an explicit private IPv4 bind") from None
    if not isinstance(address, ipaddress.IPv4Address) or not any(address in net for net in _PRIVATE_NETWORKS) or host == device_host:
        raise ValueError("Callback requires a distinct private IPv4 bind")
    raw_port = environ.get("RELAY_CALLBACK_LISTEN_PORT", "8645")
    if not isinstance(raw_port, str) or not raw_port.isascii() or not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
        raise ValueError("Callback listener port must be 1..65535")
    callback_url = environ.get("AMUSANT_GROK_CALLBACK_URL", "")
    _https_url(callback_url, "AMUSANT_GROK_CALLBACK_URL", path="/callback/results")
    parts = urlsplit(callback_url)
    expected_host = _normalize_host(parts.hostname or "")
    hosts = environ.get("AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS", "")
    if not isinstance(hosts, str) or _normalize_host(hosts) != expected_host:
        raise ValueError("Callback trusted Host must match its canonical HTTPS URL")
    raw_service_port = environ.get("AMUSANT_GROK_CALLBACK_SERVICE_PORT", "443")
    external_port = parts.port if parts.port is not None else 443
    if (not isinstance(raw_service_port, str) or not raw_service_port.isascii()
            or not raw_service_port.isdigit() or external_port != 443 or int(raw_service_port) != external_port):
        raise ValueError("Callback service port must match its canonical HTTPS URL")
    return str(address), int(raw_port)


@asynccontextmanager
async def serve_callback(app, host: str, port: int):
    """Run one Uvicorn server within the Relay event loop, without signal ownership."""
    import uvicorn

    class EmbeddedServer(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            yield

    config = uvicorn.Config(
        app, host=host, port=port, http="h11", ws="none", lifespan="on",
        access_log=False, log_config=None, proxy_headers=False, server_header=False,
        limit_concurrency=32, backlog=32, timeout_keep_alive=5,
        timeout_graceful_shutdown=5, h11_max_incomplete_event_size=16384,
    )
    server = EmbeddedServer(config)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    task = None
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(32)
        listener.setblocking(False)
        task = asyncio.create_task(server.serve(sockets=[listener]))

        async def wait_started():
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Callback listener stopped during startup")
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_started(), timeout=5)
        yield task
    finally:
        server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=7)
            except asyncio.TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        listener.close()
