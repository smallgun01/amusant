#!/usr/bin/env python3
"""Seed only the unique Hermes test volume; refuse incompatible existing state."""
import hashlib
import json
import os
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
case = os.environ.get("POC_TOOLSET_CASE", "e1").strip().lower()
if case not in {"e1", "case_a", "case_b"}:
    raise SystemExit("Refusing unknown test-only toolset case")
source = Path({
    "e1": "/poc/hermes-config.yaml",
    "case_a": "/poc/hermes-config-toolset-a.yaml",
    "case_b": "/poc/hermes-config-toolset-b.yaml",
}[case])
destination = home / "config.yaml"
expected = source.read_bytes()
home.mkdir(parents=True, exist_ok=True)
required = (
    b"provider: custom",
    b"default: local-poc-model",
    b"base_url: http://fake-provider:8000/v1",
    b"api_mode: chat_completions",
    b"streaming: false",
    b"unattended_mode: deny",
    b"single_query_mode: deny",
    b"amusant-option-d",
    b"hook_callback_timeout: 0.15",
    b"platform_toolsets:",
    b"api_server:",
    b"amusant_option_d",
)
if case in {"case_a", "case_b"}:
    required += (
        b"no_mcp",
        b"h12preflight",
        b"preflight_probe",
        b"enabled:\n    - amusant-option-d",
        b"tool_search:",
        b'enabled: "on"',
        (b"- web" if case == "case_b" else b"- no_mcp"),
        (b"search_backend: ddgs" if case == "case_b" else b"api_server:"),
        b"base_url: http://fake-provider:8000/v1",
    )
if destination.exists():
    current = destination.read_bytes()
    if not all(item in current for item in required):
        raise SystemExit("Refusing to overwrite Hermes config: test volume is incompatible")
    action = "existing-compatible-preserved" if current != expected else "already-present-and-matching"
    actual = current
else:
    destination.write_bytes(expected)
    action = "seeded"
    actual = expected
if case in {"case_a", "case_b"}:
    if yaml is None:
        raise SystemExit("PyYAML is required to emit sanitized effective-config evidence")
    config = yaml.safe_load(actual) or {}
    platform_toolsets = config.get("platform_toolsets") or {}
    api_toolsets = platform_toolsets.get("api_server") or []
    plugin_names = (config.get("plugins") or {}).get("enabled") or []
    mcp_servers = config.get("mcp_servers") or {}
    disabled_toolsets = (config.get("agent") or {}).get("disabled_toolsets") or []
    evidence = {
        "case": case,
        "config_sha256": hashlib.sha256(actual).hexdigest(),
        "configured_api_server_toolsets": sorted(str(item) for item in api_toolsets),
        "enabled_plugins": sorted(str(item) for item in plugin_names),
        "configured_mcp_servers": sorted(str(item) for item in mcp_servers),
        "enabled_mcp_servers": sorted(
            str(name) for name, value in mcp_servers.items()
            if isinstance(value, dict) and value.get("enabled") is True
        ),
        "no_mcp_sentinel_configured": "no_mcp" in api_toolsets,
        "disabled_toolsets": sorted(str(item) for item in disabled_toolsets),
        "tool_search_enabled": (config.get("tools") or {}).get("tool_search", {}).get("enabled"),
        "provider_base_url_is_local_fake": (config.get("model") or {}).get("base_url") == "http://fake-provider:8000/v1",
        "no_secret_values_recorded": True,
    }
    (home / ".h12-toolset-config-evidence.json").write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
print(json.dumps({
    "action": action,
    "case": case,
    "config_sha256": hashlib.sha256(actual).hexdigest(),
    "bytes": len(actual),
}, separators=(",", ":")))
