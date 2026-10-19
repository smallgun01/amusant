#!/usr/bin/env python3
"""Run the isolated offline Issue #85 PR-B2 Compose proof and emit sanitized evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path(__file__).resolve().parent
COMPOSE = PROJECT / "compose.yaml"
EVIDENCE = PROJECT / "evidence" / "phase1b-e1.json"
HERMES_IMAGE = "docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7"
SCHEMA = "amusant.issue-85.pr-b2.ci-evidence.v1"
MARKER_PREFIXES = ("POC_REPORT_SECRET_", "POC_FINAL_SECRET_", "POC_TOOL_SECRET_")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_capture(command, *, timeout):
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def ci_metadata():
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"{server}/{repository}/actions/runs/{run_id}" if server and repository and run_id else None
    return {
        "source_sha": os.environ.get("GITHUB_SHA"),
        "workflow_run_url": run_url,
        "workflow_run_id": run_id or None,
    }


def record_preflight_unknown(stage: str, reason: str) -> int:
    evidence = {
        "schema": SCHEMA,
        "status": "UNKNOWN",
        "scope": "offline E1 only; no OCI deployment or paid provider",
        "hermes_image": HERMES_IMAGE,
        "hermes_platform": "linux/arm64",
        "ci": ci_metadata(),
        "failure_stage": stage,
        "failure_type": reason,
        "reason": "native ARM64 Docker/Compose execution is unavailable on this host",
    }
    EVIDENCE.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": SCHEMA,
        "status": "UNKNOWN",
        "hermes_platform": "linux/arm64",
        "hermes_digest": "sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7",
        "ci_run_url": evidence["ci"]["workflow_run_url"],
        "failure_stage": stage,
        "failure_type": reason,
        "evidence_path": "spikes/004-relay-facade-hermes-e2e/evidence/phase1b-e1.json",
    }, sort_keys=True, separators=(",", ":")))
    return 1


def main() -> int:
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    stage = "docker-compose-version"
    try:
        version = run_capture(["docker", "compose", "version"], timeout=30)
    except OSError:
        return record_preflight_unknown(stage, "docker_cli_unavailable")
    if version.returncode != 0:
        return record_preflight_unknown(stage, "docker_compose_unavailable")

    stage = "docker-engine-preflight"
    try:
        engine = run_capture(["docker", "info", "--format", "{{.Architecture}}"], timeout=30)
    except OSError:
        return record_preflight_unknown(stage, "docker_engine_unavailable")
    if engine.returncode != 0:
        return record_preflight_unknown(stage, "docker_engine_unavailable")
    engine_arch = (engine.stdout or "").strip().lower()
    if engine_arch not in {"aarch64", "arm64"}:
        return record_preflight_unknown(stage, "docker_engine_not_native_arm64")

    project_name = "amusant-b2-" + secrets.token_hex(5)
    temp = tempfile.TemporaryDirectory(prefix=".amusant-b2-", dir=PROJECT)
    temp_root = Path(temp.name)
    evidence_dir = temp_root / "evidence"
    evidence_dir.mkdir()
    evidence_dir.chmod(0o777)
    env_path = temp_root / ".env"
    env_values = {
        "POC_RUN_TAG": secrets.token_hex(8),
        "POC_FACADE_KEY": secrets.token_urlsafe(32),
        "POC_HERMES_KEY": secrets.token_urlsafe(32),
        "POC_WRITER_KEY": secrets.token_urlsafe(32),
        "POC_FAKE_MODEL_KEY": secrets.token_urlsafe(32),
        "E2E_EVIDENCE_DIR": evidence_dir.as_posix(),
    }
    env_path.write_text("".join(f"{key}={value}\n" for key, value in env_values.items()), encoding="utf-8")
    env_path.chmod(0o600)

    compose = [
        "docker", "compose", "--project-name", project_name,
        "--env-file", str(env_path), "-f", str(COMPOSE),
    ]
    stage = "native-arm64-compose-e2e"
    up_result = None
    audit_result = None
    logs = None
    runner = None
    audit = None
    log_scan = {"synthetic_full_report_markers_absent": False}
    failure_type = None

    try:
        stage = "native-arm64-compose-e2e"
        up_result = run_capture(
            compose + [
                "up", "--build", "--abort-on-container-exit",
                "--exit-code-from", "relay-e2e", "relay-e2e",
            ],
            timeout=3600,
        )

        stage = "facade-read-only-audit"
        audit_result = run_capture(
            compose + ["run", "--rm", "--no-deps", "facade-audit"],
            timeout=120,
        )
        runner = read_json(evidence_dir / "relay-e2e.json")
        audit = read_json(evidence_dir / "facade-audit.json")

        stage = "container-log-marker-scan"
        logs = run_capture(
            compose + [
                "logs", "--no-color", "--no-log-prefix",
                "relay-e2e", "fake-provider", "hermes", "facade",
            ],
            timeout=60,
        )
        log_text = (logs.stdout or "") + "\n" + (logs.stderr or "")
        markers_present = any(marker in log_text for marker in MARKER_PREFIXES)
        logs_ok = logs is not None and logs.returncode == 0
        log_scan = {
            "container_logs_scan_completed": logs_ok,
            "synthetic_full_report_markers_absent": not markers_present,
            "container_logs_printed": False,
        }

        runner_ok = isinstance(runner, dict) and runner.get("status") == "PASS"
        audit_ok = isinstance(audit, dict) and audit.get("status") == "PASS"
        up_ok = up_result is not None and up_result.returncode == 0
        audit_command_ok = audit_result is not None and audit_result.returncode == 0
        status = "PASS" if up_ok and runner_ok and audit_ok and audit_command_ok and logs_ok and log_scan["synthetic_full_report_markers_absent"] else "FAIL"
        evidence = {
            "schema": SCHEMA,
            "status": status,
            "scope": "offline E1 only; no OCI deployment or paid provider",
            "hermes_image": HERMES_IMAGE,
            "hermes_platform": "linux/arm64",
            "ci": ci_metadata(),
            "checks": {
                "relay_compose_exit_zero": up_ok,
                "actual_relay_e2e_pass": runner_ok,
                "facade_read_only_audit_pass": audit_ok and audit_command_ok,
                "container_log_marker_scan_pass": logs_ok and log_scan["synthetic_full_report_markers_absent"],
            },
            "relay_e2e": runner,
            "facade_audit": audit,
            "log_scan": log_scan,
        }
        if status != "PASS":
            failure_type = "integration_evidence_not_pass"
    except Exception as exc:
        status = "FAIL"
        failure_type = type(exc).__name__
        evidence = {
            "schema": SCHEMA,
            "status": status,
            "scope": "offline E1 only; no OCI deployment or paid provider",
            "hermes_image": HERMES_IMAGE,
            "hermes_platform": "linux/arm64",
            "ci": ci_metadata(),
            "failure_stage": stage,
            "failure_type": failure_type,
            "relay_e2e": read_json(evidence_dir / "relay-e2e.json"),
            "facade_audit": read_json(evidence_dir / "facade-audit.json"),
        }
    finally:
        try:
            run_capture(compose + ["down", "--volumes", "--remove-orphans"], timeout=120)
        except Exception:
            pass
        temp.cleanup()

    EVIDENCE.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": SCHEMA,
        "status": evidence["status"],
        "hermes_platform": "linux/arm64",
        "hermes_digest": "sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7",
        "ci_run_url": evidence.get("ci", {}).get("workflow_run_url"),
        "failure_stage": evidence.get("failure_stage"),
        "failure_type": evidence.get("failure_type") or failure_type,
        "evidence_path": "spikes/004-relay-facade-hermes-e2e/evidence/phase1b-e1.json",
    }, sort_keys=True, separators=(",", ":")))
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
