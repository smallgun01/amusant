from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from grok_pilot.callback import (
    CALLBACK_BODY_LIMIT,
    CALLBACK_PATH,
    MAX_CALLBACK_INFLIGHT,
    create_callback_app,
)
from grok_pilot.cli import main as cli_main
from grok_pilot.contract import validate_routine_payload
from grok_pilot.store import MAX_RESULT_TOKEN_TTL_SECONDS, PilotStore

OWNER_ID = "offline-owner"
TRUSTED_HOST = "callback.example.test"
TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test-tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
if os.name != "nt":
    TEST_TMP_ROOT.chmod(0o700)


def temporary_database_path() -> Path:
    return TEST_TMP_ROOT / f"callback-{uuid4().hex}.sqlite3"


def cleanup_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


def report_for(task_id: str, *, speak: str = "Callback fixture completed.") -> dict[str, Any]:
    return {
        "schema_version": "amusant-grok-pilot-0",
        "task_id": task_id,
        "reported_outcome": "completed",
        "speak": speak,
        "card": {"title": "Callback result", "lines": ["Fixed offline fixture"]},
        "native_result_locator": "grok://mock/callback/1",
    }


class MutableClock:
    def __init__(self):
        self.now = datetime(2026, 10, 26, 3, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


class CallbackAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = temporary_database_path()
        self.clock = MutableClock()
        self.store = PilotStore(self.db, clock=self.clock)
        self.task_id, self.token = self.store.prepare(
            OWNER_ID, "G3-P1-offline", "Run the one fixed mock fixture.", result_token_ttl_seconds=60
        )
        self.app = create_callback_app(
            self.store, OWNER_ID, trusted_hosts=[TRUSTED_HOST], max_inflight=4
        )

    def tearDown(self):
        cleanup_database(self.db)

    def client(self, *, host: str = TRUSTED_HOST) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app),
            base_url=f"http://{host}",
            follow_redirects=False,
            trust_env=False,
        )

    def payload(self, *, task_id: str | None = None, speak: str = "Callback fixture completed."):
        return report_for(task_id or self.task_id, speak=speak)

    def auth_headers(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self.token}"}

    async def post_report(self, client: httpx2.AsyncClient, payload=None, token: str | None = None):
        return await client.post(
            CALLBACK_PATH,
            json=payload if payload is not None else self.payload(),
            headers=self.auth_headers(token),
        )

    async def test_success_is_owner_bound_durable_and_token_free(self):
        async with self.client() as client:
            response = await self.post_report(client)
        self.assertEqual(response.status_code, 200)
        ack = response.json()
        self.assertTrue(ack["accepted"])
        self.assertFalse(ack["duplicate"])
        self.assertEqual(ack["task_id"], self.task_id)
        self.assertNotIn(self.token, response.text)
        readback = self.store.readback(self.task_id)
        self.assertEqual(readback["receipt_id"], ack["receipt_id"])
        self.assertEqual(readback["native_state"], "unknown")
        self.assertNotIn(self.token, json.dumps(readback))
        with self.store._connection() as connection:
            stored = connection.execute(
                "SELECT result_token_sha256, result_token_expires_at FROM tasks WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
        self.assertEqual(stored["result_token_sha256"], hashlib.sha256(self.token.encode()).hexdigest())
        self.assertNotEqual(stored["result_token_sha256"], self.token)
        self.assertEqual(stored["result_token_expires_at"], "2026-10-26T03:01:00Z")

    async def test_duplicate_returns_same_receipt_and_changed_result_conflicts(self):
        async with self.client() as client:
            first = await self.post_report(client)
            duplicate = await self.post_report(client)
            conflict = await self.post_report(client, self.payload(speak="Changed report"))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(first.json()["receipt_id"], duplicate.json()["receipt_id"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.store.readback(self.task_id)["receipt_id"], first.json()["receipt_id"])

    async def test_auth_owner_expiry_and_revocation_fail_closed(self):
        async with self.client() as client:
            missing = await client.post(CALLBACK_PATH, json=self.payload())
            wrong_token = await self.post_report(client, token="not-the-task-token")
            other_owner_task, other_owner_token = self.store.prepare(
                "other-owner", "other-owner", "No access.", result_token_ttl_seconds=60
            )
            wrong_owner = await self.post_report(
                client, self.payload(task_id=other_owner_task), other_owner_token
            )
            no_ttl_task, no_ttl_token = self.store.prepare(OWNER_ID, "legacy", "No callback TTL.")
            legacy = await self.post_report(client, self.payload(task_id=no_ttl_task), no_ttl_token)
            self.assertEqual(missing.status_code, 401)
            self.assertEqual(wrong_token.status_code, 401)
            self.assertEqual(wrong_owner.status_code, 401)
            self.assertEqual(legacy.status_code, 401)
            self.assertIsNone(self.store.readback(other_owner_task)["receipt_id"])
            self.assertIsNone(self.store.readback(no_ttl_task)["receipt_id"])

            accepted = await self.post_report(client)
            self.store.revoke_token(self.task_id)
            revoked_replay = await self.post_report(client)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(revoked_replay.status_code, 401)
        self.assertEqual(self.store.readback(self.task_id)["receipt_id"], accepted.json()["receipt_id"])

    async def test_expiry_is_checked_before_duplicate_and_receipt_remains_readable(self):
        self.clock.now += timedelta(seconds=59)
        async with self.client() as client:
            first = await self.post_report(client)
            self.clock.now += timedelta(seconds=1)
            replay_at_exact_expiry = await self.post_report(client)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay_at_exact_expiry.status_code, 401)
        self.assertEqual(self.store.readback(self.task_id)["receipt_id"], first.json()["receipt_id"])

        expired_task, expired_token = self.store.prepare(
            OWNER_ID, "expired", "Must be denied.", result_token_ttl_seconds=1
        )
        self.clock.now += timedelta(seconds=1)
        async with self.client() as client:
            at_boundary = await self.post_report(client, self.payload(task_id=expired_task), expired_token)
        self.assertEqual(at_boundary.status_code, 401)
        self.assertIsNone(self.store.readback(expired_task)["receipt_id"])

    async def test_closed_task_accepts_late_receipt_while_capability_is_valid(self):
        self.store.close_task(self.task_id, "offline owner closed the task")
        async with self.client() as client:
            response = await self.post_report(client)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["late"])

    async def test_request_boundary_rejects_bad_method_path_query_host_and_forwarding_is_ignored(self):
        async with self.client() as client:
            wrong_method = await client.get(CALLBACK_PATH)
            wrong_slash = await client.post(f"{CALLBACK_PATH}/", json=self.payload(), headers=self.auth_headers())
            query = await client.post(f"{CALLBACK_PATH}?token={self.token}", json=self.payload(), headers=self.auth_headers())
        async with self.client(host="attacker.example.test") as hostile:
            wrong_host = await self.post_report(hostile)
        async with self.client() as client:
            unexpected_host_port = await client.post(
                CALLBACK_PATH,
                json=self.payload(),
                headers={**self.auth_headers(), "Host": f"{TRUSTED_HOST}:8443"},
            )
        self.assertEqual(wrong_method.status_code, 405)
        self.assertNotIn(wrong_method.status_code, (301, 302, 307, 308))
        self.assertEqual(wrong_slash.status_code, 404)
        self.assertEqual(query.status_code, 400)
        self.assertEqual(wrong_host.status_code, 400)
        self.assertEqual(unexpected_host_port.status_code, 400)
        self.assertIsNone(self.store.readback(self.task_id)["receipt_id"])

        async with self.client() as client:
            forwarded_spoof = await client.post(
                CALLBACK_PATH,
                json=self.payload(),
                headers={
                    **self.auth_headers(),
                    "Forwarded": "for=192.0.2.99;host=attacker.example.test;proto=http",
                    "X-Forwarded-Host": "attacker.example.test",
                    "X-Forwarded-Proto": "http",
                },
            )
        self.assertEqual(forwarded_spoof.status_code, 200)
        self.assertEqual(self.store.get_task(self.task_id)["owner_id"], OWNER_ID)

    async def test_body_auth_and_json_are_strict_and_bounded(self):
        body = json.dumps(self.payload()).encode()
        async with self.client() as client:
            query = await client.post(f"{CALLBACK_PATH}?x=1", content=body, headers={"Content-Type": "application/json", **self.auth_headers()})
            wrong_type = await client.post(CALLBACK_PATH, content=body, headers={"Content-Type": "text/plain", **self.auth_headers()})
            token_in_body = await client.post(
                CALLBACK_PATH,
                json={**self.payload(), "result_token": self.token},
                headers=self.auth_headers(),
            )
            owner_in_body = await client.post(
                CALLBACK_PATH,
                json={**self.payload(), "owner_id": OWNER_ID},
                headers=self.auth_headers(),
            )
            malformed = await client.post(CALLBACK_PATH, content=b"{", headers={"Content-Type": "application/json", **self.auth_headers()})
            invalid_utf8 = await client.post(CALLBACK_PATH, content=b"\xff", headers={"Content-Type": "application/json", **self.auth_headers()})
            duplicate_key = await client.post(
                CALLBACK_PATH,
                content=b'{"schema_version":"amusant-grok-pilot-0","schema_version":"amusant-grok-pilot-0"}',
                headers={"Content-Type": "application/json", **self.auth_headers()},
            )
            mismatched_length = await client.post(
                CALLBACK_PATH,
                content=body,
                headers={"Content-Type": "application/json", "Content-Length": "1", **self.auth_headers()},
            )
            oversized = await client.post(
                CALLBACK_PATH,
                content=b"x" * (CALLBACK_BODY_LIMIT + 1),
                headers={"Content-Type": "application/json", **self.auth_headers()},
            )
            streamed = await client.post(
                CALLBACK_PATH,
                content=self._oversized_stream(),
                headers={"Content-Type": "application/json", **self.auth_headers()},
            )
            duplicate_auth = await client.post(
                CALLBACK_PATH,
                json=self.payload(),
                headers=[("Authorization", f"Bearer {self.token}"), ("authorization", "Bearer another")],
            )
        self.assertEqual(query.status_code, 400)
        self.assertEqual(wrong_type.status_code, 415)
        self.assertEqual(token_in_body.status_code, 400)
        self.assertEqual(owner_in_body.status_code, 400)
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(invalid_utf8.status_code, 400)
        self.assertEqual(duplicate_key.status_code, 400)
        self.assertEqual(mismatched_length.status_code, 400)
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(streamed.status_code, 413)
        self.assertEqual(duplicate_auth.status_code, 401)
        self.assertIsNone(self.store.readback(self.task_id)["receipt_id"])

    @staticmethod
    async def _oversized_stream():
        yield b"x" * CALLBACK_BODY_LIMIT
        yield b"y"

    async def test_storage_failure_never_returns_accepted_ack(self):
        with self.store._connection() as connection:
            connection.execute(
                """CREATE TRIGGER fail_callback_receipt BEFORE INSERT ON receipts
                   BEGIN SELECT RAISE(ABORT, 'simulated offline storage failure'); END"""
            )
        async with self.client() as client:
            response = await self.post_report(client)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["accepted"])
        self.assertIsNone(self.store.readback(self.task_id)["receipt_id"])

    async def test_inflight_limit_rejects_new_request_without_waiting(self):
        app = create_callback_app(self.store, OWNER_ID, trusted_hosts=[TRUSTED_HOST], max_inflight=1)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=f"http://{TRUSTED_HOST}",
            follow_redirects=False,
            trust_env=False,
        ) as client:
            entered_body = asyncio.Event()
            release_body = asyncio.Event()

            async def blocked_body():
                entered_body.set()
                await release_body.wait()
                yield json.dumps(self.payload()).encode()

            first_task = asyncio.create_task(
                client.post(
                    CALLBACK_PATH,
                    content=blocked_body(),
                    headers={"Content-Type": "application/json", **self.auth_headers()},
                )
            )
            await asyncio.wait_for(entered_body.wait(), timeout=2)
            rejected = await client.post(
                CALLBACK_PATH, json=self.payload(), headers=self.auth_headers()
            )
            release_body.set()
            accepted = await first_task
        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(accepted.status_code, 200)

    async def test_two_concurrent_same_reports_share_one_receipt(self):
        async with self.client() as client:
            responses = await asyncio.gather(self.post_report(client), self.post_report(client))
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(len({response.json()["receipt_id"] for response in responses}), 1)
        self.assertEqual(sorted(response.json()["duplicate"] for response in responses), [False, True])


