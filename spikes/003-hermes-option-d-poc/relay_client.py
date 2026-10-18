#!/usr/bin/env python3
"""Exercise the real Amusant HermesRunsExecutor through the private facade."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from relay.dispatcher import AsyncDispatcher
from relay.hermes_runs_executor import HermesRunsExecutor
from relay.hermes_result import build_hermes_run_prompt
from relay.task_store import SQLiteTaskStore, TaskAdmissionRequest

STATE = Path(os.environ.get("POC_STATE_DIR", "/relay-state"))
RUN_TAG = os.environ["POC_RUN_TAG"]
FACADE_URL = os.environ["AMUSANT_HERMES_RUNS_URL"].rstrip("/")
FACADE_KEY = os.environ["AMUSANT_HERMES_API_KEY"]
TERMINAL_TASK_STATES = {"completed", "failed", "uncertain", "expired"}


def store():
    return SQLiteTaskStore(database_path=STATE / "relay.sqlite3", max_pending_tasks=8)


def executor_options(db):
    return {
        "save_backend_run_id": db.save_backend_run_id,
        "update_poll_state": db.update_hermes_poll_state,
        "update_stop_state": db.update_hermes_stop_state,
        "run_deadline_seconds": float(os.environ["AMUSANT_HERMES_RUN_DEADLINE_SECONDS"]),
        "poll_cycle_seconds": float(os.environ["AMUSANT_HERMES_POLL_CYCLE_SECONDS"]),
        "http_timeout_seconds": float(os.environ["AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS"]),
        "poll_interval_seconds": float(os.environ["AMUSANT_HERMES_POLL_INTERVAL_SECONDS"]),
        "stop_grace_seconds": float(os.environ["AMUSANT_HERMES_STOP_GRACE_SECONDS"]),
        "stop_max_attempts": int(os.environ["AMUSANT_HERMES_STOP_MAX_ATTEMPTS"]),
        "stop_retry_base_seconds": float(os.environ["AMUSANT_HERMES_STOP_RETRY_BASE_SECONDS"]),
        "stop_retry_max_seconds": float(os.environ["AMUSANT_HERMES_STOP_RETRY_MAX_SECONDS"]),
    }


def route_name(path):
    if path == "/v1/runs":
        return "post_runs"
    if path.endswith("/stop"):
        return "post_stop"
    return "get_status"


class Audit:
    def __init__(self, transport, *, case, lose_terminal_once=False):
        self.transport = transport
        self.case = case
        self.lose_terminal_once = lose_terminal_once
        self.dropped = False
        self.requests = []
        self.report_marker = f"POC_REPORT_SECRET_{RUN_TAG}_{case}_"
        self.final_marker = f"POC_FINAL_SECRET_{RUN_TAG}_{case}_"
        self.tool_marker = f"POC_TOOL_SECRET_{RUN_TAG}_{case}_"
        self.ref_hashes = []

    def request(self, method, path, body, headers):
        data = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(FACADE_URL + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=6)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            declared_length = response.headers.get("Content-Length")
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise OSError("facade response exceeded Relay adapter cap")
        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise OSError("facade response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise OSError("facade response had an invalid shape")
        output = payload.get("output")
        presentation = None
        ref_hash = None
        if isinstance(output, str):
            try:
                value = json.loads(output)
                if isinstance(value, dict):
                    presentation = value.get("presentation_status")
                    ref = value.get("artifact_ref")
                    if isinstance(ref, str) and ref:
                        ref_hash = hashlib.sha256(ref.encode()).hexdigest()
                        self.ref_hashes.append(ref_hash)
            except (ValueError, TypeError):
                pass
        contains_report = self.report_marker.encode() in raw
        contains_final = self.final_marker.encode() in raw
        contains_tool = self.tool_marker.encode() in raw
        terminal = payload.get("status") in {"completed", "failed", "cancelled", "interrupted"}
        should_drop = (
            self.lose_terminal_once
            and not self.dropped
            and method == "GET"
            and terminal
            and isinstance(output, str)
        )
        self.requests.append({
            "method": method,
            "route": route_name(path),
            "http_status": status,
            "response_bytes": len(raw),
            "content_type": content_type,
            "body_length_matches_header": declared_length is None or declared_length.isdigit() and int(declared_length) == len(raw),
            "report_marker_in_response": contains_report,
            "final_marker_in_response": contains_final,
            "tool_marker_in_response": contains_tool,
            "presentation_status": presentation,
            "artifact_ref_hash": ref_hash,
            "delivered": not should_drop,
        })
        if should_drop:
            self.dropped = True
            raise OSError("synthetic lost terminal envelope")
        return status, payload

def make_executor(db, audit):
    transport = HermesRunsExecutor(
        url=FACADE_URL,
        api_key=FACADE_KEY,
        allow_plaintext=os.environ.get("AMUSANT_HERMES_ALLOW_PLAINTEXT") == "1",
        **executor_options(db),
    )
    # The audit reads the actual Relay-facing response body bytes and retains only
    # bounded lengths, marker booleans, and a one-way hash of any opaque reference.
    return HermesRunsExecutor(
        url=FACADE_URL,
        api_key=FACADE_KEY,
        allow_plaintext=os.environ.get("AMUSANT_HERMES_ALLOW_PLAINTEXT") == "1",
        request_json=audit.request,
        **executor_options(db),
    ), transport


def owner_for_tag(tag=None):
    return "option-d-owner-" + (tag or RUN_TAG)


def make_task(db, case, *, owner=None):
    now = datetime.now(timezone.utc)
    task_id = str(uuid4())
    client_id = owner or owner_for_tag()
    task_text = f"[POC_CASE={case}] Save and present the synthetic offline report."
    request = TaskAdmissionRequest(
        task_id=task_id,
        client_id=client_id,
        conversation_id="option-d-conversation-" + RUN_TAG,
        turn_id=1,
        call_id="option-d-call-" + task_id,
        task=task_text,
        task_kind="summarize",
        backend="poc-server-selected",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        executor_kind="hermes_runs",
    )
    db.admit(request)
    return task_id, client_id


async def wait_for_task(db, task_id, dispatcher, timeout=45):
    await dispatcher.start()
    deadline = asyncio.get_running_loop().time() + timeout
    row = db.get_by_task_id(task_id)
    while asyncio.get_running_loop().time() < deadline:
        row = db.get_by_task_id(task_id)
        if row and row.get("status") in TERMINAL_TASK_STATES:
            await dispatcher.stop()
            return row
        await asyncio.sleep(0.025)
    await dispatcher.stop()
    raise RuntimeError("task did not settle")


def result_summary(db, task_id):
    result = db.get_result(task_id)
    if result is None:
        return None
    if hasattr(result, "speak"):
        return {
            "speak_chars": len(result.speak),
            "details_chars": len(result.details_md),
            "report_marker_in_result": f"POC_REPORT_SECRET_{RUN_TAG}_" in result.speak + result.details_md,
        }
    if isinstance(result, dict):
        speak = result.get("speak", "")
        details = result.get("details_md", "")
        return {
            "speak_chars": len(speak) if isinstance(speak, str) else 0,
            "details_chars": len(details) if isinstance(details, str) else 0,
            "report_marker_in_result": f"POC_REPORT_SECRET_{RUN_TAG}_" in str(speak) + str(details),
        }
    return {"result_type": type(result).__name__}


async def run_case(case, *, lose_terminal_once=False):
    db = store()
    # Each adversarial case is a separate owner so an intentionally uncertain Run
    # cannot trigger the production single-flight gate for the next fixture.
    task_id, owner = make_task(db, case, owner=owner_for_tag(case.replace("_", "-")))
    audit_holder = {}
    transport = HermesRunsExecutor(
        url=FACADE_URL,
        api_key=FACADE_KEY,
        allow_plaintext=True,
        **executor_options(db),
    )
    audit = Audit(transport, case=case, lose_terminal_once=lose_terminal_once)
    executor, _ = make_executor(db, audit)
    dispatcher = AsyncDispatcher(db, executor, poll_interval_seconds=0.025)
    row = await wait_for_task(db, task_id, dispatcher, timeout=45)
    wire = [item for item in audit.requests if item["route"] == "get_status" and item["http_status"] < 400]
    terminal_envelope = next((item for item in reversed(wire) if item["presentation_status"] is not None), None)
    task_error_code = row.get("last_error")
    if not isinstance(task_error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,80}", task_error_code):
        task_error_code = None
    http_statuses = [
        item.get("http_status") for item in audit.requests
        if type(item.get("http_status")) is int and 100 <= item["http_status"] <= 599
    ]
    record = {
        "case": case,
        "task_status": row.get("status"),
        "task_run_id_persisted": bool(row.get("backend_run_id")),
        "result": result_summary(db, task_id),
        "http_methods": [item["method"] for item in audit.requests],
        "http_statuses": http_statuses,
        "task_error_code": task_error_code,
        "relay_max_response_bytes": max((item["response_bytes"] for item in audit.requests), default=0),
        "relay_wire_headers_valid": all(item["content_type"].startswith("application/json") and item["body_length_matches_header"] for item in audit.requests),
        "report_marker_in_relay_response": any(item["report_marker_in_response"] for item in audit.requests),
        "final_marker_in_relay_response": any(item["final_marker_in_response"] for item in audit.requests),
        "tool_marker_in_relay_response": any(item["tool_marker_in_response"] for item in audit.requests),
        "presentation_status": terminal_envelope["presentation_status"] if terminal_envelope else None,
        "artifact_ref_present": bool(terminal_envelope and terminal_envelope["artifact_ref_hash"]),
        "artifact_ref_hash": terminal_envelope["artifact_ref_hash"] if terminal_envelope else None,
        "lost_terminal_delivery_retried": audit.dropped and len(audit.ref_hashes) >= 2,
        "same_reference_after_redelivery": len(set(audit.ref_hashes)) == 1 if audit.ref_hashes else False,
    }
    with sqlite3.connect(STATE / "relay.sqlite3") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO poc_case_index(case_name, task_id, owner_name) VALUES (?, ?, ?)",
            (case, task_id, owner),
        )
    return record


def create_case_index():
    with sqlite3.connect(STATE / "relay.sqlite3") as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS poc_case_index(
               case_name TEXT PRIMARY KEY, task_id TEXT NOT NULL, owner_name TEXT NOT NULL)"""
        )


