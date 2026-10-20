#!/usr/bin/env python3
"""Deterministic no-provider effective-toolset audit for Hermes v2026.9.24."""
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

# These are negative bridge probes. Case B's web_search is an authorized direct
# tool, so it is deliberately not called through either route during this offline test.
PROBES = [
    ("write_file", "write_file"),
    ("terminal", "terminal"),
    ("browser_navigate", "browser_navigate"),
    ("browser_snapshot", "browser_snapshot"),
    ("execute_code", "execute_code"),
    ("process_manage", "process_manage"),
    ("web_extract", "web_extract"),
    ("web_search", "web_search"),
    ("mcp__h12preflight__preflight_probe", "mcp__h12preflight__preflight_probe"),
]
SEARCH_QUERIES = ["save_report", *(target for target, _ in PROBES)]


def _expected_bridge_probes(case):
    return [target for target, _ in PROBES if not (case.endswith("case_b") and target == "web_search")]


def _expected_direct_probes(case):
    # The same bounded, harmless forged calls exercise Hermes's valid-tool-name
    # filter. No tool is run unless Hermes incorrectly dispatches an unadvertised name.
    return _expected_bridge_probes(case)


def _visibility_mismatch(observed, expected):
    observed = set(observed)
    expected = set(expected)
    if not observed:
        # Hermes can omit tools on a later request; that request grants less access.
        return {"extra": [], "missing": []}
    return {"extra": sorted(observed - expected), "missing": sorted(expected - observed)}


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item.get("text", "")) for item in value if isinstance(item, dict))
    return ""


