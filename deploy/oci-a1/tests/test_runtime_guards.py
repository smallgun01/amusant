from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "oci-a1"


def write_temp_file(test_case: unittest.TestCase, contents: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=Path(__file__).parent, prefix=".offline-secret-", delete=False
    )
    path = Path(handle.name)
    test_case.addCleanup(path.unlink, missing_ok=True)
    with handle:
        handle.write(contents)
    if os.name == "posix":
        path.chmod(0o400)
    return path


def load_module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class SecretLoaderTests(unittest.TestCase):
    def test_relay_reads_all_required_files_and_removes_file_paths(self):
        relay_entrypoint = load_module("oci_a1_relay_entrypoint", PACKAGE / "relay_entrypoint.py")
        values = {
            "GEMINI_API_KEY_FILE": "gemini",
            "AMUSANT_DEVICE_TOKEN_FILE": "token",
            "AMUSANT_DEVICE_PSKS_FILE": "psks",
            "AMUSANT_HERMES_API_KEY_FILE": "hermes",
        }
        expected = {
            "GEMINI_API_KEY": "offline-gemini-secret",
            "AMUSANT_DEVICE_TOKEN": "offline-device-secret",
            "AMUSANT_DEVICE_PSKS": "stick-a:" + "ab" * 32,
            "AMUSANT_HERMES_API_KEY": "offline-hermes-secret",
        }
        for name in values:
            target = name.removesuffix("_FILE")
            path = write_temp_file(self, expected[target] + "\n")
            values[name] = str(path)
        relay_entrypoint.load_secrets(values)
        for target, secret in expected.items():
            self.assertEqual(values[target], secret)
        self.assertFalse(any(name.endswith("_FILE") for name in values))

    def test_relay_rejects_direct_secrets_without_echoing_them(self):
        relay_entrypoint = load_module("oci_a1_relay_entrypoint_direct", PACKAGE / "relay_entrypoint.py")
        with self.assertRaises(relay_entrypoint.SecretConfigurationError) as raised:
            relay_entrypoint.load_secrets({"GEMINI_API_KEY": "never-print-this"})
        self.assertNotIn("never-print-this", str(raised.exception))

    def test_hermes_secret_wrapper_injects_key_only_into_process_environment(self):
        hermes_entrypoint = load_module("oci_a1_hermes_entrypoint", PACKAGE / "hermes_entrypoint.py")
        path = write_temp_file(self, "offline-hermes-key\n")
        with (
            patch.dict("os.environ", {"API_SERVER_KEY_FILE": str(path)}, clear=True),
            patch.object(hermes_entrypoint.os, "execvp") as execvp,
            patch.object(hermes_entrypoint.os, "umask"),
        ):
            self.assertEqual(hermes_entrypoint.main(), 0)
            execvp.assert_called_once_with("hermes", ["hermes", "gateway", "run"])
            self.assertEqual(hermes_entrypoint.os.environ["API_SERVER_KEY"], "offline-hermes-key")
            self.assertNotIn("API_SERVER_KEY_FILE", hermes_entrypoint.os.environ)


class EgressProxyTests(unittest.TestCase):
    def setUp(self):
        self.proxy = load_module("oci_a1_egress_proxy", PACKAGE / "egress_proxy.py")

    def test_accepts_https_connect_and_rejects_non_https_targets(self):
        self.assertEqual(
            self.proxy.parse_connect_target(b"CONNECT api.example.com:443 HTTP/1.1"),
            ("api.example.com", 443),
        )
        for request in (
            b"GET https://api.example.com/ HTTP/1.1",
            b"CONNECT 169.254.169.254:443 HTTP/1.1",
            b"CONNECT 10.0.0.8:443 HTTP/1.1",
            b"CONNECT api.example.com:8642 HTTP/1.1",
            b"CONNECT user@api.example.com:443 HTTP/1.1",
        ):
            with self.subTest(request=request), self.assertRaises(self.proxy.ProxyRequestError):
                self.proxy.parse_connect_target(request)

    def test_dns_results_must_all_be_public_and_use_supported_socket_family(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443))]
        addresses = asyncio.run(self.proxy.resolve_public_addresses(
            "api.example.com", 443, resolver=lambda *_args: public
        ))
        self.assertEqual(addresses, (self.proxy.ipaddress.ip_address("142.250.72.14"),))

        mixed = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        ]
        with self.assertRaisesRegex(self.proxy.ProxyRequestError, "not public"):
            asyncio.run(self.proxy.resolve_public_addresses(
                "rebound.example.com", 443, resolver=lambda *_args: mixed
            ))

    def test_proxy_capacity_requires_two_or_fewer_isolated_client_addresses(self):
        with self.assertRaisesRegex(ValueError, "at most 2"):
            self.proxy.ProxyCapacity(frozenset(map(
                self.proxy.ipaddress.ip_address, ("10.0.0.1", "10.0.0.2", "10.0.0.3")
            )))


class EgressProxyAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.proxy = load_module("oci_a1_egress_proxy_async", PACKAGE / "egress_proxy.py")

    async def test_hermes_capacity_cannot_consume_relays_reserved_tunnels(self):
        hermes = self.proxy.ipaddress.ip_address("10.0.0.2")
        relay = self.proxy.ipaddress.ip_address("10.0.0.3")
        capacities = self.proxy.ProxyCapacity(frozenset({hermes, relay}))
        for _ in range(self.proxy.MAX_TUNNELS_PER_CLIENT):
            self.assertTrue(await capacities.acquire_tunnel(hermes))
        self.assertFalse(await capacities.acquire_tunnel(hermes))
        self.assertTrue(await capacities.acquire_tunnel(relay))
        self.assertEqual(
            self.proxy.MAX_TUNNELS_PER_CLIENT * 2,
            self.proxy.MAX_TUNNELS,
        )

    async def test_dns_timeout_keeps_slot_until_resolver_finishes_then_releases_it(self):
        peer = self.proxy.ipaddress.ip_address("10.0.0.3")
        capacities = self.proxy.ProxyCapacity(frozenset({peer}))
        resolver_started = threading.Event()
        resolver_release = threading.Event()
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443))]

        def slow_resolver(*_args):
            resolver_started.set()
            resolver_release.wait(3)
            return public

        with patch.object(self.proxy, "DNS_TIMEOUT_SECONDS", 0.05):
            lookup = asyncio.create_task(self.proxy.resolve_public_addresses(
                "slow.example", 443, capacities=capacities, peer_ip=peer, resolver=slow_resolver
            ))
            deadline = asyncio.get_running_loop().time() + 2
            while not resolver_started.is_set() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            self.assertTrue(resolver_started.is_set())
            with self.assertRaises(self.proxy.ProxyRequestError) as raised:
                await lookup
            self.assertEqual(raised.exception.status, 504)
            claimed = []
            for _ in range(self.proxy.MAX_DNS_WORKERS_PER_CLIENT - 1):
                self.assertTrue(await capacities.acquire_dns(peer))
                claimed.append(peer)
            self.assertFalse(await capacities.acquire_dns(peer), "the timed-out lookup must keep one DNS slot")
            for claimed_peer in claimed:
                capacities.release_dns(claimed_peer)

            resolver_release.set()
            deadline = asyncio.get_running_loop().time() + 2
            acquired = 0
            while acquired < self.proxy.MAX_DNS_WORKERS_PER_CLIENT and asyncio.get_running_loop().time() < deadline:
                if await capacities.acquire_dns(peer):
                    acquired += 1
                else:
                    await asyncio.sleep(0.01)
            self.assertEqual(
                acquired,
                self.proxy.MAX_DNS_WORKERS_PER_CLIENT,
                "DNS capacity should return after the resolver thread exits",
            )
            for _ in range(acquired):
                capacities.release_dns(peer)

    async def test_dns_rejects_metadata_and_mixed_public_private_answers(self):
        peer = self.proxy.ipaddress.ip_address("10.0.0.3")
        capacities = self.proxy.ProxyCapacity(frozenset({peer}))
        answers = (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
            [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ],
        )
        for result in answers:
            with self.subTest(result=result), self.assertRaises(self.proxy.ProxyRequestError) as raised:
                await self.proxy.resolve_public_addresses(
                    "metadata.example", 443,
                    capacities=capacities,
                    peer_ip=peer,
                    resolver=lambda *_args, result=result: result,
                )
            self.assertEqual(raised.exception.status, 403)

    async def test_bogus_dns_name_fails_closed(self):
        peer = self.proxy.ipaddress.ip_address("10.0.0.3")
        capacities = self.proxy.ProxyCapacity(frozenset({peer}))

        def bogus_resolver(*_args):
            raise socket.gaierror("name does not resolve")

        with self.assertRaises(self.proxy.ProxyRequestError) as raised:
            await self.proxy.resolve_public_addresses(
                "does-not-exist.invalid", 443,
                capacities=capacities,
                peer_ip=peer,
                resolver=bogus_resolver,
            )
        self.assertEqual(raised.exception.status, 502)

    async def test_synthetic_wss_connect_forwards_upgrade_and_data_through_loopback(self):
        peer = self.proxy.ipaddress.ip_address("127.0.0.1")
        allowed = frozenset({peer})
        capacities = self.proxy.ProxyCapacity(allowed)
        origin_requests = []
        origin_port = None

        async def origin(reader, writer):
            request = await reader.readuntil(b"\r\n\r\n")
            origin_requests.append(request)
            writer.write(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Connection: Upgrade\r\nUpgrade: websocket\r\n\r\n"
            )
            await writer.drain()
            payload = await reader.readexactly(4)
            if payload == b"PING":
                writer.write(b"PONG")
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        origin_server = await asyncio.start_server(origin, "127.0.0.1", 0)
        origin_port = origin_server.sockets[0].getsockname()[1]
        public_answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443))]

        async def local_connector(_address, _port, *, family):
            self.assertEqual(family, socket.AF_INET)
            return await asyncio.open_connection("127.0.0.1", origin_port)

        async def proxy_handler(reader, writer):
            await self.proxy.handle_connect(
                reader,
                writer,
                allowed_peers=allowed,
                capacities=capacities,
                resolver=lambda *_args: public_answer,
                connect_upstream=local_connector,
            )

        proxy_server = await asyncio.start_server(proxy_handler, "127.0.0.1", 0, limit=self.proxy.MAX_HEADER_BYTES)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(b"CONNECT api.example.test:443 HTTP/1.1\r\nHost: api.example.test:443\r\n\r\n")
            await writer.drain()
            self.assertIn(b"200 Connection Established", await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"GET /live HTTP/1.1\r\nHost: api.example.test\r\n"
                b"Connection: Upgrade\r\nUpgrade: websocket\r\n"
                b"Sec-WebSocket-Key: c3ludGhldGlj\r\nSec-WebSocket-Version: 13\r\n\r\n"
            )
            await writer.drain()
            self.assertIn(b"101 Switching Protocols", await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"PING")
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(4), timeout=2), b"PONG")
            self.assertEqual(len(origin_requests), 1)
            self.assertIn(b"GET /live HTTP/1.1", origin_requests[0])
            writer.close()
            await writer.wait_closed()
        finally:
            proxy_server.close()
            origin_server.close()
            await proxy_server.wait_closed()
            await origin_server.wait_closed()

    async def test_connect_timeout_returns_502_and_releases_the_clients_tunnel_slot(self):
        peer = self.proxy.ipaddress.ip_address("127.0.0.1")
        allowed = frozenset({peer})
        capacities = self.proxy.ProxyCapacity(allowed)
        public_answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443))]
        public_answers = public_answer + [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))
        ]
        connect_calls = []

        async def timeout_connector(*_args, **_kwargs):
            connect_calls.append(1)
            await asyncio.sleep(1)

        async def proxy_handler(reader, writer):
            await self.proxy.handle_connect(
                reader, writer,
                allowed_peers=allowed,
                capacities=capacities,
                resolver=lambda *_args: public_answers,
                connect_upstream=timeout_connector,
            )

        with patch.object(self.proxy, "CONNECT_TIMEOUT_SECONDS", 0.05):
            server = await asyncio.start_server(proxy_handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                started = asyncio.get_running_loop().time()
                writer.write(b"CONNECT api.example.test:443 HTTP/1.1\r\n\r\n")
                await writer.drain()
                self.assertIn(b"502 Bad Gateway", await reader.readuntil(b"\r\n\r\n"))
                self.assertLess(asyncio.get_running_loop().time() - started, 0.5)
                self.assertEqual(connect_calls, [1], "one deadline bounds all resolved addresses")
                writer.close()
                await writer.wait_closed()
                self.assertTrue(await capacities.acquire_tunnel(peer), "failed CONNECT must release its reserved slot")
                capacities.release_tunnel(peer)
            finally:
                server.close()
                await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
