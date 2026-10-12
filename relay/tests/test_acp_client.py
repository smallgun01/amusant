import asyncio
import json
import socket
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from websockets.asyncio.server import serve

from relay.acp_client import OpenAbAcpExecutor, load_acp_executor, normalize_agent_text
from relay.dispatcher import AsyncDispatcher
from relay.task_store import ClaimedTask, SQLiteTaskStore, TaskAdmissionRequest

ACP_KEY = "acp-test-key-12"
NOW = datetime(2026, 10, 11, 13, 0, tzinfo=timezone.utc)
TASK_ID = "4db5aabc-284a-4a01-8a6d-5cddcc7934f1"


class FakeAcpAgent:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.prompts = []
        self.methods = []

    async def handler(self, ws):
        path = ws.request.path.split("?", 1)[0]
        if path != "/acp":
            await ws.close(code=1008, reason="unknown path")
            return
        auth = ws.request.headers.get("Authorization", "")
        if auth != f"Bearer {ACP_KEY}":
            await ws.close(code=1008, reason="unauthorized")
            return
        async for raw in ws:
            message = json.loads(raw)
            self.methods.append(message.get("method"))
            request_id = message.get("id")
            method = message.get("method")
            params = message.get("params") or {}
            if method == "initialize":
                if self.mode == "hang_init":
                    await asyncio.sleep(1)
                    continue
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []},
                }))
            elif method == "session/new":
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"sessionId": "sess_test"},
                }))
            elif method == "session/prompt":
                self.prompts.append(params)
                if self.mode == "hang":
                    await asyncio.sleep(1)
                    continue
                if self.mode == "overload":
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": "ACP_OVERLOADED"},
                    }))
                    continue
                if self.mode == "drop":
                    await ws.close()
                    return
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "sess_test",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "Option B is the better local value."},
                        },
                    },
                }))
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"stopReason": "end_turn"},
                }))


def claimed_task():
    return ClaimedTask(
        task_id=TASK_ID,
        client_id="authenticated-client-1",
        conversation_id="conversation-1",
        turn_id=1,
        call_id="call-1",
        task="Compare current laptop options",
        task_kind="compare",
        backend="openab",
        attempts=1,
    )


class AcpClientTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, agent):
        server = await serve(agent.handler, "127.0.0.1", 0, max_size=64 * 1024)
        port = server.sockets[0].getsockname()[1]
        return server, f"ws://127.0.0.1:{port}/acp"

    def test_cwd_is_remote_path_and_need_not_exist_locally(self):
        executor = OpenAbAcpExecutor(
            url="ws://127.0.0.1:9/acp",
            auth_key=ACP_KEY,
            cwd="/openab/work",
        )
        self.assertEqual(executor._cwd, "/openab/work")
        with self.assertRaises(ValueError):
            OpenAbAcpExecutor(url="ws://127.0.0.1:9/acp", auth_key=ACP_KEY, cwd="../escape")

    def test_invalid_acp_ports_fail_closed(self):
        for url in (
            "wss://10.0.0.8:0/acp",
            "wss://10.0.0.8:65536/acp",
            "wss://10.0.0.8:not-a-port/acp",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OpenAbAcpExecutor(url=url, auth_key=ACP_KEY, cwd="/openab/work")

    def test_metadata_and_documentation_ips_are_rejected(self):
        for host in ("169.254.169.254", "192.0.2.1", "100.64.1.1"):
            with self.assertRaises(ValueError):
                OpenAbAcpExecutor(
                    url=f"wss://{host}/acp",
                    auth_key=ACP_KEY,
                    cwd="/openab/work",
                )

    def test_infinite_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            OpenAbAcpExecutor(
                url="ws://127.0.0.1:9/acp",
                auth_key=ACP_KEY,
                cwd="/openab/work",
                timeout_seconds=float("inf"),
            )
        with self.assertRaises(ValueError):
            load_acp_executor({
                "AMUSANT_ACP_URL": "ws://127.0.0.1:9/acp",
                "AMUSANT_ACP_AUTH_KEY": ACP_KEY,
                "AMUSANT_ACP_CWD": "/openab/work",
                "AMUSANT_ACP_TIMEOUT_SECONDS": "inf",
            })

    def test_plaintext_non_loopback_requires_explicit_flag(self):
        with self.assertRaises(ValueError):
            OpenAbAcpExecutor(
                url="ws://10.0.0.8/acp",
                auth_key=ACP_KEY,
                cwd="/openab/work",
            )
        executor = OpenAbAcpExecutor(
            url="ws://10.0.0.8/acp",
            auth_key=ACP_KEY,
            cwd="/openab/work",
            allow_plaintext=True,
        )
        self.assertTrue(executor._url.startswith("ws://"))

    async def test_wss_connect_uses_verified_tls_context(self):
        captured = {}

        def fake_connect(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise OSError("offline")

        executor = OpenAbAcpExecutor(
            url="wss://10.0.0.8/acp",
            auth_key=ACP_KEY,
            cwd="/openab/work",
            connect_factory=fake_connect,
        )
        outcome = await executor.execute(claimed_task())
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.code, "acp_connect")
        tls = captured["kwargs"]["ssl"]
        self.assertIsInstance(tls, ssl.SSLContext)
        self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(tls.check_hostname is False and tls.verify_mode == ssl.CERT_NONE)

    def test_normalize_agent_text_is_bounded(self):
        result = normalize_agent_text("First line\nSecond line\n" + ("x" * 5000))
        self.assertEqual(result.card["title"], "First line")
        self.assertLessEqual(len(result.speak), 240)
        self.assertLessEqual(len(result.details_md), 4096)

    def test_load_acp_executor_fail_closed_on_partial_config(self):
        self.assertIsNone(load_acp_executor({}))
        with self.assertRaises(ValueError):
            load_acp_executor({"AMUSANT_ACP_URL": "ws://127.0.0.1:9/acp"})

    def test_private_dns_does_not_resolve_during_relay_construction(self):
        environ = {
            "AMUSANT_ACP_URL": "wss://jellii-acp.internal:8443/acp",
            "AMUSANT_ACP_AUTH_KEY": ACP_KEY,
            "AMUSANT_ACP_CWD": "/openab/work",
        }
        with patch("socket.getaddrinfo", side_effect=AssertionError("unexpected startup DNS lookup")):
            executor = load_acp_executor(environ)
        self.assertIsNotNone(executor)

    async def test_dns_resolution_is_pinned_and_original_hostname_is_verified(self):
        captured = {}

        def fake_connect(url, **kwargs):
            captured.update(kwargs)
            raise OSError("offline connect capture")

        executor = OpenAbAcpExecutor(
            url="wss://jellii-acp.internal:8443/acp",
            auth_key=ACP_KEY,
            cwd="/openab/work",
            connect_factory=fake_connect,
        )
        loop = asyncio.get_running_loop()

        async def private_lookup(host, port, **kwargs):
            self.assertEqual((host, port), ("jellii-acp.internal", 8443))
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.23.45.67", port))]

        with patch.object(loop, "getaddrinfo", new=private_lookup):
            outcome = await executor.execute(claimed_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        self.assertEqual(captured["host"], "10.23.45.67")
        self.assertEqual(captured["port"], 8443)
        self.assertEqual(captured["server_hostname"], "jellii-acp.internal")
        self.assertIsNone(captured["proxy"])

    async def test_expected_private_ip_mismatch_fails_before_dial(self):
        dial_called = False

        def fake_connect(url, **kwargs):
            nonlocal dial_called
            dial_called = True
            raise OSError("must not dial mismatched DNS target")

        executor = OpenAbAcpExecutor(
            url="wss://jellii-acp.internal:8443/acp",
            auth_key=ACP_KEY,
            cwd="/openab/work",
            expected_private_ip="10.23.45.67",
            connect_factory=fake_connect,
        )
        loop = asyncio.get_running_loop()

        async def changed_lookup(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.23.45.68", port))]

        with patch.object(loop, "getaddrinfo", new=changed_lookup):
            outcome = await executor.execute(claimed_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        self.assertFalse(dial_called)

    def test_expected_private_ip_rejects_non_rfc1918(self):
        with self.assertRaises(ValueError):
            OpenAbAcpExecutor(
                url="wss://jellii-acp.internal:8443/acp",
                auth_key=ACP_KEY,
                cwd="/openab/work",
                expected_private_ip="8.8.8.8",
            )

    async def test_dual_stack_localhost_answers_allow_ipv6_loopback(self):
        agent = FakeAcpAgent()
        server, ipv4_url = await self._serve(agent)
        port = server.sockets[0].getsockname()[1]
        url = ipv4_url.replace("127.0.0.1", "localhost")
        executor = OpenAbAcpExecutor(
            url=url,
            auth_key=ACP_KEY,
            cwd="/openab/work",
            timeout_seconds=2,
        )
        loop = asyncio.get_running_loop()
        system_getaddrinfo = loop.getaddrinfo
        lookups = []

        async def dual_stack_localhost(host, lookup_port, **kwargs):
            if host == "localhost":
                lookups.append((host, lookup_port))
                return [
                    (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", lookup_port, 0, 0)),
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", lookup_port)),
                ]
            return await system_getaddrinfo(host, lookup_port, **kwargs)

        try:
            with patch.object(loop, "getaddrinfo", new=dual_stack_localhost):
                outcome = await executor.execute(claimed_task())
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual((outcome.status, outcome.code), ("completed", "ok"))
        self.assertEqual(lookups, [("localhost", port)])
        self.assertEqual(agent.methods[:3], ["initialize", "session/new", "session/prompt"])
        self.assertEqual(len(agent.prompts), 1)

    async def test_dns_timeout_fails_before_connect(self):
        dial_called = False

        def fake_connect(url, **kwargs):
            nonlocal dial_called
            dial_called = True
            raise OSError("must not dial")

        executor = OpenAbAcpExecutor(
            url="wss://jellii-acp.internal:8443/acp",
            auth_key=ACP_KEY,
            cwd="/openab/work",
            timeout_seconds=0.05,
            connect_factory=fake_connect,
        )
        loop = asyncio.get_running_loop()

        async def slow_lookup(host, port, **kwargs):
            await asyncio.sleep(0.2)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.23.45.67", port))]

        with patch.object(loop, "getaddrinfo", new=slow_lookup):
            outcome = await executor.execute(claimed_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        self.assertFalse(dial_called)

    async def test_mixed_private_and_public_dns_answers_are_rejected_before_dial(self):
        dial_called = False

        def fake_connect(url, **kwargs):
            nonlocal dial_called
            dial_called = True
            raise OSError("must not dial")

        executor = OpenAbAcpExecutor(
            url="wss://jellii-acp.internal:8443/acp",
            auth_key=ACP_KEY,
            cwd="/openab/work",
            connect_factory=fake_connect,
        )
        loop = asyncio.get_running_loop()

        async def mixed_getaddrinfo(host, port, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.23.45.67", port)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
            ]

        with patch.object(loop, "getaddrinfo", new=mixed_getaddrinfo):
            outcome = await executor.execute(claimed_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        self.assertFalse(dial_called)

    def test_private_ca_pem_is_optional_bounded_and_wss_only(self):
        base = {
            "AMUSANT_ACP_URL": "wss://10.0.0.8/acp",
            "AMUSANT_ACP_AUTH_KEY": ACP_KEY,
            "AMUSANT_ACP_CWD": "/openab/work",
        }
        ca_pem = "-----BEGIN CERTIFICATE-----\nunit-test\n-----END CERTIFICATE-----\n"
        executor = load_acp_executor({**base, "AMUSANT_ACP_CA_PEM": ca_pem})
        self.assertEqual(executor._ca_pem, ca_pem.strip())
        with self.assertRaises(ValueError):
            load_acp_executor({**base, "AMUSANT_ACP_CA_PEM": "not a PEM certificate"})
        with self.assertRaises(ValueError):
            load_acp_executor({**base, "AMUSANT_ACP_CA_PEM": ca_pem * 4096})
        with self.assertRaises(ValueError):
            load_acp_executor({**base, "AMUSANT_ACP_URL": "ws://127.0.0.1:8080/acp", "AMUSANT_ACP_CA_PEM": ca_pem})

    async def test_happy_path_text_prompt_without_audio(self):
        agent = FakeAcpAgent()
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = OpenAbAcpExecutor(url=url, auth_key=ACP_KEY, cwd=temp_dir, timeout_seconds=2)
            try:
                outcome = await executor.execute(claimed_task())
            finally:
                server.close()
                await server.wait_closed()
        self.assertEqual(outcome.status, "completed")
        self.assertIn("Option B", outcome.result.speak)
        prompt = agent.prompts[0]["prompt"]
        self.assertEqual(prompt[0]["type"], "text")
        self.assertNotIn("audio", json.dumps(prompt))
        self.assertEqual(agent.methods[:3], ["initialize", "session/new", "session/prompt"])

    async def test_unauthorized_is_failed_not_completed(self):
        agent = FakeAcpAgent()
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = OpenAbAcpExecutor(url=url, auth_key="wrong-token-12", cwd=temp_dir, timeout_seconds=2)
            try:
                outcome = await executor.execute(claimed_task())
            finally:
                server.close()
                await server.wait_closed()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.code, "acp_connect")

    async def test_overall_deadline_covers_initialize(self):
        agent = FakeAcpAgent(mode="hang_init")
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = OpenAbAcpExecutor(url=url, auth_key=ACP_KEY, cwd="/openab/work", timeout_seconds=0.05)
            try:
                started = asyncio.get_running_loop().time()
                outcome = await executor.execute(claimed_task())
                elapsed = asyncio.get_running_loop().time() - started
            finally:
                server.close()
                await server.wait_closed()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.code, "acp_connect")
        self.assertLess(elapsed, 0.5)

    async def test_prompt_timeout_is_uncertain(self):
        agent = FakeAcpAgent(mode="hang")
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = OpenAbAcpExecutor(url=url, auth_key=ACP_KEY, cwd=temp_dir, timeout_seconds=0.05)
            try:
                with self.assertRaises(Exception):
                    await executor.execute(claimed_task())
            finally:
                server.close()
                await server.wait_closed()

    async def test_disconnect_after_prompt_is_uncertain(self):
        agent = FakeAcpAgent(mode="drop")
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = OpenAbAcpExecutor(url=url, auth_key=ACP_KEY, cwd=temp_dir, timeout_seconds=2)
            try:
                with self.assertRaises(Exception):
                    await executor.execute(claimed_task())
            finally:
                server.close()
                await server.wait_closed()

    async def test_overload_before_completion_is_failed(self):
        agent = FakeAcpAgent(mode="overload")
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            executor = OpenAbAcpExecutor(url=url, auth_key=ACP_KEY, cwd=temp_dir, timeout_seconds=2)
            try:
                outcome = await executor.execute(claimed_task())
            finally:
                server.close()
                await server.wait_closed()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.code, "acp_overloaded")

    async def test_dispatcher_persists_bounded_result_from_fake_acp(self):
        agent = FakeAcpAgent()
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            store = SQLiteTaskStore(database_path=db_path, max_pending_tasks=8)
            store.admit(TaskAdmissionRequest(
                task_id=TASK_ID,
                client_id="authenticated-client-1",
                conversation_id="conversation-1",
                turn_id=1,
                call_id="call-1",
                task="Compare current laptop options",
                task_kind="compare",
                backend="openab",
                created_at=NOW,
                expires_at=NOW + timedelta(seconds=600),
            ))
            executor = OpenAbAcpExecutor(url=url, auth_key=ACP_KEY, cwd=temp_dir, timeout_seconds=2)
            try:
                handled = await AsyncDispatcher(store, executor, now=lambda: NOW).dispatch_available()
            finally:
                server.close()
                await server.wait_closed()
            self.assertEqual(handled, 1)
            self.assertEqual(store.get_by_task_id(TASK_ID)["status"], "completed")
            result = store.get_result(TASK_ID)
            self.assertIn("Option B", result["speak"])
            self.assertEqual(result["card"]["title"], "Option B is the better local value.")


if __name__ == "__main__":
    unittest.main()
