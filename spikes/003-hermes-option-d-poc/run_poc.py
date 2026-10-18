#!/usr/bin/env python3
"""Reproducible pinned-Hermes Option D proof runner; never removes Docker volumes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPOSE_FILE = ROOT / "compose.yaml"
EVIDENCE_FILE = ROOT / "evidence" / "phase1.json"
IMAGE = "docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7"
SOURCE_COMMIT = "f97608f178d1ffeca59860195ab7da295f7c8e5f"
REPORT_CAP = 4096
PROJECT = "amusant-issue-73-" + uuid.uuid4().hex[:12]
CLIENT_OUTPUT_SCANS = []
CLIENT_FAILURES = []
ENV = os.environ.copy()
ENV.update({
    "POC_RUN_TAG": secrets.token_hex(8),
    "POC_HERMES_KEY": secrets.token_urlsafe(36),
    "POC_FAKE_MODEL_KEY": secrets.token_urlsafe(36),
    "POC_FACADE_KEY": secrets.token_urlsafe(36),
})

class RunnerError(RuntimeError):
    def __init__(self, kind: str, step: str):
        super().__init__(step)
        self.kind = kind
        self.step = step


def command(argv: list[str], *, timeout: int = 60, env=None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, cwd=ROOT, env=env or ENV, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise RunnerError("infrastructure", "command_timeout") from None
    except OSError:
        raise RunnerError("infrastructure", "command_unavailable") from None


def compose(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return command([
        "docker", "compose", "--project-name", PROJECT,
        "--file", str(COMPOSE_FILE), *args,
    ], timeout=timeout)


def require_success(result: subprocess.CompletedProcess, step: str) -> str:
    if result.returncode != 0:
        raise RunnerError("infrastructure", step)
    return result.stdout


def parse_json_stdout(result: subprocess.CompletedProcess, step: str):
    raw = require_success(result, step).strip()
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise RunnerError("infrastructure", step + "_invalid_output") from None
    if not isinstance(value, dict):
        raise RunnerError("infrastructure", step + "_invalid_shape")
    return value


def normalized_networks(service: dict) -> set[str]:
    value = service.get("networks") or {}
    if isinstance(value, list):
        return set(value)
    if isinstance(value, dict):
        return set(value)
    return set()


def memory_bytes(value) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)?\s*", value, re.I)
        if match:
            amount = float(match.group(1))
            unit = (match.group(2) or "b").lower()
            factors = {"b": 1, "kb": 1000, "kib": 1024, "mb": 1000**2, "mib": 1024**2,
                       "gb": 1000**3, "gib": 1024**3, "tb": 1000**4, "tib": 1024**4}
            return int(amount * factors[unit])
    return 0


def docker_ids(*args: str) -> set[str]:
    result = command(["docker", *args], timeout=30)
    return {line.strip() for line in require_success(result, "docker_inventory").splitlines() if line.strip()}


def project_volumes() -> set[str]:
    return docker_ids("volume", "ls", "--quiet", "--filter", "label=com.docker.compose.project=" + PROJECT)


def compose_run_client(action: str, *, timeout: int = 120) -> dict:
    result = compose("run", "--rm", "--no-deps", "-T", "relay-client", action, timeout=timeout)
    combined = result.stdout + result.stderr
    marker_flags = {
        "report_marker_present": ("POC_REPORT_SECRET_" + ENV["POC_RUN_TAG"] + "_") in combined,
        "final_marker_present": ("POC_FINAL_SECRET_" + ENV["POC_RUN_TAG"] + "_") in combined,
        "tool_marker_present": ("POC_TOOL_SECRET_" + ENV["POC_RUN_TAG"] + "_") in combined,
    }
    CLIENT_OUTPUT_SCANS.append({"action": action, "captured_output_bytes": len(combined.encode("utf-8", errors="ignore")), **marker_flags})
    if any(marker_flags.values()):
        raise RunnerError("failure", "relay_client_output_contains_payload_marker")
    raw = result.stdout.strip()
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise RunnerError("infrastructure", "relay_" + action.replace("-", "_") + "_invalid_output") from None
    if not isinstance(value, dict):
        raise RunnerError("infrastructure", "relay_" + action.replace("-", "_") + "_invalid_shape")
    if value.get("failed_case") or value.get("error_type"):
        case = str(value.get("failed_case", ""))
        error_type = str(value.get("error_type", "Unknown"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", case):
            case = "unknown"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,40}", error_type):
            error_type = "Unknown"
        diagnostic = {"action": action, "case": case, "error_type": error_type}
        code = value.get("error_code")
        if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", code):
            diagnostic["error_code"] = code
        if value.get("run_id_persisted") is True or value.get("run_id_persisted") is False:
            diagnostic["run_id_persisted"] = value["run_id_persisted"]
        task_status = value.get("task_status")
        if task_status in {"queued", "running", "completed", "failed", "uncertain"}:
            diagnostic["task_status"] = task_status
        methods = value.get("relay_http_methods")
        if isinstance(methods, list) and all(method in {"GET", "POST"} for method in methods):
            diagnostic["relay_http_methods"] = methods
        statuses = value.get("relay_http_statuses")
        if isinstance(statuses, list) and all(type(status) is int and 100 <= status <= 599 for status in statuses):
            diagnostic["relay_http_statuses"] = statuses
        CLIENT_FAILURES.append(diagnostic)
        CLIENT_OUTPUT_SCANS[-1]["failure_diagnostic"] = diagnostic
        if value.get("failed_case"):
            raise RunnerError("infrastructure", "relay_case_exception_" + case + "_" + error_type)
        raise RunnerError("infrastructure", "relay_client_exception_" + action.replace("-", "_") + "_" + error_type)
    value["_process_exit_code"] = result.returncode
    return value


def mib(value: str) -> float:
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$", value)
    if not match:
        return 0.0
    amount = float(match.group(1))
    unit = match.group(2).lower()
    factor = {"b": 1 / 1024**2, "kb": 1 / 1024, "kib": 1 / 1024,
              "mb": 1, "mib": 1, "gb": 1024, "gib": 1024,
              "tb": 1024**2, "tib": 1024**2}.get(unit, 0)
    return amount * factor


class StatsMonitor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.done = threading.Event()
        self.peak_mib = 0.0
        self.samples = 0

    def run(self):
        while not self.done.is_set():
            try:
                ids = docker_ids("ps", "--quiet", "--filter", "label=com.docker.compose.project=" + PROJECT)
                if ids:
                    result = command(["docker", "stats", "--no-stream", "--format", "{{json .}}", *sorted(ids)], timeout=25)
                    if result.returncode == 0:
                        for line in result.stdout.splitlines():
                            try:
                                row = json.loads(line)
                                usage = str(row.get("MemUsage", "")).split("/", 1)[0]
                                self.peak_mib = max(self.peak_mib, mib(usage))
                                self.samples += 1
                            except (json.JSONDecodeError, AttributeError):
                                continue
            except RunnerError:
                pass
            self.done.wait(1.0)

    def finish(self):
        self.done.set()
        self.join(timeout=30)
        return {"sample_count": self.samples, "peak_observed_memory_mib": round(self.peak_mib, 2)}


def inspect_artifacts_script() -> str:
    idem_task = "poc-idem-" + ENV["POC_RUN_TAG"]
    return r'''import hashlib, json, os, sqlite3
from pathlib import Path
state = Path("/state")
conn = sqlite3.connect(state / "option-d.sqlite3")
conn.row_factory = sqlite3.Row
requests = [dict(r) for r in conn.execute("SELECT * FROM requests")]
artifacts = [dict(r) for r in conn.execute("SELECT * FROM artifacts")]
by_case = {}
all_files = set()
for path in (state / "artifacts").glob("*"):
    if path.is_file(): all_files.add(str(path))
artifact_summary = []
for a in artifacts:
    p = Path(a["path"])
    try:
        data = p.read_bytes()
        file_ok = hashlib.sha256(data).hexdigest() == a["sha256"] and len(data) == a["byte_length"]
        prefix = ("POC_REPORT_SECRET_" + os.environ.get("POC_RUN_TAG", "") + "_" + a["poc_case"] + "_").encode()
        marker_ok = data.startswith(prefix)
    except OSError:
        data, file_ok, marker_ok = b"", False, False
    matches = [r for r in requests if r["owner_scope"] == a["owner_scope"] and r["task_id"] == a["task_id"] and r["session_id"] == a["session_id"] and r["run_id"] == a["run_id"]]
    bound = len(matches) == 1 and matches[0]["artifact_ref"] == a["artifact_ref"] and matches[0]["artifact_status"] == "committed"
    terminal_seen = bool(matches and matches[0]["terminal_seen_at"])
    ordered = bool(terminal_seen and a["committed_at"] <= matches[0]["terminal_seen_at"])
    ref_hash = hashlib.sha256(a["artifact_ref"].encode()).hexdigest()
    by_case.setdefault(a["poc_case"], []).append({"bytes": len(data), "sha256_valid": file_ok, "report_marker_valid": marker_ok, "identity_binding_valid": bound, "commit_before_terminal": ordered, "terminal_seen": terminal_seen, "artifact_ref_hash": ref_hash})
    all_files.discard(str(p))
case_summaries = []
for name in sorted({r["poc_case"] for r in requests} | set(by_case)):
    rows = [r for r in requests if r["poc_case"] == name]
    case_summaries.append({
        "case": name,
        "request_count": len(rows),
        "submit_states": sorted(r["submit_state"] for r in rows),
        "run_id_persisted_count": sum(bool(r["run_id"]) for r in rows),
        "artifact_statuses": sorted(r["artifact_status"] for r in rows),
        "presentation_errors": sorted(r["presentation_error"] or "none" for r in rows),
        "upstream_post_counts": sorted(int(r["upstream_post_count"]) for r in rows),
        "submit_upstream_statuses": sorted(int(r["submit_upstream_status"] or 0) for r in rows),
        "submit_upstream_errors": sorted(r["submit_upstream_error"] or "none" for r in rows),
        "remote_failure_types": sorted(r["remote_failure_type"] or "none" for r in rows),
        "remote_failure_categories": sorted(r["remote_failure_category"] or "none" for r in rows),
        "terminal_upstream_statuses": sorted(int(r["terminal_upstream_status"] or 0) for r in rows),
        "terminal_upstream_errors": sorted(r["terminal_upstream_error"] or "none" for r in rows),
        "terminal_upstream_codes": sorted(r["terminal_upstream_code"] or "none" for r in rows),
        "hermes_run_statuses": sorted(r["hermes_run_status"] or "none" for r in rows),
        "writer_call_counts": sorted(int(r["writer_call_count"] or 0) for r in rows),
        "writer_result_codes": sorted(r["writer_result_code"] or "none" for r in rows),
        "writer_report_byte_counts": sorted(int(r["writer_report_bytes"] or 0) for r in rows),
        "raw_output_byte_counts": sorted(int(r["raw_output_bytes"] or 0) for r in rows),
        "artifacts": by_case.get(name, []),
    })
idem = conn.execute("SELECT upstream_post_count, submit_state, run_id FROM requests WHERE task_id=?", (''' + repr(idem_task) + r''',)).fetchone()
print(json.dumps({
    "request_case_summaries": case_summaries,
    "idempotency_request": None if idem is None else {"upstream_post_count": int(idem[0]), "accepted": idem[1] == "accepted", "run_id_present": bool(idem[2])},
    "registered_artifact_count": len(artifacts),
    "unregistered_artifact_file_count": len(all_files),
}, separators=(",", ":")))
'''


def hermes_readiness_probe() -> dict:
    script = (
        "import json,os,urllib.error,urllib.request\n"
        "req=urllib.request.Request('http://127.0.0.1:8642/health/detailed',headers={'Authorization':'Bearer '+os.environ.get('API_SERVER_KEY','')})\n"
        "status=0\n"
        "ready=False\n"
        "try:\n"
        "    with urllib.request.urlopen(req,timeout=3) as response:\n"
        "        status=response.status\n"
        "        body=json.loads(response.read(16384))\n"
        "        ready=isinstance(body,dict) and body.get('status')=='ok'\n"
        "except urllib.error.HTTPError as exc:\n"
        "    status=exc.code\n"
        "except Exception:\n"
        "    pass\n"
        "print(json.dumps({'http_status':status,'ready':ready},separators=(',',':')))"
    )
    result = compose("exec", "-T", "hermes", "python3", "-c", script, timeout=15)
    return parse_json_stdout(result, "hermes_readiness")


def hermes_plugin_registration_probe() -> dict:
    result = compose("logs", "--no-color", "--no-log-prefix", "--tail", "3000", "hermes", timeout=30)
    logs = result.stdout + result.stderr if result.returncode == 0 else ""
    failure = re.search(r"Failed to load plugin 'amusant-option-d-poc': ([A-Za-z][A-Za-z0-9_.]{0,50})", logs)
    return {
        "log_read_succeeded": result.returncode == 0,
        "manifest_seen": "Parsed manifest: key=amusant-option-d-poc" in logs,
        "tool_registered": "Plugin amusant-option-d-poc registered tool: poc_save_report" in logs,
        "hook_registered": "Plugin amusant-option-d-poc registered hook: transform_llm_output" in logs,
        "plugin_load_failed": failure is not None,
        "plugin_load_error_type": failure.group(1) if failure else None,
        "plugin_not_enabled": "Skipping 'amusant-option-d-poc' (not in plugins.enabled)" in logs,
    }


def hermes_api_toolset_probe() -> dict:
    script = """import json, os, urllib.error, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8642/v1/toolsets",
    headers={"Authorization": "Bearer " + os.environ.get("API_SERVER_KEY", "")},
)
status = 0
body = {}
try:
    with urllib.request.urlopen(req, timeout=5) as response:
        status = response.status
        body = json.loads(response.read(1048576))
