from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from uuid import uuid4

from relay.task_store import SQLiteTaskStore, TaskAdmissionRequest


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "posix", "SIGTERM process behavior is exercised on POSIX CI")
class LiveBridgeSigtermProcessTests(unittest.TestCase):
    def test_sigterm_closes_listener_drains_boundedly_and_keeps_run_id_without_repost(self):
        state = {"posts": 0, "gets": 0}
        state_lock = threading.Lock()
        post_seen = threading.Event()
        get_started = threading.Event()

        class HermesHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def _send(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_connection_close()
                self.end_headers()
                self.wfile.write(body)

            def send_connection_close(self):
                self.send_header("Connection", "close")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                with state_lock:
                    state["posts"] += 1
                self._send({"run_id": "offline-sigterm-run"})
                post_seen.set()

            def do_GET(self):
                get_started.set()
                with state_lock:
                    state["gets"] += 1
                # Keep one real executor request in flight while SIGTERM arrives.
                time.sleep(0.8)
                self._send({"status": "running"})

        hermes = ThreadingHTTPServer(("127.0.0.1", 0), HermesHandler)
        hermes.daemon_threads = True
        hermes_thread = threading.Thread(target=hermes.serve_forever, daemon=True)
        hermes_thread.start()

        with tempfile.TemporaryDirectory(prefix="amusant-sigterm-") as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            task_id = str(uuid4())
            now = datetime.now(timezone.utc)
            store = SQLiteTaskStore(database_path=db_path, max_pending_tasks=8)
            store.admit(TaskAdmissionRequest(
                task_id=task_id,
                client_id="offline-owner",
                conversation_id="offline-conversation",
                turn_id=1,
                call_id="offline-call-1",
                task="summarize the local shutdown fixture",
                task_kind="summarize",
                backend="offline-hermes",
                created_at=now,
                expires_at=now + timedelta(minutes=5),
                executor_kind="hermes_runs",
            ))

            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                relay_port = listener.getsockname()[1]

            env = dict(os.environ)
            env.update({
                "AMUSANT_DEVICE_TOKEN": "offline-device-token-123456",
                "AMUSANT_LIVE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "offline-unused-gemini-key",
                "AMUSANT_CLIENT_ID": "offline-owner",
                "AMUSANT_BACKEND_ID": "offline-backend",
                "AMUSANT_TASK_STORE": "sqlite",
                "AMUSANT_TASK_DB": str(db_path),
                "AMUSANT_SLOW_EXECUTOR": "hermes_runs",
                "AMUSANT_HERMES_RUNS_URL": f"http://127.0.0.1:{hermes.server_port}",
                "AMUSANT_HERMES_API_KEY": "offline-hermes-api-key-123",
                "AMUSANT_HERMES_ALLOW_PLAINTEXT": "1",
                "AMUSANT_HERMES_RUN_DEADLINE_SECONDS": "60",
                "AMUSANT_HERMES_POLL_CYCLE_SECONDS": "10",
                "AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS": "2",
                "AMUSANT_HERMES_POLL_INTERVAL_SECONDS": "0.1",
                "AMUSANT_HERMES_STOP_GRACE_SECONDS": "1",
                "AMUSANT_HERMES_STOP_MAX_ATTEMPTS": "2",
                "AMUSANT_HERMES_STOP_RETRY_BASE_SECONDS": "0.1",
                "AMUSANT_HERMES_STOP_RETRY_MAX_SECONDS": "0.2",
                "AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS": "0.2",
            })
            process = subprocess.Popen(
                [sys.executable, "-m", "relay.live_bridge", "--host", "127.0.0.1", "--port", str(relay_port)],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            def wait_for_listener(child, port):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and child.poll() is None:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            return
                    except OSError:
                        time.sleep(0.02)
                self.fail("Relay listener did not start")

            try:
                wait_for_listener(process, relay_port)
                self.assertTrue(post_seen.wait(10), "the seeded task was not submitted to the local Hermes fixture")
                self.assertTrue(get_started.wait(5), "the Hermes Run was not polled")

                run_id_deadline = time.monotonic() + 5
                task_row = None
                while time.monotonic() < run_id_deadline:
                    task_row = store.get_by_task_id(task_id)
                    if task_row and task_row.get("backend_run_id") == "offline-sigterm-run":
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(task_row)
                self.assertEqual(task_row.get("backend_run_id"), "offline-sigterm-run")

                started = time.monotonic()
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=6)
                elapsed = time.monotonic() - started
                self.assertEqual(process.returncode, 0, msg=f"stdout={stdout}\nstderr={stderr}")
                self.assertLess(elapsed, 5)
                self.assertNotIn("Traceback", stderr)
                self.assertNotIn("CancelledError", stderr)

                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", relay_port), timeout=0.2)
                time.sleep(0.05)
                with state_lock:
                    self.assertEqual(state["posts"], 1, "shutdown must not submit the same task twice")

                reopened = SQLiteTaskStore(database_path=db_path, max_pending_tasks=8)
                recovered = reopened.get_by_task_id(task_id)
                self.assertEqual(recovered["backend_run_id"], "offline-sigterm-run")

                with state_lock:
                    prior_gets = state["gets"]
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    restart_port = listener.getsockname()[1]
                restarted = subprocess.Popen(
                    [sys.executable, "-m", "relay.live_bridge", "--host", "127.0.0.1", "--port", str(restart_port)],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    wait_for_listener(restarted, restart_port)
                    get_deadline = time.monotonic() + 5
                    while time.monotonic() < get_deadline:
                        with state_lock:
                            resumed_gets = state["gets"]
                            submitted = state["posts"]
                        if resumed_gets > prior_gets:
                            break
                        if restarted.poll() is not None:
                            break
                        time.sleep(0.02)
                    self.assertGreater(resumed_gets, prior_gets, "restart should resume the known Run by GET")
                    self.assertEqual(submitted, 1, "a persisted Run ID must never be submitted again")
                    restarted.send_signal(signal.SIGTERM)
                    restart_stdout, restart_stderr = restarted.communicate(timeout=6)
                    self.assertEqual(restarted.returncode, 0, msg=f"stdout={restart_stdout}\nstderr={restart_stderr}")
                    self.assertNotIn("Traceback", restart_stderr)
                    self.assertNotIn("CancelledError", restart_stderr)
                    with self.assertRaises(OSError):
                        socket.create_connection(("127.0.0.1", restart_port), timeout=0.2)
                finally:
                    if restarted.poll() is None:
                        restarted.kill()
                        restarted.communicate(timeout=5)

                with state_lock:
                    self.assertEqual(state["posts"], 1, "the restarted Relay must continue GET-only")
                with sqlite3.connect(db_path) as connection:
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
                hermes.shutdown()
                hermes.server_close()
                hermes_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