async def run_all_cases():
    create_case_index()
    cases = [
        "success_10k", "success_30k", "writer_redelivery", "no_writer", "writer_fail", "writer_slow",
        "wrong_binding", "malformed_json", "oversized_final", "missing_speech",
        "hook_exception", "hook_empty", "hook_timeout", "late_mutation",
        "lost_envelope_10k",
    ]
    results = []
    for case in cases:
        try:
            result = await run_case(case, lose_terminal_once=(case == "lost_envelope_10k"))
        except Exception as exc:
            print(json.dumps({"phase": "run_cases", "failed_case": case, "error_type": type(exc).__name__}, separators=(",", ":")))
            return 2
        results.append(result)
    print(json.dumps({"phase": "run_cases", "cases": results}, separators=(",", ":")))
    return 0


async def start_restart_case():
    db = store()
    task_id, owner = make_task(db, "restart_10k", owner=owner_for_tag("restart"))
    claimed = db.claim_due(now=datetime.now(timezone.utc), lease_seconds=60, limit=1)
    if not claimed or claimed[0].task_id != task_id:
        raise RuntimeError("restart task was not claimable")
    started = datetime.now(timezone.utc)
    db.mark_dispatch_started(task_id, now=started)
    task = replace(claimed[0], dispatch_started_at=started.isoformat())
    transport = HermesRunsExecutor(
        url=FACADE_URL, api_key=FACADE_KEY, allow_plaintext=True, **executor_options(db)
    )
    audit = Audit(transport, case="restart_10k")
    executor, _ = make_executor(db, audit)
    running = asyncio.create_task(executor.execute(task))
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        row = db.get_by_task_id(task_id)
        if row and row.get("backend_run_id"):
            run_id = row["backend_run_id"]
            with open(STATE / "restart.json", "w", encoding="utf-8") as stream:
                json.dump({"task_id": task_id, "owner": owner, "run_id": run_id}, stream)
            os.chmod(STATE / "restart.json", 0o600)
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
            print(json.dumps({
                "phase": "restart_start",
                "task_status": row.get("status"),
                "run_id_persisted": True,
                "relay_methods_before_restart": [item["method"] for item in audit.requests],
            }, separators=(",", ":")))
            return 0
        if running.done():
            try:
                result = running.result()
            except Exception as exc:
                error_code = getattr(exc, "code", "unknown")
                if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", error_code):
                    error_code = "unknown"
                print(json.dumps({
                    "phase": "restart_start",
                    "failed_case": "restart_start",
                    "error_type": type(exc).__name__,
                    "error_code": error_code,
                    "task_status": row.get("status"),
                    "run_id_persisted": bool(row.get("backend_run_id")),
                    "relay_http_methods": [item["method"] for item in audit.requests],
                    "relay_http_statuses": [item["http_status"] for item in audit.requests],
                }, separators=(",", ":")))
                return 2
            raise RuntimeError("Run settled before simulated Relay restart: " + str(result.status))
        await asyncio.sleep(0.02)
    running.cancel()
    try:
        await running
    except asyncio.CancelledError:
        pass
    raise RuntimeError("Relay did not persist Run ID before restart deadline")