def _parse_arguments(raw):
    try:
        value = raw if isinstance(raw, dict) else json.loads(raw) if isinstance(raw, str) else {}
    except (ValueError, TypeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _assistant_tool_calls(messages):
    found = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                continue
            found.append({
                "id": call.get("id"),
                "action": function["name"],
                "args": _parse_arguments(function.get("arguments")),
            })
    return found


def _tool_results_by_id(messages):
    rows = {}
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        if call_id is None:
            continue
        rows.setdefault(str(call_id), []).append({
            "role": "tool",
            "name": message.get("name") if isinstance(message.get("name"), str) else None,
            "content": message.get("content", ""),
        })
    return rows


def _result_for_call(call, results_by_id):
    call_id = call.get("id")
    rows = results_by_id.get(str(call_id), []) if call_id is not None else []
    if len(rows) != 1:
        return None, {"call_id": call_id, "result_call_id": None, "result_count": len(rows),
                      "matched_call_result_id": False, "result_role": None, "result_name": None}
    row = rows[0]
    return row, {"call_id": call_id, "result_call_id": str(call_id), "result_count": 1,
                 "matched_call_result_id": True, "result_role": row["role"], "result_name": row["name"]}


def _find_action_call(calls, action):
    return next((call for call in calls if call["action"] == action), None)


def _find_bridge_target_calls(calls):
    found = {}
    for call in calls:
        if call["action"] != "tool_call":
            continue
        entries = call["args"].get("calls", [])
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                found.setdefault(entry["name"], []).append(call)
    return found


def _find_direct_calls(calls, target):
    return [call for call in calls if call["action"] == target]


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


def _string_leaves(value, depth=0):
    if depth > 10:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_leaves(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _string_leaves(item, depth + 1)


def _result_payload(row):
    return _content_text(row.get("content", "")) if isinstance(row, dict) else ""


def _has_structured_error(raw):
    parsed = _decode_json(raw)
    return parsed is not None and any(
        bool(item.get("error")) or item.get("isError") is True
        for item in _walk(parsed)
    )


def _response_shape(raw):
    text = _content_text(raw)
    parsed = _decode_json(text)
    if not text:
        kind = "empty"
    elif isinstance(parsed, dict):
        kind = "json_object"
    elif isinstance(parsed, list):
        kind = "json_array"
    elif parsed is not None:
        kind = "json_scalar"
    else:
        kind = "plain_text"
    length = len(text.encode("utf-8", errors="replace"))
    bucket = "0" if length == 0 else "1-256" if length <= 256 else "257-1024" if length <= 1024 else ">1024"
    return {"content_kind": kind, "size_bucket": bucket, "structured_error": _has_structured_error(text)}


def _bridge_rejection(raw):
    text = _content_text(raw)
    parsed = _decode_json(text)
    strings = [text, *list(_string_leaves(parsed))]
    joined = " ".join(value.lower() for value in strings)
    if "directly-listed tool, not a deferred one" in joined:
        return "registered_not_deferred"
    if "not a known tool name" in joined:
        return "unknown_tool_name"
    if "not available in this session" in joined:
        return "session_scope_denied"
    return None


def _direct_tool_filter_rejection(raw, target):
    text = _content_text(raw).strip()
    prefix = f"Tool '{target}' does not exist. Available tools:"
    return text.startswith(prefix)


def _commit_confirmed(raw):
    parsed = _decode_json(raw)
    return any(item.get("committed") is True for item in _walk(parsed)) if parsed is not None else False


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


def _bridge_exchange(call, row, match):
    record = dict(match)
    record["response_shape"] = _response_shape(_result_payload(row)) if row else None
    record["denial_stage"] = None
    record["classification"] = "missing_or_ambiguous_result"
    record["dispatch_prevented"] = False
    if row and row.get("name") == "tool_call":
        refusal = _bridge_rejection(_result_payload(row))
        if refusal:
            record["classification"] = refusal
            record["denial_stage"] = "hermes_tool_search_deferred_name_validation"
            record["dispatch_prevented"] = True
        elif _has_structured_error(_result_payload(row)):
            record["classification"] = "unclassified_structured_error"
        else:
            record["classification"] = "non_denial_tool_result"
    return record


def _direct_exchange(call, row, match, target):
    record = dict(match)
    record["target"] = target
    record["response_shape"] = _response_shape(_result_payload(row)) if row else None
    record["denial_stage"] = None
    record["classification"] = "missing_or_ambiguous_result"
    record["dispatch_prevented"] = False
    if row and row.get("name") == target and _direct_tool_filter_rejection(_result_payload(row), target):
        record["classification"] = "unknown_tool_name_rejected"
        record["denial_stage"] = "agent_valid_tool_name_filter"
        record["dispatch_prevented"] = True
    elif row:
        record["classification"] = "direct_denial_not_proven"
    return record


def _case_probe_path(case, target):
    safe = re.sub(r"[^a-z0-9_-]", "-", target.lower())
    return f"/h12-preflight-{case}-{safe}"


def _probe_arguments(case, target):
    path = _case_probe_path(case, target)
    callback = f"http://fake-provider:8000{path}"
    if target == "write_file":
        return {"path": f"/opt/data/.h12-{case}-write-file-executed", "content": "synthetic-denied-probe"}
    if target == "terminal":
        return {"command": f"python3 -c \"from urllib.request import urlopen; urlopen('{callback}')\""}
    if target == "browser_navigate" or target == "browser_snapshot":
        return {"url": callback}
    if target == "execute_code":
        return {"code": f"from urllib.request import urlopen; urlopen('{callback}')"}
    if target == "process_manage":
        return {"action": "start", "command": f"python3 -c \"from urllib.request import urlopen; urlopen('{callback}')\""}
    if target == "web_extract":
        return {"url": f"https://example.invalid{path}"}
    if target == "web_search":
        return {"query": f"{case} offline preflight must not reach a provider"}
    return {"query": f"{case} synthetic MCP probe"}


def _save_evidence(state, case, evidence):
    with state.lock:
        hits = sorted(set(getattr(state, "browser_probe_hits", [])))
    prefix = f"/h12-preflight-{case}-"
    evidence["browser_probe_hits"] = [path for path in hits if path.startswith(prefix)]
    evidence["updated_at_epoch"] = int(time.time())
    path = EVIDENCE / f"toolset-provider-{case}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json_text(evidence) + "\n", encoding="utf-8")
    tmp.replace(path)


def _call(action, args, sequence, call_id=None):
    return {
        "id": call_id or f"h12-{sequence}",
        "type": "function",
        "function": {"name": action, "arguments": _json_text(args)},
    }


def _response(handler, sequence, *, action=None, args=None, final=None, tool_calls=None):
    if tool_calls is not None:
        message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        finish_reason = "tool_calls"
    elif action:
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


def _presentation():
    return _json_text({
        "schema_version": 1,
        "speak": "Offline tool-access probe sequence finished.",
        "card": {"title": "Offline preflight", "lines": ["Synthetic provider only.", "Probe evidence is sanitized."]},
        "details_md": "The deterministic offline toolset probe sequence completed.",
    })


def _exchange_for_action(calls, results_by_id, action):
    call = _find_action_call(calls, action)
    if call is None:
        return None, None, None
    row, match = _result_for_call(call, results_by_id)
    match["action"] = action
    match["response_shape"] = _response_shape(_result_payload(row)) if row else None
    return call, row, match


def _expected_visible(case):
    return BRIDGES | ({"web_search"} if case.endswith("case_b") else set())


def _status(evidence, case):
    fail = []
    unknown = []
    if evidence["model_visible_tool_mismatches"]:
        fail.append("unexpected_model_visible_tools")
    if evidence["hermes_api_toolsets"].get("http_status") != 200:
        unknown.append("toolsets_endpoint_unavailable")
    if evidence.get("tool_search") is None:
        unknown.append("tool_search_unverified")
    elif evidence["tool_search"] != {"total_available": 1, "matches": ["save_report"]}:
        fail.append("unexpected_deferred_catalog")
    if evidence.get("tool_describe") is None:
        unknown.append("tool_describe_unverified")
    elif evidence["tool_describe"].get("described_names") != ["save_report"]:
        fail.append("unexpected_describe_catalog")
    if evidence.get("authorized_save_report") != "COMMITTED":
        fail.append("authorized_save_report_not_committed")

    for target in _expected_bridge_probes(case):
        record = evidence.get("bridge_probe_results", {}).get(target)
        if not isinstance(record, dict) or not record.get("matched_call_result_id"):
            unknown.append("bridge_probe_result_missing:" + target)
        elif record.get("classification") not in {"registered_not_deferred", "unknown_tool_name", "session_scope_denied"} or not record.get("dispatch_prevented"):
            if record.get("classification") == "non_denial_tool_result":
                fail.append("bridge_probe_not_denied:" + target)
            else:
                unknown.append("bridge_probe_denial_unclassified:" + target)

    for target in _expected_direct_probes(case):
        record = evidence.get("direct_tool_denials", {}).get(target)
        if not isinstance(record, dict) or not record.get("matched_call_result_id"):
            unknown.append("direct_probe_result_missing:" + target)
        elif record.get("classification") != "unknown_tool_name_rejected" or not record.get("dispatch_prevented"):
            unknown.append("direct_denial_not_proven:" + target)

    if evidence.get("tool_search_exchange", {}).get("matched_call_result_id") is not True:
        unknown.append("tool_search_exchange_unmatched")
    if evidence.get("tool_describe_exchange", {}).get("matched_call_result_id") is not True:
        unknown.append("tool_describe_exchange_unmatched")
    if evidence.get("authorized_save_report_exchange", {}).get("matched_call_result_id") is not True:
        unknown.append("save_report_exchange_unmatched")
    if evidence.get("browser_probe_hits"):
        fail.append("unauthorized_side_effect_observed")

    evidence["failure_codes"] = sorted(set(fail + unknown))
    evidence["failure_code"] = evidence["failure_codes"][0] if evidence["failure_codes"] else None
    evidence["status"] = "FAIL" if fail else "UNKNOWN" if unknown else "PASS"


def handle_toolset_case(handler, payload, messages, model_tools, case):
    """Exercise bridge refusal and direct schema-filter denial with correlated IDs."""
    state = handler.server.state
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
                "tool_search_exchange": {},
                "tool_describe": None,
                "tool_describe_exchange": {},
                "authorized_save_report": "NOT_RUN",
                "authorized_save_report_exchange": {},
                "bridge_refusals": {},
                "bridge_probe_results": {},
                "direct_tool_denials": {},
                "direct_forged_call_names": [],
                "browser_probe_hits": [],
                "no_prompt_or_report_bodies_recorded": True,
            }
            state.toolset_cases[case] = evidence
        evidence["model_visible_tools_by_request"].append(sorted(set(model_tools)))
        evidence["model_visible_tools"] = sorted(set(evidence["model_visible_tools"]) | set(model_tools))
        mismatch = _visibility_mismatch(model_tools, _expected_visible(case))
        if mismatch["extra"] or mismatch["missing"]:
            evidence["model_visible_tool_mismatches"].append({
                "request_index": len(evidence["model_visible_tools_by_request"]), **mismatch,
            })
    calls = _assistant_tool_calls(messages)
    results_by_id = _tool_results_by_id(messages)

    # Stop issuing calls if Hermes has exposed an unexpected schema or the API
    # inventory endpoint is unavailable. This is a safe fail-closed boundary.
    if evidence["model_visible_tool_mismatches"]:
        evidence["failure_codes"] = ["unexpected_model_visible_tools"]
        evidence["failure_code"] = "unexpected_model_visible_tools"
        evidence["status"] = "FAIL"
        _save_evidence(state, case, evidence)
        _response(handler, sequence, final=_presentation())
        return
    if evidence["hermes_api_toolsets"].get("http_status") != 200:
        evidence["failure_codes"] = ["toolsets_endpoint_unavailable"]
        evidence["failure_code"] = "toolsets_endpoint_unavailable"
        evidence["status"] = "UNKNOWN"
        _save_evidence(state, case, evidence)
        _response(handler, sequence, final=_presentation())
        return

    # Capture each primary bridge exchange with its exact tool-call/result IDs.
    search_call, search_row, search_match = _exchange_for_action(calls, results_by_id, "tool_search")
    if search_call is None:
        _save_evidence(state, case, evidence)
        _response(handler, sequence, action="tool_search", args={"queries": SEARCH_QUERIES, "limit": 20})
        return
    evidence["tool_search_exchange"] = search_match
    if search_row:
        summary = _tool_search_summary(_result_payload(search_row))
        evidence["tool_search"] = summary

    describe_call, describe_row, describe_match = _exchange_for_action(calls, results_by_id, "tool_describe")
    if describe_call is None:
        described = ["save_report", *(target for target, _ in PROBES)]
        _save_evidence(state, case, evidence)
        _response(handler, sequence, action="tool_describe", args={"names": described})
        return
    evidence["tool_describe_exchange"] = describe_match
    if describe_row:
        evidence["tool_describe"] = _tool_describe_summary(_result_payload(describe_row))

    targets = _find_bridge_target_calls(calls)
    report_calls = targets.get("save_report", [])
    if not report_calls:
        _save_evidence(state, case, evidence)
        _response(handler, sequence, action="tool_call", args={"calls": [{
            "name": "save_report",
            "arguments": {"report": "H1.2 offline effective-toolset preflight synthetic report."},
        }]})
        return
    report_call = report_calls[0]
    report_row, report_match = _result_for_call(report_call, results_by_id)
    report_match["action"] = "tool_call"
    evidence["authorized_save_report_exchange"] = report_match
    if report_row:
        evidence["authorized_save_report"] = "COMMITTED" if _commit_confirmed(_result_payload(report_row)) else "NOT_CONFIRMED"

    # Complete every negative bridge probe even when one response needs further
    # classification. Case B's direct web_search is explicitly authorized; it is
    # recorded as skipped so the fake-provider test never performs real web access.
    for target in _expected_bridge_probes(case):
        target_calls = targets.get(target, [])
        if target_calls:
            call = target_calls[0]
            row, match = _result_for_call(call, results_by_id)
            detail = _bridge_exchange(call, row, match)
            detail["target"] = target
            evidence["bridge_probe_results"][target] = detail
            if detail["dispatch_prevented"]:
                evidence["bridge_refusals"][target] = detail["classification"]
            else:
                evidence["bridge_refusals"][target] = "NOT_CONFIRMED"
        if not target_calls:
            arguments = _probe_arguments(case, target)
            _save_evidence(state, case, evidence)
            _response(handler, sequence, action="tool_call", args={"calls": [{"name": target, "arguments": arguments}]})
            return
    if case.endswith("case_b"):
        evidence["bridge_probe_results"]["web_search"] = {
            "target": "web_search", "status": "SKIPPED_AUTHORIZED_DIRECT",
            "classification": "authorized_direct_tool_not_negatively_probed",
            "dispatch_prevented": None, "matched_call_result_id": None,
        }

    # Forge direct, unadvertised calls in a single bounded batch. Hermes's valid
    # tool-name filter must return one denial per ID before any dispatcher runs.
    direct_names = _expected_direct_probes(case)
    direct_calls = {name: _find_direct_calls(calls, name) for name in direct_names}
    for target in direct_names:
        found = direct_calls[target]
        if found:
            row, match = _result_for_call(found[0], results_by_id)
            evidence["direct_tool_denials"][target] = _direct_exchange(found[0], row, match, target)
    missing = [target for target in direct_names if not direct_calls[target]]
    if missing:
        forged = []
        for index, target in enumerate(missing, start=1):
            call_id = f"h12-forged-{case}-{index}"
            forged.append(_call(target, _probe_arguments(case, target), sequence, call_id=call_id))
            if target not in evidence["direct_forged_call_names"]:
                evidence["direct_forged_call_names"].append(target)
        _save_evidence(state, case, evidence)
        _response(handler, sequence, tool_calls=forged)
        return

    _status(evidence, case)
    _save_evidence(state, case, evidence)
    _response(handler, sequence, final=_presentation())

