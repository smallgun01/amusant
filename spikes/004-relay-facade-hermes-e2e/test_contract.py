from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import hashlib
import json
import sqlite3
import subprocess
import sys
import secrets
import shutil
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch


os.environ.setdefault("POC_RUN_TAG", "offline-contract-test")
from fake_provider import report_body, request_tool  # noqa: E402
import run_e2e  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path(__file__).resolve().parent
IMAGE = "docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7"


def service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^networks:\n)",
        compose,
    )
    if not match:
        raise AssertionError(f"Compose service is missing: {service}")
    return match.group(1)


class Issue85E2EContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (PROJECT / "compose.yaml").read_text(encoding="utf-8")
        cls.hermes_config = (PROJECT / "hermes-config.yaml").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        cls.b2_workflow = (ROOT / ".github" / "workflows" / "issue-85-pr-b2-e2e.yml").read_text(encoding="utf-8")

    def test_fake_model_generates_exact_bounded_and_large_reports(self):
        small = report_body("restart_10k")
        large = report_body("success_30k")
        self.assertEqual(len(small.encode("utf-8")), 10_000)
        self.assertEqual(len(large.encode("utf-8")), 30_000)
        self.assertIn("POC_REPORT_SECRET_", small)
        self.assertIn("POC_REPORT_SECRET_", large)

    def test_fake_model_uses_the_production_artifact_tool_name(self):
        report = {"report": report_body("restart_10k")}
        self.assertEqual(request_tool("save_report", report, ["save_report"], []), ("save_report", report))
        self.assertEqual(
            request_tool("save_report", report, ["tool_search", "tool_describe", "tool_call"], []),
            ("tool_search", {"queries": ["save_report"]}),
        )

    def test_compose_networks_keep_relay_separate_from_hermes(self):
        self.assertEqual(self.compose.count("internal: true"), 2)
        self.assertNotRegex(self.compose, r"(?m)^\s+ports\s*:")
        self.assertNotIn("external: true", self.compose)
        relay = service_block(self.compose, "relay-e2e")
        hermes = service_block(self.compose, "hermes")
        provider = service_block(self.compose, "fake-provider")
        facade = service_block(self.compose, "facade")
        self.assertRegex(relay, r"(?m)^\s+relay_api:\s*$")
        self.assertNotRegex(relay, r"(?m)^\s+hermes_api:\s*$")
        self.assertRegex(hermes, r"(?m)^\s+hermes_api:\s*$")
        self.assertNotRegex(hermes, r"(?m)^\s+relay_api:\s*$")
        self.assertRegex(provider, r"(?m)^\s+hermes_api:\s*$")
        self.assertRegex(facade, r"(?m)^\s+relay_api:\s*$")
        self.assertRegex(facade, r"(?m)^\s+hermes_api:\s*$")

    def test_integrated_route_probe_checks_raw_hermes_dns_port_get_and_sse(self):
        relay_e2e = (PROJECT / "relay_e2e.py").read_text(encoding="utf-8")
        for check in (
            'socket.getaddrinfo("hermes", 8642',
            'socket.create_connection(("172.30.64.3", 8642)',
            '_raw_hermes_http_denied("/health")',
            '"/v1/runs/run_synthetic_probe/events"',
            'accept="text/event-stream"',
        ):
            self.assertIn(check, relay_e2e)

    def test_runtime_factory_is_synchronous(self):
        tree = ast.parse((PROJECT / "relay_e2e.py").read_text(encoding="utf-8"))
        runtime_factory = next(
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "start_runtime"
        )
        self.assertIsInstance(runtime_factory, ast.FunctionDef)

    def test_open_turn_sends_audio_before_waiting_for_live_provider(self):
        relay_e2e = (PROJECT / "relay_e2e.py").read_text(encoding="utf-8")
        open_turn = relay_e2e.split("async def open_turn", 1)[1].split(
            "async def invoke_tool", 1
        )[0]
        audio_send = open_turn.index('await websocket.send(b"\\x00" * MIN_PCM_BYTES)')
        provider_wait = open_turn.index("provider = await eventually(")
        self.assertLess(audio_send, provider_wait)

    def test_single_flight_busy_probe_uses_a_new_live_session(self):
        relay_e2e = (PROJECT / "relay_e2e.py").read_text(encoding="utf-8")
        slow_phase = relay_e2e.split('PHASE = "slow-restart-admission"', 1)[1].split(
            'PHASE = "drop-completed-envelope"', 1
        )[0]
        first_session_close = slow_phase.index("await slow_ws.close()")
        busy_session_open = slow_phase.index("busy_ws, busy_provider = await open_turn(")
        self.assertLess(first_session_close, busy_session_open)
        self.assertIn('busy_provider, "b2-restart-busy", "delegate_task"', slow_phase)

    def test_cancel_probe_uses_a_new_live_session_after_delegate_action(self):
        relay_e2e = (PROJECT / "relay_e2e.py").read_text(encoding="utf-8")
        stop_phase = relay_e2e.split('PHASE = "stop-run"', 1)[1].split(
            'PHASE = "ambiguous-submit"', 1
        )[0]
        first_session_close = stop_phase.index("await stop_ws.close()")
        cancel_session_open = stop_phase.index(
            "stop_request_ws, stop_request_provider = await open_turn("
        )
        self.assertLess(first_session_close, cancel_session_open)
        self.assertIn('stop_request_provider, "b2-stop-request", "cancel_task"', stop_phase)

    def test_stop_probe_distinguishes_stop_handling_from_confirmed_cancellation(self):
        relay_e2e = (PROJECT / "relay_e2e.py").read_text(encoding="utf-8")
        stop_phase = relay_e2e.split('PHASE = "stop-run"', 1)[1].split(
            'PHASE = "ambiguous-submit"', 1
        )[0]
        self.assertIn('value.get("status") in {"completed", "failed", "uncertain"}', stop_phase)
        self.assertIn('stop_terminal_error_code = stop_row.get("last_error")', stop_phase)
        self.assertIn('stop_outcome = classify_stop_outcome(', stop_phase)
        self.assertIn('CHECKS["stop_hermes_status"] = diagnostic_token(audit.stop_remote_status)', stop_phase)
        self.assertIn('CHECKS["stop_hermes_error_category"] = diagnostic_token(audit.stop_remote_error_category)', stop_phase)
        self.assertIn('CHECKS["stop_hermes_exit_category"] = diagnostic_token(audit.stop_remote_exit_category)', stop_phase)
        self.assertIn('CHECKS["stop_hermes_interrupted"] = diagnostic_token(audit.stop_remote_interrupted)', stop_phase)
        self.assertIn('CHECKS["stop_hermes_partial"] = diagnostic_token(audit.stop_remote_partial)', stop_phase)
        self.assertIn('store2.lookup_inbox(client_id=CLIENT_ID, task_id=stop_task_id)', stop_phase)
        self.assertIn('CHECKS["stop_terminal_outcome"] = stop_outcome or "unclassified"', stop_phase)
        self.assertIn('CHECKS["stop_cancel_confirmed"] = "true" if stop_outcome == "cancelled" else "false"', stop_phase)
        self.assertIn('CHECKS["stop_handling"] = "PASS"', stop_phase)
        self.assertIn('CHECKS["stop_terminal_error_code"] = diagnostic_token(stop_terminal_error_code)', stop_phase)
        self.assertIn('CHECKS["stop_visible_status"] = diagnostic_token(', stop_phase)
        self.assertIn('CHECKS["stop_visible_code"] = diagnostic_token(', stop_phase)
        self.assertIn('CHECKS["stop_fake_model_request_active"] = "PASS"', stop_phase)
        self.assertIn('CHECKS["stop_fixture_release_signaled_after_stopping_observed"] = (', stop_phase)
        self.assertIn('audit.stop_provider_release_signaled', stop_phase)
        self.assertIn('self.stop_remote_status in {', relay_e2e)
        self.assertIn('"stopping", "cancelled", "interrupted"', relay_e2e)
        self.assertIn('self.stop_post_accepted = 200 <= status < 300', relay_e2e)
        self.assertIn('lambda: audit.stop_post_completed', stop_phase)
        self.assertIn('CHECKS["stop_hermes_stop_http_status"] = diagnostic_token(', stop_phase)
        self.assertLess(
            stop_phase.index("stop_row = await wait_row("),
            stop_phase.index('CHECKS["stop_fixture_release_signaled_after_stopping_observed"] = ('),
        )
        self.assertIn("audit.stop_calls == 1", stop_phase)
        self.assertIn("audit.stop_targeted_posts == 1", stop_phase)
        self.assertIn("stop_submit_attempts == 1", stop_phase)
        self.assertIn("audit.stop_remote_terminal_after_accepted_stop", stop_phase)
        self.assertIn("Relay did not send a Hermes stop POST before terminal state ", stop_phase)
        self.assertIn('stop_receipt.get("status") == "stopping"', stop_phase)

    def test_stop_readme_distinguishes_handling_from_actual_cancellation(self):
        readme = (PROJECT / "README.md").read_text(encoding="utf-8")
        self.assertIn("stop_terminal_outcome=interrupt_failed", readme)
        self.assertIn("stop_cancel_confirmed=false", readme)
        self.assertIn("stop-handling PASS", readme)
        self.assertIn("open post-E1 / pre-live-canary gate", readme)

    def test_stop_fake_request_outlasts_relay_poll_cycle(self):
        provider_service = service_block(self.compose, "fake-provider")
        fake_provider = (PROJECT / "fake_provider.py").read_text(encoding="utf-8")
        relay_e2e = (PROJECT / "relay_e2e.py").read_text(encoding="utf-8")
        stop_delay = float(re.search(r'FAKE_STOP_DELAY_SECONDS: "([0-9.]+)"', provider_service).group(1))
        poll_cycle = float(re.search(r'"AMUSANT_HERMES_POLL_CYCLE_SECONDS": "([0-9.]+)"', relay_e2e).group(1))
        compose_poll_cycle = float(re.search(r'AMUSANT_HERMES_POLL_CYCLE_SECONDS: "([0-9.]+)"', self.compose).group(1))
        poll_wait = float(re.search(r"STOP_POLL_CYCLE_WAIT_SECONDS = ([0-9.]+)", relay_e2e).group(1))
        dispatch_timeout = float(re.search(r"STOP_DISPATCH_TIMEOUT_SECONDS = ([0-9.]+)", relay_e2e).group(1))
        stop_grace = float(re.search(r'"AMUSANT_HERMES_STOP_GRACE_SECONDS": "([0-9.]+)"', relay_e2e).group(1))
        compose_stop_grace = float(re.search(r'AMUSANT_HERMES_STOP_GRACE_SECONDS: "([0-9.]+)"', self.compose).group(1))
        self.assertEqual(stop_delay, 60.0)
        self.assertEqual(poll_cycle, 15.0)
        self.assertEqual(poll_wait, 16.0)
        self.assertEqual(compose_poll_cycle, poll_cycle)
        self.assertEqual(stop_grace, 30.0)
        self.assertEqual(compose_stop_grace, stop_grace)
        self.assertGreater(stop_delay, poll_cycle * 2)
        self.assertGreater(poll_wait, poll_cycle)
        self.assertGreater(stop_grace, poll_cycle)
        self.assertGreater(dispatch_timeout, poll_cycle)
        self.assertIn('STOP_DELAY = float(os.environ.get("FAKE_STOP_DELAY_SECONDS", "60"))', fake_provider)
        self.assertIn('if case == "stop_10k" and not called:', fake_provider)
        self.assertIn('active_marker.write_text("active", encoding="ascii")', fake_provider)
        self.assertIn('release_marker.unlink()', fake_provider)
        self.assertIn('"stop_10k.release"', relay_e2e)
        self.assertIn('${E2E_EVIDENCE_DIR:?required temporary evidence directory}:/evidence', self.compose)

    def test_runtime_pins_hermes_and_uses_only_the_local_fake_model(self):
        self.assertIn(IMAGE, self.compose)
        self.assertIn(IMAGE, (PROJECT / "run_e2e.py").read_text(encoding="utf-8"))
        self.assertIn("provider: custom", self.hermes_config)
        self.assertIn("base_url: http://fake-provider:8000/v1", self.hermes_config)
        self.assertIn("POC_FAKE_MODEL_KEY", self.compose)
        self.assertNotIn("GOOGLE_API_KEY", self.compose)
        self.assertNotIn("OPENAI_API_KEY", self.compose)

    def test_native_e2e_workflow_is_path_selective_and_manual(self):
        self.assertNotIn("issue-85-relay-facade-hermes-e2e:", self.workflow)
        trigger = self.b2_workflow.split("jobs:", 1)[0]
        for event in ("pull_request:", "push:", "workflow_dispatch:"):
            self.assertIn(event, trigger)
        for relevant_path in (
            '".github/workflows/**"',
            '"deploy/oci-a1/**"',
            '"fast_voice/**"',
            '"relay/**"',
            '"spikes/004-relay-facade-hermes-e2e/**"',
            '"pyproject.toml"',
            '"uv.lock"',
        ):
            self.assertIn(relevant_path, trigger)
        self.assertIn("runs-on: ubuntu-24.04-arm", self.b2_workflow)
        self.assertIn("python3 spikes/004-relay-facade-hermes-e2e/run_e2e.py", self.b2_workflow)
        self.assertIn("name: issue-85-pr-b2-integrated-e2e-${{ github.run_id }}", self.b2_workflow)
        self.assertIn("path: spikes/004-relay-facade-hermes-e2e/evidence/phase1b-e1.json", self.b2_workflow)
        self.assertIn("persist-credentials: false", self.b2_workflow)
        preflight = self.b2_workflow.split("- name: Verify Docker Compose is available", 1)[1].split(
            "- name: Check the integrated proof contract", 1
        )[0]
        self.assertIn("continue-on-error: true", preflight)

    def test_runner_records_unknown_before_temp_setup_without_native_docker(self):
        state = PROJECT / (".runner-contract-" + secrets.token_hex(6))
        state.mkdir()
        try:
            evidence = state / "evidence.json"
            with (
                patch.object(run_e2e, "EVIDENCE", evidence),
                patch.object(run_e2e, "run_capture", side_effect=FileNotFoundError),
                patch.object(run_e2e.tempfile, "TemporaryDirectory", side_effect=AssertionError),
                redirect_stdout(StringIO()),
            ):
                code = run_e2e.main()
            result = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertEqual(result["failure_stage"], "docker-compose-version")
            self.assertEqual(result["failure_type"], "docker_cli_unavailable")
        finally:
            shutil.rmtree(state, ignore_errors=True)

    def _run_facade_audit(self, *, mismatch=False):
        state = PROJECT / (".audit-contract-" + secrets.token_hex(6))
        state.mkdir()
        try:
            artifact_dir = state / "artifacts"
            artifact_dir.mkdir()
            database = state / "facade-ledger.sqlite3"
            reports = (
                ("task-10k", "session-10k", "run_10k", "art_10k", b"A" * 10_000),
                ("task-30k", "session-30k", "run_30k", "art_30k", b"B" * 30_000),
            )
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE requests (
                        owner_scope TEXT, task_id TEXT, session_id TEXT, run_id TEXT,
                        submit_state TEXT, artifact_status TEXT, artifact_ref TEXT,
                        upstream_post_count INTEGER, writer_report_bytes INTEGER,
                        raw_output_bytes INTEGER, created_at REAL
                    );
                    CREATE TABLE artifacts (
                        artifact_ref TEXT, owner_scope TEXT, task_id TEXT, session_id TEXT,
                        run_id TEXT, path TEXT, sha256 TEXT, byte_length INTEGER
                    );
                    """
                )
                for index, (task_id, session_id, run_id, reference, report) in enumerate(reports):
                    name = f"report-{index}.bin"
                    (artifact_dir / name).write_bytes(report)
                    owner_scope = f"owner-{index}"
                    artifact_task_id = "spoofed-task" if mismatch and index == 1 else task_id
                    connection.execute(
                        "INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (owner_scope, task_id, session_id, run_id, "accepted", "committed", reference, 1, len(report), 240, index),
                    )
                    connection.execute(
                        "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)",
                        (reference, owner_scope, artifact_task_id, session_id, run_id,
                         f"artifacts/{name}", hashlib.sha256(report).hexdigest(), len(report)),
                    )
            connection.close()
            evidence = state / "evidence" / "audit.json"
            environment = dict(os.environ)
            environment["FACADE_STATE_DIR"] = str(state)
            environment["E2E_EVIDENCE_PATH"] = str(evidence)
            result = subprocess.run(
                [sys.executable, str(PROJECT / "audit_facade.py")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            return result.returncode, json.loads(evidence.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(state, ignore_errors=True)

    def test_facade_audit_accepts_bound_artifacts_and_verifies_digests(self):
        return_code, evidence = self._run_facade_audit()
        self.assertEqual(return_code, 0)
        self.assertEqual(evidence["status"], "PASS")
        self.assertTrue(evidence["checks"]["artifact_request_bindings_verified"])
        self.assertTrue(evidence["checks"]["committed_report_digests_verified"])

    def test_facade_audit_rejects_artifact_bound_to_a_different_task(self):
        return_code, evidence = self._run_facade_audit(mismatch=True)
        self.assertEqual(return_code, 1)
        self.assertEqual(evidence["status"], "FAIL")
        self.assertFalse(evidence["checks"]["artifact_request_bindings_verified"])


if __name__ == "__main__":
    unittest.main()