async def resume_restart_case():
    state = json.loads((STATE / "restart.json").read_text(encoding="utf-8"))
    db = store()
    recovery = db.recover_after_restart()
    row = db.get_by_task_id(state["task_id"])
    if not row or row.get("backend_run_id") != state["run_id"]:
        raise RuntimeError("Run ID did not survive Relay client restart")
    transport = HermesRunsExecutor(
        url=FACADE_URL, api_key=FACADE_KEY, allow_plaintext=True, **executor_options(db)
    )
    audit = Audit(transport, case="restart_10k")
    executor, _ = make_executor(db, audit)
    dispatcher = AsyncDispatcher(db, executor, poll_interval_seconds=0.025)
    final = await wait_for_task(db, state["task_id"], dispatcher, timeout=45)
    after = db.get_by_task_id(state["task_id"])
    methods = [item["method"] for item in audit.requests]
    terminal_envelope = next((item for item in reversed(audit.requests) if item["presentation_status"] is not None), None)
    task_error_code = final.get("last_error")
    if not isinstance(task_error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,80}", task_error_code):
        task_error_code = None
    print(json.dumps({
        "phase": "restart_resume",
        "recovery_uncertain": getattr(recovery, "uncertain", None),
        "task_status": final.get("status"),
        "same_run_id": after.get("backend_run_id") == state["run_id"],
        "run_id_persisted": bool(after.get("backend_run_id")),
        "methods_after_restart": methods,
        "get_only_after_restart": bool(methods) and all(method == "GET" for method in methods),
        "report_marker_in_relay_response": any(item["report_marker_in_response"] for item in audit.requests),
        "final_marker_in_relay_response": any(item["final_marker_in_response"] for item in audit.requests),
        "tool_marker_in_relay_response": any(item["tool_marker_in_response"] for item in audit.requests),
        "artifact_ref_hash": terminal_envelope["artifact_ref_hash"] if terminal_envelope else None,
        "presentation_status": terminal_envelope["presentation_status"] if terminal_envelope else None,
        "task_error_code": task_error_code,
    }, separators=(",", ":")))
    return 0 if final.get("status") == "completed" and methods and all(m == "GET" for m in methods) else 2


