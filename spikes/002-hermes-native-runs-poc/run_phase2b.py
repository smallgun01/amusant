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
PROJECT = "amusant-hermes-runs-h1-v1"
IMAGE = "docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7"
HERMES_SOURCE_TAG = "v2026.9.24"
HERMES_SOURCE_COMMIT = "f97608f178d1ffeca59860195ab7da295f7c8e5f"
STOP_ROUTE_SOURCE = "gateway/platforms/api_server_runs.py"
_DOCKER_ENV_KEYS = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "XDG_RUNTIME_DIR")
COMPOSE_ENV = {key: os.environ[key] for key in _DOCKER_ENV_KEYS if key in os.environ}
COMPOSE_ENV["POC_API_KEY"] = "poc-" + secrets.token_urlsafe(24)
COMPOSE_ENV["POC_FAKE_MODEL_KEY"] = "fake-" + secrets.token_urlsafe(24)
COMPOSE_ENV["POC_RUN_TAG"] = uuid.uuid4().hex[:12]
# Explicit test-only timing values; no product/default policy is inferred.
COMPOSE_ENV["POC_RUN_DEADLINE_SECONDS"] = "60"
COMPOSE_ENV["POC_POLL_CYCLE_SECONDS"] = "2"
COMPOSE_ENV["POC_HTTP_TIMEOUT_SECONDS"] = "3"
COMPOSE_ENV["POC_POLL_INTERVAL_SECONDS"] = "0.05"
COMPOSE_ENV["POC_STOP_GRACE_SECONDS"] = "30"
COMPOSE_ENV["POC_STOP_MAX_ATTEMPTS"] = "3"
COMPOSE_ENV["POC_STOP_RETRY_BASE_SECONDS"] = "0.05"
COMPOSE_ENV["POC_STOP_RETRY_MAX_SECONDS"] = "0.2"


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


def wait_for_provider_case_active(slow_case, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [row for row in provider_stats().get("requests", []) if row.get("slow_case") == slow_case]
        if any(row.get("active") is True for row in matches):
            return matches
        time.sleep(0.05)
    raise RuntimeError("fake provider request did not become active")


def resource_snapshot():
    wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    memory = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, _, value = line.partition(":")
            if name in wanted:
                memory[name] = int(value.strip().split()[0])
        memory["loadavg"] = Path("/proc/loadavg").read_text().split()[:3]
    except Exception as exc:
        memory["read_error"] = type(exc).__name__
    return memory


def docker_resource_snapshot():
    try:
        result = subprocess.run(
            ["docker", "system", "df", "--format", "{{.Type}} {{.TotalCount}} {{.Size}} {{.Reclaimable}}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip().splitlines() if result.returncode == 0 else ["unavailable"]
    except Exception as exc:
        return ["unavailable:" + type(exc).__name__]


def poc_container_stats():
    try:
        ids = compose("ps", "-q", "hermes", "fake-provider", check=False).stdout.split()
        if not ids:
            return []
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}} {{.MemUsage}} {{.MemPerc}}", *ids],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip().splitlines() if result.returncode == 0 else ["unavailable"]
    except Exception as exc:
        return ["unavailable:" + type(exc).__name__]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    run_tag = COMPOSE_ENV["POC_RUN_TAG"]
    gates = {}
    started = False
    setup_error = None
    resources_before = resource_snapshot()
    docker_before = docker_resource_snapshot()
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

            # Focused compatibility gate against the pinned Hermes HTTP server.
            # Its source returns 200 {status: stopping} for an active Run; GET is
            # still required to observe a terminal state before slot release.
            stop_stage = "submit"
            try:
                stop_submit = client(
                    "submit",
                    "--key", "p2b-" + run_tag + "-stop-contract",
                    "--input", "Synthetic [POC_STOP_CONTRACT] cancellation contract task.",
                )
                stop_run_id = stop_submit.get("run_id")
                require(stop_submit.get("http_status", 500) < 400 and stop_run_id, "stop-contract Run was not created")
                stop_stage = "wait_for_active_fake_provider"
                wait_for_provider_case_active("stop_contract", timeout=20)
                stop_stage = "get_running_before_stop"
                before_stop = client("get", "--run-id", stop_run_id)
                stop_stage = "post_stop"
                stop_ack = client("stop", "--run-id", stop_run_id)
                stop_stage = "get_after_stop"
                after_stop = client("get", "--run-id", stop_run_id)
                stop_stage = "poll_terminal_get"
                terminal = client("get", "--run-id", stop_run_id, "--wait", "--timeout", "45")
                stop_stage = "count_fake_provider_requests"
                stop_requests = [
                    row for row in provider_stats().get("requests", [])
                    if row.get("slow_case") == "stop_contract"
                ]
                final_status = terminal.get("status")
                terminals = {"completed", "failed", "cancelled", "interrupted"}
                stop_pass = (
                    before_stop.get("status") == "running"
                    and stop_ack.get("http_status") == 200
                    and stop_ack.get("status") == "stopping"
                    and after_stop.get("status") in {"running", "stopping", *terminals}
                    and final_status in terminals
                    and len(stop_requests) == 1
                )
                gates["pinned_stop_contract"] = {
                    "status": "PASS" if stop_pass else "FAIL",
                    "source_tag": HERMES_SOURCE_TAG,
                    "source_commit": HERMES_SOURCE_COMMIT,
                    "source_file": STOP_ROUTE_SOURCE,
                    "before_stop_get_status": before_stop.get("status"),
                    "stop_http_status": stop_ack.get("http_status"),
                    "stop_ack_status": stop_ack.get("status"),
                    "get_status_after_stop": after_stop.get("status"),
                    "terminal_get_status": final_status,
                    "fake_provider_requests_for_run": len(stop_requests),
                    "completion_won_stop_race": final_status == "completed",
                }
            except Exception as exc:
                gates["pinned_stop_contract"] = {
                    "status": "FAIL",
                    "source_tag": HERMES_SOURCE_TAG,
                    "source_commit": HERMES_SOURCE_COMMIT,
                    "stage": stop_stage,
                    "error_type": type(exc).__name__,
                }
        except Exception as exc:
            gates.setdefault("adapter_and_recovery", {"status": "FAIL", "error_type": type(exc).__name__, "reason": str(exc)[:200]})

    poc_usage_before_stop = poc_container_stats()
    try:
        compose("stop", "hermes", "fake-provider", check=False)
    except Exception:
        pass

    worktree_status = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    evidence = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "command": "cd spikes/002-hermes-native-runs-poc && python3 run_phase2b.py",
        "working_tree_dirty_at_test": bool(worktree_status),
        "provider_count_metric": "fake provider completion requests; Relay Run submissions are separately counted from Relay HTTP methods",
        "repo_head": subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "branch": subprocess.run(["git", "-C", str(REPO_ROOT), "branch", "--show-current"], capture_output=True, text=True).stdout.strip(),
        "hermes_image": IMAGE,
        "hermes_source_tag": HERMES_SOURCE_TAG,
        "hermes_source_commit": HERMES_SOURCE_COMMIT,
        "stop_route_source": STOP_ROUTE_SOURCE,
        "host_resources_before": resources_before,
        "host_resources_after": resource_snapshot(),
        "docker_usage_before": docker_before,
        "poc_container_usage_before_stop": poc_usage_before_stop,
        "docker_usage_after": docker_resource_snapshot(),
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
