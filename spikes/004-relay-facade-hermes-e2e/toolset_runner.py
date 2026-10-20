#!/usr/bin/env python3
"""Submit one synthetic run through the private Facade and keep only bounded evidence."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import time

CASE = os.environ["POC_TOOLSET_CASE"]
FACADE_KEY = os.environ["POC_FACADE_KEY"]
EVIDENCE = Path("/evidence") / f"toolset-runner-{CASE}.json"
TASK_ID = "h12-toolset-" + CASE + "-" + os.environ["POC_RUN_TAG"]
CLIENT_ID = "h12-toolset-offline-owner"
SESSION_KEY = hmac.new(FACADE_KEY.encode("utf-8"), CLIENT_ID.encode("utf-8"), hashlib.sha256).hexdigest()
RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9_-]{1,250}$")
MAX_RUN_SECONDS = 120


def request(method, path, body=None):
    connection = http.client.HTTPConnection("facade", 8643, timeout=8)
    headers = {
        "Authorization": "Bearer " + FACADE_KEY,
        "X-Hermes-Session-Key": SESSION_KEY,
        "Accept": "application/json",
    }
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    if method == "POST":
        headers["Idempotency-Key"] = TASK_ID
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read(16 * 1024)
        try:
            value = json.loads(raw or b"{}")
        except (ValueError, TypeError):
            value = {}
        return response.status, value
    finally:
        connection.close()


def write_evidence(value):
    EVIDENCE.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def main():
    evidence = {
        "case": CASE,
        "status": "UNKNOWN",
        "facade_submit_attempts": 0,
        "run_id_present": False,
        "terminal_status": None,
        "artifact_committed": False,
        "presentation_ready": False,
        "elapsed_seconds": None,
        "provider": "synthetic_fake_provider_only",
        "stop_requested": False,
        "stop_cancel_confirmed": False,
        "no_prompt_or_result_body_recorded": True,
    }
    started = time.monotonic()
    # One POST only. An ambiguous response is not retried.
    evidence["facade_submit_attempts"] = 1
    try:
        status, accepted = request("POST", "/v1/runs", {
            "input": f"[POC_CASE=h12_toolset_{CASE}] Complete the deterministic offline toolset preflight.",
        })
        run_id = accepted.get("run_id") if isinstance(accepted, dict) else None
        if status not in {200, 202} or not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            evidence["failure_code"] = "facade_submit_not_confirmed"
            write_evidence(evidence)
            return 1
        evidence["run_id_present"] = True
        deadline = started + MAX_RUN_SECONDS
        while time.monotonic() < deadline:
            get_status, result = request("GET", "/v1/runs/" + run_id)
            if get_status != 200 or not isinstance(result, dict):
                evidence["failure_code"] = "facade_readback_not_confirmed"
                write_evidence(evidence)
                return 1
            run_status = result.get("status")
            if run_status in {"completed", "failed", "cancelled", "interrupted", "expired"}:
                evidence["terminal_status"] = run_status
                if run_status == "completed" and isinstance(result.get("output"), str):
                    try:
                        output = json.loads(result["output"])
                    except (ValueError, TypeError):
                        output = {}
                    evidence["artifact_committed"] = output.get("artifact_status") == "committed"
                    evidence["presentation_ready"] = (
                        output.get("presentation_status") == "ready"
                        and output.get("execution_status") == "completed"
                        and type(output.get("schema_version")) is int
                        and output.get("schema_version") == 1
                        and isinstance(output.get("speak"), str)
                        and 1 <= len(output["speak"]) <= 240
                        and isinstance(output.get("card"), dict)
                        and isinstance(output.get("artifact_ref"), str)
                        and RUN_ID_RE.fullmatch(run_id) is not None
                    )
                break
            time.sleep(0.5)
        else:
            evidence["failure_code"] = "offline_run_deadline"
            write_evidence(evidence)
            return 1
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if evidence["terminal_status"] == "completed" and evidence["artifact_committed"] and evidence["presentation_ready"]:
            evidence["status"] = "PASS"
            write_evidence(evidence)
            return 0
        evidence["failure_code"] = "bounded_facade_result_not_ready"
        write_evidence(evidence)
        return 1
    except Exception as exc:
        evidence["failure_code"] = "runner_" + type(exc).__name__
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_evidence(evidence)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
