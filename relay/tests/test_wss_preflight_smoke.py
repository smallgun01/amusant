from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from dataclasses import asdict
import io
import json
import logging
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Headers, Response

from relay.acp_smoke import EXIT_PASS, SmokeReport
from relay.devtools.nginx_sidecar_smoke import WssProxyTests
from relay.task_store import AdmissionCapacity
from relay.wss_preflight_smoke import (
    NETWORK_PROBE_NAMES,
    PROBE_NAMES,
    WssPreflightFailed,
    _PREFLIGHT_WEBSOCKETS_LOGGER,
    _print_report,
    _resolve_expected_task_ip,
    run_network_probes,
    run_preflight_smoke,
)


class FakeStore:
    def __init__(self, occupied=False, error=None):
        self.occupied = occupied
        self.error = error
        self.checked = []

    def has_client_tasks(self, client_id):
        self.checked.append(client_id)
        if self.error:
            raise self.error
        return self.occupied


class WssPreflightSmokeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("openssl") is None:
            raise unittest.SkipTest("openssl required for ephemeral test CA")
        cls.temp = tempfile.TemporaryDirectory(prefix="wss-preflight-")
        cls.ca_path, _ = WssProxyTests._make_ca(Path(cls.temp.name), "preflight-ca")
        cls.ca_pem = Path(cls.ca_path).read_text(encoding="ascii")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def environ(self):
        return {
            "AMUSANT_TASK_STORE": "dynamodb",
            "AMUSANT_CLIENT_ID": "p4-t4b-test-run-0001",
            "AMUSANT_DDB_TABLE": "test-ledger",
            "AMUSANT_DDB_REGION": "us-east-1",
            "AMUSANT_ACP_URL": "wss://jellii-acp.internal:8443/acp",
            "AMUSANT_ACP_AUTH_KEY": "preflight-bearer-local-test",
            "AMUSANT_ACP_CWD": "/home/agent/workspace",
            "AMUSANT_ACP_CA_PEM": self.ca_pem,
            "AMUSANT_ACP_EXPECTED_PRIVATE_IP": "10.23.45.67",
        }

    async def test_each_failed_probe_stops_before_smoke(self):
        for failed_name in NETWORK_PROBE_NAMES:
            store = FakeStore()
            smoke_calls = []

            async def probes(**kwargs):
                statuses = {name: "pass" for name in NETWORK_PROBE_NAMES}
                statuses[failed_name] = "failed"
                return statuses

            async def smoke(environ):
                smoke_calls.append(environ)
                return SmokeReport(EXIT_PASS, "task-id", "completed", "pending", None, True)

            report = await run_preflight_smoke(
                self.environ(),
                store_builder=lambda environ, **kwargs: store,
                probe_runner=probes,
                smoke_runner=smoke,
            )
            self.assertNotEqual(report.exit_code, EXIT_PASS, failed_name)
            self.assertIsNone(report.smoke, failed_name)
            self.assertEqual(smoke_calls, [], failed_name)
            self.assertTrue(store.checked[0].startswith("p4-t4b-"))

    async def test_nonempty_or_unreadable_client_partition_stops_before_probes(self):
        for store in (
            FakeStore(occupied=True),
            FakeStore(error=AdmissionCapacity("task_capacity")),
            FakeStore(error=RuntimeError("hidden detail")),
        ):
            probe_calls = []
            smoke_calls = []

            async def probes(**kwargs):
                probe_calls.append(True)
                return {name: "pass" for name in NETWORK_PROBE_NAMES}

            async def smoke(environ):
                smoke_calls.append(True)
                return SmokeReport(EXIT_PASS, "task-id", "completed", "pending", None, True)

            report = await run_preflight_smoke(
                self.environ(),
                store_builder=lambda environ, **kwargs: store,
                probe_runner=probes,
                smoke_runner=smoke,
            )
            self.assertNotEqual(report.exit_code, EXIT_PASS)
            self.assertEqual(probe_calls, [])
            self.assertEqual(smoke_calls, [])
            self.assertNotIn("hidden detail", json.dumps(asdict(report)))

    async def test_slow_task_store_guard_times_out_before_network_or_smoke(self):
        store = FakeStore()
        release = threading.Event()
        store.has_client_tasks = lambda client_id: (release.wait(0.2) or False)
        probe_calls = []
        smoke_calls = []

        async def probes(**kwargs):
            probe_calls.append(True)
            return {name: "pass" for name in NETWORK_PROBE_NAMES}

        async def smoke(environ):
            smoke_calls.append(True)
            return SmokeReport(EXIT_PASS, "task-id", "completed", "pending", None, True)

        with patch("relay.wss_preflight_smoke.PREFLIGHT_TIMEOUT_SECONDS", 0.02):
            report = await run_preflight_smoke(
                self.environ(),
                store_builder=lambda environ, **kwargs: store,
                probe_runner=probes,
                smoke_runner=smoke,
            )
        self.assertEqual(report.error, "task_store_timeout")
        self.assertEqual(report.probes["client_partition_empty"], "unknown")
        self.assertEqual(probe_calls, [])
        self.assertEqual(smoke_calls, [])
        # Let the bounded fake worker finish while its event loop is still alive.
        await asyncio.sleep(0.22)

    async def test_network_probe_matrix_pins_tls_and_never_sends_prompt(self):
        loop = asyncio.get_running_loop()
        calls = []
        sent = []

        class ProbeSocket:
            async def send(self, raw):
                sent.append(json.loads(raw))

            async def recv(self):
                raise ConnectionClosed(Close(1008, "unauthorized"), None)

        class Context:
            def __init__(self, websocket=None, error=None):
                self.websocket = websocket
                self.error = error

            async def __aenter__(self):
                if self.error is not None:
                    raise self.error
                return self.websocket

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def fake_connect(uri, **kwargs):
            calls.append((uri, kwargs))
            if len(calls) <= 2:
                return Context(error=ssl.SSLCertVerificationError("synthetic rejection"))
            if len(calls) <= 4:
                return Context(websocket=ProbeSocket())
            if "/not-acp" in uri:
                return Context(error=InvalidStatus(Response(404, "Not Found", Headers(), b"")))
            raise AssertionError("unexpected WebSocket probe")

        async def only_expected_a(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

        async def port_8080_refused(host, port):
            self.assertEqual(port, 8080)
            raise ConnectionRefusedError("synthetic network denial")

        with patch("relay.wss_preflight_smoke.connect", new=fake_connect):
            with patch.object(loop, "getaddrinfo", new=only_expected_a):
                with patch("relay.wss_preflight_smoke.asyncio.open_connection", new=port_8080_refused):
                    results = await run_network_probes(
                        url="wss://jellii-acp.internal:8443/acp",
                        hostname="jellii-acp.internal",
                        expected_ip="127.0.0.1",
                        port=8443,
                        ca_pem=self.ca_pem,
                        deadline=loop.time() + 10,
                    )

        self.assertEqual(results, {name: "pass" for name in NETWORK_PROBE_NAMES})
        self.assertEqual(len(calls), 5)
        self.assertEqual([message["method"] for message in sent], ["initialize", "initialize"])
        self.assertEqual(calls[0][1]["server_hostname"], "jellii-acp.internal")
        self.assertEqual(calls[1][1]["server_hostname"], "preflight-wrong-san.invalid")
        for _, kwargs in calls:
            self.assertEqual(kwargs["host"], "127.0.0.1")
            self.assertEqual(kwargs["port"], 8443)
            self.assertIsNone(kwargs["proxy"])
            self.assertEqual(kwargs["ssl"].verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(kwargs["ssl"].check_hostname)

    async def test_preflight_transport_logger_filters_debug_credentials(self):
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        logger = _PREFLIGHT_WEBSOCKETS_LOGGER
        previous_level = logger.level
        try:
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.debug('Authorization: Bearer preflight-credential-sentinel')
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            handler.close()
        self.assertNotIn('preflight-credential-sentinel', captured.getvalue())

    async def test_incomplete_or_unknown_probe_matrix_fails_closed(self):
        smoke_calls = []

        async def incomplete(**kwargs):
            return {"dns_current_task_ip": "pass"}

        async def unknown(**kwargs):
            results = {name: "pass" for name in NETWORK_PROBE_NAMES}
            results[NETWORK_PROBE_NAMES[-1]] = "unknown"
            return results

        async def smoke(environ):
            smoke_calls.append(True)
            return SmokeReport(EXIT_PASS, "task-id", "completed", "pending", None, True)

        for runner in (incomplete, unknown):
            report = await run_preflight_smoke(
                self.environ(),
                store_builder=lambda environ, **kwargs: FakeStore(),
                probe_runner=runner,
                smoke_runner=smoke,
            )
            self.assertNotEqual(report.exit_code, EXIT_PASS)
            self.assertIsNone(report.smoke)
        self.assertEqual(smoke_calls, [])

    async def test_success_calls_existing_smoke_once_with_isolated_client_id(self):
        store = FakeStore()
        smoke_calls = []

        async def probes(**kwargs):
            return {name: "pass" for name in NETWORK_PROBE_NAMES}

        async def smoke(environ):
            smoke_calls.append(dict(environ))
            return SmokeReport(EXIT_PASS, "runtime-task-id", "completed", "pending", None, True)

        original = self.environ()
        report = await run_preflight_smoke(
            original,
            store_builder=lambda environ, **kwargs: store,
            probe_runner=probes,
            smoke_runner=smoke,
        )
        self.assertEqual(report.exit_code, EXIT_PASS)
        self.assertEqual(len(smoke_calls), 1)
        client_id = smoke_calls[0]["AMUSANT_CLIENT_ID"]
        self.assertEqual(client_id, original["AMUSANT_CLIENT_ID"])
        self.assertTrue(client_id.startswith("p4-t4b-"))
        self.assertEqual(store.checked, [client_id])
        self.assertEqual(report.probes, {name: "pass" for name in PROBE_NAMES})

        output = io.StringIO()
        with redirect_stdout(output):
            _print_report(report)
        logged = output.getvalue()
        self.assertNotIn("10.23.45.67", logged)
        self.assertNotIn("preflight-bearer-local-test", logged)
        self.assertNotIn(self.ca_pem, logged)

    async def test_sqlite_endpoint_and_plaintext_configs_fail_before_store(self):
        for changes in (
            {"AMUSANT_TASK_STORE": "sqlite"},
            {"AWS_ENDPOINT_URL_DYNAMODB": "http://local.invalid"},
            {"AMUSANT_ACP_ALLOW_PLAINTEXT": "1"},
            {"AMUSANT_ACP_URL": "ws://jellii-acp.internal:8443/acp"},
            {"AMUSANT_CLIENT_ID": "old-template-id"},
        ):
            store_calls = []
            smoke_calls = []

            def build_store(environ, **kwargs):
                store_calls.append(True)
                return FakeStore()

            async def probes(**kwargs):
                return {name: "pass" for name in NETWORK_PROBE_NAMES}

            async def smoke(environ):
                smoke_calls.append(True)
                return SmokeReport(EXIT_PASS, "task-id", "completed", "pending", None, True)

            report = await run_preflight_smoke(
                {**self.environ(), **changes},
                store_builder=build_store,
                probe_runner=probes,
                smoke_runner=smoke,
            )
            self.assertNotEqual(report.exit_code, EXIT_PASS)
            self.assertEqual(store_calls, [])
            self.assertEqual(smoke_calls, [])

    async def test_dns_answer_must_exactly_match_expected_task_ip(self):
        loop = asyncio.get_running_loop()

        async def wrong_answer(host, port, **kwargs):
            return [(2, 1, 6, "", ("10.23.45.68", port))]

        with patch.object(loop, "getaddrinfo", new=wrong_answer):
            with self.assertRaises(WssPreflightFailed) as caught:
                await _resolve_expected_task_ip(
                    "jellii-acp.internal", 8443, "10.23.45.67", deadline=loop.time() + 2
                )
        self.assertEqual(caught.exception.code, "dns_target_mismatch")


if __name__ == "__main__":
    unittest.main()