async def ambiguous_submit_case():
    db = store()
    task_id, _owner = make_task(db, "ambiguous_10k", owner=owner_for_tag("ambiguous"))
    transport = HermesRunsExecutor(
        url=FACADE_URL, api_key=FACADE_KEY, allow_plaintext=True, **executor_options(db)
    )
    methods = []
    accepted = []
    def lose_acceptance(method, path, body, headers):
        methods.append(method)
        response = transport._http_request(method, path, body, headers)
        if method == "POST" and response[0] < 400:
            accepted.append(True)
            raise OSError("synthetic lost POST response after facade acceptance")
        return response
    executor = HermesRunsExecutor(
        url=FACADE_URL, api_key=FACADE_KEY, allow_plaintext=True,
        request_json=lose_acceptance, **executor_options(db)
    )
    dispatcher = AsyncDispatcher(db, executor)
    handled = await dispatcher.dispatch_available()
    row = db.get_by_task_id(task_id)
    recovery = db.recover_after_restart()
    after = db.get_by_task_id(task_id)
    retry_count = await AsyncDispatcher(db, executor).dispatch_available()
    final = db.get_by_task_id(task_id)
    result = {
        "phase": "ambiguous_submit",
        "handled": handled,
        "facade_accepted_post": bool(accepted),
        "task_status": row.get("status"),
        "run_id_persisted": bool(row.get("backend_run_id")),
        "restart_status": after.get("status"),
        "uncertain_recovery_count": getattr(recovery, "uncertain", None),
        "dispatch_after_restart": retry_count,
        "final_status": final.get("status"),
        "relay_http_methods": methods,
        "no_post_replay": methods == ["POST"] and retry_count == 0,
    }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["facade_accepted_post"] and result["no_post_replay"] and final.get("status") == "uncertain" else 2


