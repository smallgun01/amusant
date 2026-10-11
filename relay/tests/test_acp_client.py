import asyncio
import json
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

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
