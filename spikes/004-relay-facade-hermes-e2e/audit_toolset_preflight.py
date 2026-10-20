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
    "case_b": {"search", "amusant_option_d", "no_mcp"},
}[CASE]
REQUIRED_DISABLED = {"file", "terminal", "browser", "code_execution", "process_manage"}
ALLOWED_BRIDGE_DENIALS = {"registered_not_deferred", "unknown_tool_name", "session_scope_denied"}
PROBE_NAMES = {
    "write_file", "terminal", "browser_navigate", "browser_snapshot", "execute_code",
    "process_manage", "web_extract", "web_search", "mcp__h12preflight__preflight_probe",
}
EXPECTED_BRIDGE_PROBES = PROBE_NAMES - ({"web_search"} if CASE == "case_b" else set())
EXPECTED_DIRECT_PROBES = set(EXPECTED_BRIDGE_PROBES)


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main():
    config = load(HOME / ".h12-toolset-config-evidence.json")
    provider = load(EVIDENCE / f"toolset-provider-{CASE}.json")
    runner = load(EVIDENCE / f"toolset-runner-{CASE}.json")
    network = load(EVIDENCE / "network-isolation.json")
    mcp_started = (HOME / ".h12-preflight-mcp-started").exists()
    mcp_called = (HOME / ".h12-preflight-mcp-called").exists()
    write_file_marker = (HOME / f".h12-{CASE}-write-file-executed").exists()

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
        "model_visible_tools_by_request": provider.get("model_visible_tools_by_request", []) if isinstance(provider, dict) else [],
        "model_visible_tool_mismatches": provider.get("model_visible_tool_mismatches", []) if isinstance(provider, dict) else [],
        "hermes_api_toolsets_http_status": provider.get("hermes_api_toolsets", {}).get("http_status") if isinstance(provider, dict) else None,
        "hermes_api_toolset_names": sorted(provider.get("hermes_api_toolsets", {}).get("toolset_names", [])) if isinstance(provider, dict) else [],
        "tool_search": provider.get("tool_search") if isinstance(provider, dict) else None,
        "tool_search_exchange": provider.get("tool_search_exchange") if isinstance(provider, dict) else None,
        "tool_describe": provider.get("tool_describe") if isinstance(provider, dict) else None,
        "tool_describe_exchange": provider.get("tool_describe_exchange") if isinstance(provider, dict) else None,
        "authorized_save_report": provider.get("authorized_save_report") if isinstance(provider, dict) else None,
        "authorized_save_report_exchange": provider.get("authorized_save_report_exchange") if isinstance(provider, dict) else None,
        "bridge_refusals": provider.get("bridge_refusals", {}) if isinstance(provider, dict) else {},
        "bridge_probe_results": provider.get("bridge_probe_results", {}) if isinstance(provider, dict) else {},
        "direct_tool_denials": provider.get("direct_tool_denials", {}) if isinstance(provider, dict) else {},
        "mcp_server_started": mcp_started,
        "mcp_tool_called": mcp_called,
        "mcp_startup_is_not_the_policy_assertion": True,
        "write_file_marker_created": write_file_marker,
        "execution_probe_hits": provider.get("browser_probe_hits", []) if isinstance(provider, dict) else [],
        "network_isolation": network if isinstance(network, dict) else None,
        "runner_status": runner.get("status") if isinstance(runner, dict) else None,
        "terminal_status": runner.get("terminal_status") if isinstance(runner, dict) else None,
        "artifact_committed": bool(runner and runner.get("artifact_committed")),
        "presentation_ready": bool(runner and runner.get("presentation_ready")),
        "no_secrets_prompts_or_full_results_recorded": True,
    }

    expected_visible = {"tool_search", "tool_describe", "tool_call"} | ({"web_search"} if CASE == "case_b" else set())
    provider_status = provider.get("status") if isinstance(provider, dict) else None
    structural_ok = (
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
        and provider_status == "PASS"
        and provider.get("hermes_api_toolsets", {}).get("http_status") == 200
        and set(provider.get("model_visible_tools", [])) == expected_visible
        and bool(provider.get("model_visible_tools_by_request"))
        and not provider.get("model_visible_tool_mismatches")
        and all(not row or set(row) == expected_visible for row in provider.get("model_visible_tools_by_request", []))
        and provider.get("tool_search") == {"total_available": 1, "matches": ["save_report"]}
        and provider.get("tool_search_exchange", {}).get("matched_call_result_id") is True
        and provider.get("tool_search_exchange", {}).get("result_role") == "tool"
        and provider.get("tool_search_exchange", {}).get("result_name") == "tool_search"
        and provider.get("tool_describe", {}).get("described_names") == ["save_report"]
        and provider.get("tool_describe", {}).get("not_found_names") is not None
        and "mcp__h12preflight__preflight_probe" in provider.get("tool_describe", {}).get("not_found_names", [])
        and provider.get("tool_describe_exchange", {}).get("matched_call_result_id") is True
        and provider.get("tool_describe_exchange", {}).get("result_role") == "tool"
        and provider.get("tool_describe_exchange", {}).get("result_name") == "tool_describe"
        and provider.get("authorized_save_report") == "COMMITTED"
        and provider.get("authorized_save_report_exchange", {}).get("matched_call_result_id") is True
        and provider.get("authorized_save_report_exchange", {}).get("result_role") == "tool"
        and provider.get("authorized_save_report_exchange", {}).get("result_name") == "tool_call"
        and set(provider.get("bridge_probe_results", {})) >= EXPECTED_BRIDGE_PROBES
        and set(provider.get("direct_forged_call_names", [])) == EXPECTED_DIRECT_PROBES
        and set(provider.get("direct_tool_denials", {})) == EXPECTED_DIRECT_PROBES
        and not mcp_called and not write_file_marker
        and provider.get("browser_probe_hits") == []
        and isinstance(network, dict)
        and network.get("status") == "PASS"
        and network.get("external_egress_disabled") is True
        and network.get("networks_internal") == {"hermes_api": True, "relay_api": True}
        and runner is not None and runner.get("status") == "PASS"
        and runner.get("terminal_status") == "completed"
        and runner.get("artifact_committed") is True
        and runner.get("presentation_ready") is True
    )
    bridge_ok = all(
        isinstance(provider.get("bridge_probe_results", {}).get(name), dict)
        and provider["bridge_probe_results"][name].get("matched_call_result_id") is True
        and provider["bridge_probe_results"][name].get("result_role") == "tool"
        and provider["bridge_probe_results"][name].get("result_name") == "tool_call"
        and provider["bridge_probe_results"][name].get("classification") in ALLOWED_BRIDGE_DENIALS
        and provider["bridge_probe_results"][name].get("dispatch_prevented") is True
        and provider["bridge_probe_results"][name].get("denial_stage") == "hermes_tool_search_deferred_name_validation"
        for name in EXPECTED_BRIDGE_PROBES
    ) if isinstance(provider, dict) else False
    direct_ok = all(
        isinstance(provider.get("direct_tool_denials", {}).get(name), dict)
        and provider["direct_tool_denials"][name].get("matched_call_result_id") is True
        and provider["direct_tool_denials"][name].get("result_role") == "tool"
        and provider["direct_tool_denials"][name].get("result_name") == name
        and provider["direct_tool_denials"][name].get("classification") == "unknown_tool_name_rejected"
        and provider["direct_tool_denials"][name].get("dispatch_prevented") is True
        and provider["direct_tool_denials"][name].get("denial_stage") == "agent_valid_tool_name_filter"
        for name in EXPECTED_DIRECT_PROBES
    ) if isinstance(provider, dict) else False
    case_b_web_search_skip = CASE != "case_b" or (
        isinstance(provider, dict)
        and provider.get("bridge_probe_results", {}).get("web_search", {}).get("status") == "SKIPPED_AUTHORIZED_DIRECT"
        and provider.get("bridge_probe_results", {}).get("web_search", {}).get("dispatch_prevented") is None
        and "web_search" not in provider.get("direct_tool_denials", {})
    )
    evidence["bridge_denials_structurally_proven"] = bridge_ok
    evidence["direct_denials_structurally_proven"] = direct_ok
    evidence["case_b_web_search_authorized_and_not_invoked"] = case_b_web_search_skip

    if structural_ok and bridge_ok and direct_ok and case_b_web_search_skip:
        evidence["status"] = "PASS"
        evidence["failure_code"] = None
    elif mcp_called or write_file_marker or (isinstance(provider, dict) and provider.get("browser_probe_hits")):
        evidence["status"] = "FAIL"
        evidence["failure_code"] = "unauthorized_tool_side_effect_observed"
    elif provider_status == "FAIL":
        evidence["status"] = "FAIL"
        evidence["failure_code"] = "provider_preflight_failed"
    else:
        evidence["status"] = "UNKNOWN"
        evidence["failure_code"] = "effective_toolset_denial_or_network_isolation_not_proven"

    (EVIDENCE / f"toolset-audit-{CASE}.json").write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(json.dumps({"case": CASE, "status": evidence["status"], "failure_code": evidence["failure_code"]}, separators=(",", ":")))
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

