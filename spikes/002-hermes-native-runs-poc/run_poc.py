#!/usr/bin/env python3
"""Run G1-G4 using disposable clients on the internal Compose network."""
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
_DOCKER_ENV_KEYS = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "XDG_RUNTIME_DIR")
COMPOSE_ENV = {key: os.environ[key] for key in _DOCKER_ENV_KEYS if key in os.environ}
COMPOSE_ENV["POC_API_KEY"] = "poc-" + secrets.token_urlsafe(24)
COMPOSE_ENV["POC_FAKE_MODEL_KEY"] = "fake-" + secrets.token_urlsafe(24)


def compose(*args, check=True):
    cmd = ["docker", "compose", "--project-name", PROJECT, "-f", str(COMPOSE), *args]
    result = subprocess.run(cmd, cwd=ROOT, env=COMPOSE_ENV, capture_output=True, text=True, timeout=120)
    if check and result.returncode != 0:
        raise RuntimeError("docker compose failed: " + " ".join(args) + "; " + result.stderr[-1200:])
    return result


def client(*args):
    result = compose("run", "--rm", "--no-deps", "client", *args, check=False)
    if result.returncode != 0:
        raise RuntimeError("disposable client failed: " + result.stderr[-800:])
    for line in reversed(result.stdout.splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("disposable client returned no JSON result")


def wait_fake(timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = client("stats")
            if result.get("http_status") == 200:
                return result
            last = result
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(0.5)
    raise RuntimeError("fake provider health timeout: " + str(last))


def wait_health(timeout=120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = client("health")
            if result.get("api_http_status") == 200 and result.get("fake_http_status") == 200:
                return result
            last = result
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(1)
    raise RuntimeError("Hermes API health timeout: " + str(last))


def provider_snapshot():
    result = client("stats")
    if result.get("http_status") != 200:
        raise RuntimeError("fake provider stats unavailable")
    return result


def wait_provider(slow_case, active, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = provider_snapshot().get("requests", [])
        found = [r for r in records if r.get("slow_case") == slow_case]
        if any(bool(r.get("active")) == active for r in found):
            return found
        time.sleep(0.5)
    raise RuntimeError("fake provider request did not reach expected phase: " + slow_case)


def record_gate(gates, name, fn):
    try:
        gates[name] = {"status": "PASS", "evidence": fn()}
    except Exception as exc:
        gates[name] = {"status": "FAIL", "reason": type(exc).__name__ + ": " + str(exc)[:500]}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def hermes_volume_name():
    result = compose("ps", "-q", "hermes", check=False)
    ids = result.stdout.strip().splitlines()
    if result.returncode != 0 or not ids:
        raise RuntimeError("Hermes container not found")
    inspect = subprocess.run(["docker", "inspect", "--format", "{{range .Mounts}}{{if eq .Destination \"/opt/data\"}}{{.Name}}{{end}}{{end}}", ids[-1]], capture_output=True, text=True, timeout=10)
    if inspect.returncode != 0:
        raise RuntimeError("could not inspect Hermes volume")
    return inspect.stdout.strip()


def main():
    run_tag = uuid.uuid4().hex[:12]
    gates = {}
    setup_error = None
    started = False
    try:
        compose("--profile", "bootstrap", "run", "--rm", "--no-deps", "seed")
        compose("up", "-d", "fake-provider")
        wait_fake(timeout=30)
        compose("up", "-d", "hermes")
        wait_health(timeout=120)
        started = True
    except Exception as exc:
        setup_error = type(exc).__name__ + ": " + str(exc)[:800]

    if not started:
        # No Run was submitted. This is NOT_RUN for G1-G4, not API behavior.
        for name in ("G1", "G2", "G3", "G4"):
            gates[name] = {"status": "NOT_RUN", "reason": "setup prerequisite blocked; no Hermes Run request was sent"}
    else:
        run_state_dir = ROOT / "runtime" / run_tag
        run_state_dir.mkdir(parents=True, exist_ok=True)
        g1_key = "p2a-" + run_tag + "-g1"
        g1_input = "Synthetic G1 request. Return the fixed local POC marker only."
        g1_run = {"run_id": None}

        def gate1():
            submitted = client("submit", "--key", g1_key, "--input", g1_input, "--wait")
            g1_run["run_id"] = submitted.get("run_id")
            require(submitted.get("http_status", 500) < 400 and g1_run["run_id"], "POST /v1/runs did not return a Run ID")
            require(submitted.get("status") == "completed", "Run did not reach completed status")
            require(submitted.get("result_marker_seen") is True, "GET result omitted deterministic marker")
            return {"run_id": g1_run["run_id"], "status": submitted.get("status"), "result_marker_seen": True}

        record_gate(gates, "G1", gate1)

        def gate2():
            require(g1_run["run_id"], "G1 Run ID unavailable")
            before = provider_snapshot()["count"]
            require(before > 0, "G2 requires G1 to have reached the fake model")
            same = client("submit", "--key", g1_key, "--input", g1_input)
            require(same.get("http_status", 500) < 400, "same-key retry was rejected")
            require(same.get("run_id") == g1_run["run_id"], "same-key retry created a different Run ID")
            conflict = client("submit", "--key", g1_key, "--input", g1_input + " Changed payload.")
            require(conflict.get("http_status", 0) >= 400, "same key with different payload was not rejected")
            time.sleep(1)
            after = provider_snapshot()["count"]
            require(after == before, "idempotent retry caused an extra fake model request")
            return {"same_payload_run_id": same.get("run_id"), "changed_payload_http_status": conflict.get("http_status"), "fake_completion_count_before_after": [before, after], "fake_completion_count_unchanged": True}

        record_gate(gates, "G2", gate2)

        def gate3():
            key = "p2a-" + run_tag + "-g3"
            submitted = client("submit", "--key", key, "--input", "Synthetic [POC_CLIENT_RESTART] request.")
            run_id = submitted.get("run_id")
            require(submitted.get("http_status", 500) < 400 and run_id, "submit before client restart failed")
            pointer = run_state_dir / "client-pointer.json"
            pointer.write_text(json.dumps({"run_id": run_id, "idempotency_key": key}, separators=(",", ":")), encoding="utf-8")
            wait_provider("client_restart", True, timeout=20)
            # The submit container has exited; a new client container resumes from the saved run_id.
            resumed_id = json.loads(pointer.read_text(encoding="utf-8"))["run_id"]
            resumed = client("get", "--run-id", resumed_id, "--wait", "--timeout", "40")
            require(resumed.get("status") == "completed", "new client container did not recover completed Run")
            require(resumed.get("run_id") == run_id, "client restart changed Run ID")
            require(resumed.get("result_marker_seen") is True, "client restart lost result")
            records = [x for x in provider_snapshot()["requests"] if x.get("slow_case") == "client_restart"]
            require(len(records) == 1, "client restart caused a duplicate fake model request")
            return {"run_id": run_id, "status": resumed.get("status"), "result_marker_seen": True, "provider_requests_for_case": len(records), "client_containers": 2, "pointer_reloaded": True}

        record_gate(gates, "G3", gate3)

        def gate4():
            complete_key = "p2a-" + run_tag + "-g4-complete"
            complete = client("submit", "--key", complete_key, "--input", "Synthetic G4 completed-run request.", "--wait")
            require(complete.get("status") == "completed" and complete.get("result_marker_seen") is True, "setup run for restart test did not complete")
            completed_run_id = complete.get("run_id")
            volume_before = hermes_volume_name()
            require(volume_before == "amusant-hermes-runs-h1-v1-home", "expected named persistent Hermes volume not mounted")
            compose("restart", "--timeout", "3", "hermes")
            wait_health(timeout=60)
            recovered = client("get", "--run-id", completed_run_id, "--wait", "--timeout", "20")
            require(recovered.get("status") == "completed" and recovered.get("result_marker_seen") is True, "completed Run result not recoverable after Hermes restart")
            volume_after = hermes_volume_name()
            require(volume_after == volume_before, "Hermes restart changed persistent volume")

            active_key = "p2a-" + run_tag + "-g4-active"
            active = client("submit", "--key", active_key, "--input", "Synthetic [POC_HERMES_RESTART] request.")
            require(active.get("http_status", 500) < 400 and active.get("run_id"), "active run submission failed")
            try:
                wait_provider("hermes_restart", True, timeout=60)
            except Exception as exc:
                current = client("get", "--run-id", active.get("run_id"))
                raise RuntimeError("active Run=" + str(active.get("run_id")) + "; status=" + str(current.get("status")) + "; provider wait=" + type(exc).__name__) from None
            before_count = len([x for x in provider_snapshot()["requests"] if x.get("slow_case") == "hermes_restart"])
            require(before_count == 1, "expected exactly one active fake request before restart")
            compose("restart", "--timeout", "2", "hermes")
            wait_health(timeout=60)
            interrupted = client("get", "--run-id", active.get("run_id"), "--wait", "--timeout", "20")
            require(interrupted.get("status") == "interrupted", "active Run did not resolve to interrupted after Hermes restart")
            time.sleep(2)
            after_count = len([x for x in provider_snapshot()["requests"] if x.get("slow_case") == "hermes_restart"])
            require(after_count == before_count, "Hermes restart replayed active Run to fake provider")
            return {"completed_run": {"run_id": completed_run_id, "status": recovered.get("status"), "result_marker_seen": True}, "active_run": {"run_id": active.get("run_id"), "status": interrupted.get("status"), "provider_requests_before_after": [before_count, after_count]}, "persistent_volume": volume_before}

        record_gate(gates, "G4", gate4)

    try:
        compose("stop", "hermes", "fake-provider", check=False)
    except Exception:
        pass
    evidence = {
        "run_tag": run_tag,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "repo_head": subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "branch": subprocess.run(["git", "-C", str(REPO_ROOT), "branch", "--show-current"], capture_output=True, text=True).stdout.strip(),
        "hermes_image": IMAGE,
        "mode": "local Docker; internal network; deterministic fake provider; no AWS; no external model API",
        "setup_error": setup_error,
        "prior_setup_attempt": "A host-probe-only setup attempt ended before Hermes startup; no Run was submitted.",
        "gates": gates,
        "hermes_volume_retained": True,
    }
    evidence_dir = ROOT / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / ("phase2a-" + run_tag + ".json")
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(evidence_path), "gates": {k: v.get("status") for k, v in gates.items()}, "setup_error": setup_error}, indent=2))
    return 0 if started and all(v.get("status") == "PASS" for v in gates.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
