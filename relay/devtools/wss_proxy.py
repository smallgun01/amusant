"""Dev-only loopback WSS proxy for local ACP transport tests.

This is a contract/feasibility harness, not a production-ready proxy. It binds
only to loopback, accepts only /acp, forwards only Authorization to a loopback
ws:// fake ACP upstream, and never logs headers or message bodies.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import ssl
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

MAX_MESSAGE_SIZE = 64 * 1024


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _require_local_upstream(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "ws"
        or parsed.path != "/acp"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is None
        or not 1 <= parsed.port <= 65535
        or not parsed.hostname
    ):
        raise ValueError("dev proxy upstream must be loopback ws://IP:port/acp")
    try:
        upstream_address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ValueError("dev proxy upstream must use a loopback IP literal") from error
    if not upstream_address.is_loopback:
        raise ValueError("dev proxy upstream must use a loopback IP literal")
    return url


class LoopbackWssProxy:
    """Terminate verified WSS locally and forward ACP frames to loopback ws."""

    def __init__(self, *, upstream_url: str, cert_file: str, key_file: str) -> None:
        self.upstream_url = _require_local_upstream(upstream_url)
        self.cert_file = cert_file
        self.key_file = key_file

    def _tls_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.cert_file, self.key_file)
        return context

    async def start(self, *, host: str = "127.0.0.1", port: int = 0) -> Server:
        if not _is_loopback(host):
            raise ValueError("dev WSS proxy must bind to loopback only")
        return await serve(
            self._handler,
            host,
            port,
            ssl=self._tls_context(),
            max_size=MAX_MESSAGE_SIZE,
            ping_interval=20,
            ping_timeout=20,
        )

    async def _handler(self, client: ServerConnection) -> None:
        request = client.request
        if request is None or request.path != "/acp":
            await client.close(code=1008, reason="only /acp is allowed")
            return

        auth = request.headers.get("Authorization")
        headers = {"Authorization": auth} if auth is not None else {}
        try:
            async with connect(
                self.upstream_url,
                additional_headers=headers,
                open_timeout=5,
                max_size=MAX_MESSAGE_SIZE,
                ping_interval=20,
                ping_timeout=20,
            ) as upstream:
                await self._bridge(client, upstream)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not log exception text: proxy errors may contain request details.
            await client.close(code=1011, reason="upstream unavailable")

    @staticmethod
    async def _copy(
        source: ClientConnection | ServerConnection,
        destination: ClientConnection | ServerConnection,
    ) -> None:
        async for message in source:
            await destination.send(message)

    @classmethod
    async def _bridge(cls, client: ServerConnection, upstream: ClientConnection) -> None:
        tasks = {
            asyncio.create_task(cls._copy(client, upstream)),
            asyncio.create_task(cls._copy(upstream, client)),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except ConnectionClosed:
                pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default="ws://127.0.0.1:8080/acp")
    parser.add_argument("--cert", required=True, help="dev/test certificate PEM")
    parser.add_argument("--key", required=True, help="dev/test private key PEM")
    parser.add_argument("--host", default="127.0.0.1", help="loopback bind only")
    parser.add_argument("--port", type=int, default=8443)
    return parser.parse_args()


async def _run() -> None:
    args = _parse_args()
    proxy = LoopbackWssProxy(
        upstream_url=args.upstream,
        cert_file=args.cert,
        key_file=args.key,
    )
    server = await proxy.start(host=args.host, port=args.port)
    bound = server.sockets[0].getsockname()
    print(f"dev WSS proxy listening on {bound[0]}:{bound[1]}/acp")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_run())