class CallbackConfigurationTests(unittest.TestCase):
    def test_requires_exact_host_owner_and_bounded_concurrency_config(self):
        db = temporary_database_path()
        store = PilotStore(db)
        try:
            for hosts, owner, limit in (
                ([], OWNER_ID, 1),
                (["*.example.test"], OWNER_ID, 1),
                (["https://callback.example.test"], OWNER_ID, 1),
                ([TRUSTED_HOST], " offline-owner", 1),
                ([TRUSTED_HOST], OWNER_ID, 0),
                ([TRUSTED_HOST], OWNER_ID, MAX_CALLBACK_INFLIGHT + 1),
            ):
                with self.subTest(hosts=hosts, owner=owner, limit=limit):
                    with self.assertRaises(ValueError):
                        create_callback_app(store, owner, trusted_hosts=hosts, max_inflight=limit)
        finally:
            cleanup_database(db)

    def test_prepare_ttl_has_no_default_and_has_a_hard_finite_bound(self):
        db = temporary_database_path()
        store = PilotStore(db)
        try:
            task_id, _ = store.prepare(OWNER_ID, "without-ttl", "Legacy G2 contract remains valid.")
            self.assertIsNone(store.get_task(task_id)["result_token_expires_at"])
            for ttl in (0, -1, MAX_RESULT_TOKEN_TTL_SECONDS + 1, True, 1.5):
                with self.subTest(ttl=ttl):
                    with self.assertRaises(ValueError):
                        store.prepare(OWNER_ID, "invalid-ttl", "Invalid TTL.", result_token_ttl_seconds=ttl)
            ttl_task, _ = store.prepare(
                OWNER_ID,
                "finite-ttl",
                "Has an explicit finite TTL.",
                result_token_ttl_seconds=MAX_RESULT_TOKEN_TTL_SECONDS,
            )
            self.assertIsNotNone(store.get_task(ttl_task)["result_token_expires_at"])
        finally:
            cleanup_database(db)


class CallbackGoldenPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_mock_webhook_callback_receipt_cli_readback_without_second_business_run(self):
        db = temporary_database_path()
        clock = MutableClock()
        store = PilotStore(db, clock=clock)
        task_id, token = store.prepare(
            OWNER_ID, "G3-P1-Golden-Path", "Run one fixed, side-effect-free mock fixture.", result_token_ttl_seconds=300
        )
        webhook_calls = 0
        fixture_runs = 0

        async def mock_routine(request: Request):
            nonlocal webhook_calls, fixture_runs
            webhook_calls += 1
            task = validate_routine_payload(await request.json())
            fixture_runs += 1
            return JSONResponse(
                {
                    "schema_version": task["schema_version"],
                    "task_id": task["task_id"],
                    "result_token": task["result_token"],
                    **report_for(task["task_id"]),
                }
            )

        mock_app = Starlette(routes=[Route("/routine", mock_routine, methods=["POST"])])
        callback_app = create_callback_app(
            store, OWNER_ID, trusted_hosts=[TRUSTED_HOST], max_inflight=4
        )
        request_body = {
            "schema_version": "amusant-grok-pilot-0",
            "task_id": task_id,
            "result_token": token,
            "task_label": "G3-P1-Golden-Path",
            "instruction": "Run one fixed, side-effect-free mock fixture.",
        }
        try:
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=mock_app),
                base_url="http://mock-routine.example.test",
                follow_redirects=False,
                trust_env=False,
            ) as mock_client:
                webhook = await mock_client.post("/routine", json=request_body)
            self.assertEqual(webhook.status_code, 200)
            mock_report = webhook.json()
            callback_payload = {key: value for key, value in mock_report.items() if key != "result_token"}

            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=callback_app),
                base_url=f"http://{TRUSTED_HOST}",
                follow_redirects=False,
                trust_env=False,
            ) as callback_client:
                first = await callback_client.post(
                    CALLBACK_PATH,
                    json=callback_payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                # Simulate a lost HTTP acknowledgement by resubmitting only the same report.
                replay = await callback_client.post(
                    CALLBACK_PATH,
                    json=callback_payload,
                    headers={"Authorization": f"Bearer {token}"},
                )

            self.assertEqual(first.status_code, 200)
            self.assertFalse(first.json()["duplicate"])
            self.assertEqual(replay.status_code, 200)
            self.assertTrue(replay.json()["duplicate"])
            self.assertEqual(first.json()["receipt_id"], replay.json()["receipt_id"])
            self.assertEqual(webhook_calls, 1)
            self.assertEqual(fixture_runs, 1)
            self.assertNotIn(token, first.text)

            reopened = PilotStore(db, clock=clock)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(cli_main(["readback", "--db", str(db), "--task-id", task_id]), 0)
            readback = json.loads(stdout.getvalue())
            self.assertEqual(readback["receipt_id"], first.json()["receipt_id"])
            self.assertEqual(readback["native_state"], "unknown")
            self.assertNotIn(token, json.dumps(readback))
            self.assertEqual(reopened.readback(task_id)["receipt_id"], first.json()["receipt_id"])
            self.assertEqual(webhook_calls, 1)
            self.assertEqual(fixture_runs, 1)
        finally:
            cleanup_database(db)


class CallbackMigrationTests(unittest.TestCase):
    def test_existing_g2_tasks_migrate_additively_and_fail_closed_for_callback(self):
        db = temporary_database_path()
        task_id = str(uuid4())
        token = "legacy-g2-task-capability"
        now = "2026-10-26T03:00:00Z"
        try:
            connection = sqlite3.connect(db)
            try:
                connection.execute(
                    """CREATE TABLE tasks (
                        task_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        task_label TEXT NOT NULL,
                        instruction TEXT NOT NULL,
                        result_token_sha256 TEXT,
                        token_revoked INTEGER NOT NULL DEFAULT 0 CHECK(token_revoked IN (0, 1)),
                        submission_state TEXT NOT NULL,
                        sender_pid INTEGER,
                        native_state TEXT NOT NULL DEFAULT 'unknown' CHECK(native_state = 'unknown'),
                        created_at TEXT NOT NULL,
                        closed_at TEXT,
                        close_reason TEXT
                    )"""
                )
                connection.execute(
                    """INSERT INTO tasks(task_id, owner_id, task_label, instruction, result_token_sha256,
                       submission_state, created_at) VALUES(?, ?, ?, ?, ?, 'prepared', ?)""",
                    (task_id, OWNER_ID, "legacy-g2", "Existing G2 task.", hashlib.sha256(token.encode()).hexdigest(), now),
                )
                connection.commit()
            finally:
                connection.close()
            if os.name != "nt":
                db.chmod(0o600)

            store = PilotStore(db)
            with store._connection() as connection:
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
            self.assertIn("result_token_expires_at", columns)
            self.assertIsNone(store.get_task(task_id)["result_token_expires_at"])

            async def check_callback_rejects_legacy_token():
                app = create_callback_app(store, OWNER_ID, trusted_hosts=[TRUSTED_HOST], max_inflight=2)
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url=f"http://{TRUSTED_HOST}",
                    trust_env=False,
                ) as client:
                    return await client.post(
                        CALLBACK_PATH,
                        json=report_for(task_id),
                        headers={"Authorization": f"Bearer {token}"},
                    )

            response = asyncio.run(check_callback_rejects_legacy_token())
            self.assertEqual(response.status_code, 401)
            self.assertIsNone(store.readback(task_id)["receipt_id"])
        finally:
            cleanup_database(db)


if __name__ == "__main__":
    unittest.main()
