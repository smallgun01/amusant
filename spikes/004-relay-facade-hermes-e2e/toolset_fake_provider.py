#!/usr/bin/env python3
"""Deterministic no-provider toolset audit flow for Hermes v2026.9.24."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import re
import time

EVIDENCE = Path("/evidence")
HERMES_HOST = os.environ.get("POC_HERMES_API_HOST", "172.30.64.3")
HERMES_PORT = int(os.environ.get("POC_HERMES_API_PORT", "8642"))
HERMES_KEY_FILE = Path(os.environ.get("POC_HERMES_KEY_FILE", "/run/test-secrets/hermes_api_key"))
CASE_PREFIX = "h12_toolset_"
BRIDGES = {"tool_search", "tool_describe", "tool_call"}
PROBES = [
    ("write_file", {"path": "/opt/data/.h12-preflight-write-file-executed", "content": "synthetic-denied-probe"}),
    ("terminal", {"command": "python3 -c \"from urllib.request import urlopen; urlopen('http://fake-provider:8000/h12-preflight-terminal-probe')\""}),
    ("browser_navigate", {"url": "http://fake-provider:8000/h12-preflight-browser-probe"}),
    ("browser_snapshot", {"url": "http://fake-provider:8000/h12-preflight-browser-probe"}),
    ("execute_code", {"code": "from urllib.request import urlopen; urlopen('http://fake-provider:8000/h12-preflight-code-probe')"}),
    ("process_manage", {"action": "start", "command": "python3 -c pass"}),
    ("web_extract", {"url": "https://example.invalid/h12-preflight"}),
    ("web_search", {"query": "offline toolset preflight must not search"}),
    ("mcp__h12preflight__preflight_probe", {"query": "synthetic MCP probe"}),
]
SEARCH_QUERIES = [
    "save_report", "write_file", "terminal", "browser_navigate",
    "execute_code", "web_search", "mcp__h12preflight__preflight_probe",
]


def _visibility_mismatch(observed, expected):
    observed = set(observed)
    expected = set(expected)
    if not observed:
        # Hermes may make a final model request with tools omitted; an empty
        # schema grants less access and is therefore safe for this allowlist gate.
        return {"extra": [], "missing": []}
    return {
        "extra": sorted(observed - expected),
        "missing": sorted(expected - observed),
    }


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item.get("text", "")) for item in value if isinstance(item, dict))
    return ""


def _bridge_calls(messages):
    found = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or function.get("name") not in BRIDGES:
                continue
            raw = function.get("arguments")
            try:
                args = raw if isinstance(raw, dict) else json.loads(raw) if isinstance(raw, str) else {}
            except (ValueError, TypeError):
                args = {}
            targets = []
            if function.get("name") == "tool_call":
                targets = [
                    row.get("name") for row in args.get("calls", [])
                    if isinstance(row, dict) and isinstance(row.get("name"), str)
                ]
            found.append({"id": call.get("id"), "action": function["name"], "args": args, "targets": targets})
    return found


def _results_by_id(messages, calls):
    ids = {str(call.get("id")): call for call in calls if call.get("id") is not None}
    results = {}
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id", ""))
        if call_id in ids:
            results[call_id] = _content_text(message.get("content", ""))
    return results


def _get_toolsets():
    try:
        key = HERMES_KEY_FILE.read_text(encoding="utf-8").strip()
        connection = http.client.HTTPConnection(HERMES_HOST, HERMES_PORT, timeout=3)
        try:
            connection.request("GET", "/v1/toolsets", headers={"Authorization": "Bearer " + key})
            response = connection.getresponse()
            raw = response.read(256 * 1024)
            parsed = json.loads(raw or b"{}")
            names = set()

            def visit(value, depth=0):
                if depth > 8:
                    return
                if isinstance(value, dict):
                    for field in ("name", "toolset", "toolset_name"):
                        candidate = value.get(field)
                        if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", candidate):
                            names.add(candidate)
                    for item in value.values():
                        visit(item, depth + 1)
                elif isinstance(value, list):
                    for item in value:
                        visit(item, depth + 1)

            visit(parsed)
            return {"http_status": response.status, "toolset_names": sorted(names)}
        finally:
            connection.close()
    except Exception as exc:
        # Error type only; never persist response bodies, keys, or exception text.
        return {"http_status": None, "toolset_names": [], "error_type": type(exc).__name__}


def _decode_json(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _walk(value, depth=0):
    if depth > 10:
        return
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, depth + 1)
    elif isinstance(value, str):
        parsed = _decode_json(value)
        if parsed is not None and parsed != value:
            yield from _walk(parsed, depth + 1)


def _tool_search_summary(raw):
    value = _decode_json(raw)
    if not isinstance(value, dict):
        return None
    matches = set()
    for group in value.get("results", []) if isinstance(value.get("results"), list) else []:
        if isinstance(group, dict):
            matches.update(name for name in group.get("matches", []) if isinstance(name, str))
    return {"total_available": value.get("total_available"), "matches": sorted(matches)}


def _tool_describe_summary(raw):
    value = _decode_json(raw)
    if not isinstance(value, dict):
        return None
    tools = value.get("tools") if isinstance(value.get("tools"), dict) else {}
    errors = value.get("errors") if isinstance(value.get("errors"), dict) else {}
    not_found = value.get("not_found") if isinstance(value.get("not_found"), list) else []
    return {
        "described_names": sorted(name for name in tools if isinstance(name, str)),
        "error_names": sorted(name for name in errors if isinstance(name, str)),
        "not_found_names": sorted(name for name in not_found if isinstance(name, str)),
    }


def _commit_confirmed(raw):
    parsed = _decode_json(raw)
    return any(item.get("committed") is True for item in _walk(parsed)) if parsed is not None else False


def _has_structured_error(raw):
    parsed = _decode_json(raw)
    return parsed is not None and any(
        bool(item.get("error")) or item.get("isError") is True
        for item in _walk(parsed)
    )


def _bridge_rejection(raw):
    parsed = _decode_json(raw)
    texts = []
    for item in _walk(parsed):
        for value in item.values():
            if isinstance(value, str):
                texts.append(value.lower())
    joined = " ".join(texts)
    if "directly-listed tool, not a deferred one" in joined:
        return "registered_not_deferred"
    if "not a known tool name" in joined:
        return "unknown_tool_name"
    if "not available in this session" in joined:
        return "session_scope_denied"
    return None


def _save_evidence(state, case, evidence):
    with state.lock:
        hits = sorted(set(getattr(state, "browser_probe_hits", [])))
    evidence["browser_probe_hits"] = hits
    evidence["updated_at_epoch"] = int(time.time())
    path = EVIDENCE / f"toolset-provider-{case}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json_text(evidence) + "\n", encoding="utf-8")
    tmp.replace(path)


def _call(action, args, sequence):
    return {
        "id": f"h12-{sequence}",
        "type": "function",
        "function": {"name": action, "arguments": _json_text(args)},
    }


def _response(handler, sequence, *, action=None, args=None, final=None):
    if action:
        message = {"role": "assistant", "content": None, "tool_calls": [_call(action, args, sequence)]}
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": final or "{}"}
        finish_reason = "stop"
    handler.send_json(200, {
        "id": f"chatcmpl-h12-toolset-{sequence}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "local-poc-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 8, "total_tokens": 16},
    })


def handle_toolset_case(handler, payload, messages, model_tools, case):
    """Drive one synthetic API Server run and refuse to continue on any unexpected tool."""
    state = handler.server.state
    is_case_b = case.endswith("case_b")
    expected_visible = BRIDGES | ({"web_search"} if is_case_b else set())
    observed_visible = sorted(set(model_tools))
    with state.lock:
        state.sequence += 1
        sequence = state.sequence
        evidence = getattr(state, "toolset_cases", {}).get(case)
        if evidence is None:
            evidence = {
                "case": case,
                "status": "UNKNOWN",
                "provider": "synthetic_fake_provider_only",
                "hermes_api_toolsets": _get_toolsets(),
                "model_visible_tools": [],
                "model_visible_tools_by_request": [],
                "model_visible_tool_mismatches": [],
                "tool_search": None,
                "tool_describe": None,
                "authorized_save_report": "NOT_RUN",
                "bridge_refusals": {},
                "browser_probe_hits": [],
                "no_prompt_or_report_bodies_recorded": True,
            }
            state.toolset_cases[case] = evidence
        request_index = len(evidence["model_visible_tools_by_request"]) + 1
        evidence["model_visible_tools_by_request"].append(observed_visible)
        evidence["model_visible_tools"] = sorted(
            set(evidence["model_visible_tools"]) | set(observed_visible)
        )
        mismatch = _visibility_mismatch(observed_visible, expected_visible)
        if mismatch["extra"] or mismatch["missing"]:
            evidence["model_visible_tool_mismatches"].append({
                "request_index": request_index,
                **mismatch,
            })

    if mismatch["extra"] or mismatch["missing"]:
        evidence["status"] = "FAIL"
        evidence["failure_code"] = "unexpected_model_visible_tools"
        _save_evidence(state, case, evidence)
        _response(handler, sequence, final=_presentation())
        return
    if evidence["hermes_api_toolsets"].get("http_status") != 200:
        evidence["status"] = "UNKNOWN"
        evidence["failure_code"] = "toolsets_endpoint_unavailable"
        _save_evidence(state, case, evidence)
        _response(handler, sequence, final=_presentation())
        return

    calls = _bridge_calls(messages)
    results = _results_by_id(messages, calls)
    search_call = next((call for call in calls if call["action"] == "tool_search"), None)
    describe_call = next((call for call in calls if call["action"] == "tool_describe"), None)
    underlying_calls = [call for call in calls if call["action"] == "tool_call"]

    if search_call is None:
        _save_evidence(state, case, evidence)
        _response(handler, sequence, action="tool_search", args={"queries": SEARCH_QUERIES, "limit": 20})
        return
    if evidence["tool_search"] is None:
        raw = results.get(str(search_call.get("id")), "")
        summary = _tool_search_summary(raw)
        evidence["tool_search"] = summary or {"total_available": None, "matches": []}
        if summary is None:
            evidence["status"] = "UNKNOWN"
            evidence["failure_code"] = "tool_search_result_unreadable"
            _save_evidence(state, case, evidence)
            _response(handler, sequence, final=_presentation())
            return
        if summary.get("total_available") != 1 or set(summary.get("matches", [])) != {"save_report"}:
            evidence["status"] = "FAIL"
            evidence["failure_code"] = "unexpected_deferred_catalog"
            _save_evidence(state, case, evidence)
            _response(handler, sequence, final=_presentation())
            return

    if describe_call is None:
        described = ["save_report", *(target for target, _ in PROBES)]
        _save_evidence(state, case, evidence)
        _response(handler, sequence, action="tool_describe", args={"names": described})
        return
    if evidence["tool_describe"] is None:
        raw = results.get(str(describe_call.get("id")), "")
        summary = _tool_describe_summary(raw)
        evidence["tool_describe"] = summary or {"described_names": [], "error_names": [], "not_found_names": []}
        if summary is None:
            evidence["status"] = "UNKNOWN"
            evidence["failure_code"] = "tool_describe_result_unreadable"
            _save_evidence(state, case, evidence)
            _response(handler, sequence, final=_presentation())
            return
        if set(summary["described_names"]) != {"save_report"}:
            evidence["status"] = "FAIL"
            evidence["failure_code"] = "unexpected_describe_catalog"
            _save_evidence(state, case, evidence)
            _response(handler, sequence, final=_presentation())
            return

    report_call = next((call for call in underlying_calls if "save_report" in call["targets"]), None)
    if report_call is None:
        _save_evidence(state, case, evidence)
        _response(handler, sequence, action="tool_call", args={"calls": [{
            "name": "save_report",
            "arguments": {"report": "H1.2 offline effective-toolset preflight synthetic report."},
        }]})
        return
    if evidence["authorized_save_report"] == "NOT_RUN":
        raw = results.get(str(report_call.get("id")), "")
        evidence["authorized_save_report"] = "COMMITTED" if _commit_confirmed(raw) else "NOT_CONFIRMED"
        if evidence["authorized_save_report"] != "COMMITTED":
            evidence["status"] = "FAIL"
            evidence["failure_code"] = "authorized_plugin_bridge_call_not_committed"
            _save_evidence(state, case, evidence)
            _response(handler, sequence, final=_presentation())
            return

    for target, _arguments in PROBES:
        prior = next((call for call in underlying_calls if target in call["targets"]), None)
        if prior is None:
            next_probe = {name: args for name, args in PROBES}[target]
            _save_evidence(state, case, evidence)
            _response(handler, sequence, action="tool_call", args={"calls": [{
                "name": target, "arguments": next_probe,
            }]})
            return
        raw = results.get(str(prior.get("id")), "")
        refusal = _bridge_rejection(raw)
        if refusal is None:
            evidence["bridge_refusals"][target] = "NOT_CONFIRMED"
            if _has_structured_error(raw):
                evidence["status"] = "UNKNOWN"
                evidence["failure_code"] = "bridge_error_not_classified"
            elif _decode_json(raw) is not None:
                evidence["status"] = "FAIL"
                evidence["failure_code"] = "bridge_returned_unapproved_tool_result"
            else:
                evidence["status"] = "UNKNOWN"
                evidence["failure_code"] = "bridge_refusal_not_proven"
            _save_evidence(state, case, evidence)
            _response(handler, sequence, final=_presentation())
            return
        evidence["bridge_refusals"][target] = refusal

    evidence["status"] = "PASS"
    evidence["failure_code"] = None
    _save_evidence(state, case, evidence)
    _response(handler, sequence, final=_presentation())


def _presentation():
    return _json_text({
        "schema_version": 1,
        "speak": "Offline tool access preflight passed.",
        "card": {"title": "Offline preflight", "lines": ["Synthetic provider only.", "Tool access was checked."]},
        "details_md": "A bounded synthetic result confirms completion of the test fixture.",
    })

