#!/usr/bin/env python3
"""Read-only, networkless audit of disposable Hermes Home and sanitized run evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path

CASE = os.environ["POC_TOOLSET_CASE"]
EVIDENCE = Path("/evidence")
HOME = Path("/audit-home")
EXPECTED_TOOLSETS = {
    "case_a": {"amusant_option_d", "no_mcp"},
    "case_b": {"web", "amusant_option_d", "no_mcp"},
}[CASE]
REQUIRED_DISABLED = {"file", "terminal", "browser", "code_execution", "web_extract", "process_manage"}
ALLOWED_REFUSALS = {"registered_not_deferred", "unknown_tool_name", "session_scope_denied"}
PROBE_NAMES = {
    "write_file", "terminal", "browser_navigate", "browser_snapshot", "execute_code",
    "process_manage", "web_extract", "web_search", "mcp__h12preflight__preflight_probe",
}


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main():
    config = load(HOME / ".h12-toolset-config-evidence.json")
    provider = load(EVIDENCE / f"toolset-provider-h12_toolset_{CASE}.json")
    runner = load(EVIDENCE / f"toolset-runner-{CASE}.json")
    mcp_started = (HOME / ".h12-preflight-mcp-started").exists()
    mcp_called = (HOME / ".h12-preflight-mcp-called").exists()
    write_file_marker = (HOME / ".h12-preflight-write-file-executed").exists()

    evidence = {
        "case": CASE,
        "status": "UNKNOWN",
        "config_evidence_present": isinstance(config, dict),
        "configured_api_server_toolsets": sorted(config.get("configured_api_server_toolsets", [])) if isinstance(config, dict) else [],
        "enabled_plugins": sorted(config.get("enabled_plugins", [])) if isinstance(config, dict) else [],
        "enabled_mcp_servers": sorted(config.get("enabled_mcp_servers", [])) if isinstance(config, dict) else [],
        "no_mcp_sentinel_configured": bool(config and config.get("no_mcp_sentinel_configured")),
        "disabled_toolsets": sorted(config.get("disabled_toolsets", [])) if isinstance(config, dict) else [],
        "fake_provider_only": bool(config and config.get("provider_base_url_is_local_fake")),
        "provider_evidence_present": isinstance(provider, dict),
        "model_visible_tools": sorted(provider.get("model_visible_tools", [])) if isinstance(provider, dict) else [],
        "hermes_api_toolsets_http_status": provider.get("hermes_api_toolsets", {}).get("http_status") if isinstance(provider, dict) else None,
        "hermes_api_toolset_names": sorted(provider.get("hermes_api_toolsets", {}).get("toolset_names", [])) if isinstance(provider, dict) else [],
        "tool_search": provider.get("tool_search") if isinstance(provider, dict) else None,
        "tool_describe": provider.get("tool_describe") if isinstance(provider, dict) else None,
        "authorized_save_report": provider.get("authorized_save_report") if isinstance(provider, dict) else None,
        "bridge_refusals": provider.get("bridge_refusals", {}) if isinstance(provider, dict) else {},
        "mcp_server_started": mcp_started,
        "mcp_tool_called": mcp_called,
        "write_file_marker_created": write_file_marker,
        "browser_terminal_code_probe_hits": sorted(provider.get("browser_probe_hits", [])) if isinstance(provider, dict) else [],
        "runner_status": runner.get("status") if isinstance(runner, dict) else None,
        "terminal_status": runner.get("terminal_status") if isinstance(runner, dict) else None,
        "artifact_committed": bool(runner and runner.get("artifact_committed")),
        "presentation_ready": bool(runner and runner.get("presentation_ready")),
        "no_secrets_prompts_or_full_results_recorded": True,
    }

    expected_visible = {"tool_search", "tool_describe", "tool_call"} | ({"web_search"} if CASE == "case_b" else set())
    expected_refusals = set(PROBE_NAMES)
    valid = (
        config is not None
        and config.get("case") == CASE
        and set(config.get("configured_api_server_toolsets", [])) == EXPECTED_TOOLSETS
        and config.get("enabled_plugins") == ["amusant-option-d"]
        and config.get("configured_mcp_servers") == ["h12preflight"]
        and config.get("enabled_mcp_servers") == ["h12preflight"]
        and config.get("no_mcp_sentinel_configured") is True
        and REQUIRED_DISABLED <= set(config.get("disabled_toolsets", []))
        and config.get("tool_search_enabled") == "on"
        and config.get("provider_base_url_is_local_fake") is True
        and provider is not None
        and provider.get("status") == "PASS"
        and provider.get("hermes_api_toolsets", {}).get("http_status") == 200
        and set(provider.get("model_visible_tools", [])) == expected_visible
        and provider.get("tool_search") == {"total_available": 1, "matches": ["save_report"]}
        and provider.get("tool_describe", {}).get("described_names") == ["save_report"]
        and provider.get("authorized_save_report") == "COMMITTED"
        and set(provider.get("bridge_refusals", {})) == expected_refusals
        and set(provider.get("bridge_refusals", {}).values()) <= ALLOWED_REFUSALS
        and not mcp_started and not mcp_called and not write_file_marker
        and provider.get("browser_probe_hits") == []
        and runner is not None and runner.get("status") == "PASS"
        and runner.get("terminal_status") == "completed"
        and runner.get("artifact_committed") is True
        and runner.get("presentation_ready") is True
    )
    if valid:
        evidence["status"] = "PASS"
    elif (
        config is None or provider is None or runner is None
        or provider.get("status") == "UNKNOWN"
        or provider.get("hermes_api_toolsets", {}).get("http_status") != 200
        or runner.get("status") == "UNKNOWN"
    ):
        evidence["status"] = "UNKNOWN"
    else:
        evidence["status"] = "FAIL"
    evidence["failure_code"] = None if valid else "effective_toolset_or_side_effect_gate_not_proven"
    (EVIDENCE / f"toolset-audit-{CASE}.json").write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(json.dumps({"case": CASE, "status": evidence["status"], "failure_code": evidence["failure_code"]}, separators=(",", ":")))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
