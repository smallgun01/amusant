import asyncio
import io
import json
import logging
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from relay.acp_client import AcpUncertainError, OpenAbAcpExecutor, load_acp_executor
from relay.devtools.wss_proxy import LoopbackWssProxy
from relay.task_store import ClaimedTask

TOKEN = "local-wss-test-bearer-12"


def _task() -> ClaimedTask:
    return ClaimedTask(
        task_id="4db5aabc-284a-4a01-8a6d-5cddcc7934f1",
        client_id="test-client",
        conversation_id="test-conversation",
        turn_id=1,
        call_id="test-call",
        task="local WSS contract smoke",
        task_kind="test",
        backend="openab",
        attempts=1,
    )


class FakeAcpServer:
    def __init__(self, *, expected_token: str = TOKEN, mode: str = "ok") -> None:
        self.expected_token = expected_token
        self.mode = mode
        self.methods: list[str] = []
        self.prompt_count = 0

    async def handler(self, websocket) -> None:
        request = websocket.request
        if request.path != "/acp":
            await websocket.close(code=1008, reason="wrong path")
            return
        if request.headers.get("Authorization") != f"Bearer {self.expected_token}":
            await websocket.close(code=1008, reason="unauthorized")
            return
        async for raw in websocket:
            message = json.loads(raw)
            method = message.get("method")
            self.methods.append(method)
            request_id = message.get("id")
            if method == "initialize":
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []},
                }))
            elif method == "session/new":
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"sessionId": "local-wss-session"},
                }))
            elif method == "session/prompt":
                self.prompt_count += 1
                if self.mode == "hang":
                    await asyncio.sleep(2.0)
                if self.mode == "drop":
                    await websocket.close()
                    return
                try:
                    await websocket.send(json.dumps({
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "local-wss-session",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "Local verified WSS result."},
                            },
                        },
                    }))
                    await websocket.send(json.dumps({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"stopReason": "end_turn"},
                    }))
                except ConnectionClosed:
                    return


class WssProxyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("openssl") is None:
            raise unittest.SkipTest("openssl is required to generate ephemeral test certificates")
        cls._temp_dir = tempfile.TemporaryDirectory(prefix="amusant-wss-test-")
        root = Path(cls._temp_dir.name)
        cls.good_ca, good_ca_key = cls._make_ca(root, "good-test-ca")
        cls.other_ca, _ = cls._make_ca(root, "untrusted-test-ca")
        cls.good_cert, cls.good_key = cls._make_server_cert(
            root, "good-server", "IP:127.0.0.1", cls.good_ca, good_ca_key
        )
        cls.wrong_host_cert, cls.wrong_host_key = cls._make_server_cert(
            root, "wrong-host", "IP:127.0.0.2", cls.good_ca, good_ca_key
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp_dir.cleanup()

    @staticmethod
    def _make_ca(root: Path, name: str) -> tuple[str, str]:
        cert = root / f"{name}.crt"
        key = root / f"{name}.key"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
                "-days", "1", "-nodes", "-keyout", str(key), "-out", str(cert),
                "-subj", f"/CN={name}", "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return str(cert), str(key)

    @staticmethod
    def _make_server_cert(
        root: Path, name: str, san: str, ca_cert: str, ca_key: str
    ) -> tuple[str, str]:
        cert = root / f"{name}.crt"
        key = root / f"{name}.key"
        csr = root / f"{name}.csr"
        extensions = root / f"{name}.ext"
        extensions.write_text(
            "\n".join([
                "basicConstraints=critical,CA:FALSE",
                "keyUsage=critical,digitalSignature,keyEncipherment",
                "extendedKeyUsage=serverAuth",
                f"subjectAltName={san}",
            ]) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(csr), "-subj", f"/CN={name}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "openssl", "x509", "-req", "-in", str(csr), "-CA", ca_cert,
                "-CAkey", ca_key, "-CAcreateserial", "-out", str(cert),
                "-days", "1", "-sha256", "-extfile", str(extensions),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return str(cert), str(key)

    async def asyncSetUp(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.previous_exception_handler = self.loop.get_exception_handler()
        self.loop.set_exception_handler(self._handle_expected_tls_rejection)
        self.agent = FakeAcpServer()
        self.fake_logger = logging.getLogger("amusant.tests.fake_acp")
        self.fake_logger.setLevel(logging.INFO)
        self.upstream = await serve(
            self.agent.handler,
            "127.0.0.1",
            0,
            max_size=64 * 1024,
            logger=self.fake_logger,
        )
        upstream_port = self.upstream.sockets[0].getsockname()[1]
        self.upstream_url = f"ws://127.0.0.1:{upstream_port}/acp"
        self.proxy_server = None
        self.proxy_url = None

    def _handle_expected_tls_rejection(self, loop, context) -> None:
        message = context.get("message", "")
        error = context.get("exception")
        if message == "Error on transport creation for incoming connection" and isinstance(error, ConnectionResetError):
            return
        if self.previous_exception_handler is not None:
            self.previous_exception_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    async def asyncTearDown(self) -> None:
        self.loop.set_exception_handler(self.previous_exception_handler)
        if self.proxy_server is not None:
            self.proxy_server.close()
            await self.proxy_server.wait_closed()
        self.upstream.close()
        await self.upstream.wait_closed()

    async def _start_proxy(self, cert: str | None = None, key: str | None = None) -> str:
        proxy = LoopbackWssProxy(
            upstream_url=self.upstream_url,
            cert_file=cert or self.good_cert,
            key_file=key or self.good_key,
        )
        self.proxy_server = await proxy.start(host="127.0.0.1", port=0)
        port = self.proxy_server.sockets[0].getsockname()[1]
        self.proxy_url = f"wss://127.0.0.1:{port}/acp"
        return self.proxy_url

    def _executor(
        self,
        url: str,
        *,
        token: str = TOKEN,
        timeout: float = 1.0,
        ca_file: str | None = None,
    ) -> OpenAbAcpExecutor:
        environ = {
            "AMUSANT_ACP_URL": url,
            "AMUSANT_ACP_AUTH_KEY": token,
            "AMUSANT_ACP_CWD": "/openab/work",
            "AMUSANT_ACP_TIMEOUT_SECONDS": str(timeout),
        }
        if ca_file is not None:
            environ["AMUSANT_ACP_CA_PEM"] = Path(ca_file).read_text(encoding="ascii")
        executor = load_acp_executor(environ)
        if executor is None:
            raise AssertionError("ACP executor unexpectedly disabled")
        return executor

    async def test_debug_root_never_logs_bearer_prompt_or_result(self):
        url = await self._start_proxy()
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        root = logging.getLogger()
        previous_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            outcome = await self._executor(url, ca_file=self.good_ca).execute(_task())
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)
            handler.close()

        self.assertEqual(outcome.status, "completed")
        logs = captured.getvalue()
        leaks = {
            "bearer": TOKEN in logs,
            "prompt": _task().task in logs,
            "result": "Local verified WSS result." in logs,
        }
        self.assertEqual(leaks, {"bearer": False, "prompt": False, "result": False})

    async def test_verified_wss_proxy_completes_authenticated_acp(self):
        url = await self._start_proxy()
        outcome = await self._executor(url, ca_file=self.good_ca).execute(_task())
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.code, "ok")
        self.assertEqual(outcome.result.speak, "Local verified WSS result.")
        self.assertEqual(self.agent.methods, ["initialize", "session/new", "session/prompt"])
        self.assertEqual(self.agent.prompt_count, 1)

    async def test_private_ca_is_added_without_replacing_default_roots(self):
        url = await self._start_proxy()
        system_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        system_context.load_verify_locations(cafile=self.other_ca)
        captured = {}

        def inspect_connect(_url, **kwargs):
            captured["context"] = kwargs["ssl"]
            raise OSError("offline test")

        executor = OpenAbAcpExecutor(
            url=url,
            auth_key=TOKEN,
            cwd="/openab/work",
            ca_pem=Path(self.good_ca).read_text(encoding="ascii"),
            connect_factory=inspect_connect,
        )
        with patch("relay.acp_client.ssl.create_default_context", return_value=system_context):
            outcome = await executor.execute(_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        context = captured["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(len(context.get_ca_certs()), 2)

    async def test_untrusted_ca_fails_before_acp_initialize(self):
        url = await self._start_proxy()
        outcome = await self._executor(url, ca_file=self.other_ca).execute(_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        self.assertEqual(self.agent.methods, [])
        self.assertEqual(self.agent.prompt_count, 0)

    async def test_malformed_ca_bundle_fails_before_acp_initialize(self):
        url = await self._start_proxy()
        executor = OpenAbAcpExecutor(
            url=url,
            auth_key=TOKEN,
            cwd="/openab/work",
            ca_pem="-----BEGIN CERTIFICATE-----\nnot-a-certificate\n-----END CERTIFICATE-----",
        )
        outcome = await executor.execute(_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        self.assertEqual(self.agent.methods, [])
        self.assertEqual(self.agent.prompt_count, 0)

    async def test_hostname_mismatch_fails_closed(self):
        url = await self._start_proxy(self.wrong_host_cert, self.wrong_host_key)
        outcome = await self._executor(url, ca_file=self.good_ca).execute(_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        self.assertEqual(self.agent.methods, [])
        self.assertEqual(self.agent.prompt_count, 0)

    async def test_bad_bearer_is_forwarded_and_rejected_before_prompt(self):
        url = await self._start_proxy()
        outcome = await self._executor(url, token="wrong-bearer-token", ca_file=self.good_ca).execute(_task())
        self.assertEqual((outcome.status, outcome.code), ("failed", "acp_connect"))
        self.assertEqual(self.agent.prompt_count, 0)
        self.assertNotIn("session/prompt", self.agent.methods)

    async def test_missing_bearer_is_not_accepted_by_upstream(self):
        url = await self._start_proxy()
        context = ssl.create_default_context(cafile=self.good_ca)
        async with connect(url, ssl=context, open_timeout=1, max_size=64 * 1024) as websocket:
            with self.assertRaises(ConnectionClosed):
                await asyncio.wait_for(websocket.recv(), timeout=1)
        self.assertEqual(self.agent.prompt_count, 0)
        self.assertNotIn("session/prompt", self.agent.methods)

    async def test_timeout_after_prompt_is_uncertain(self):
        self.agent.mode = "hang"
        url = await self._start_proxy()
        with self.assertRaises(AcpUncertainError):
            await self._executor(url, timeout=1.0, ca_file=self.good_ca).execute(_task())
        self.assertEqual(self.agent.prompt_count, 1)

    async def test_disconnect_after_prompt_is_uncertain(self):
        self.agent.mode = "drop"
        url = await self._start_proxy()
        with self.assertRaises(AcpUncertainError):
            await self._executor(url, ca_file=self.good_ca).execute(_task())
        self.assertEqual(self.agent.prompt_count, 1)

    async def test_proxy_rejects_non_acp_path(self):
        url = await self._start_proxy()
        context = ssl.create_default_context(cafile=self.good_ca)
        wrong_path = url.replace("/acp", "/other")
        async with connect(wrong_path, ssl=context, open_timeout=1, max_size=64 * 1024) as websocket:
            with self.assertRaises(ConnectionClosed) as caught:
                await websocket.recv()
        self.assertEqual(caught.exception.rcvd.code, 1008)
        self.assertEqual(self.agent.methods, [])

    async def test_dev_proxy_rejects_non_loopback_bind_and_upstream(self):
        for unsafe_upstream in (
            "ws://192.0.2.20:8080/acp",
            "ws://localhost:8080/acp",
        ):
            with self.subTest(upstream=unsafe_upstream), self.assertRaises(ValueError):
                LoopbackWssProxy(
                    upstream_url=unsafe_upstream,
                    cert_file=self.good_cert,
                    key_file=self.good_key,
                )
        proxy = LoopbackWssProxy(
            upstream_url=self.upstream_url,
            cert_file=self.good_cert,
            key_file=self.good_key,
        )
        with self.assertRaises(ValueError):
            await proxy.start(host="0.0.0.0", port=0)
