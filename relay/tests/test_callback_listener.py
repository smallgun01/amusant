"""Private listener lifecycle and callback transport tests, using loopback fixtures only."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from websockets.asyncio.client import connect as websocket_connect

from relay.callback_listener import callback_listener_config, serve_callback
from relay.grok_callback import create_grok_callback_app
from relay.live_bridge import run
from relay.task_store import SQLiteTaskStore, TaskAdmissionRequest


class CallbackListenerConfigTests(unittest.TestCase):
    def test_requires_distinct_private_bind_and_exact_canonical_host(self):
        env = {
            "RELAY_CALLBACK_BIND_IP": "172.29.2.2",
            "RELAY_CALLBACK_LISTEN_PORT": "8645",
            "AMUSANT_GROK_CALLBACK_URL": "https://callback.invalid/callback/results",
            "AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS": "callback.invalid",
            "AMUSANT_GROK_CALLBACK_SERVICE_PORT": "443",
        }
        self.assertEqual(callback_listener_config(env, device_host="172.29.1.2"), ("172.29.2.2", 8645))
        for change in (
            {"RELAY_CALLBACK_BIND_IP": "0.0.0.0"},
            {"RELAY_CALLBACK_BIND_IP": "127.0.0.1"},
            {"RELAY_CALLBACK_BIND_IP": "8.8.8.8"},
            {"RELAY_CALLBACK_BIND_IP": "172.29.1.2"},
            {"RELAY_CALLBACK_BIND_IP": "::"},
            {"RELAY_CALLBACK_LISTEN_PORT": "0"},
            {"AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS": "evil.invalid"},
            {"AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS": "callback.invalid,evil.invalid"},
            {"AMUSANT_GROK_CALLBACK_URL": "https://callback.invalid:0/callback/results"},
            {"AMUSANT_GROK_CALLBACK_URL": "https://callback.invalid/callback/results?token=bad"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                callback_listener_config({**env, **change}, device_host="172.29.1.2")


class CallbackListenerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(__file__).parent / f".callback-listener-{uuid4().hex}"
        self.root.mkdir()
        self.addCleanup(self.cleanup_root)
        self.store = SQLiteTaskStore(database_path=self.root / "ledger.sqlite3", max_pending_tasks=8)
        self.task_id = str(uuid4())
        self.owner = "offline-owner"
        self.token = "fake-task-capability-0123456789"
        now = datetime.now(timezone.utc)
        self.store.admit(TaskAdmissionRequest(
            task_id=self.task_id, client_id=self.owner, conversation_id="conversation", turn_id=1,
            call_id="call", task="offline fixture", task_kind="agent", backend="grok_bot",
            created_at=now, expires_at=now + timedelta(minutes=5), executor_kind="grok_callback",
        ))
        self.store.claim_due(now=now, lease_seconds=30)
        self.store.begin_grok_submission(
            self.task_id, token_sha256=hashlib.sha256(self.token.encode()).hexdigest(),
            token_expires_at=now + timedelta(minutes=5), callback_deadline_at=now + timedelta(minutes=2), now=now,
        )
        self.app = create_grok_callback_app(
            self.store, self.owner, trusted_hosts=["callback.invalid"], expected_port=443, max_inflight=4,
        )

    def cleanup_root(self):
        for path in self.root.iterdir():
            if path.is_file():
                path.chmod(0o666)
            path.unlink(missing_ok=True)
        self.root.rmdir()

    def port(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    async def request(self, port, *, host="callback.invalid", token=None, payload=None, forwarded=""):
        body = json.dumps(payload if payload is not None else {
            "schema_version": "amusant-grok-pilot-0", "task_id": self.task_id,
            "reported_outcome": "completed", "speak": "Offline result.",
            "card": {"title": "Result", "lines": ["Done."]},
        }).encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        request = (
            f"POST /callback/results HTTP/1.1\r\nHost: {host}\r\n"
            f"Authorization: Bearer {token or self.token}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n{forwarded}\r\n"
        ).encode() + body
        writer.write(request)
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        await writer.wait_closed()
        headers, raw_body = response.split(b"\r\n\r\n", 1)
        return int(headers.split(b" ", 2)[1]), json.loads(raw_body)

    async def test_real_loopback_server_auth_host_receipt_and_shutdown(self):
        port = self.port()
        async with serve_callback(self.app, "127.0.0.1", port):
            status, _ = await self.request(port, host="attacker.invalid", forwarded="X-Forwarded-Host: callback.invalid\r\n")
            self.assertEqual(status, 400)
            status, _ = await self.request(port, token="wrong-capability")
            self.assertEqual(status, 401)
            status, _ = await self.request(port, payload={"reported_outcome": []})
            self.assertEqual(status, 400)
            self.assertIsNone(self.store.get_result(self.task_id))
            status, receipt = await self.request(port, forwarded="X-Forwarded-Host: attacker.invalid\r\nX-Forwarded-Proto: http\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(receipt["native_state"], "unknown")
            self.assertIsNotNone(self.store.get_result(self.task_id))
            self.assertIsNotNone(self.store.get_inbox(self.task_id))
            status, duplicate = await self.request(port)
            self.assertEqual(status, 200)
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["receipt_id"], receipt["receipt_id"])
        with self.assertRaises(OSError):
            await asyncio.open_connection("127.0.0.1", port)
        self.store.recover_after_restart()
        self.assertEqual(self.store.claim_due(now=datetime.now(timezone.utc), lease_seconds=30), [])

    async def test_occupied_port_fails_startup(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with self.assertRaises(OSError):
                async with serve_callback(self.app, "127.0.0.1", occupied.getsockname()[1]):
                    self.fail("listener must not start on an occupied port")

    async def test_server_itself_rejects_public_or_wildcard_binds(self):
        for host in ("0.0.0.0", "8.8.8.8", "::", "localhost"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                async with serve_callback(self.app, host, 8645):
                    self.fail("unsafe listener must not start")

    async def test_callback_bind_failure_stops_wss_before_dispatch(self):
        class Dispatcher:
            started = False
            async def start(self):
                self.started = True
            async def stop(self):
                pass

        async def handler(_websocket):
            pass

        dispatcher = Dispatcher()
        device_port = self.port()
        env = {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "offline-fixture",
               "AMUSANT_SLOW_EXECUTOR": "grok_callback", "AMUSANT_CLIENT_ID": self.owner}
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with (
                patch.dict(os.environ, env, clear=True),
                patch("relay.callback_listener.callback_listener_config", return_value=("127.0.0.1", occupied.getsockname()[1])),
                patch("relay.live_bridge.build_process_runtime", return_value=(handler, dispatcher, self.store)),
                patch("relay.application.build_grok_callback_receiver_app", return_value=self.app),
                patch("relay.live_bridge.serve") as device_serve,
                patch("relay.live_bridge.install_shutdown_handlers", return_value=()),
                patch("relay.live_bridge.remove_shutdown_handlers"),
            ):
                with self.assertRaises(OSError):
                    await run("127.0.0.1", device_port, "offline-device-token", device_mode="pcm")
                device_serve.assert_not_called()
        self.assertFalse(dispatcher.started)
        with self.assertRaises(OSError):
            await asyncio.open_connection("127.0.0.1", device_port)

    async def test_shutdown_already_requested_does_not_start_dispatch(self):
        class Dispatcher:
            started = False
            async def start(self):
                self.started = True
            async def stop(self):
                pass

        async def handler(_websocket):
            pass

        dispatcher = Dispatcher()
        device_port, callback_port = self.port(), self.port()
        while callback_port == device_port:
            callback_port = self.port()
        env = {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "offline-fixture",
               "AMUSANT_SLOW_EXECUTOR": "grok_callback", "AMUSANT_CLIENT_ID": self.owner}

        def install(event, _loop):
            event.set()
            return ()

        with (
            patch.dict(os.environ, env, clear=True),
            patch("relay.callback_listener.callback_listener_config", return_value=("127.0.0.1", callback_port)),
            patch("relay.live_bridge.build_process_runtime", return_value=(handler, dispatcher, self.store)),
            patch("relay.application.build_grok_callback_receiver_app", return_value=self.app),
            patch("relay.live_bridge.install_shutdown_handlers", side_effect=install),
            patch("relay.live_bridge.remove_shutdown_handlers"),
        ):
            await run("127.0.0.1", device_port, "offline-device-token", device_mode="pcm")
        self.assertFalse(dispatcher.started)

    async def test_device_bind_failure_closes_callback_without_dispatch(self):
        class Dispatcher:
            started = False
            async def start(self):
                self.started = True
            async def stop(self):
                pass

        async def handler(_websocket):
            pass

        dispatcher = Dispatcher()
        callback_port = self.port()
        env = {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "offline-fixture",
               "AMUSANT_SLOW_EXECUTOR": "grok_callback", "AMUSANT_CLIENT_ID": self.owner}
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with (
                patch.dict(os.environ, env, clear=True),
                patch("relay.callback_listener.callback_listener_config", return_value=("127.0.0.1", callback_port)),
                patch("relay.live_bridge.build_process_runtime", return_value=(handler, dispatcher, self.store)),
                patch("relay.application.build_grok_callback_receiver_app", return_value=self.app),
                patch("relay.live_bridge.install_shutdown_handlers", return_value=()),
                patch("relay.live_bridge.remove_shutdown_handlers"),
            ):
                with self.assertRaises(OSError):
                    await run("127.0.0.1", occupied.getsockname()[1], "offline-device-token", device_mode="pcm")
        self.assertFalse(dispatcher.started)
        with self.assertRaises(OSError):
            await asyncio.open_connection("127.0.0.1", callback_port)

    async def test_slow_body_times_out_without_receipt(self):
        port = self.port()
        with patch("relay.grok_callback.CALLBACK_BODY_TIMEOUT_SECONDS", 0.05):
            async with serve_callback(self.app, "127.0.0.1", port):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write((
                    "POST /callback/results HTTP/1.1\r\nHost: callback.invalid\r\n"
                    f"Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n"
                    "Content-Length: 100\r\nConnection: close\r\n\r\n{"
                ).encode())
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), 2)
                writer.close()
                await writer.wait_closed()
                self.assertIn(b" 408 ", response.split(b"\r\n", 1)[0])
                self.assertIsNone(self.store.get_result(self.task_id))

    async def test_one_inflight_callback_rejects_concurrent_request(self):
        app = create_grok_callback_app(
            self.store, self.owner, trusted_hosts=["callback.invalid"], expected_port=443, max_inflight=1,
        )
        port = self.port()
        async with serve_callback(app, "127.0.0.1", port):
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write((
                "POST /callback/results HTTP/1.1\r\nHost: callback.invalid\r\n"
                f"Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n"
                "Content-Length: 100\r\nConnection: close\r\n\r\n{"
            ).encode())
            await writer.drain()
            await asyncio.sleep(0.1)
            try:
                status, _ = await self.request(port)
                self.assertEqual(status, 429)
                self.assertIsNone(self.store.get_result(self.task_id))
            finally:
                writer.close()
                await writer.wait_closed()

    async def test_process_starts_callback_before_dispatch_and_drains_without_repost(self):
        database = self.root / "process.sqlite3"
        store = SQLiteTaskStore(database_path=database, max_pending_tasks=8)
        task_id = str(uuid4())
        now = datetime.now(timezone.utc)
        store.admit(TaskAdmissionRequest(
            task_id=task_id, client_id="process-owner", conversation_id="conversation", turn_id=1,
            call_id="process-call", task="offline fixture", task_kind="agent", backend="grok_bot",
            created_at=now, expires_at=now + timedelta(minutes=5), executor_kind="grok_callback",
        ))
        callback_port, device_port = self.port(), self.port()
        while device_port == callback_port:
            device_port = self.port()
        seen = threading.Event()
        calls = []

        def fake_routine(_url, body, _bearer, _proxy, _timeout):
            payload = json.loads(body)
            calls.append(payload["task_id"])
            report = {
                "schema_version": "amusant-grok-pilot-0", "task_id": task_id,
                "reported_outcome": "completed", "speak": "Offline response.",
                "card": {"title": "Result", "lines": ["Done."]},
            }
            connection = http.client.HTTPConnection("127.0.0.1", callback_port, timeout=3)
            try:
                connection.request("POST", "/callback/results", json.dumps(report).encode(), {
                    "Host": "callback.invalid", "Authorization": f"Bearer {payload['result_token']}",
                    "Content-Type": "application/json",
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                seen.set()
            finally:
                connection.close()
            return 200

        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "offline-unused-fixture",
            "AMUSANT_CLIENT_ID": "process-owner", "AMUSANT_BACKEND_ID": "grok_bot",
            "AMUSANT_TASK_STORE": "sqlite", "AMUSANT_TASK_DB": str(database),
            "AMUSANT_SLOW_EXECUTOR": "grok_callback", "AMUSANT_GROK_ROUTINE_URL": "https://routine.invalid/webhook",
            "AMUSANT_GROK_ROUTINE_TOKEN": "offline-routine-token",
            "AMUSANT_GROK_CALLBACK_URL": "https://callback.invalid/callback/results",
            "AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS": "callback.invalid",
            "AMUSANT_GROK_CALLBACK_SERVICE_PORT": "443", "HTTPS_PROXY": "http://proxy.invalid:3128",
        }
        stop = {}

        def install(event, _loop):
            stop["event"] = event
            return ()

        with (
            patch.dict(os.environ, env, clear=True),
            patch("relay.callback_listener.callback_listener_config", return_value=("127.0.0.1", callback_port)),
            patch("relay.grok_callback_executor._post_once", side_effect=fake_routine),
            patch("relay.live_bridge.install_shutdown_handlers", side_effect=install),
            patch("relay.live_bridge.remove_shutdown_handlers"),
        ):
            process = asyncio.create_task(run("127.0.0.1", device_port, "offline-device-token", device_mode="pcm"))
            try:
                for _ in range(200):
                    if seen.is_set() or process.done():
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(seen.is_set(), "fake Callback must succeed before Webhook ACK")
                self.assertEqual(calls, [task_id])
                self.assertEqual(store.get_by_task_id(task_id)["status"], "completed")
                self.assertIsNotNone(store.get_inbox(task_id))
            finally:
                if "event" in stop:
                    stop["event"].set()
                await asyncio.wait_for(process, 10)
            stop.clear()
            restarted = asyncio.create_task(run("127.0.0.1", device_port, "offline-device-token", device_mode="pcm"))
            try:
                ready = False
                for _ in range(200):
                    if restarted.done():
                        break
                    try:
                        async with websocket_connect(
                            f"ws://127.0.0.1:{device_port}/ws", open_timeout=2, close_timeout=1,
                            additional_headers={"Authorization": "Bearer offline-device-token"},
                            proxy=None,
                        ):
                            pass
                        _reader, writer = await asyncio.open_connection("127.0.0.1", callback_port)
                        writer.close()
                        await writer.wait_closed()
                        ready = True
                        break
                    except (OSError, TimeoutError):
                        await asyncio.sleep(0.02)
                self.assertTrue(ready, "both listeners must reopen on restart")
                await asyncio.sleep(0.2)
                self.assertEqual(calls, [task_id], "restart must not resubmit a completed Webhook")
            finally:
                if "event" in stop:
                    stop["event"].set()
                await asyncio.wait_for(restarted, 10)
        self.assertEqual(calls, [task_id])
        for port in (device_port, callback_port):
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
