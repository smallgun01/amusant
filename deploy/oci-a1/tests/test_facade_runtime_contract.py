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


if __name__ == "__main__":
    unittest.main()
