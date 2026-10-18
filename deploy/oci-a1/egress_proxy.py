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
MAX_PROXY_CLIENTS = 2
MAX_TUNNELS_PER_CLIENT = MAX_TUNNELS // MAX_PROXY_CLIENTS
MAX_DNS_WORKERS = 8
MAX_DNS_WORKERS_PER_CLIENT = MAX_DNS_WORKERS // MAX_PROXY_CLIENTS
DNS_TIMEOUT_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 8.0
HEADER_TIMEOUT_SECONDS = 5.0


class ProxyRequestError(ValueError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


class ProxyCapacity:
    """Give Relay and Hermes independent tunnel/DNS reservations."""

    def __init__(self, allowed_peers: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]):
        if not allowed_peers:
            raise ValueError("proxy has no allowed client addresses")
        if len(allowed_peers) > MAX_PROXY_CLIENTS:
            raise ValueError(f"proxy supports at most {MAX_PROXY_CLIENTS} isolated client addresses")
        self.tunnel_slots = {peer: asyncio.Semaphore(MAX_TUNNELS_PER_CLIENT) for peer in allowed_peers}
        self.dns_slots = {peer: asyncio.Semaphore(MAX_DNS_WORKERS_PER_CLIENT) for peer in allowed_peers}
        self.dns_total = asyncio.Semaphore(MAX_DNS_WORKERS)

    async def acquire_tunnel(self, peer: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        semaphore = self.tunnel_slots.get(peer)
        if semaphore is None or semaphore.locked():
            return False
        await semaphore.acquire()
        return True

    def release_tunnel(self, peer: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        self.tunnel_slots[peer].release()

    async def acquire_dns(self, peer: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        per_client = self.dns_slots.get(peer)
        if per_client is None or self.dns_total.locked() or per_client.locked():
            return False
        # Both semaphores are immediately available; these acquires do not queue
        # or yield between the checks, so one client cannot occupy a partial slot.
        await self.dns_total.acquire()
        try:
            await per_client.acquire()
        except BaseException:
            self.dns_total.release()
            raise
        return True

    def release_dns(self, peer: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        self.dns_slots[peer].release()
        self.dns_total.release()


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
        raise ProxyRequestError("only HTTPS port 443 is permitted", status=403)
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
            raise ProxyRequestError("destination is not public", status=403)
        host = literal.compressed
    return host, port


async def resolve_public_addresses(
    host: str,
    port: int,
    *,
    capacities: ProxyCapacity | None = None,
    peer_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None,
    resolver=None,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    reserved = False
    if capacities is not None:
        if peer_ip is None or not await capacities.acquire_dns(peer_ip):
            raise ProxyRequestError("DNS capacity temporarily unavailable", status=503)
        reserved = True
    resolver = resolver or socket.getaddrinfo
    future = asyncio.get_running_loop().run_in_executor(
        None, resolver, host, port, 0, socket.SOCK_STREAM
    )
    def finish_resolution(done: asyncio.Future) -> None:
        try:
            done.exception()
        except (asyncio.CancelledError, Exception):
            pass
        if reserved:
            capacities.release_dns(peer_ip)

    # wait_for(shield(...)) bounds this request without freeing a slot while
    # the underlying getaddrinfo thread is still stuck in the OS resolver.
    future.add_done_callback(finish_resolution)
    try:
        infos = await asyncio.wait_for(asyncio.shield(future), timeout=DNS_TIMEOUT_SECONDS)
    except TimeoutError:
        raise ProxyRequestError("destination resolution timed out", status=504) from None
    except OSError:
        raise ProxyRequestError("destination resolution failed", status=502) from None
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except (IndexError, TypeError, ValueError):
            raise ProxyRequestError("destination resolution was invalid", status=502) from None
        if not address.is_global:
            raise ProxyRequestError("destination is not public", status=403)
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ProxyRequestError("destination has no public address", status=502)
    return tuple(addresses)


def _response(status: int, message: str) -> bytes:
    reason = {
        400: "Bad Request",
        403: "Forbidden",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }[status]
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
    capacities: ProxyCapacity | None = None,
    resolver=None,
    connect_upstream=None,
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    tunnel_reserved = False
    peer_ip = None
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
        if capacities is not None:
            if not await capacities.acquire_tunnel(peer_ip):
                writer.write(_response(503, "capacity temporarily unavailable"))
                await writer.drain()
                return
            tunnel_reserved = True
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=HEADER_TIMEOUT_SECONDS)
        if len(header) > MAX_HEADER_BYTES:
            writer.write(_response(400, "bad request"))
            await writer.drain()
            return
        host, port = parse_connect_target(header.split(b"\r\n", 1)[0])
        addresses = await resolve_public_addresses(
            host, port, capacities=capacities, peer_ip=peer_ip, resolver=resolver
        )
        connect_upstream = connect_upstream or asyncio.open_connection
        connect_deadline = asyncio.get_running_loop().time() + CONNECT_TIMEOUT_SECONDS
        for address in addresses:
            remaining = connect_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    connect_upstream(
                        str(address), port,
                        family=socket.AF_INET if address.version == 4 else socket.AF_INET6,
                    ),
                    timeout=remaining,
                )
                break
            except (OSError, TimeoutError):
                continue
        if upstream_writer is None:
            writer.write(_response(502, "upstream unavailable"))
            await writer.drain()
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await _tunnel(reader, writer, upstream_reader, upstream_writer)
    except ProxyRequestError as exc:
        message = "destination denied" if exc.status == 403 else (
            "capacity temporarily unavailable" if exc.status == 503 else "upstream unavailable"
            if exc.status in {502, 504} else "bad request"
        )
        writer.write(_response(exc.status, message))
        try:
            await writer.drain()
        except OSError:
            pass
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
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
        if tunnel_reserved:
            capacities.release_tunnel(peer_ip)


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
    peer_values = [value.strip() for value in os.environ["EGRESS_PROXY_ALLOWED_CLIENTS"].split(",") if value.strip()]
    allowed_peers = frozenset(ipaddress.ip_address(value) for value in peer_values)
    if len(allowed_peers) != len(peer_values):
        raise ValueError("proxy client addresses must be unique")
    if not allowed_peers:
        raise ValueError("proxy has no allowed client addresses")
    capacities = ProxyCapacity(allowed_peers)
    proxy_port = int(os.environ.get("EGRESS_PROXY_PORT", "3128"))

    async def bounded_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await handle_connect(reader, writer, allowed_peers=allowed_peers, capacities=capacities)

    proxy = await asyncio.start_server(
        bounded_client,
        host=str(bind_ip), port=proxy_port, limit=MAX_HEADER_BYTES,
    )
    health = await asyncio.start_server(_healthcheck, host="127.0.0.1", port=3129)
    async with proxy, health:
        await asyncio.gather(proxy.serve_forever(), health.serve_forever())


if __name__ == "__main__":
    asyncio.run(serve())

