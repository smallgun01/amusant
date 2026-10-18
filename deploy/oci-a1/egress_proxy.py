"""Small CONNECT-only proxy with public-IP allowlisting for the Hermes container."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket

MAX_HEADER_BYTES = 8192
CONNECT_PORT = 443
TUNNEL_BUFFER_BYTES = 64 * 1024
MAX_TUNNELS = 32


class ProxyRequestError(ValueError):
    pass


def parse_connect_target(request_line: bytes) -> tuple[str, int]:
    try:
        method, authority, version = request_line.decode("ascii").split(" ")
    except (UnicodeDecodeError, ValueError):
        raise ProxyRequestError("invalid CONNECT request") from None
    if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ProxyRequestError("only HTTPS CONNECT is supported")
    if authority.startswith("["):
        host, separator, tail = authority[1:].partition("]:")
        if not separator:
            raise ProxyRequestError("invalid CONNECT authority")
    else:
        host, separator, tail = authority.rpartition(":")
        if not separator or ":" in host:
            raise ProxyRequestError("invalid CONNECT authority")
    if not host or any(char in host for char in "/\\?#@%\r\n\t "):
        raise ProxyRequestError("invalid CONNECT host")
    try:
        port = int(tail)
    except ValueError:
        raise ProxyRequestError("invalid CONNECT port") from None
    if port != CONNECT_PORT:
        raise ProxyRequestError("only HTTPS port 443 is permitted")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            raise ProxyRequestError("invalid CONNECT host") from None
        if not host or len(host) > 253:
            raise ProxyRequestError("invalid CONNECT host")
    else:
        if not literal.is_global:
            raise ProxyRequestError("destination is not public")
        host = literal.compressed
    return host, port


async def resolve_public_addresses(host: str, port: int) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM
        )
    except OSError:
        raise ProxyRequestError("destination resolution failed") from None
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            raise ProxyRequestError("destination resolution was invalid") from None
        if not address.is_global:
            raise ProxyRequestError("destination is not public")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ProxyRequestError("destination has no public address")
    return tuple(addresses)


def _response(status: int, message: str) -> bytes:
    reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}[status]
    payload = f"{message}\n".encode("ascii")
    return (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Connection: close\r\n"
        "Content-Type: text/plain\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode("ascii") + payload


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        chunk = await reader.read(TUNNEL_BUFFER_BYTES)
        if not chunk:
            return
        writer.write(chunk)
        await writer.drain()


async def _tunnel(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    tasks = {
        asyncio.create_task(_pipe(client_reader, upstream_writer)),
        asyncio.create_task(_pipe(upstream_reader, client_writer)),
    }
    _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def handle_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    allowed_peers: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        peer = writer.get_extra_info("peername")
        try:
            peer_ip = ipaddress.ip_address(peer[0])
        except (TypeError, ValueError, IndexError):
            return
        if peer_ip not in allowed_peers:
            writer.write(_response(403, "forbidden"))
            await writer.drain()
            return
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        if len(header) > MAX_HEADER_BYTES:
            writer.write(_response(400, "bad request"))
            await writer.drain()
            return
        host, port = parse_connect_target(header.split(b"\r\n", 1)[0])
        addresses = await resolve_public_addresses(host, port)
        for address in addresses:
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        str(address), port,
                        family=socket.AF_INET if address.version == 4 else socket.AF_INET6,
                    ),
                    timeout=8,
                )
                break
            except (OSError, asyncio.TimeoutError):
                continue
        if upstream_writer is None:
            writer.write(_response(502, "upstream unavailable"))
            await writer.drain()
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await _tunnel(reader, writer, upstream_reader, upstream_writer)
    except ProxyRequestError as exc:
        status = 403 if "not public" in str(exc) or "port 443" in str(exc) else 400
        writer.write(_response(status, "destination denied" if status == 403 else "bad request"))
        try:
            await writer.drain()
        except OSError:
            pass
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        writer.write(_response(400, "bad request"))
        try:
            await writer.drain()
        except OSError:
            pass
    except (OSError, ConnectionError):
        return
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except OSError:
                pass
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def _healthcheck(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        if request.split(b"\r\n", 1)[0] == b"GET /healthz HTTP/1.1":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def serve() -> None:
    bind_ip = ipaddress.ip_address(os.environ["EGRESS_PROXY_BIND_IP"])
    allowed_peers = frozenset(
        ipaddress.ip_address(value.strip())
        for value in os.environ["EGRESS_PROXY_ALLOWED_CLIENTS"].split(",")
        if value.strip()
    )
    if not allowed_peers:
        raise ValueError("proxy has no allowed client addresses")
    proxy_port = int(os.environ.get("EGRESS_PROXY_PORT", "3128"))
    semaphore = asyncio.Semaphore(MAX_TUNNELS)

    async def bounded_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with semaphore:
            await handle_connect(reader, writer, allowed_peers=allowed_peers)

    proxy = await asyncio.start_server(
        bounded_client,
        host=str(bind_ip), port=proxy_port, limit=MAX_HEADER_BYTES,
    )
    health = await asyncio.start_server(_healthcheck, host="127.0.0.1", port=3129)
    async with proxy, health:
        await asyncio.gather(proxy.serve_forever(), health.serve_forever())


if __name__ == "__main__":
    asyncio.run(serve())
