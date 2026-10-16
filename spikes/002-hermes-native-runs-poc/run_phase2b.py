#!/usr/bin/env python3
"""Run Phase 2B adapter gates on the pinned internal-only Hermes/fake-provider stack."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
COMPOSE = ROOT / "compose.yaml"
PROJECT = "amusant-hermes-runs-p2a-v4"
IMAGE = "docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7"
_DOCKER_ENV_KEYS = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "XDG_RUNTIME_DIR")
COMPOSE_ENV = {key: os.environ[key] for key in _DOCKER_ENV_KEYS if key in os.environ}
COMPOSE_ENV["POC_API_KEY"] = "poc-" + secrets.token_urlsafe(24)
COMPOSE_ENV["POC_FAKE_MODEL_KEY"] = "fake-" + secrets.token_urlsafe(24)
COMPOSE_ENV["POC_RUN_TAG"] = uuid.uuid4().hex[:12]


def compose(*args, check=True):
    command = ["docker", "compose", "--project-name", PROJECT, "-f", str(COMPOSE), *args]
    result = subprocess.run(command, cwd=ROOT, env=COMPOSE_ENV, capture_output=True, text=True, timeout=120)
    if check and result.returncode != 0:
        raise RuntimeError("compose " + args[0] + " failed")
    return result


def run_client(*args):
    result = compose("run", "--rm", "--no-deps", "phase2b-relay", *args, check=False)
    if result.returncode != 0:
        raise RuntimeError("relay client failed: " + (result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "nonzero exit"))
    for line in reversed(result.stdout.splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise RuntimeError("relay client returned no JSON summary")


def client(*args):
    result = compose("run", "--rm", "--no-deps", "client", *args, check=False)
    if result.returncode != 0:
        raise RuntimeError("Hermes harness client failed")
    for line in reversed(result.stdout.splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("Hermes harness client returned no JSON")


def wait_stack(timeout=120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            health = client("health")
            if health.get("api_http_status") == 200 and health.get("fake_http_status") == 200:
                return
            last = "health_not_ready"
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(1)
    raise RuntimeError("local stack health timeout: " + str(last))


def provider_stats():
    result = client("stats")
    if result.get("http_status") != 200:
        raise RuntimeError("fake provider stats unavailable")
    return result


def main():
    run_tag = COMPOSE_ENV["POC_RUN_TAG"]
    gates = {}
    started = False
    setup_error = None
    try:
        compose("--profile", "bootstrap", "run", "--rm", "--no-deps", "seed")
        compose("run", "--rm", "--no-deps", "phase2b-state-init")
        compose("up", "-d", "fake-provider")
        compose("up", "-d", "hermes")
        wait_stack()
        started = True
    except Exception as exc:
        setup_error = type(exc).__name__

    if started:
        try:
            start = run_client("start")
            gates["relay_submit_mapping"] = {
                "status": "PASS" if start.get("phase") == "paused_after_submit" and start.get("run_id_persisted") and start.get("fake_provider_active") and start.get("relay_http_methods", []).count("POST") == 1 else "FAIL",
                "relay_task_status": start.get("task_status"),
                "run_id_persisted_before_poll_resume": bool(start.get("run_id_persisted")),
                "fake_provider_active_at_relay_exit": bool(start.get("fake_provider_active")),
                "relay_submit_count_before_restart": start.get("relay_http_methods", []).count("POST"),
            }
            if gates["relay_submit_mapping"]["status"] != "PASS":
                raise RuntimeError("Relay did not exit with a persisted active Run")

            resumed = run_client("resume")
            provider_request_count = len([row for row in provider_stats().get("requests", []) if row.get("slow_case") == "client_restart"])
            resumed_methods = resumed.get("relay_http_methods", [])
            gates["relay_process_restart_recovery"] = {
                "status": "PASS" if resumed.get("task_status") == "completed" and resumed.get("result_present") and resumed.get("run_id_persisted") and resumed_methods and all(method == "GET" for method in resumed_methods) else "FAIL",
                "task_status": resumed.get("task_status"),
                "result_recovered": bool(resumed.get("result_present")),
                "existing_run_id_reused": bool(resumed.get("run_id_persisted")),
                "relay_http_methods_after_restart": resumed_methods,
                "fake_provider_requests_for_task": provider_request_count,
            }
            if gates["relay_process_restart_recovery"]["status"] != "PASS":
                raise RuntimeError("Relay task did not recover by polling the persisted Run ID")

            before = provider_stats()["count"]
            ambiguous = run_client("ambiguous")
            after = provider_stats()["count"]
            gates["ambiguous_submit_fail_closed"] = {
                "status": "PASS" if ambiguous.get("server_accepted_post") and ambiguous.get("task_status") == "uncertain" and not ambiguous.get("run_id_persisted") and ambiguous.get("restart_left_uncertain") and ambiguous.get("dispatch_after_restart") == 0 and ambiguous.get("final_status") == "uncertain" and ambiguous.get("relay_http_methods") == ["POST"] else "FAIL",
                "server_accepted_post_but_relay_lost_response": bool(ambiguous.get("server_accepted_post")),
                "task_status": ambiguous.get("task_status"),
                "run_id_persisted": bool(ambiguous.get("run_id_persisted")),
                "remains_uncertain_after_restart_recovery": bool(ambiguous.get("restart_left_uncertain")),
                "dispatch_after_restart": ambiguous.get("dispatch_after_restart"),
                "relay_http_methods_total": ambiguous.get("relay_http_methods"),
                "fake_provider_request_count_before_after": [before, after],
            }
            if gates["ambiguous_submit_fail_closed"]["status"] != "PASS":
                raise RuntimeError("ambiguous submit was not preserved as uncertain without replay")
        except Exception as exc:
            gates.setdefault("adapter_and_recovery", {"status": "FAIL", "error_type": type(exc).__name__, "reason": str(exc)[:200]})

    try:
        compose("stop", "hermes", "fake-provider", check=False)
    except Exception:
        pass

    worktree_status = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    evidence = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "working_tree_dirty_at_test": bool(worktree_status),
        "provider_count_metric": "fake provider completion requests; Relay Run submissions are separately counted from Relay HTTP methods",
        "repo_head": subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "branch": subprocess.run(["git", "-C", str(REPO_ROOT), "branch", "--show-current"], capture_output=True, text=True).stdout.strip(),
        "hermes_image": IMAGE,
        "mode": "local Docker only; internal network; deterministic fake provider; no AWS; no paid model API",
        "setup_status": "PASS" if started else "FAIL",
        "setup_error_type": setup_error,
        "gates": gates,
        "volumes_retained": True,
        "external_model_calls": 0,
    }
    evidence_dir = ROOT / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    attempt = evidence_dir / ("phase2b-" + run_tag + ".json")
    attempt.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    all_pass = started and bool(gates) and all(gate.get("status") == "PASS" for gate in gates.values())
    if all_pass:
        sanitized = {key: value for key, value in evidence.items() if key not in {"repo_head", "branch"}}
        sanitized["repo_head"] = evidence["repo_head"]
        sanitized["gates"] = gates
        (evidence_dir / "phase2b-gates-sanitized.json").write_text(json.dumps(sanitized, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(attempt), "gates": {name: item.get("status") for name, item in gates.items()}, "setup_status": evidence["setup_status"], "external_model_calls": 0}, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
