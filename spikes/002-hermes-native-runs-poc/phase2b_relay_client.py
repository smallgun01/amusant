#!/usr/bin/env python3
"""Exercise the real Relay Hermes adapter against the internal fake-provider stack."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
import urllib.request
from uuid import uuid4

from relay.dispatcher import AsyncDispatcher
from relay.hermes_runs_executor import HermesRunsExecutor
from relay.task_store import SQLiteTaskStore, TaskAdmissionRequest

STATE_DIR = Path("/state")
RUN_TAG = os.environ["POC_RUN_TAG"]
STATE_FILE = STATE_DIR / f"phase2b-{RUN_TAG}.json"
FAKE_BASE = os.environ.get("POC_FAKE_BASE", "http://fake-provider:8000").rstrip("/")


def store():
    return SQLiteTaskStore(database_path=STATE_DIR / "relay.db", max_pending_tasks=8)


def fake_requests():
    with urllib.request.urlopen(FAKE_BASE + "/_poc/requests", timeout=3) as response:
        value = json.loads(response.read())
    return value.get("requests", []) if isinstance(value, dict) else []


def wait_for_client_restart_active(timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if any(row.get("slow_case") == "client_restart" and row.get("active") for row in fake_requests()):
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def make_executor(db, methods):
    transport = HermesRunsExecutor(
        url=os.environ["AMUSANT_HERMES_RUNS_URL"],
        api_key=os.environ["AMUSANT_HERMES_API_KEY"],
        save_backend_run_id=db.save_backend_run_id,
        allow_plaintext=os.environ.get("AMUSANT_HERMES_ALLOW_PLAINTEXT") == "1",
    )

    def record(method, path, body, headers):
        methods.append(method)
        return transport._http_request(method, path, body, headers)

    return HermesRunsExecutor(
        url=os.environ["AMUSANT_HERMES_RUNS_URL"],
        api_key=os.environ["AMUSANT_HERMES_API_KEY"],
        save_backend_run_id=db.save_backend_run_id,
        allow_plaintext=os.environ.get("AMUSANT_HERMES_ALLOW_PLAINTEXT") == "1",
        request_json=record,
    )


def new_task(db, task_text):
    now = datetime.now(timezone.utc)
    task_id = str(uuid4())
    owner = "poc-owner-" + RUN_TAG
    request = TaskAdmissionRequest(
        task_id=task_id,
        client_id=owner,
        conversation_id="poc-conversation-" + RUN_TAG,
        turn_id=1,
        call_id="poc-call-" + task_id,
        task=task_text,
        task_kind="summarize",
        backend="poc-server-selected",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        executor_kind="hermes_runs",
    )
    db.admit(request)
    STATE_FILE.write_text(json.dumps({"task_id": task_id, "owner": owner}, separators=(",", ":")), encoding="utf-8")
    os.chmod(STATE_FILE, 0o600)
    return task_id


async def start_case():
    db = store()
    task_id = new_task(db, "Synthetic [POC_CLIENT_RESTART] result-recovery task.")
    claimed = db.claim_due(now=datetime.now(timezone.utc), lease_seconds=60, limit=1)
    if not claimed or claimed[0].task_id != task_id:
        raise RuntimeError("Relay task was not claimable")
    task = claimed[0]
    db.mark_dispatch_started(task_id, now=datetime.now(timezone.utc))
    methods = []
    executor = make_executor(db, methods)
    running = asyncio.create_task(executor.execute(task))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        row = db.get_by_task_id(task_id)
        if row and row.get("backend_run_id") and wait_for_client_restart_active(timeout=0.05):
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
            print(json.dumps({"phase": "paused_after_submit", "task_status": row["status"], "run_id_persisted": True, "fake_provider_active": True, "relay_http_methods": methods}, separators=(",", ":")))
            return 0
        if running.done():
            try:
                outcome = running.result()
            except Exception:
                outcome = None
            if outcome is not None and outcome.status == "completed":
                db.complete(task_id, result=outcome.result)
            else:
                db.mark_uncertain(task_id, code="hermes_result_unconfirmed")
            print(json.dumps({"phase": "finished_before_restart", "task_status": db.get_by_task_id(task_id)["status"], "run_id_persisted": bool(db.get_by_task_id(task_id).get("backend_run_id")), "fake_provider_active": False}, separators=(",", ":")))
            return 2
        await asyncio.sleep(0.05)
    running.cancel()
    try:
        await running
    except asyncio.CancelledError:
        pass
    raise RuntimeError("Relay did not persist Run ID while fake task remained active")


async def resume_case():
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    db = store()
    task_id = state["task_id"]
    row = db.get_by_task_id(task_id)
    if not row or not row.get("backend_run_id"):
        raise RuntimeError("Relay Run ID mapping did not survive client restart")
    methods = []
    executor = make_executor(db, methods)
    dispatcher = AsyncDispatcher(db, executor, poll_interval_seconds=0.05)
    await dispatcher.start()
    try:
        deadline = asyncio.get_running_loop().time() + 40
        while asyncio.get_running_loop().time() < deadline:
            row = db.get_by_task_id(task_id)
            if row and row["status"] in {"completed", "failed", "uncertain"}:
                print(json.dumps({"phase": "resumed", "task_status": row["status"], "run_id_persisted": bool(row.get("backend_run_id")), "attempts": row["attempts"], "result_present": db.get_result(task_id) is not None, "relay_http_methods": methods}, separators=(",", ":")))
                return 0 if row["status"] == "completed" else 2
            await asyncio.sleep(0.05)
        raise RuntimeError("Relay Dispatcher did not settle resumed Hermes Run")
    finally:
        await dispatcher.stop()


def ambiguous_case():
    db = store()
    task_id = new_task(db, "Synthetic [POC_AMBIGUOUS_SUBMIT] unknown-submit task.")
    transport = HermesRunsExecutor(
        url=os.environ["AMUSANT_HERMES_RUNS_URL"],
        api_key=os.environ["AMUSANT_HERMES_API_KEY"],
        save_backend_run_id=db.save_backend_run_id,
        allow_plaintext=os.environ.get("AMUSANT_HERMES_ALLOW_PLAINTEXT") == "1",
    )

    methods = []
    accepted_post = []

    def lose_post_response(method, path, body, headers):
        methods.append(method)
        response = transport._http_request(method, path, body, headers)
        if method == "POST" and response[0] < 400:
            accepted_post.append(True)
            raise OSError("simulated lost response after server acceptance")
        return response

    executor = HermesRunsExecutor(
        url=os.environ["AMUSANT_HERMES_RUNS_URL"],
        api_key=os.environ["AMUSANT_HERMES_API_KEY"],
        save_backend_run_id=db.save_backend_run_id,
        allow_plaintext=os.environ.get("AMUSANT_HERMES_ALLOW_PLAINTEXT") == "1",
        request_json=lose_post_response,
    )
    handled = asyncio.run(AsyncDispatcher(db, executor).dispatch_available())
    row = db.get_by_task_id(task_id)
    recovered = db.recover_after_restart()
    after = db.get_by_task_id(task_id)
    retried = asyncio.run(AsyncDispatcher(db, executor).dispatch_available())
    final = db.get_by_task_id(task_id)
    print(json.dumps({"phase": "ambiguous_submit", "handled": handled, "server_accepted_post": bool(accepted_post), "task_status": row["status"], "dispatch_state": row["dispatch_state"], "run_id_persisted": bool(row.get("backend_run_id")), "restart_left_uncertain": after["status"] == "uncertain", "uncertain_recovery_count": recovered.uncertain, "dispatch_after_restart": retried, "final_status": final["status"], "relay_http_methods": methods}, separators=(",", ":")))
    return 0 if accepted_post and row["status"] == "uncertain" and after["status"] == "uncertain" and final["status"] == "uncertain" and retried == 0 and methods == ["POST"] and not row.get("backend_run_id") else 2


def main():
    if len(os.sys.argv) != 2 or os.sys.argv[1] not in {"start", "resume", "ambiguous"}:
        raise SystemExit("usage: phase2b_relay_client.py start|resume|ambiguous")
    if os.sys.argv[1] == "start":
        return asyncio.run(start_case())
    if os.sys.argv[1] == "resume":
        return asyncio.run(resume_case())
    return ambiguous_case()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__}, separators=(",", ":")), file=os.sys.stderr)
        raise SystemExit(1)