except urllib.error.HTTPError as exc:
    status = exc.code
except Exception:
    pass
rows = body.get("data", []) if isinstance(body, dict) else []
row = next((item for item in rows if isinstance(item, dict) and item.get("name") == "amusant_option_d"), {}) if isinstance(rows, list) else {}
tools = row.get("tools", []) if isinstance(row, dict) else []
print(json.dumps({
    "http_status": status,
    "toolset_seen": bool(row),
    "toolset_enabled": row.get("enabled") is True if isinstance(row, dict) else False,
    "tool_registered": "poc_save_report" in tools if isinstance(tools, list) else False,
}, separators=(",", ":")))
"""
    result = compose("exec", "-T", "hermes", "python3", "-c", script, timeout=20)
    return parse_json_stdout(result, "hermes_api_toolset")


def inspect_fake_provider() -> dict:
    script = "import json,urllib.request; r=urllib.request.urlopen('http://fake-provider:8000/_poc/stats',timeout=5); print(json.dumps(json.load(r),separators=(',',':')))"
    result = compose("exec", "-T", "hermes", "python3", "-c", script, timeout=20)
    value = parse_json_stdout(result, "provider_stats")
    requests = value.get("requests")
    if not isinstance(requests, list):
        raise RunnerError("infrastructure", "provider_stats_shape")
    summary = {}
    for row in requests:
        if not isinstance(row, dict):
            continue
        case = row.get("case")
        if not isinstance(case, str):
            continue
        item = summary.setdefault(case, {"provider_turns": 0, "tool_calls_requested": [], "report_tool_call_count": 0, "report_tool_result_codes": [], "write_file_requested": False, "spoofed_identity_fields_included": False, "tools_field_types": [], "tools_field_counts": [], "functions_field_types": [], "functions_field_counts": []})
        item["provider_turns"] += 1
        item["poc_tool_offered"] = item.get("poc_tool_offered", False) or row.get("poc_tool_offered") is True or "poc_save_report" in row.get("tool_names", [])
        for field in ("tools", "functions"):
            field_type = row.get(field + "_field_type")
            field_count = row.get(field + "_field_count")
            if field_type in {"missing", "null", "list", "dict", "string", "other"}:
                item[field + "_field_types"].append(field_type)
            if type(field_count) is int and 0 <= field_count <= 1000:
                item[field + "_field_counts"].append(field_count)
        result_codes = row.get("report_tool_result_codes", [])
        allowed_codes = {"committed", "invalid_arguments", "report_invalid", "report_over_cap", "trusted_request_context_unavailable", "artifact_write_failed", "artifact_write_timeout", "artifact_conflict", "artifact_commit_failed", "invalid_tool_result", "other_tool_result"}
        if isinstance(result_codes, list):
            item["report_tool_result_codes"].extend(code for code in result_codes if code in allowed_codes)
        item["spoofed_identity_fields_included"] = item["spoofed_identity_fields_included"] or row.get("spoofed_identity_fields_included") is True
        item["write_file_observed"] = item.get("write_file_observed", False) or "write_file" in row.get("called_tools", [])
        action = row.get("next_tool")
        if isinstance(action, str):
            item["tool_calls_requested"].append(action)
            if action == "poc_save_report":
                item["report_tool_call_count"] += 1
            if action == "write_file":
                item["write_file_requested"] = True
    return summary


def inspect_facade_state() -> dict:
    result = compose("exec", "-T", "facade", "python3", "-c", inspect_artifacts_script(), timeout=30)
    return parse_json_stdout(result, "artifact_state")


def scan_logs() -> dict:
    found = {}
    for service in ("facade", "fake-provider", "hermes"):
        result = compose("logs", "--no-color", "--no-log-prefix", "--tail", "3000", service, timeout=30)
        logs = result.stdout if result.returncode == 0 else ""
        found[service] = {
            "log_read_succeeded": result.returncode == 0,
            "report_marker_present": ("POC_REPORT_SECRET_" + ENV["POC_RUN_TAG"] + "_") in logs,
            "final_marker_present": ("POC_FINAL_SECRET_" + ENV["POC_RUN_TAG"] + "_") in logs,
            "tool_marker_present": ("POC_TOOL_SECRET_" + ENV["POC_RUN_TAG"] + "_") in logs,
            "captured_log_bytes": len(logs.encode("utf-8", errors="ignore")),
        }
        if service == "hermes":
            state = hermes_plugin_registration_probe()
            found[service]["plugin_manifest_seen"] = state.get("manifest_seen") is True
            found[service]["plugin_tool_registered"] = state.get("tool_registered") is True
            found[service]["plugin_hook_registered"] = state.get("hook_registered") is True
            found[service]["plugin_load_failed"] = state.get("plugin_load_failed") is True
            found[service]["plugin_load_error_type"] = state.get("plugin_load_error_type")
            found[service]["plugin_not_enabled"] = state.get("plugin_not_enabled") is True
    return found


def inspect_container_states() -> list[dict]:
    ids = docker_ids("ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=" + PROJECT)
    if not ids:
        return []
    result = command(["docker", "inspect", *sorted(ids)], timeout=30)
    raw = require_success(result, "container_inspection")
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        raise RunnerError("infrastructure", "container_inspection_invalid") from None
    out = []
    for row in rows:
        state = row.get("State", {})
        limit = row.get("HostConfig", {}).get("Memory", 0)
        out.append({
            "service": row.get("Config", {}).get("Labels", {}).get("com.docker.compose.service", "unknown"),
            "running": bool(state.get("Running")),
            "exit_code": int(state.get("ExitCode", 0)),
            "oom_killed": bool(state.get("OOMKilled")),
            "configured_memory_limit_bytes": int(limit or 0),
        })
    return out


def validate_run_outputs(results: dict, db: dict, provider: dict, evidence: dict):
    case_rows = results.get("cases")
    if not isinstance(case_rows, list):
        raise RunnerError("failure", "case_results_missing")
    cases = {row.get("case"): row for row in case_rows if isinstance(row, dict)}
    expected_cases = {
        "success_10k": ("completed", "ready"),
        "success_30k": ("completed", "ready"),
        "writer_redelivery": ("completed", "ready"),
        "no_writer": ("failed", None),
        "writer_fail": ("failed", None),
        "writer_slow": ("failed", None),
        "wrong_binding": ("failed", None),
        "malformed_json": ("completed", "failed"),
        "oversized_final": ("completed", "failed"),
        "missing_speech": ("completed", "failed"),
        "hook_exception": ("completed", "failed"),
        "hook_empty": ("completed", "failed"),
        "hook_timeout": ("completed", "failed"),
        "late_mutation": ("completed", "failed"),
        "lost_envelope_10k": ("completed", "ready"),
    }
    if set(cases) != set(expected_cases):
        raise RunnerError("failure", "case_set_mismatch")
    if int(results.get("_process_exit_code", 1)) != 0:
        raise RunnerError("failure", "case_runner_exit")
    for name, (status, presentation) in expected_cases.items():
        row = cases[name]
        if row.get("task_status") != status or row.get("presentation_status") != presentation:
            raise RunnerError("failure", "case_outcome_" + name)
        if row.get("task_run_id_persisted") is not True:
            raise RunnerError("failure", "run_id_not_persisted_" + name)
        if row.get("report_marker_in_relay_response") or row.get("final_marker_in_relay_response") or row.get("tool_marker_in_relay_response"):
            raise RunnerError("failure", "payload_crossed_relay_boundary_" + name)
        if row.get("relay_wire_headers_valid") is not True:
            raise RunnerError("failure", "relay_wire_headers_invalid_" + name)
        if int(row.get("relay_max_response_bytes", REPORT_CAP + 1)) > REPORT_CAP:
            raise RunnerError("failure", "relay_response_over_cap_" + name)
        methods = row.get("http_methods", [])
        if methods.count("POST") != 1:
            raise RunnerError("failure", "unexpected_post_count_" + name)
        if presentation is not None:
            if not row.get("artifact_ref_present") or not row.get("artifact_ref_hash"):
                raise RunnerError("failure", "artifact_reference_missing_" + name)
        elif row.get("artifact_ref_present"):
            raise RunnerError("failure", "uncommitted_reference_exposed_" + name)
    if cases["lost_envelope_10k"].get("lost_terminal_delivery_retried") is not True or cases["lost_envelope_10k"].get("same_reference_after_redelivery") is not True:
        raise RunnerError("failure", "lost_envelope_recovery_failed")
    if provider.get("no_writer", {}).get("report_tool_call_count") != 0:
        raise RunnerError("failure", "uncalled_writer_was_invoked")
    for name in ("writer_fail", "writer_slow", "wrong_binding"):
        if provider.get(name, {}).get("report_tool_call_count") != 1:
            raise RunnerError("failure", "writer_failure_case_not_exercised_" + name)
    if provider.get("wrong_binding", {}).get("spoofed_identity_fields_included") is not True:
        raise RunnerError("failure", "identity_spoof_not_exercised")
    redelivery_calls = provider.get("writer_redelivery", {}).get("report_tool_call_count")
    if redelivery_calls != 2:
        raise RunnerError("failure", "artifact_writer_redelivery_not_exercised")
    if provider.get("late_mutation", {}).get("write_file_requested") is not True:
        raise RunnerError("failure", "late_finalizer_tool_not_requested")
    if provider.get("late_mutation", {}).get("provider_turns", 0) < 2 or provider.get("late_mutation", {}).get("write_file_observed") is not True:
        raise RunnerError("failure", "late_finalizer_tool_result_not_observed")

    summaries = {row["case"]: row for row in db.get("request_case_summaries", [])}
    for name, size in (("success_10k", 10000), ("success_30k", 30000), ("writer_redelivery", 10000)):
        row = summaries.get(name)
        arts = row.get("artifacts", []) if row else []
        if len(arts) != 1 or arts[0].get("bytes") != size or not arts[0].get("sha256_valid") or not arts[0].get("report_marker_valid") or not arts[0].get("identity_binding_valid") or not arts[0].get("commit_before_terminal"):
            raise RunnerError("failure", "durable_artifact_check_" + name)
        if cases[name].get("artifact_ref_hash") != arts[0].get("artifact_ref_hash"):
            raise RunnerError("failure", "artifact_reference_binding_" + name)
    for name in ("malformed_json", "oversized_final", "missing_speech", "hook_exception", "hook_empty", "hook_timeout", "late_mutation", "lost_envelope_10k"):
        row = summaries.get(name)
        arts = row.get("artifacts", []) if row else []
        if len(arts) != 1 or not arts[0].get("sha256_valid") or not arts[0].get("identity_binding_valid") or not arts[0].get("commit_before_terminal"):
            raise RunnerError("failure", "artifact_survives_presentation_failure_" + name)
        if cases[name].get("artifact_ref_hash") != arts[0].get("artifact_ref_hash"):
            raise RunnerError("failure", "fallback_reference_binding_" + name)
    if summaries.get("late_mutation", {}).get("presentation_errors", ["none"]) == ["none"]:
        raise RunnerError("failure", "late_finalizer_mutation_not_rejected")
    for name in ("no_writer", "writer_fail", "writer_slow", "wrong_binding"):
        row = summaries.get(name)
        if not row or row.get("artifacts"):
            raise RunnerError("failure", "false_artifact_commit_" + name)
        statuses = row.get("artifact_statuses", [])
        expected = "pending" if name in {"no_writer", "wrong_binding"} else "failed"
        if expected not in statuses:
            raise RunnerError("failure", "artifact_failure_state_" + name)
    idem = db.get("idempotency_request")
    if not idem or idem.get("upstream_post_count") != 1 or not idem.get("accepted") or not idem.get("run_id_present"):
        raise RunnerError("failure", "facade_idempotency_post_count")

    access = evidence["phases"]["access"]
    expected_access = (
        access.get("first_post_status") in {200, 202}
        and access.get("replay_post_status") in {200, 202}
        and access.get("same_run_id_on_replay") is True
        and access.get("changed_payload_status") == 409
        and access.get("wrong_owner_get_status") == 404
        and access.get("events_route_status") == 404
        and access.get("direct_hermes_unreachable_from_relay_network") is True
        and int(access.get("max_post_response_bytes", REPORT_CAP + 1)) <= REPORT_CAP
    )
    if not expected_access or int(access.get("_process_exit_code", 1)) != 0:
        raise RunnerError("failure", "access_idempotency_or_bypass_gate")
    ambiguous = evidence["phases"]["ambiguous"]
    if int(ambiguous.get("_process_exit_code", 1)) != 0 or not ambiguous.get("facade_accepted_post") or not ambiguous.get("no_post_replay") or ambiguous.get("final_status") != "uncertain":
        raise RunnerError("failure", "ambiguous_submission_replayed")
    restart = evidence["phases"]["restart_resume"]
    if int(restart.get("_process_exit_code", 1)) != 0 or restart.get("task_status") != "completed" or restart.get("same_run_id") is not True or restart.get("get_only_after_restart") is not True:
        raise RunnerError("failure", "restart_recovery_not_get_only")
    if restart.get("report_marker_in_relay_response") or restart.get("final_marker_in_relay_response") or restart.get("tool_marker_in_relay_response"):
        raise RunnerError("failure", "restart_payload_crossed_boundary")
    stop = evidence["phases"]["stop"]
    if int(stop.get("_process_exit_code", 1)) != 0 or not stop.get("post_confirmed") or not stop.get("run_id_preserved") or stop.get("terminal_get_status") not in {"completed", "failed", "cancelled", "interrupted", "expired"}:
        raise RunnerError("failure", "stop_contract_not_confirmed")
    if db.get("unregistered_artifact_file_count") != 0:
        raise RunnerError("failure", "orphan_artifact_file")


def main() -> int:
    evidence = {
        "schema_version": 1,
        "verdict": "INCONCLUSIVE",
        "server": {"version": "v2026.9.24", "source_commit": SOURCE_COMMIT, "image": IMAGE},
        "host": {"runner_os": os.environ.get("RUNNER_OS", "local"), "github_actions": os.environ.get("GITHUB_ACTIONS") == "true"},
        "checks": {}, "phases": {}, "logs": {}, "resources": {},
        "limitations": [
            "The provider is a deterministic local fake and no paid model is configured.",
            "Only internal Docker networks are attached to the proof services; the host pulls the pinned image before execution.",
            "This result covers the pinned proof harness and does not approve production architecture, configuration, deployment, or merge.",
        ],
    }
    baseline_running = set()
    monitor = None
    project_created = False
    error = None
    current_step = "startup"
    try:
        info = command(["docker", "info", "--format", "{{.MemTotal}}"], timeout=30)
        require_success(info, "docker_info")
        try:
            evidence["resources"]["docker_host_memory_bytes"] = int(info.stdout.strip())
        except ValueError:
            evidence["resources"]["docker_host_memory_bytes"] = None
        baseline_running = docker_ids("ps", "--quiet", "--no-trunc")
        evidence["resources"]["preexisting_running_container_count"] = len(baseline_running)

        current_step = "compose_config"
        config_result = compose("config", "--format", "json", timeout=30)
        config = json.loads(require_success(config_result, "compose_config"))
        services = config.get("services", {})
        networks = config.get("networks", {})
        if not isinstance(services, dict) or not isinstance(networks, dict):
            raise RunnerError("failure", "compose_config_shape")
        for service_name, service in services.items():
            if service.get("image") != IMAGE:
                raise RunnerError("failure", "image_not_exactly_pinned")
            if service.get("ports"):
                raise RunnerError("failure", "host_port_published")
            if service.get("network_mode") not in {None, "none"}:
                raise RunnerError("failure", "unexpected_network_mode")
        if not all((networks.get(name) or {}).get("internal") is True for name in ("hermes_private", "relay_front")) or any((value or {}).get("internal") is not True for value in networks.values()):
            raise RunnerError("failure", "network_not_internal")
        expected_nets = {
            "fake-provider": {"hermes_private"},
            "hermes": {"hermes_private"},
            "facade": {"hermes_private", "relay_front"},
            "relay-client": {"relay_front"},
        }
        for name, expected in expected_nets.items():
            if normalized_networks(services[name]) != expected:
                raise RunnerError("failure", "network_membership_" + name)
        if services["seed"].get("network_mode") != "none" or services["state-init"].get("network_mode") != "none":
            raise RunnerError("failure", "bootstrap_network_enabled")
        mem_caps = {name: memory_bytes(services[name].get("mem_limit", 0)) for name in ("state-init", "seed", "fake-provider", "hermes", "facade", "relay-client")}
        if any(value <= 0 for value in mem_caps.values()):
            raise RunnerError("failure", "memory_caps_missing")
        evidence["resources"]["configured_memory_caps_bytes"] = mem_caps
        evidence["checks"]["compose_isolated_and_pinned"] = {"pass": True, "host_ports": 0, "external_networks": 0}
        evidence["checks"]["memory_limits_configured"] = {"pass": True}

        existing_volumes = project_volumes()
        if existing_volumes:
            raise RunnerError("infrastructure", "unique_volume_project_collision")
        project_created = True
        current_step = "compose_up"
        up = compose("up", "--detach", "--pull", "missing", "hermes", "fake-provider", "facade", timeout=900)
        require_success(up, "compose_up")
        new_volumes = project_volumes()
        if len(new_volumes) != 3:
            raise RunnerError("failure", "proof_volume_set_unexpected")
        evidence["resources"]["proof_volume_count"] = len(new_volumes)
        evidence["resources"]["proof_volumes_removed"] = False
        monitor = StatsMonitor()
        monitor.start()

        current_step = "hermes_readiness"
        readiness = None
        for _ in range(90):
            try:
                readiness = hermes_readiness_probe()
                if readiness.get("http_status") == 200 and readiness.get("ready") is True:
                    break
                readiness = None
            except RunnerError:
                readiness = None
            time.sleep(1)
        if not readiness:
            raise RunnerError("infrastructure", "hermes_readiness_timeout")
        evidence["checks"]["hermes_readiness"] = {"pass": True}

        current_step = "hermes_plugin_registration"
        plugin_state = None
        for _ in range(90):
            candidate = hermes_plugin_registration_probe()
            if candidate.get("tool_registered") and candidate.get("hook_registered"):
                plugin_state = candidate
                break
            if candidate.get("plugin_load_failed") or candidate.get("plugin_not_enabled"):
                plugin_state = candidate
                break
            time.sleep(1)
        if plugin_state is None:
            plugin_state = {"log_read_succeeded": False}
        plugin_pass = plugin_state.get("tool_registered") is True and plugin_state.get("hook_registered") is True
        evidence["checks"]["hermes_plugin_registration"] = {
            "pass": plugin_pass,
            "manifest_seen": plugin_state.get("manifest_seen") is True,
            "tool_registered": plugin_state.get("tool_registered") is True,
            "hook_registered": plugin_state.get("hook_registered") is True,
            "plugin_load_failed": plugin_state.get("plugin_load_failed") is True,
            "plugin_load_error_type": plugin_state.get("plugin_load_error_type"),
            "plugin_not_enabled": plugin_state.get("plugin_not_enabled") is True,
        }
        if not plugin_pass:
            raise RunnerError("infrastructure", "plugin_registration_not_confirmed")

        current_step = "hermes_api_toolset"
        toolset_state = None
        for _ in range(10):
            try:
                candidate = hermes_api_toolset_probe()
                if candidate.get("http_status") == 200:
                    toolset_state = candidate
                    break
            except RunnerError:
                pass
            time.sleep(1)
        if toolset_state is None:
            toolset_state = {"http_status": 0, "toolset_seen": False, "toolset_enabled": False, "tool_registered": False}
        toolset_pass = (
            toolset_state.get("http_status") == 200
            and toolset_state.get("toolset_seen") is True
            and toolset_state.get("toolset_enabled") is True
            and toolset_state.get("tool_registered") is True
        )
        evidence["checks"]["hermes_api_toolset"] = {
            "pass": toolset_pass,
            "http_status": toolset_state.get("http_status") if type(toolset_state.get("http_status")) is int else 0,
            "toolset_seen": toolset_state.get("toolset_seen") is True,
            "toolset_enabled": toolset_state.get("toolset_enabled") is True,
            "tool_registered": toolset_state.get("tool_registered") is True,
        }
        if not toolset_pass:
            raise RunnerError("infrastructure", "api_plugin_toolset_not_enabled")

        current_step = "facade_health"
        health = None
        for _ in range(90):
            try:
                health = compose_run_client("health", timeout=20)
                if health.get("_process_exit_code") == 0 and health.get("status") == 200 and health.get("response_bytes", REPORT_CAP + 1) <= REPORT_CAP:
                    break
                health = None
            except RunnerError:
                health = None
            time.sleep(1)
        if not health:
            raise RunnerError("infrastructure", "facade_health_timeout")
        evidence["phases"]["health"] = health

        # Isolate restart recovery before other fixtures leave any intentionally active Runs.
        current_step = "restart_start"
        evidence["phases"]["restart_start"] = compose_run_client("restart-start", timeout=60)
        time.sleep(0.5)
        current_step = "restart_resume"
        evidence["phases"]["restart_resume"] = compose_run_client("restart-resume", timeout=120)
        current_step = "case_matrix"
        evidence["phases"]["cases"] = compose_run_client("cases", timeout=600)
        current_step = "access"
        evidence["phases"]["access"] = compose_run_client("access", timeout=60)
        current_step = "stop"
        evidence["phases"]["stop"] = compose_run_client("stop", timeout=60)
        # This fixture intentionally loses the Run ID; leave it last so it cannot occupy server capacity.
        current_step = "ambiguous"
        evidence["phases"]["ambiguous"] = compose_run_client("ambiguous", timeout=60)

        provider_summary = inspect_fake_provider()
        evidence["checks"]["provider_tool_trace"] = {"pass": True, "cases": provider_summary}
        evidence["provider_tool_trace"] = {"cases": provider_summary}
        evidence["checks"]["artifact_database_and_files"] = {"pass": True}
        evidence["checks"]["artifact_database_and_files"].update(inspect_facade_state())
        current_step = "log_scan"
        evidence["logs"] = scan_logs()
        evidence["logs"]["relay_client_output"] = CLIENT_OUTPUT_SCANS
        for name in ("facade", "fake-provider"):
            if evidence["logs"].get(name, {}).get("report_marker_present") or evidence["logs"].get(name, {}).get("final_marker_present") or evidence["logs"].get(name, {}).get("tool_marker_present"):
                raise RunnerError("failure", "relay_adjacent_log_contains_payload_marker")
        current_step = "container_inspection"
        states = inspect_container_states()
        evidence["resources"]["container_states"] = states
        state_by_service = {row["service"]: row for row in states}
        expected_running = ("fake-provider", "hermes", "facade")
        if any(name not in state_by_service or not state_by_service[name]["running"] for name in expected_running):
            raise RunnerError("failure", "proof_service_not_running")
        expected_complete = ("state-init", "seed")
        if any(name not in state_by_service or state_by_service[name]["running"] or state_by_service[name]["exit_code"] != 0 for name in expected_complete):
            raise RunnerError("failure", "proof_bootstrap_not_complete")
        for name, expected_limit in evidence["resources"]["configured_memory_caps_bytes"].items():
            # relay-client runs with --rm, so Docker removes each completed container before inspection.
            # Its positive service cap was checked in resolved Compose config above.
            if name == "relay-client":
                continue
            if name not in state_by_service or state_by_service[name]["configured_memory_limit_bytes"] != expected_limit:
                raise RunnerError("failure", "runtime_memory_limit_mismatch_" + name)
        if any(row["oom_killed"] for row in states):
            raise RunnerError("failure", "container_reported_oom_killed")
        if any(row["exit_code"] == 137 and not row["oom_killed"] for row in states):
            evidence["resources"]["exit_137_without_oom_evidence"] = True
        current_step = "contract_validation"
        validate_run_outputs(evidence["phases"]["cases"], evidence["checks"]["artifact_database_and_files"], evidence["checks"]["provider_tool_trace"]["cases"], evidence)
        evidence["checks"]["end_to_end_contract"] = {"pass": True}
        evidence["verdict"] = "PASS"
    except RunnerError as exc:
        error = {"kind": exc.kind, "step": exc.step}
        if CLIENT_FAILURES:
            error["client_diagnostic"] = CLIENT_FAILURES[-1]
        evidence["failure"] = error
        evidence["verdict"] = "FAIL" if exc.kind == "failure" else "INCONCLUSIVE"
    except Exception as exc:
        error = {"kind": "infrastructure", "step": current_step + "_" + type(exc).__name__}
        evidence["failure"] = error
        evidence["verdict"] = "INCONCLUSIVE"
    finally:
        evidence["logs"].setdefault("relay_client_output", CLIENT_OUTPUT_SCANS)
        if project_created and not any(name in evidence["resources"] for name in ("container_states",)):
            try:
                evidence["resources"]["container_states"] = inspect_container_states()
            except RunnerError as exc:
                evidence["resources"]["container_inspection_error"] = exc.step
        if project_created and "provider_tool_trace" not in evidence:
            try:
                evidence["provider_tool_trace"] = {"pass": False, "cases": inspect_fake_provider()}
            except RunnerError as exc:
                evidence["provider_tool_trace_error"] = exc.step
        if project_created and "artifact_database_and_files" not in evidence:
            try:
                evidence["artifact_database_and_files"] = {"pass": False, **inspect_facade_state()}
            except RunnerError as exc:
                evidence["artifact_database_error"] = exc.step
        if monitor is not None:
            evidence["resources"].update(monitor.finish())
        if project_created:
            stopped = compose("stop", "hermes", "facade", "fake-provider", timeout=90)
            evidence["resources"]["proof_containers_stopped"] = stopped.returncode == 0
        try:
            remaining = docker_ids("ps", "--quiet", "--no-trunc")
            evidence["resources"]["preexisting_containers_preserved"] = baseline_running.issubset(remaining)
        except RunnerError:
            evidence["resources"]["preexisting_containers_preserved"] = False
        evidence["resources"]["proof_volumes_removed"] = False
        try:
            evidence["resources"]["proof_volume_count_after_stop"] = len(project_volumes())
        except RunnerError:
            evidence["resources"]["proof_volume_count_after_stop"] = None
        if evidence.get("verdict") == "PASS":
            if evidence["resources"].get("preexisting_containers_preserved") is not True:
                evidence["verdict"] = "FAIL"
                error = {"kind": "failure", "step": "preexisting_container_changed"}
                evidence["failure"] = error
            elif evidence["resources"].get("proof_containers_stopped") is not True:
                evidence["verdict"] = "FAIL"
                error = {"kind": "failure", "step": "proof_containers_not_stopped"}
                evidence["failure"] = error
            elif evidence["resources"].get("proof_volume_count_after_stop") != 3:
                evidence["verdict"] = "FAIL"
                error = {"kind": "failure", "step": "proof_volumes_not_preserved"}
                evidence["failure"] = error
        EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_FILE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    diagnostic_summary = {}
    provider_cases = evidence.get("provider_tool_trace", {}).get("cases", {})
    if isinstance(provider_cases, dict) and "restart_10k" in provider_cases:
        provider = provider_cases["restart_10k"]
        diagnostic_summary["restart_provider"] = {
            "provider_turns": provider.get("provider_turns"),
            "tool_calls_requested": provider.get("tool_calls_requested"),
            "report_tool_call_count": provider.get("report_tool_call_count"),
            "report_tool_result_codes": provider.get("report_tool_result_codes"),
            "poc_tool_offered": provider.get("poc_tool_offered"),
            "tools_field_types": provider.get("tools_field_types"),
            "tools_field_counts": provider.get("tools_field_counts"),
            "functions_field_types": provider.get("functions_field_types"),
            "functions_field_counts": provider.get("functions_field_counts"),
        }
    if isinstance(provider_cases, dict) and "success_10k" in provider_cases:
        provider = provider_cases["success_10k"]
        diagnostic_summary["success_10k_provider"] = {
            "provider_turns": provider.get("provider_turns"),
            "tool_calls_requested": provider.get("tool_calls_requested"),
            "report_tool_call_count": provider.get("report_tool_call_count"),
            "report_tool_result_codes": provider.get("report_tool_result_codes"),
            "poc_tool_offered": provider.get("poc_tool_offered"),
            "tools_field_types": provider.get("tools_field_types"),
            "tools_field_counts": provider.get("tools_field_counts"),
            "functions_field_types": provider.get("functions_field_types"),
            "functions_field_counts": provider.get("functions_field_counts"),
        }
    case_results = evidence.get("phases", {}).get("cases", {}).get("cases", [])
    if isinstance(case_results, list):
        allowed_statuses = {"queued", "running", "completed", "failed", "uncertain"}
        allowed_presentations = {"ready", "failed"}
        known_cases = {
            "success_10k", "success_30k", "writer_redelivery", "no_writer", "writer_fail",
            "writer_slow", "wrong_binding", "malformed_json", "oversized_final",
            "missing_speech", "hook_exception", "hook_empty", "hook_timeout", "late_mutation",
            "lost_envelope_10k",
        }
        safe_cases = []
        for case_row in case_results:
            if not isinstance(case_row, dict):
                continue
            case_name = case_row.get("case")
            if not isinstance(case_name, str) or case_name not in known_cases:
                continue
            task_status = case_row.get("task_status")
            presentation_status = case_row.get("presentation_status")
            task_error_code = case_row.get("task_error_code")
            raw_methods = case_row.get("http_methods", [])
            raw_statuses = case_row.get("http_statuses", [])
            outcome = {
                "case": case_name,
                "task_status": task_status if isinstance(task_status, str) and task_status in allowed_statuses else None,
                "presentation_status": presentation_status if isinstance(presentation_status, str) and presentation_status in allowed_presentations else None,
                "task_error_code": task_error_code if isinstance(task_error_code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,80}", task_error_code) else None,
                "task_run_id_persisted": case_row.get("task_run_id_persisted") is True,
                "artifact_ref_present": case_row.get("artifact_ref_present") is True,
                "relay_wire_headers_valid": case_row.get("relay_wire_headers_valid") is True,
                "relay_max_response_bytes": case_row.get("relay_max_response_bytes") if type(case_row.get("relay_max_response_bytes")) is int and 0 <= case_row.get("relay_max_response_bytes") <= 100000 else None,
                "http_methods": [method for method in raw_methods if isinstance(method, str) and method in {"GET", "POST"}] if isinstance(raw_methods, list) else [],
                "http_statuses": [status for status in raw_statuses if type(status) is int and 100 <= status <= 599] if isinstance(raw_statuses, list) else [],
                "report_marker_in_relay_response": case_row.get("report_marker_in_relay_response") is True,
                "final_marker_in_relay_response": case_row.get("final_marker_in_relay_response") is True,
                "tool_marker_in_relay_response": case_row.get("tool_marker_in_relay_response") is True,
            }
            safe_cases.append(outcome)
        diagnostic_summary["case_outcomes"] = safe_cases
    start_phase = evidence.get("phases", {}).get("restart_start")
    if isinstance(start_phase, dict):
        diagnostic_summary["restart_start"] = {
            "task_status": start_phase.get("task_status"),
            "run_id_persisted": start_phase.get("run_id_persisted"),
            "relay_methods_before_restart": start_phase.get("relay_methods_before_restart"),
        }
    resume_phase = evidence.get("phases", {}).get("restart_resume")
    if isinstance(resume_phase, dict):
        diagnostic_summary["restart_resume"] = {
            "task_status": resume_phase.get("task_status"),
            "same_run_id": resume_phase.get("same_run_id"),
            "get_only_after_restart": resume_phase.get("get_only_after_restart"),
            "presentation_status": resume_phase.get("presentation_status"),
            "task_error_code": resume_phase.get("task_error_code"),
            "report_marker_in_relay_response": resume_phase.get("report_marker_in_relay_response"),
            "final_marker_in_relay_response": resume_phase.get("final_marker_in_relay_response"),
            "tool_marker_in_relay_response": resume_phase.get("tool_marker_in_relay_response"),
        }
    db_summaries = evidence.get("artifact_database_and_files", {}).get("request_case_summaries", [])
    restart_rows = [row for row in db_summaries if isinstance(row, dict) and row.get("case") == "restart_10k"]
    if restart_rows:
        row = restart_rows[0]
        diagnostic_summary["restart_facade"] = {
            "request_count": row.get("request_count"),
            "submit_states": row.get("submit_states"),
            "run_id_persisted_count": row.get("run_id_persisted_count"),
            "upstream_post_counts": row.get("upstream_post_counts"),
            "submit_upstream_statuses": row.get("submit_upstream_statuses"),
            "submit_upstream_errors": row.get("submit_upstream_errors"),
            "remote_failure_types": row.get("remote_failure_types"),
            "remote_failure_categories": row.get("remote_failure_categories"),
            "terminal_upstream_statuses": row.get("terminal_upstream_statuses"),
            "terminal_upstream_errors": row.get("terminal_upstream_errors"),
            "terminal_upstream_codes": row.get("terminal_upstream_codes"),
            "hermes_run_statuses": row.get("hermes_run_statuses"),
            "writer_call_counts": row.get("writer_call_counts"),
            "writer_result_codes": row.get("writer_result_codes"),
            "writer_report_byte_counts": row.get("writer_report_byte_counts"),
            "artifact_statuses": row.get("artifact_statuses"),
        }
    facade_case_rows = evidence.get("artifact_database_and_files", {}).get("request_case_summaries", [])
    if isinstance(facade_case_rows, list):
        for case_name in ("success_10k", "restart_10k"):
            rows = [row for row in facade_case_rows if isinstance(row, dict) and row.get("case") == case_name]
            if rows:
                row = rows[0]
                diagnostic_summary[case_name + "_facade"] = {
                    "terminal_upstream_statuses": row.get("terminal_upstream_statuses"),
                    "terminal_upstream_errors": row.get("terminal_upstream_errors"),
                    "terminal_upstream_codes": row.get("terminal_upstream_codes"),
                    "hermes_run_statuses": row.get("hermes_run_statuses"),
                    "writer_call_counts": row.get("writer_call_counts"),
                    "writer_result_codes": row.get("writer_result_codes"),
                    "writer_report_byte_counts": row.get("writer_report_byte_counts"),
                    "artifact_statuses": row.get("artifact_statuses"),
                }
    toolset_check = evidence.get("checks", {}).get("hermes_api_toolset")
    if isinstance(toolset_check, dict):
        diagnostic_summary["hermes_api_toolset"] = {
            "http_status": toolset_check.get("http_status"),
            "toolset_seen": toolset_check.get("toolset_seen") is True,
            "toolset_enabled": toolset_check.get("toolset_enabled") is True,
            "tool_registered": toolset_check.get("tool_registered") is True,
        }
    plugin_check = evidence.get("checks", {}).get("hermes_plugin_registration")
    if isinstance(plugin_check, dict):
        diagnostic_summary["hermes_plugin_registration"] = {
            "manifest_seen": plugin_check.get("manifest_seen") is True,
            "tool_registered": plugin_check.get("tool_registered") is True,
            "hook_registered": plugin_check.get("hook_registered") is True,
            "plugin_load_failed": plugin_check.get("plugin_load_failed") is True,
            "plugin_load_error_type": plugin_check.get("plugin_load_error_type"),
            "plugin_not_enabled": plugin_check.get("plugin_not_enabled") is True,
        }
    safe_output = {
        "phase": "option_d_poc",
        "verdict": evidence["verdict"],
        "server_version": evidence["server"]["version"],
        "image_digest": IMAGE.rsplit("@sha256:", 1)[-1],
        "checks": {name: row.get("pass") for name, row in evidence.get("checks", {}).items()},
        "failure": error,
        "diagnostics": diagnostic_summary,
        "evidence_file": str(EVIDENCE_FILE.relative_to(ROOT)),
    }
    print(json.dumps(safe_output, separators=(",", ":")))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())