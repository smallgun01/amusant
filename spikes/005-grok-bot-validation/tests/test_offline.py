from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import socket
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx2
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from grok_pilot.cli import main as cli_main
from grok_pilot.contract import ContractError, validate_report, validate_routine_payload
from grok_pilot.mcp_server import MCP_BODY_LIMIT, create_mcp_app
from grok_pilot.routine import submit_once
from grok_pilot.store import Conflict, InvalidTransition, PilotStore, StoreError, Unauthorized

OWNER_ID = "offline-owner"
OWNER_BEARER = "test-only-owner-bearer"
_previous_logging_disable = logging.root.manager.disable
logging.disable(logging.CRITICAL)
TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test-tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
if os.name != "nt":
    TEST_TMP_ROOT.chmod(0o700)


def temporary_database_path() -> Path:
    return TEST_TMP_ROOT / f"test-{uuid4().hex}.sqlite3"


def cleanup_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


def tearDownModule():
    logging.disable(_previous_logging_disable)


def report_for(task_id: str, result_token: str, *, speak: str = "Mock task completed.") -> dict[str, Any]:
    return {
        "schema_version": "amusant-grok-pilot-0",
        "task_id": task_id,
        "result_token": result_token,
        "reported_outcome": "completed",
        "speak": speak,
        "card": {"title": "Mock result", "lines": ["Fixed fixture result"]},
        "native_result_locator": "grok://mock/result/42",
    }


class RunningServer:
    def __init__(self, app):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        self.socket.setblocking(False)
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(
            uvicorn.Config(app, log_level="info", access_log=False, lifespan="on")
        )
        self.task: asyncio.Task | None = None

    async def start(self) -> str:
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        for _ in range(200):
            if self.server.started:
                return f"http://127.0.0.1:{self.port}"
            if self.task.done():
                await self.task
            await asyncio.sleep(0.01)
        raise RuntimeError("loopback test server did not start")

    async def stop(self) -> None:
        self.server.should_exit = True
        if self.task is not None:
            await asyncio.wait_for(self.task, timeout=5)
        self.socket.close()


class OfflineContractTests(unittest.TestCase):
    def test_digest_ignores_json_key_order_and_result_token(self):
        task_id = str(uuid4())
        first = report_for(task_id, "capability-one")
        second = {key: first[key] for key in reversed(list(first))}
        second["result_token"] = "capability-two"
        self.assertEqual(validate_report(first).digest, validate_report(second).digest)

    def test_rejects_unknown_fields_control_chars_and_utf8_overflow(self):
        valid = report_for(str(uuid4()), "capability")
        with self.assertRaises(ContractError):
            validate_report({**valid, "owner_id": OWNER_ID})
        with self.assertRaises(ContractError):
            validate_report({**valid, "speak": "bad\ntext"})
        oversized = {
            **valid,
            "speak": "結" * 240,
            "native_result_locator": "界" * 512,
            "card": {"title": "標" * 80, "lines": ["字" * 120] * 4},
        }
        with self.assertRaisesRegex(ContractError, "3000 UTF-8 bytes"):
            validate_report(oversized)
        without_locator = dict(valid)
        without_locator.pop("native_result_locator")
        self.assertIsNone(validate_report(without_locator).report["native_result_locator"])


class OfflineStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = temporary_database_path()
        self.store = PilotStore(self.db)
        self.task_id, self.token = self.store.prepare(OWNER_ID, "case-offline", "Do one mock task.")

    def tearDown(self):
        cleanup_database(self.db)

    def test_write_once_owner_token_late_close_and_revoke(self):
        valid = validate_report(report_for(self.task_id, self.token))
        first = self.store.submit_report(OWNER_ID, valid)
        duplicate = self.store.submit_report(OWNER_ID, valid)
        self.assertEqual(first.receipt_id, duplicate.receipt_id)
        self.assertTrue(duplicate.duplicate)
        with self.assertRaises(Conflict):
            self.store.submit_report(OWNER_ID, validate_report(report_for(self.task_id, self.token, speak="Changed")))

        task_id, token = self.store.prepare(OWNER_ID, "late-case", "Late mock report.")
        self.store.close_task(task_id, "owner inspected and closed")
        late = self.store.submit_report(OWNER_ID, validate_report(report_for(task_id, token)))
        self.assertTrue(late.late)
        self.store.revoke_token(task_id)
        with self.assertRaises(StoreError):
            self.store.submit_report(OWNER_ID, validate_report(report_for(task_id, token)))
        self.assertTrue(self.store.readback(task_id)["closed_at"])
        self.assertEqual(self.store.readback(task_id)["native_state"], "unknown")
        evidence = json.dumps(self.store.evidence(), ensure_ascii=False)
        for private_value in (self.token, "Do one mock task.", "Mock task completed.", "grok://mock/result/42"):
            self.assertNotIn(private_value, evidence)

    def test_owner_and_token_are_both_required(self):
        task_id, token = self.store.prepare("another-owner", "isolated", "Owner mismatch.")
        with self.assertRaises(StoreError):
            self.store.submit_report(OWNER_ID, validate_report(report_for(task_id, token)))
        with self.assertRaises(StoreError):
            self.store.submit_report(OWNER_ID, validate_report(report_for(self.task_id, "wrong-token")))

    def test_result_token_is_only_stored_as_a_hash(self):
        with self.store._connection() as connection:
            stored = connection.execute(
                "SELECT result_token_sha256 FROM tasks WHERE task_id=?", (self.task_id,)
            ).fetchone()[0]
        self.assertEqual(stored, hashlib.sha256(self.token.encode("utf-8")).hexdigest())
        self.assertNotEqual(stored, self.token)

    def test_cli_submit_is_dry_run_by_default(self):
        stdout = io.StringIO()
        with patch("grok_pilot.cli.submit_once") as live_post, contextlib.redirect_stdout(stdout):
            result_code = cli_main(
                [
                    "submit",
                    "--db",
                    str(self.db),
                    "--task-id",
                    self.task_id,
                    "--routine-url",
                    "https://routine.example.invalid/hook",
                ]
            )
        self.assertEqual(result_code, 0)
        live_post.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue())["mode"], "dry-run")
        self.assertEqual(self.store.get_task(self.task_id)["submission_state"], "prepared")

    def test_callback_mode_rejects_missing_or_expired_ttl_before_mock_post(self):
        mock_calls = []

        def mock_webhook(request: httpx2.Request) -> httpx2.Response:
            mock_calls.append(request)
            return httpx2.Response(200)

        def mocked_submit(store, task_id, result_token, routine_url, routine_token, *, callback_mode=False):
            return submit_once(
                store,
                task_id,
                result_token,
                routine_url,
                routine_token,
                callback_mode=callback_mode,
                transport=httpx2.MockTransport(mock_webhook),
            )

        def run_live(task_id: str, token: str) -> tuple[int, str]:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "GROK_PILOT_RESULT_TOKEN": token,
                    "GROK_PILOT_ROUTINE_BEARER_TOKEN": "offline-mock-routine-key",
                },
            ), patch("grok_pilot.cli.submit_once", side_effect=mocked_submit):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = cli_main(
                        [
                            "submit",
                            "--db",
                            str(self.db),
                            "--task-id",
                            task_id,
                            "--routine-url",
                            "https://routine.example.invalid/hook",
                            "--live",
                            "--callback-mode",
                        ]
                    )
            return result, stdout.getvalue() + stderr.getvalue()

        result, output = run_live(self.task_id, self.token)
        self.assertEqual(result, 2)
        self.assertIn("finite result-token TTL", output)
        self.assertEqual(mock_calls, [])
        self.assertEqual(self.store.get_task(self.task_id)["submission_state"], "prepared")

        with self.store._connection() as connection:
            connection.execute(
                "UPDATE tasks SET result_token_expires_at=? WHERE task_id=?",
                ("2000-01-01T00:00:00Z", self.task_id),
            )
        result, _ = run_live(self.task_id, self.token)
        self.assertEqual(result, 2)
        self.assertEqual(mock_calls, [])
        self.assertEqual(self.store.get_task(self.task_id)["submission_state"], "prepared")

        finite_task, finite_token = self.store.prepare(
            OWNER_ID, "callback-mode-live-mock", "Finite TTL mock send.", result_token_ttl_seconds=300
        )
        result, _ = run_live(finite_task, finite_token)
        self.assertEqual(result, 0)
        self.assertEqual(len(mock_calls), 1)
        self.assertEqual(mock_calls[0].method, "POST")
        self.assertEqual(self.store.get_task(finite_task)["submission_state"], "accepted")

    def test_callback_mode_atomic_send_transition_rechecks_ttl(self):
        task_id, token = self.store.prepare(
            OWNER_ID, "callback-mode-expiry-race", "Must remain prepared.", result_token_ttl_seconds=300
        )
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE tasks SET result_token_expires_at=? WHERE task_id=?",
                ("2000-01-01T00:00:00Z", task_id),
            )

        with self.assertRaises(Unauthorized):
            self.store.begin_submission(
                task_id,
                result_token=token,
                require_result_token_expiry=True,
            )
        self.assertEqual(self.store.get_task(task_id)["submission_state"], "prepared")

    def test_storage_error_does_not_leave_a_receipt(self):
        with self.store._connection() as connection:
            connection.execute(
                """CREATE TRIGGER fail_receipt BEFORE INSERT ON receipts
                   BEGIN SELECT RAISE(ABORT, 'test storage failure'); END"""
            )
        with self.assertRaises(StoreError):
            self.store.submit_report(OWNER_ID, validate_report(report_for(self.task_id, self.token)))
        self.assertIsNone(self.store.readback(self.task_id)["receipt_id"])

    def test_crash_after_sending_becomes_unknown_and_cannot_resend(self):
        child_script = (
            "import os, sys; "
            "from grok_pilot.store import PilotStore; "
            "PilotStore(sys.argv[1]).begin_submission(sys.argv[2]); "
            "os._exit(17)"
        )
        crashed = subprocess.run(
            [sys.executable, "-c", child_script, str(self.db), self.task_id],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        restarted = PilotStore(self.db)
        self.assertEqual(restarted.get_task(self.task_id)["submission_state"], "sending")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result_code = cli_main(["recover", "--db", str(self.db), "--task-id", self.task_id])
        self.assertEqual(result_code, 0)
        recovered = json.loads(stdout.getvalue())
        self.assertEqual(recovered["submission_state"], "acceptance_unknown")
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            return httpx2.Response(200)

        with self.assertRaises(InvalidTransition):
            submit_once(
                restarted,
                self.task_id,
                self.token,
                "https://routine.example.invalid/hook",
                "offline-routine-key",
                transport=httpx2.MockTransport(handler),
            )
        self.assertEqual(attempts, 0)

    def test_successful_submit_once_allows_observers_and_keeps_early_receipt(self):
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            self.assertEqual(request.method, "POST")
            observer = PilotStore(self.db)
            self.assertEqual(observer.get_task(self.task_id)["submission_state"], "sending")
            with self.assertRaises(InvalidTransition):
                observer.recover_submission(self.task_id)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(cli_main(["list", "--db", str(self.db)]), 0)
            self.assertEqual(json.loads(stdout.getvalue())[0]["submission_state"], "sending")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    cli_main(["readback", "--db", str(self.db), "--task-id", self.task_id]), 0
                )
            self.assertEqual(json.loads(stdout.getvalue())["submission_state"], "sending")

            receipt = observer.submit_report(
                OWNER_ID, validate_report(report_for(self.task_id, self.token))
            )
            self.assertFalse(receipt.duplicate)
            return httpx2.Response(200)

        state = submit_once(
            self.store,
            self.task_id,
            self.token,
            "https://routine.example.invalid/hook",
            "offline-routine-key",
            transport=httpx2.MockTransport(handler),
        )
        self.assertEqual(attempts, 1)
        self.assertEqual(state, "accepted")
        result = PilotStore(self.db).readback(self.task_id)
        self.assertEqual(result["submission_state"], "accepted")
        self.assertIsNotNone(result["receipt_id"])
        self.assertEqual(result["native_state"], "unknown")

    def test_early_result_survives_later_webhook_acceptance(self):
        self.store.begin_submission(self.task_id)
        receipt = self.store.submit_report(OWNER_ID, validate_report(report_for(self.task_id, self.token)))
        self.store.finish_submission(self.task_id, "accepted")
        result = self.store.readback(self.task_id)
        self.assertEqual(result["submission_state"], "accepted")
        self.assertEqual(result["receipt_id"], receipt.receipt_id)
        self.assertEqual(result["native_state"], "unknown")

    def test_redirect_is_not_followed_and_not_retried(self):
        attempts = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal attempts
            attempts += 1
            return httpx2.Response(307, headers={"Location": "https://routine.example.invalid/second"})

        state = submit_once(
            self.store,
            self.task_id,
            self.token,
            "https://routine.example.invalid/first",
            "offline-routine-key",
            transport=httpx2.MockTransport(handler),
        )
        self.assertEqual(state, "acceptance_unknown")
        self.assertEqual(attempts, 1)
        with self.assertRaises(InvalidTransition):
            submit_once(
                self.store,
                self.task_id,
                self.token,
                "https://routine.example.invalid/first",
                "offline-routine-key",
                transport=httpx2.MockTransport(handler),
            )
        self.assertEqual(attempts, 1)

    @unittest.skipUnless(os.name == "posix", "POSIX file modes are not available on Windows")
    def test_private_sqlite_directory_database_and_sidecar_modes(self):
        connection = self.store._connect()
        observer = None
        try:
            observer = self.store._connect()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE tasks SET close_reason='permission-check' WHERE task_id=?", (self.task_id,)
            )
            self.store._secure_sqlite_artifacts()
            self.assertEqual(stat.S_IMODE(self.db.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{self.db}{suffix}")
                self.assertTrue(sidecar.exists())
                self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
        finally:
            connection.rollback()
            connection.close()
            if observer is not None:
                observer.close()
            self.store._secure_sqlite_artifacts()

    @unittest.skipUnless(os.name == "posix", "POSIX file modes are not available on Windows")
    def test_insecure_existing_storage_is_refused(self):
        unsafe_parent = TEST_TMP_ROOT / f"unsafe-{uuid4().hex}"
        unsafe_parent.mkdir(mode=0o755)
        unsafe_parent.chmod(0o755)
        try:
            with self.assertRaisesRegex(StoreError, "parent must have POSIX mode 0700"):
                PilotStore(unsafe_parent / "pilot.sqlite3")
        finally:
            unsafe_parent.rmdir()

        unsafe_db = TEST_TMP_ROOT / f"unsafe-{uuid4().hex}.sqlite3"
        unsafe_db.write_bytes(b"existing database placeholder")
        unsafe_db.chmod(0o644)
        try:
            with self.assertRaisesRegex(StoreError, "existing SQLite database must have POSIX mode 0600"):
                PilotStore(unsafe_db)
            self.assertEqual(stat.S_IMODE(unsafe_db.stat().st_mode), 0o644)
        finally:
            unsafe_db.unlink()


class McpGoldenPathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = temporary_database_path()
        self.store = PilotStore(self.db)
        self.task_id, self.result_token = self.store.prepare(
            OWNER_ID, "G2-Golden-Path", "Run one fixed, side-effect-free mock fixture."
        )
        self.webhook_calls = 0
        self.fixture_runs = 0

        async def mock_routine(request: Request):
            self.webhook_calls += 1
            task_body = validate_routine_payload(await request.json())
            self.fixture_runs += 1
            # A fixed result stands in for one business fixture. It performs no external action.
            result = report_for(task_body["task_id"], task_body["result_token"])
            return JSONResponse(result)

        mock_app = Starlette(routes=[Route("/routine", mock_routine, methods=["POST"])])
        self.mock_server = RunningServer(mock_app)
        mock_origin = await self.mock_server.start()

        async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                f"{mock_origin}/routine",
                json={
                    "schema_version": "amusant-grok-pilot-0",
                    "task_id": self.task_id,
                    "result_token": self.result_token,
                    "task_label": "G2-Golden-Path",
                    "instruction": "Run one fixed, side-effect-free mock fixture.",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.report = response.json()

        mcp_app = create_mcp_app(self.store, OWNER_ID, OWNER_BEARER)
        self.mcp_server = RunningServer(mcp_app)
        mcp_origin = await self.mcp_server.start()
        self.mcp_url = f"{mcp_origin}/mcp"

    async def asyncTearDown(self):
        await self.mcp_server.stop()
        await self.mock_server.stop()
        cleanup_database(self.db)

    async def _call_submit(self, report: dict[str, Any], bearer: str = OWNER_BEARER):
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {bearer}"},
            follow_redirects=False,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(self.mcp_url, http_client=http_client) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    return listed, await session.call_tool("submit_result", arguments=report)

    async def test_prepare_mock_webhook_mcp_durable_receipt_cli_readback(self):
        listed, first_call = await self._call_submit(self.report)
        self.assertEqual([tool.name for tool in listed.tools], ["submit_result"])
        self.assertFalse(listed.tools[0].input_schema.get("additionalProperties", True))
        self.assertFalse(first_call.is_error)
        first_ack = json.loads(first_call.content[0].text)
        self.assertTrue(first_ack["accepted"])
        self.assertFalse(first_ack["duplicate"])
        self.assertNotIn(self.result_token, first_call.content[0].text)

        # Simulate a lost MCP ACK: resend only the same result, never the Routine POST.
        _, retry_call = await self._call_submit(self.report)
        self.assertFalse(retry_call.is_error)
        retry_ack = json.loads(retry_call.content[0].text)
        self.assertTrue(retry_ack["duplicate"])
        self.assertEqual(first_ack["receipt_id"], retry_ack["receipt_id"])
        self.assertEqual(self.webhook_calls, 1)
        self.assertEqual(self.fixture_runs, 1)

        # Reopen the database and exercise the actual CLI readback path.
        reopened = PilotStore(self.db)
        self.assertEqual(reopened.readback(self.task_id)["receipt_id"], first_ack["receipt_id"])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result_code = cli_main(["readback", "--db", str(self.db), "--task-id", self.task_id])
        self.assertEqual(result_code, 0)
        readback = json.loads(stdout.getvalue())
        self.assertEqual(readback["receipt_id"], first_ack["receipt_id"])
        persisted_report = {key: value for key, value in self.report.items() if key != "result_token"}
        self.assertEqual(readback["report"], persisted_report)
        self.assertEqual(readback["native_state"], "unknown")

        # Restart the MCP service against a newly opened store and replay once more.
        await self.mcp_server.stop()
        self.mcp_server = RunningServer(create_mcp_app(reopened, OWNER_ID, OWNER_BEARER))
        restarted_origin = await self.mcp_server.start()
        self.mcp_url = f"{restarted_origin}/mcp"
        _, after_restart = await self._call_submit(self.report)
        self.assertFalse(after_restart.is_error)
        restarted_ack = json.loads(after_restart.content[0].text)
        self.assertTrue(restarted_ack["duplicate"])
        self.assertEqual(restarted_ack["receipt_id"], first_ack["receipt_id"])
        self.assertEqual(self.webhook_calls, 1)
        self.assertEqual(self.fixture_runs, 1)

    async def test_mcp_tool_errors_do_not_create_receipts(self):
        async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
            unauthorized = await client.post(
                self.mcp_url,
                content=b"{}",
                headers={
                    "Authorization": "Bearer incorrect-owner-bearer",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertIsNone(PilotStore(self.db).readback(self.task_id)["receipt_id"])

        malformed = {**self.report, "result_token": "incorrect-capability"}
        _, denied = await self._call_submit(malformed)
        self.assertTrue(denied.is_error)
        self.assertIsNone(PilotStore(self.db).readback(self.task_id)["receipt_id"])

        unknown_field = {**self.report, "owner_id": OWNER_ID}
        _, unknown = await self._call_submit(unknown_field)
        self.assertTrue(unknown.is_error)
        self.assertIsNone(PilotStore(self.db).readback(self.task_id)["receipt_id"])

        wrong_owner_id, wrong_owner_token = self.store.prepare("other-owner", "other-owner", "No access.")
        _, owner_denied = await self._call_submit(report_for(wrong_owner_id, wrong_owner_token))
        self.assertTrue(owner_denied.is_error)
        self.assertIsNone(PilotStore(self.db).readback(wrong_owner_id)["receipt_id"])

        async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
            oversized_response = await client.post(
                self.mcp_url,
                content=b"x" * (MCP_BODY_LIMIT + 1),
                headers={
                    "Authorization": f"Bearer {OWNER_BEARER}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
        self.assertEqual(oversized_response.status_code, 413)
        self.assertIsNone(PilotStore(self.db).readback(self.task_id)["receipt_id"])


if __name__ == "__main__":
    unittest.main()
