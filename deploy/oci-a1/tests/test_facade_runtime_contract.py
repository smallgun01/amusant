from __future__ import annotations

import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "oci-a1"
OWNER_SESSION_KEY = "runtime-test-owner-key-123456"
SESSION_ID = "amusant-session-runtime-smoke-1234567890"
RUN_ID = "run_runtime_smoke_123"


class _ResponseCapture:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, value):
        self.status = value

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


class FacadeRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / (".runtime-contract-" + secrets.token_hex(6))
        root.mkdir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        state = root / "results"
        state.mkdir()
        self.secret_paths = {}
        for name, value in (
            ("facade", "test-facade-key-123456"),
            ("hermes", "test-hermes-key-123456"),
            ("writer", "test-writer-key-123456"),
        ):
            path = root / (name + ".key")
            path.write_text(value + "\n", encoding="utf-8")
            if os.name == "posix":
                path.chmod(0o400)
            self.secret_paths[name] = path

        values = {
            "AMUSANT_RESULT_STATE_DIR": str(state),
            "AMUSANT_FACADE_API_KEY_FILE": str(self.secret_paths["facade"]),
            "AMUSANT_HERMES_API_KEY_FILE": str(self.secret_paths["hermes"]),
            "AMUSANT_FACADE_WRITER_API_KEY_FILE": str(self.secret_paths["writer"]),
            "AMUSANT_FACADE_BIND_IP": "127.0.0.1",
            "AMUSANT_FACADE_WRITER_BIND_IP": "127.0.0.1",
        }
        self.env_patch = patch.dict(os.environ, values)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        self.old_modules = {
            key: sys.modules.pop(key, None)
            for key in ("hermes_plugin", "hermes_plugin.reprojection")
        }
        self.addCleanup(self._restore_plugin_modules)
        sys.path.insert(0, str(PACKAGE))
        self.addCleanup(lambda: sys.path.remove(str(PACKAGE)) if str(PACKAGE) in sys.path else None)

        spec = importlib.util.spec_from_file_location(
            "oci_a1_facade_contract_test", PACKAGE / "facade.py"
        )
        self.facade = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(self.facade)
        self.facade.initialize()
        self.facade.connect().close()
        self._seed_request(SESSION_ID, "task-runtime-smoke", RUN_ID)

        self.upstream = Mock(return_value=(200, {"status": "running"}, None, 0))
        self.upstream_patch = patch.object(self.facade, "upstream_request", self.upstream)
        self.upstream_patch.start()
        self.addCleanup(self.upstream_patch.stop)

        self.relay_server = ThreadingHTTPServer(("127.0.0.1", 0), self.facade.Handler)
        self.writer_server = ThreadingHTTPServer(("127.0.0.1", 0), self.facade.WriterHandler)
        self.relay_thread = threading.Thread(target=self.relay_server.serve_forever, daemon=True)
        self.writer_thread = threading.Thread(target=self.writer_server.serve_forever, daemon=True)
        self.relay_thread.start()
        self.writer_thread.start()
        self.addCleanup(self._stop_servers)

    def _restore_plugin_modules(self):
        for name in ("hermes_plugin", "hermes_plugin.reprojection"):
            sys.modules.pop(name, None)
            old = self.old_modules.get(name)
            if old is not None:
                sys.modules[name] = old

    def _stop_servers(self):
        for server, thread in (
            (self.writer_server, self.writer_thread),
            (self.relay_server, self.relay_thread),
        ):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def _seed_request(self, session_id, task_id, run_id):
        owner_scope = self.facade.owner_scope_from_header(OWNER_SESSION_KEY)
        with self.facade.connect() as conn:
            conn.execute(
                """INSERT INTO requests
                   (owner_scope, task_id, session_id, body_hash, run_id, submit_state,
                    artifact_status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'accepted', 'pending', 1)""",
                (owner_scope, task_id, session_id, hashlib.sha256(task_id.encode()).hexdigest(),
                 run_id),
            )

    def _request(self, port, method, path, body=None, *, key=None, session=SESSION_ID,
                 owner_key=OWNER_SESSION_KEY, extra_headers=None):
        headers = dict(extra_headers or {})
        if key == "facade":
            headers["Authorization"] = "Bearer " + self.facade.FACADE_KEY
            headers["X-Hermes-Session-Key"] = owner_key
        elif key == "writer":
            headers["Authorization"] = "Bearer " + self.facade.WRITER_KEY
            headers["X-Amusant-Session-ID"] = session
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw or b"{}"), raw
        finally:
            connection.close()

    def _relay(self, method, path, body=None, **kwargs):
        return self._request(
            self.relay_server.server_port, method, path, body, key=kwargs.pop("key", "facade"),
            **kwargs,
        )

    def _writer(self, method, path, body=None, **kwargs):
        return self._request(
            self.writer_server.server_port, method, path, body, key=kwargs.pop("key", "writer"),
            **kwargs,
        )

    def _commit_and_project(self):
        code, committed, _ = self._writer(
            "POST", "/internal/v1/artifacts", {"report": "private synthetic report"}
        )
        self.assertEqual(code, 200, committed)
        self.assertEqual(committed, {"committed": True})
        presentation = json.dumps({
            "schema_version": 1,
            "speak": "The report is ready.",
            "card": {"title": "Report ready", "lines": ["One bounded summary."]},
            "details_md": "Short legacy detail.",
        }, separators=(",", ":"))
        code, projected, _ = self._writer(
            "POST", "/internal/v1/presentation", {"presentation": presentation}
        )
        self.assertEqual(code, 200)
        self.assertNotIn("private synthetic report", json.dumps(projected))
        return presentation, projected["output"]

    def _v2_presentation(self):
        return json.dumps({
            "schema_version": 2,
            "speak": "I compared the options.",
            "card": {"title": "Comparison", "lines": ["Option A costs less."]},
        }, separators=(",", ":"))

    def _pin_v2(self, session=SESSION_ID):
        with self.facade.connect() as conn:
            conn.execute(
                "UPDATE requests SET result_contract_version=2 WHERE session_id=?", (session,)
            )

    def test_initialize_keeps_facade_state_private(self):
        if os.name != "posix":
            self.skipTest("POSIX file owner and mode checks")
        self.assertEqual(Path(self.facade.STATE).stat().st_uid, os.geteuid())
        self.assertEqual(Path(self.facade.STATE).stat().st_mode & 0o777, 0o700)
        self.assertEqual(Path(self.facade.DB_PATH).stat().st_uid, os.geteuid())
        self.assertEqual(Path(self.facade.DB_PATH).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(self.facade.ARTIFACT_DIR).stat().st_mode & 0o777, 0o700)

    def test_raw_runs_and_writer_routes_require_their_separate_credentials(self):
        code, _, _ = self._relay("GET", "/v1/runs/" + RUN_ID, key=None)
        self.assertEqual(code, 401)
        code, _, _ = self._relay(
            "POST", "/v1/runs", None, key=None,
            extra_headers={"Idempotency-Key": "task-no-auth"},
        )
        self.assertEqual(code, 401)
        code, _, _ = self._writer(
            "POST", "/internal/v1/artifacts", {"report": "private"},
            key=None,
        )
        self.assertEqual(code, 401)
        self.upstream.assert_not_called()

    def test_writer_rejects_caller_supplied_artifact_reference(self):
        code, payload, raw = self._writer(
            "POST", "/internal/v1/artifacts",
            {"report": "forged report", "artifact_ref": "art_attacker_supplied_reference"},
        )
        self.assertEqual(code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertNotIn(b"art_attacker_supplied_reference", raw)
        row = self.facade.request_by_session(SESSION_ID)
        self.assertIsNone(row["artifact_ref"])
        self.assertIsNone(self.facade.artifact_for(
            row["owner_scope"], row["task_id"], row["run_id"]
        ))

    def test_writer_request_and_response_bodies_are_bounded(self):
        oversized = _ResponseCapture()
        oversized.headers = {"Content-Length": str(self.facade.MAX_WRITER_BODY_BYTES + 1)}
        oversized.rfile = io.BytesIO(b"not-read")
        oversized.body_error = None
        oversized.close_connection = False
        body = self.facade.Handler.read_json_body(
            oversized, max_bytes=self.facade.MAX_WRITER_BODY_BYTES
        )
        self.assertIsNone(body)
        self.assertEqual(oversized.body_error, "body_over_cap")
        self.assertTrue(oversized.close_connection)

        captured = _ResponseCapture()
        self.facade.Handler.send_json(
            captured, 200, {"value": "x" * (self.facade.MAX_RELAY_BODY_BYTES + 1)}
        )
        body = captured.wfile.getvalue()
        self.assertEqual(captured.status, 502)
        self.assertLessEqual(len(body), self.facade.MAX_RELAY_BODY_BYTES)

    def test_invalid_upstream_run_ids_remain_uncertain(self):
        owner_scope = self.facade.owner_scope_from_header(OWNER_SESSION_KEY)
        invalid_values = ("run_bad/path", "run_!", "run_" + "a" * 251)
        for index, invalid in enumerate(invalid_values):
            task_id = "task-invalid-run-" + str(index)
            self.upstream.return_value = (202, {"run_id": invalid}, None, 0)
            code, payload, _ = self._relay(
                "POST", "/v1/runs", {"input": "synthetic"},
                extra_headers={"Idempotency-Key": task_id},
            )
            self.assertEqual(code, 502)
            self.assertEqual(payload["error"]["code"], "submission_uncertain")
            row = self.facade.request_by_owner_task(owner_scope, task_id)
            self.assertEqual(row["submit_state"], "uncertain")
            self.assertIsNone(row["run_id"])
            self.assertEqual(row["upstream_post_count"], 1)

    def test_report_writer_waits_for_delayed_submit_acceptance(self):
        owner_scope = self.facade.owner_scope_from_header(OWNER_SESSION_KEY)
        task_id = "task-writer-acceptance-race"
        run_id = "run_writer_acceptance_race"
        post_started = threading.Event()
        release_post = threading.Event()
        writer_observed_submitting = threading.Event()
        submit_result = []
        writer_result = []

        def delayed_post(*_args, **_kwargs):
            post_started.set()
            if not release_post.wait(timeout=4):
                return 502, None, "upstream_timeout", 0
            return 202, {"run_id": run_id}, None, 0

        self.upstream.side_effect = delayed_post

        def submit():
            submit_result.append(self._relay(
                "POST", "/v1/runs", {"input": "synthetic"},
                extra_headers={"Idempotency-Key": task_id},
            ))

        original_lookup = self.facade.request_by_session

        def observe_writer_lookup(session_id):
            row = original_lookup(session_id)
            if (
                session_id == writer_session_id
                and row and row.get("submit_state") == "submitting"
            ):
                writer_observed_submitting.set()
            return row

        submit_thread = threading.Thread(target=submit, name="relay-submit")
        writer_thread = None
        submit_thread.start()
        self.assertTrue(post_started.wait(timeout=1))
        row = self.facade.request_by_owner_task(owner_scope, task_id)
        self.assertEqual(row["submit_state"], "submitting")
        writer_session_id = row["session_id"]

        def write_report():
            writer_result.append(self._writer(
                "POST", "/internal/v1/artifacts", {"report": "early synthetic report"},
                session=row["session_id"],
            ))

        writer_thread = threading.Thread(target=write_report, name="writer-rendezvous")
        try:
            with patch.object(self.facade, "request_by_session", observe_writer_lookup):
                writer_thread.start()
                self.assertTrue(writer_observed_submitting.wait(timeout=1))
                release_post.set()
                writer_thread.join(timeout=2.5)
                submit_thread.join(timeout=1)
        finally:
            release_post.set()
            if writer_thread.is_alive():
                writer_thread.join(timeout=2)
            if submit_thread.is_alive():
                submit_thread.join(timeout=2)

        self.assertFalse(writer_thread.is_alive())
        self.assertFalse(submit_thread.is_alive())
        self.assertEqual(len(submit_result), 1)
        self.assertEqual(submit_result[0][0], 202)
        self.assertEqual(len(writer_result), 1)
        self.assertEqual(writer_result[0][0], 200)
        accepted = self.facade.request_by_owner_task(owner_scope, task_id)
        self.assertEqual(accepted["submit_state"], "accepted")
        self.assertEqual(accepted["run_id"], run_id)
        self.assertEqual(accepted["upstream_post_count"], 1)
        artifact = self.facade.artifact_for(owner_scope, task_id, run_id)
        self.assertIsNotNone(artifact)
        with self.facade.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM artifacts WHERE owner_scope=? AND task_id=? AND run_id=?",
                (owner_scope, task_id, run_id),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.upstream.assert_called_once()

    def test_presentation_writer_waits_for_acceptance_transition(self):
        presentation, expected_output = self._commit_and_project()
        with self.facade.connect() as conn:
            conn.execute(
                "UPDATE requests SET submit_state='submitting' WHERE session_id=?",
                (SESSION_ID,),
            )
        writer_observed_submitting = threading.Event()
        writer_result = []
        original_lookup = self.facade.request_by_session

        def observe_writer_lookup(session_id):
            row = original_lookup(session_id)
            if (
                session_id == SESSION_ID
                and row and row.get("submit_state") == "submitting"
            ):
                writer_observed_submitting.set()
            return row

        def project():
            writer_result.append(self._writer(
                "POST", "/internal/v1/presentation",
                {"presentation": presentation}, session=SESSION_ID,
            ))

        writer_thread = threading.Thread(target=project, name="presentation-rendezvous")
        with patch.object(self.facade, "request_by_session", observe_writer_lookup):
            writer_thread.start()
            self.assertTrue(writer_observed_submitting.wait(timeout=1))
            with self.facade.connect() as conn:
                conn.execute(
                    "UPDATE requests SET submit_state='accepted' WHERE session_id=?",
                    (SESSION_ID,),
                )
            writer_thread.join(timeout=2.5)

        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(len(writer_result), 1)
        self.assertEqual(writer_result[0][0], 200)
        self.assertEqual(writer_result[0][1]["output"], expected_output)

    def test_writer_endpoints_fail_closed_when_acceptance_stays_pending(self):
        presentation, _output = self._commit_and_project()
        for path, body in (
            ("/internal/v1/artifacts", {"report": "private synthetic report"}),
            ("/internal/v1/presentation", {"presentation": presentation}),
        ):
            with self.subTest(path=path):
                with self.facade.connect() as conn:
                    conn.execute(
                        "UPDATE requests SET submit_state='submitting' WHERE session_id=?",
                        (SESSION_ID,),
                    )
                started = time.monotonic()
                code, payload, _raw = self._writer(
                    "POST", path, body, session=SESSION_ID
                )
                elapsed = time.monotonic() - started
                with self.facade.connect() as conn:
                    conn.execute(
                        "UPDATE requests SET submit_state='accepted' WHERE session_id=?",
                        (SESSION_ID,),
                    )
                self.assertEqual(code, 503, payload)
                self.assertEqual(
                    payload["error"]["code"],
                    "trusted_request_context_unavailable",
                )
                self.assertLess(elapsed, 2.75)

    def test_report_writer_fails_closed_when_submit_ack_never_arrives(self):
        owner_scope = self.facade.owner_scope_from_header(OWNER_SESSION_KEY)
        task_id = "task-writer-acceptance-timeout"
        post_started = threading.Event()
        release_post = threading.Event()
        submit_result = []

        def delayed_failure(*_args, **_kwargs):
            post_started.set()
            release_post.wait(timeout=4)
            return 502, None, "upstream_timeout", 0

        self.upstream.side_effect = delayed_failure
        submit_thread = threading.Thread(
            target=lambda: submit_result.append(self._relay(
                "POST", "/v1/runs", {"input": "synthetic"},
                extra_headers={"Idempotency-Key": task_id},
            )),
            name="relay-submit-timeout",
        )
        submit_thread.start()
        self.assertTrue(post_started.wait(timeout=1))
        row = self.facade.request_by_owner_task(owner_scope, task_id)
        started = time.monotonic()
        try:
            code, payload, _raw = self._writer(
                "POST", "/internal/v1/artifacts",
                {"report": "must not be committed"},
                session=row["session_id"],
            )
            elapsed = time.monotonic() - started
        finally:
            release_post.set()
            submit_thread.join(timeout=2)

        self.assertFalse(submit_thread.is_alive())
        self.assertEqual(len(submit_result), 1)
        self.assertEqual(submit_result[0][0], 502)
        self.assertEqual(code, 503, payload)
        self.assertEqual(
            payload["error"]["code"],
            "trusted_request_context_unavailable",
        )
        self.assertLess(elapsed, 2.75)
        unresolved = self.facade.request_by_owner_task(owner_scope, task_id)
        self.assertEqual(unresolved["submit_state"], "uncertain")
        self.assertIsNone(unresolved["run_id"])
        self.assertIsNone(unresolved["artifact_ref"])
        self.assertEqual(unresolved["upstream_post_count"], 1)
        self.upstream.assert_called_once()

    def test_completed_get_fails_closed_when_committed_report_changes_or_disappears(self):
        _presentation, envelope = self._commit_and_project()
        self.upstream.return_value = (
            200, {"status": "completed", "output": envelope}, None, len(envelope)
        )
        code, good, _ = self._relay("GET", "/v1/runs/" + RUN_ID)
        self.assertEqual(code, 200)
        self.assertEqual(good["status"], "completed")

        owner_scope = self.facade.owner_scope_from_header(OWNER_SESSION_KEY)
        artifact = self.facade.artifact_for(owner_scope, "task-runtime-smoke", RUN_ID)
        path = Path(artifact["path"])
        original = path.read_bytes()
        for mutation in ("delete", "change"):
            with self.subTest(mutation=mutation):
                if mutation == "delete":
                    path.unlink()
                else:
                    path.write_bytes(original + b"tampered")
                code, failed, _ = self._relay("GET", "/v1/runs/" + RUN_ID)
                self.assertEqual(code, 502)
                self.assertNotEqual(failed.get("status"), "completed")
                self.assertIn(
                    failed["error"]["code"],
                    {"artifact_unavailable", "artifact_integrity_invalid"},
                )
                path.write_bytes(original)
                with self.facade.connect() as conn:
                    conn.execute(
                        "UPDATE requests SET artifact_status='committed' WHERE owner_scope=? AND task_id=?",
                        (owner_scope, "task-runtime-smoke"),
                    )

    def test_v2_answer_without_writer_attempt_is_bound_and_replays_without_artifact(self):
        self._pin_v2()
        presentation = self._v2_presentation()
        code, projected, _ = self._writer(
            "POST", "/internal/v1/presentation", {"presentation": presentation}
        )
        self.assertEqual(code, 200, projected)
        envelope = json.loads(projected["output"])
        self.assertEqual(envelope["schema_version"], 2)
        self.assertEqual(envelope["artifact_status"], "none")
        self.assertIsNone(envelope["artifact_ref"])

        self.upstream.return_value = (
            200, {"status": "completed", "output": projected["output"]}, None,
            len(projected["output"].encode("utf-8")),
        )
        headers = {"X-Amusant-Result-Contract": "2"}
        code, result, _ = self._relay("GET", "/v1/runs/" + RUN_ID, extra_headers=headers)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(json.loads(result["output"])["artifact_status"], "none")
        self.assertEqual(self.facade.request_by_session(SESSION_ID)["writer_call_count"], 0)
        with self.facade.connect() as conn:
            repair = conn.execute(
                "SELECT * FROM presentation_repairs WHERE session_hash=?",
                (hashlib.sha256(SESSION_ID.encode()).hexdigest(),),
            ).fetchone()
        self.assertIsNotNone(repair)
        self.assertEqual(repair["binding_sha256"], hashlib.sha256(
            "\0".join((self.facade.owner_scope_from_header(OWNER_SESSION_KEY), "task-runtime-smoke", SESSION_ID, RUN_ID)).encode()
        ).hexdigest())

    def test_v2_report_commit_preserves_ref_and_failed_writer_is_not_none(self):
        self._pin_v2()
        code, committed, _ = self._writer(
            "POST", "/internal/v1/artifacts", {"report": "private v2 report"}
        )
        self.assertEqual(code, 200, committed)
        code, projected, _ = self._writer(
            "POST", "/internal/v1/presentation", {"presentation": self._v2_presentation()}
        )
        self.assertEqual(code, 200, projected)
        envelope = json.loads(projected["output"])
        self.assertEqual(envelope["artifact_status"], "committed")
        reference = envelope["artifact_ref"]
        self.assertIsNotNone(reference)

        self.upstream.return_value = (200, {"status": "completed", "output": projected["output"]}, None, 0)
        code, result, _ = self._relay(
            "GET", "/v1/runs/" + RUN_ID,
            extra_headers={"X-Amusant-Result-Contract": "2"},
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(result["output"])["artifact_ref"], reference)

        other_session = "amusant-session-v2-failure-1234567890"
        self._seed_request(other_session, "task-v2-failure", "run_v2_failure")
        self._pin_v2(other_session)
        code, failed_attempt, _ = self._writer(
            "POST", "/internal/v1/artifact-attempt",
            {"code": "artifact_attempt_invalid"}, session=other_session,
        )
        self.assertEqual(code, 400)
        self.assertEqual(failed_attempt["error"]["code"], "artifact_attempt_invalid")
        code, failure_projection, _ = self._writer(
            "POST", "/internal/v1/presentation", {"presentation": self._v2_presentation()},
            session=other_session,
        )
        self.assertEqual(code, 200)
        failed_envelope = json.loads(failure_projection["output"])
        self.assertEqual(failed_envelope["artifact_status"], "failed")
        self.assertEqual(failed_envelope["presentation_status"], "failed")
        self.assertIsNone(failed_envelope["artifact_ref"])
        self.assertNotIn("private", failed_envelope["speak"].lower())

        committed_session = "amusant-session-v2-committed-conflict-1234567890"
        self._seed_request(committed_session, "task-v2-committed-conflict", "run_v2_committed_conflict")
        self._pin_v2(committed_session)
        code, committed, _ = self._writer(
            "POST", "/internal/v1/artifacts", {"report": "preserved committed report"},
            session=committed_session,
        )
        self.assertEqual(code, 200, committed)
        committed_ref = self.facade.request_by_session(committed_session)["artifact_ref"]
        code, conflict, _ = self._writer(
            "POST", "/internal/v1/artifacts", {"report": "conflicting report"},
            session=committed_session,
        )
        self.assertEqual(code, 409, conflict)
        code, projected_after_conflict, _ = self._writer(
            "POST", "/internal/v1/presentation", {"presentation": self._v2_presentation()},
            session=committed_session,
        )
        self.assertEqual(code, 200, projected_after_conflict)
        preserved = json.loads(projected_after_conflict["output"])
        self.assertEqual(preserved["artifact_status"], "committed")
        self.assertEqual(preserved["artifact_ref"], committed_ref)
        self.assertEqual(preserved["presentation_status"], "failed")

    def test_v2_flood_output_returns_bounded_delivery_error_and_pins_digests(self):
        session = "amusant-session-v2-flood-1234567890"
        task_id = "task-v2-flood"
        run_id = "run_v2_flood"
        self._seed_request(session, task_id, run_id)
        self._pin_v2(session)
        raw = "x" * 500_000
        self.upstream.return_value = (200, {"status": "completed", "output": raw}, None, len(raw))
        code, result, _ = self._relay(
            "GET", "/v1/runs/" + run_id,
            session=session,
            extra_headers={"X-Amusant-Result-Contract": "2"},
        )
        self.assertEqual(code, 200)
        output = result["output"]
        self.assertLessEqual(len(output.encode("utf-8")), self.facade.MAX_ENVELOPE_BYTES)
        self.assertNotIn(raw, output)
        failure = json.loads(output)
        self.assertEqual(failure["execution_status"], "completed")
        self.assertEqual(failure["presentation_status"], "failed")
        with self.facade.connect() as conn:
            repair = conn.execute(
                "SELECT source_sha256,output_sha256 FROM presentation_repairs WHERE session_hash=?",
                (hashlib.sha256(session.encode()).hexdigest(),),
            ).fetchone()
        self.assertEqual(repair["source_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(repair["output_sha256"], hashlib.sha256(output.encode()).hexdigest())

    def test_result_contract_version_is_pinned_at_first_submit_and_retry(self):
        task_id = "task-result-contract-pin"
        run_id = "run_result_contract_pin"
        self.upstream.return_value = (202, {"run_id": run_id}, None, 0)
        headers = {
            "Idempotency-Key": task_id,
            "X-Amusant-Result-Contract": "2",
        }
        code, submitted, _ = self._relay("POST", "/v1/runs", {"input": "fixture"}, extra_headers=headers)
        self.assertEqual(code, 202, submitted)
        row = self.facade.request_by_owner_task(
            self.facade.owner_scope_from_header(OWNER_SESSION_KEY), task_id
        )
        self.assertEqual(row["result_contract_version"], 2)
        self.assertEqual(row["run_id"], run_id)

        code, retry, _ = self._relay("POST", "/v1/runs", {"input": "fixture"}, extra_headers=headers)
        self.assertEqual(code, 200, retry)
        self.assertEqual(retry["run_id"], run_id)
        mismatch = {**headers, "X-Amusant-Result-Contract": "1"}
        code, rejected, _ = self._relay("POST", "/v1/runs", {"input": "fixture"}, extra_headers=mismatch)
        self.assertEqual(code, 409)
        self.assertEqual(rejected["error"]["code"], "idempotency_conflict")
        self.upstream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
