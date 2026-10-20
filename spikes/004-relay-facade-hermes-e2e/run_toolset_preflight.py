#!/usr/bin/env python3
"""Run two disposable native-ARM64 Hermes toolset preflights without an external provider."""
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
EVIDENCE = PROJECT / "evidence" / "toolset-preflight.json"
HERMES_IMAGE = "docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7"
SCHEMA = "amusant.h12.offline-effective-toolset-preflight.v1"


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


def inspect_network_isolation(project_name, evidence_dir):
    expected = {"hermes_api": False, "relay_api": False}
    try:
        result = run_capture(
            ["docker", "network", "inspect", f"{project_name}_hermes_api", f"{project_name}_relay_api"],
            timeout=30,
        )
        raw = json.loads(result.stdout) if result.returncode == 0 else None
        if isinstance(raw, list):
            for row in raw:
                if not isinstance(row, dict) or not isinstance(row.get("Name"), str):
                    continue
                name = row["Name"]
                for suffix in expected:
                    if name.endswith("_" + suffix):
                        expected[suffix] = row.get("Internal") is True
        passed = all(expected.values())
        summary = {
            "status": "PASS" if passed else "UNKNOWN",
            "networks_internal": expected,
            "external_egress_disabled": passed,
        }
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        summary = {
            "status": "UNKNOWN",
            "networks_internal": expected,
            "external_egress_disabled": False,
        }
    (evidence_dir / "network-isolation.json").write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return summary


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


def unknown_evidence(reason):
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": SCHEMA,
        "status": "UNKNOWN",
        "runner": "ubuntu-24.04-arm",
        "hermes_image": HERMES_IMAGE,
        "hermes_platform": "linux/arm64",
        "provider_mode": "synthetic fake-provider only; no external model or search request",
        "ci": ci_metadata(),
        "failure_code": reason,
        "cases": {"case_a": "UNKNOWN", "case_b": "UNKNOWN"},
        "stop_test_performed": False,
        "stop_cancel_confirmed": False,
    }
    EVIDENCE.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"schema": SCHEMA, "status": "UNKNOWN", "failure_code": reason}, separators=(",", ":")))
    return 1


def run_case(case):
    project_name = "amusant-h12-toolset-" + case.replace("_", "-") + "-" + secrets.token_hex(4)
    temp = tempfile.TemporaryDirectory(prefix=".h12-toolset-", dir=PROJECT)
    temp_root = Path(temp.name)
    evidence_dir = temp_root / "evidence"
    evidence_dir.mkdir()
    evidence_dir.chmod(0o777)
    env_path = temp_root / ".env"
    values = {
        "POC_RUN_TAG": secrets.token_hex(8),
        "POC_FACADE_KEY": secrets.token_urlsafe(32),
        "POC_HERMES_KEY": secrets.token_urlsafe(32),
        "POC_WRITER_KEY": secrets.token_urlsafe(32),
        "POC_FAKE_MODEL_KEY": secrets.token_urlsafe(32),
        "POC_TOOLSET_CASE": case,
        "E2E_EVIDENCE_DIR": evidence_dir.as_posix(),
    }
    env_path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass

    compose = ["docker", "compose", "--project-name", project_name, "--env-file", str(env_path), "-f", str(COMPOSE)]
    stage = "native-arm64-toolset-run"
    up = audit = None
    runner_record = provider_record = audit_record = None
    network_record = None
    try:
        up = run_capture(
            compose + ["up", "--build", "--abort-on-container-exit", "--exit-code-from", "toolset-runner", "toolset-runner"],
            timeout=900,
        )
        runner_record = read_json(evidence_dir / f"toolset-runner-{case}.json")
        provider_record = read_json(evidence_dir / f"toolset-provider-h12_toolset_{case}.json")

        # Prove the test-only service networks are Docker-internal before the
        # networkless volume audit evaluates the permission evidence.
        network_record = inspect_network_isolation(project_name, evidence_dir)

        stage = "networkless-volume-audit"
        audit = run_capture(compose + ["run", "--rm", "--no-deps", "toolset-audit"], timeout=120)
        audit_record = read_json(evidence_dir / f"toolset-audit-{case}.json")
        if audit_record is None:
            audit_record = None

        status = audit_record.get("status") if isinstance(audit_record, dict) else "UNKNOWN"
        return {
            "status": status if up and up.returncode == 0 and audit and audit.returncode == 0 else "FAIL" if status == "PASS" else status,
            "failure_stage": None if up and up.returncode == 0 and audit and audit.returncode == 0 and status == "PASS" else stage,
            "runner": runner_record,
            "provider": provider_record,
            "audit": audit_record,
            "network_isolation": network_record,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "UNKNOWN",
            "failure_stage": stage,
            "failure_code": "toolset_run_" + type(exc).__name__,
            "runner": runner_record,
            "provider": provider_record,
            "audit": audit_record,
            "network_isolation": network_record,
        }
    finally:
        try:
            run_capture(compose + ["down", "--volumes", "--remove-orphans"], timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            pass
        temp.cleanup()


def main():
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    try:
        version = run_capture(["docker", "compose", "version"], timeout=30)
    except OSError:
        return unknown_evidence("docker_compose_unavailable")
    if version.returncode != 0:
        return unknown_evidence("docker_compose_unavailable")
    try:
        engine = run_capture(["docker", "info", "--format", "{{.Architecture}}"], timeout=30)
    except OSError:
        return unknown_evidence("docker_engine_unavailable")
    if engine.returncode != 0:
        return unknown_evidence("docker_engine_unavailable")
    architecture = (engine.stdout or "").strip().lower()
    if architecture not in {"aarch64", "arm64"}:
        return unknown_evidence("docker_engine_not_native_arm64")

    cases = {case: run_case(case) for case in ("case_a", "case_b")}
    statuses = {case: row.get("status", "UNKNOWN") for case, row in cases.items()}
    overall = "PASS" if all(value == "PASS" for value in statuses.values()) else (
        "FAIL" if any(value == "FAIL" for value in statuses.values()) else "UNKNOWN"
    )
    record = {
        "schema": SCHEMA,
        "status": overall,
        "runner": "ubuntu-24.04-arm",
        "docker_architecture": architecture,
        "hermes_image": HERMES_IMAGE,
        "hermes_platform": "linux/arm64",
        "provider_mode": "synthetic fake-provider only; no external model or search request",
        "preflight_run_limit_seconds": 120,
        "product_timing_changed": False,
        "stop_test_performed": False,
        "stop_cancel_confirmed": False,
        "ci": ci_metadata(),
        "cases": cases,
    }
    EVIDENCE.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    # These records are deliberately sanitized by the provider, runner, and
    # audit helpers: names/statuses only, with no prompt, report, secret, or
    # provider response body. Printing them makes a failed preflight diagnosable
    # from the retained Actions log as well as from the downloadable artifact.
    print(json.dumps({
        "schema": SCHEMA,
        "status": overall,
        "ci_run_url": record["ci"]["workflow_run_url"],
        "sanitized_cases": cases,
    }, sort_keys=True, separators=(",", ":")))
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

