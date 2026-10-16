from __future__ import annotations

import asyncio
import unittest

from relay.application import load_relay_process_config
from relay.hermes_runs_executor import (
    HermesConfigError,
    HermesRequestError,
    HermesRunsExecutor,
    load_hermes_runs_executor,
)
from relay.task_store import ClaimedTask


class HermesRunsExecutorTests(unittest.TestCase):
    def make_task(self, *, owner="client-a", run_id=None):
        return ClaimedTask(
            task_id="task-01",
            client_id=owner,
            conversation_id="conversation-01",
            turn_id=1,
            call_id="call-01",
            task="summarize the local fixture",
            task_kind="summarize",
            backend="server-selected",
            attempts=1,
            executor_kind="hermes_runs",
            backend_run_id=run_id,
        )

    def make_executor(self, request, saved, **kwargs):
        return HermesRunsExecutor(
            url="http://127.0.0.1:8642",
            api_key="local-test-api-key-123",
            save_backend_run_id=lambda task_id, run_id: saved.append((task_id, run_id)),
            poll_interval_seconds=0.001,
            request_json=request,
            **kwargs,
        )

    def test_submit_persists_run_id_before_poll_and_bounds_result(self):
        saved = []
        calls = []

        def request(method, path, body, headers):
            calls.append((method, path, body, headers))
            if method == "POST":
                return 200, {"run_id": "run-123"}
            self.assertEqual(saved, [("task-01", "run-123")])
            return 200, {"status": "completed", "output": "A bounded deterministic result."}

        executor = self.make_executor(request, saved)
        outcome = asyncio.run(executor.execute(self.make_task()))
        self.assertEqual(outcome.status, "completed")
        self.assertIn("bounded deterministic result", outcome.result.details_md)
        self.assertEqual([call[0] for call in calls], ["POST", "GET"])
        self.assertEqual(calls[0][1], "/v1/runs")
        self.assertEqual(calls[0][3]["Idempotency-Key"], "task-01")
        self.assertEqual(calls[1][1], "/v1/runs/run-123")
        self.assertNotEqual(calls[0][3]["X-Hermes-Session-Key"], "client-a")

    def test_restart_resumes_known_run_id_without_resubmitting(self):
        calls = []
        saved = []

        def request(method, path, body, headers):
            calls.append((method, path))
            return 200, {"status": "completed", "output": "Recovered after restart."}

        outcome = asyncio.run(self.make_executor(request, saved).execute(self.make_task(run_id="run-known")))
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(calls, [("GET", "/v1/runs/run-known")])
        self.assertEqual(saved, [])

    def test_ambiguous_submit_is_not_replayed_or_polled(self):
        calls = []

        def request(method, path, body, headers):
            calls.append(method)
            raise OSError("simulated lost response")

        with self.assertRaises(HermesRequestError):
            asyncio.run(self.make_executor(request, []).execute(self.make_task()))
        self.assertEqual(calls, ["POST"])

    def test_submit_4xx_is_known_failure(self):
        executor = self.make_executor(lambda *args: (422, {"error": "rejected"}), [])
        outcome = asyncio.run(executor.execute(self.make_task()))
        self.assertEqual((outcome.status, outcome.code), ("failed", "hermes_submit_rejected"))

    def test_terminal_failure_and_interruption_are_distinct(self):
        failed = self.make_executor(lambda *args: (200, {"status": "failed"}), [])
        failed_outcome = asyncio.run(failed.execute(self.make_task(run_id="run-1")))
        self.assertEqual((failed_outcome.status, failed_outcome.code), ("failed", "hermes_failed"))

        interrupted = self.make_executor(lambda *args: (200, {"status": "interrupted"}), [])
        interrupted_outcome = asyncio.run(interrupted.execute(self.make_task(run_id="run-2")))
        self.assertEqual((interrupted_outcome.status, interrupted_outcome.code), ("uncertain", "hermes_interrupted"))

    def test_missing_run_or_result_confirmation_stays_unknown(self):
        executor = self.make_executor(lambda *args: (200, {"status": "running"}), [], timeout_seconds=0.01)
        with self.assertRaises(HermesRequestError):
            asyncio.run(executor.execute(self.make_task(run_id="run-1")))

        no_result = self.make_executor(lambda *args: (200, {"status": "completed"}), [])
        outcome = asyncio.run(no_result.execute(self.make_task(run_id="run-3")))
        self.assertEqual((outcome.status, outcome.code), ("uncertain", "hermes_result_unconfirmed"))

        denied_status = self.make_executor(lambda *args: (403, {"error": "forbidden"}), [])
        denied = asyncio.run(denied_status.execute(self.make_task(run_id="run-4")))
        self.assertEqual((denied.status, denied.code), ("uncertain", "hermes_result_unconfirmed"))

    def test_owner_session_namespace_is_stable_and_isolated(self):
        executor = self.make_executor(lambda *args: (200, {"status": "completed", "output": "ok"}), [])
        first = executor._session_key("client-a")
        self.assertEqual(first, executor._session_key("client-a"))
        self.assertNotEqual(first, executor._session_key("client-b"))

    def test_private_http_and_origin_validation(self):
        with self.assertRaises(HermesConfigError):
            HermesRunsExecutor(url="http://hermes.internal:8642", api_key="local-test-api-key-123", save_backend_run_id=lambda *_: None)
        with self.assertRaises(HermesConfigError):
            HermesRunsExecutor(url="https://example.com/path", api_key="local-test-api-key-123", save_backend_run_id=lambda *_: None)
        executor = HermesRunsExecutor(
            url="http://hermes:8642", api_key="local-test-api-key-123",
            save_backend_run_id=lambda *_: None, allow_plaintext=True,
            request_json=lambda *args: (200, {"status": "completed", "output": "ok"}),
        )
        self.assertIsNotNone(executor)

    def test_loader_requires_pair_and_server_executor_selection_is_bounded(self):
        self.assertIsNone(load_hermes_runs_executor({}, save_backend_run_id=lambda *_: None))
        with self.assertRaises(HermesConfigError):
            load_hermes_runs_executor({"AMUSANT_HERMES_API_KEY": "local-test-api-key-123"}, save_backend_run_id=lambda *_: None)

        common = {
            "AMUSANT_CLIENT_ID": "client-a",
            "AMUSANT_BACKEND_ID": "server-backend",
            "AMUSANT_TASK_DB": "/tmp/tasks.db",
        }
        config = load_relay_process_config({**common, "AMUSANT_SLOW_EXECUTOR": "hermes_runs"}, expected_device_token="device-token-123")
        self.assertEqual(config.slow_executor_kind, "hermes_runs")
        with self.assertRaises(ValueError):
            load_relay_process_config({**common, "AMUSANT_SLOW_EXECUTOR": "shell"}, expected_device_token="device-token-123")


if __name__ == "__main__":
    unittest.main()