def request_facade(method, path, *, owner_key, task_id=None, body=None):
    headers = {
        "Authorization": "Bearer " + FACADE_KEY,
        "X-Hermes-Session-Key": owner_key,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    if task_id:
        headers["Idempotency-Key"] = task_id
    request = urllib.request.Request(FACADE_URL + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=6) as response:
            raw = response.read(65537)
            return response.status, json.loads(raw or b"{}"), len(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read(65537)
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = {}
        return exc.code, payload, len(raw)


def access_and_idempotency_gate():
    transport = HermesRunsExecutor(
        url=FACADE_URL, api_key=FACADE_KEY, allow_plaintext=True,
        **executor_options(store()),
    )
    owner_name = "option-d-idempotency-owner-" + RUN_TAG
    session_key = transport._session_key(owner_name)
    task_id = "poc-idem-" + RUN_TAG
    input_value = f"[POC_CASE=no_writer] idempotency proof input {RUN_TAG}"
    first_code, first, first_bytes = request_facade(
        "POST", "/v1/runs", owner_key=session_key, task_id=task_id, body={"input": input_value},
    )
    second_code, second, second_bytes = request_facade(
        "POST", "/v1/runs", owner_key=session_key, task_id=task_id, body={"input": input_value},
    )
    conflict_code, conflict, _ = request_facade(
        "POST", "/v1/runs", owner_key=session_key, task_id=task_id, body={"input": input_value + " changed"},
    )
    run_id = first.get("run_id")
    wrong_owner_code = request_facade(
        "GET", "/v1/runs/" + quote(run_id, safe=""), owner_key="wrong-owner-" + RUN_TAG,
    )[0] if isinstance(run_id, str) else 0
    event_code = request_facade(
        "GET", "/v1/runs/" + quote(run_id, safe="") + "/events", owner_key=session_key,
    )[0] if isinstance(run_id, str) else 0
    try:
        addr = socket.getaddrinfo("hermes", 8642, type=socket.SOCK_STREAM)
        direct_unreachable = True
        for item in addr:
            try:
                with socket.create_connection((item[4][0], 8642), timeout=0.5):
                    direct_unreachable = False
                    break
            except OSError:
                continue
    except OSError:
        direct_unreachable = True
    result = {
        "phase": "access_and_idempotency",
        "first_post_status": first_code,
        "replay_post_status": second_code,
        "same_run_id_on_replay": bool(run_id and second.get("run_id") == run_id),
        "changed_payload_status": conflict_code,
        "wrong_owner_get_status": wrong_owner_code,
        "events_route_status": event_code,
        "direct_hermes_unreachable_from_relay_network": direct_unreachable,
        "max_post_response_bytes": max(first_bytes, second_bytes),
    }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if (
        first_code in {200, 202}
        and second_code in {200, 202}
        and result["same_run_id_on_replay"]
        and conflict_code == 409
        and wrong_owner_code == 404
        and event_code == 404
        and direct_unreachable
    ) else 2


def stop_gate():
    db = store()
    owner_name = owner_for_tag("stop")
    transport = HermesRunsExecutor(
        url=FACADE_URL, api_key=FACADE_KEY, allow_plaintext=True,
        **executor_options(db),
    )
    session_key = transport._session_key(owner_name)
    task_id = "poc-stop-" + RUN_TAG
    body = {"input": f"[POC_CASE=stop_10k] synthetic stop contract"}
    post_code, accepted, _ = request_facade(
        "POST", "/v1/runs", owner_key=session_key, task_id=task_id, body=body,
    )
    run_id = accepted.get("run_id")
    if post_code >= 400 or not isinstance(run_id, str):
        raise RuntimeError("facade did not confirm stop-contract Run ID")
    stop_code, stop_payload, _ = request_facade(
        "POST", "/v1/runs/" + quote(run_id, safe="") + "/stop", owner_key=session_key,
    )
    terminal = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        get_code, payload, _ = request_facade(
            "GET", "/v1/runs/" + quote(run_id, safe=""), owner_key=session_key,
        )
        status = payload.get("status") if isinstance(payload, dict) else None
        if get_code < 400 and status in {"completed", "failed", "cancelled", "interrupted", "expired"}:
            terminal = status
            break
        time.sleep(0.05)
    result = {
        "phase": "stop_contract",
        "post_confirmed": post_code in {200, 202},
        "stop_http_status": stop_code,
        "stop_ack_status": stop_payload.get("status"),
        "terminal_get_status": terminal,
        "run_id_preserved": bool(run_id),
    }
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["post_confirmed"] and stop_code == 200 and result["stop_ack_status"] in {"stopping", "cancelled", "interrupted", "completed"} and terminal else 2


async def main_async(command):
    if command == "health":
        status, payload, size = request_facade("GET", "/health", owner_key="health-probe")
        print(json.dumps({"phase": "health", "status": status, "response_bytes": size}, separators=(",", ":")))
        return 0 if status == 200 and payload.get("ok") is True else 2
    if command == "cases":
        return await run_all_cases()
    if command == "restart-start":
        return await start_restart_case()
    if command == "restart-resume":
        return await resume_restart_case()
    if command == "ambiguous":
        return await ambiguous_submit_case()
    return 2


def main():
    import sys
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in {"health", "cases", "restart-start", "restart-resume", "ambiguous"}:
        return asyncio.run(main_async(command))
    if command == "access":
        return access_and_idempotency_gate()
    if command == "stop":
        return stop_gate()
    raise SystemExit("usage: relay_client.py health|cases|restart-start|restart-resume|ambiguous|access|stop")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Do not emit task text, report bodies, reference values or authorization data.
        print(json.dumps({"error_type": type(exc).__name__}, separators=(",", ":")))
        raise SystemExit(1)
