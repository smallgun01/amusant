#!/usr/bin/env python3
"""Deterministic local OpenAI Chat Completions server; stores no prompts or report bodies."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUN_TAG = os.environ["POC_RUN_TAG"]
SLOW_DELAY = float(os.environ.get("FAKE_SLOW_DELAY_SECONDS", "8"))
STOP_DELAY = float(os.environ.get("FAKE_STOP_DELAY_SECONDS", "12"))
PRESENTATION = {
    "schema_version": 1,
    "speak": "The report is ready for review.",
    "card": {"title": "Report ready", "lines": ["Synthetic report saved.", "Review the recorded findings."]},
    "details_md": "A deterministic synthetic report was saved for this proof.",
}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def text_parts(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item.get("text", "")) for item in value if isinstance(item, dict))
    return ""


def case_from_messages(messages):
    text = "\n".join(
        text_parts(message.get("content", ""))
        for message in messages
        if isinstance(message, dict) and message.get("role") == "user"
    )
    match = re.search(r"\[POC_CASE=([a-z0-9_]{1,40})\]", text)
    return match.group(1) if match else "unspecified"


def _collect_tool_names(value, result, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", name):
            result.add(name)
        for key in ("function", "functions", "tool", "tools"):
            if key in value:
                _collect_tool_names(value[key], result, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _collect_tool_names(item, result, depth + 1)


def tool_names(payload):
    result = set()
    for key in ("tools", "functions", "function"):
        if key in payload:
            _collect_tool_names(payload[key], result)
    return sorted(result)


def field_shape(payload, key):
    if key not in payload:
        return "missing", 0
    value = payload[key]
    if value is None:
        return "null", 0
    if isinstance(value, list):
        return "list", min(len(value), 1000)
    if isinstance(value, dict):
        return "dict", min(len(value), 1000)
    if isinstance(value, str):
        return "string", 0
    return "other", 0


def _function_arguments(call):
    function = call.get("function") if isinstance(call, dict) else None
    raw = function.get("arguments") if isinstance(function, dict) else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _bridge_calls(messages, bridge_name):
    result = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and function.get("name") == bridge_name:
                result.append(_function_arguments(call))
    return result


def _bridge_request_seen(messages, bridge_name, key, target):
    for arguments in _bridge_calls(messages, bridge_name):
        value = arguments.get(key)
        values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        if target in values:
            return True
    return False


def request_tool(target, arguments, available_tools, messages):
    """Use Hermes's native tool-search bridge when a plugin/file tool is deferred."""
    if target in available_tools:
        return target, arguments
    if "tool_call" not in available_tools:
        return target, arguments
    if "tool_search" in available_tools and not _bridge_request_seen(messages, "tool_search", "queries", target):
        return "tool_search", {"queries": [target]}
    if "tool_describe" in available_tools and not _bridge_request_seen(messages, "tool_describe", "names", target):
        return "tool_describe", {"names": [target]}
    return "tool_call", {"calls": [{"name": target, "arguments": arguments}]}


def requested_tool_arguments(action, arguments, wanted):
    if action == wanted and isinstance(arguments, dict):
        return arguments
    if action != "tool_call" or not isinstance(arguments, dict):
        return None
    calls = arguments.get("calls")
    for item in calls if isinstance(calls, list) else []:
        if isinstance(item, dict) and item.get("name") == wanted and isinstance(item.get("arguments"), dict):
            return item["arguments"]
    return None


def called_tools(messages):
    result = set()
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool" and isinstance(message.get("name"), str):
            result.add(message["name"])
        calls = message.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if isinstance(name, str):
                result.add(name)
            if name == "tool_call":
                for item in _function_arguments(call).get("calls", []):
                    if isinstance(item, dict) and isinstance(item.get("name"), str):
                        result.add(item["name"])
    return result




def report_tool_result_codes(messages):
    allowed = {
        "committed", "invalid_arguments", "report_invalid", "report_over_cap",
        "trusted_request_context_unavailable", "artifact_write_failed",
        "artifact_write_timeout", "artifact_conflict", "artifact_commit_failed",
    }
    pending = {}
    results = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and function.get("name") == "tool_call":
                    names = [
                        item.get("name") for item in _function_arguments(call).get("calls", [])
                        if isinstance(item, dict) and isinstance(item.get("name"), str)
                    ]
                    pending[call.get("id")] = names
            continue
        if message.get("role") != "tool":
            continue
        name = message.get("name")
        is_report_result = name == "save_report" or (
            name == "tool_call" and "save_report" in pending.get(message.get("tool_call_id"), [])
        )
        if not is_report_result:
            continue
        try:
            value = json.loads(text_parts(message.get("content", "")))
        except (ValueError, TypeError):
            results.append("invalid_tool_result")
            continue
        if isinstance(value, dict) and value.get("committed") is True and set(value) == {"committed"}:
            results.append("committed")
        elif isinstance(value, dict) and value.get("error") in allowed:
            results.append(value["error"])
        else:
            results.append("other_tool_result")
    return results
def report_body(case):
    if case == "success_30k":
        size = 30000
    else:
        size = 10000
    prefix = f"POC_REPORT_SECRET_{RUN_TAG}_{case}_"
    return prefix + ("R" * (size - len(prefix)))


def standard_presentation(case):
    value = dict(PRESENTATION)
    if case == "missing_speech":
        value.pop("speak")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.sequence = 0
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    server_version = "OptionDFakeProvider/1"

    def log_message(self, _fmt, *_args):
        return

    def send_json(self, status, value):
        data = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            self.send_json(200, {"ok": True})
            return
        if self.path == "/v1/models":
            self.send_json(200, {
                "object": "list",
                "data": [{"id": "local-poc-model", "object": "model", "created": 0, "owned_by": "offline-poc"}],
            })
            return
        if self.path == "/_poc/stats":
            with self.server.state.lock:
                records = [dict(row) for row in self.server.state.requests]
            self.send_json(200, {"count": len(records), "requests": records})
            return
        self.send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_json(404, {"error": {"message": "not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self.send_json(400, {"error": {"message": "invalid JSON"}})
            return
        messages = payload.get("messages", [])
        case = case_from_messages(messages)
        tools = tool_names(payload)
        tools_field_type, tools_field_count = field_shape(payload, "tools")
        functions_field_type, functions_field_count = field_shape(payload, "functions")
        called = called_tools(messages)
        is_late_case = case == "late_mutation"
        next_tool = None
        arguments = None

        with self.server.state.lock:
            writer_calls = sum(
                1 for row in self.server.state.requests
                if row.get("case") == case and row.get("report_tool_requested") is True
            )
            write_file_calls = sum(
                1 for row in self.server.state.requests
                if row.get("case") == case and row.get("write_file_requested") is True
            )
        should_write = case != "no_writer" and (
            writer_calls == 0 or (case == "writer_redelivery" and writer_calls == 1)
        )
        if should_write:
            report = report_body(case)
            report_arguments = {"report": report}
            if case == "wrong_binding":
                report_arguments.update({
                    "owner_scope": "model-spoofed-owner",
                    "task_id": "model-spoofed-task",
                    "run_id": "model-spoofed-run",
                    "artifact_ref": "model-spoofed-reference",
                })
            next_tool, arguments = request_tool("save_report", report_arguments, tools, messages)
        elif is_late_case and write_file_calls == 0:
            write_arguments = {
                "path": "/proc/amusant-option-d-poc-denied.txt",
                "content": f"POC_TOOL_SECRET_{RUN_TAG}_{case}_This deterministic write must fail.",
            }
            next_tool, arguments = request_tool("write_file", write_arguments, tools, messages)

        report_tool_arguments = requested_tool_arguments(next_tool, arguments, "save_report")
        write_file_arguments = requested_tool_arguments(next_tool, arguments, "write_file")
        report_tool_requested = isinstance(report_tool_arguments, dict)
        write_file_requested = isinstance(write_file_arguments, dict)

        if case == "stop_10k" and not called:
            delay = STOP_DELAY
        elif case in {"restart_10k", "ambiguous_10k"} and not called:
            delay = SLOW_DELAY
        else:
            delay = 0.0
        with self.server.state.lock:
            self.server.state.sequence += 1
            sequence = self.server.state.sequence
            self.server.state.requests.append({
                "sequence": sequence,
                "case": case,
                "active": True,
                "tool_names": tools,
                "poc_tool_offered": "save_report" in tools,
                "tool_search_bridge_offered": {"tool_call", "tool_describe", "tool_search"} <= set(tools),
                "report_tool_requested": report_tool_requested,
                "write_file_requested": write_file_requested,
                "tools_field_type": tools_field_type,
                "tools_field_count": tools_field_count,
                "functions_field_type": functions_field_type,
                "functions_field_count": functions_field_count,
                "called_tools": sorted(called),
                "report_tool_result_codes": report_tool_result_codes(messages),
                "next_tool": next_tool,
                "spoofed_identity_fields_included": bool(case == "wrong_binding" and isinstance(report_tool_arguments, dict) and set(report_tool_arguments) > {"report"}),
                "delay_seconds": delay,
                "created_at": time.time(),
            })
            record = self.server.state.requests[-1]

        if delay:
            time.sleep(delay)

        if next_tool:
            function_args = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            response_message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_option_d_{sequence}",
                    "type": "function",
                    "function": {"name": next_tool, "arguments": function_args},
                }],
            }
            finish_reason = "tool_calls"
        else:
            if case == "malformed_json":
                content = "{ malformed synthetic envelope"
            elif case == "oversized_final":
                prefix = f"POC_FINAL_SECRET_{RUN_TAG}_{case}_"
                content = prefix + ("F" * (30000 - len(prefix)))
            else:
                content = standard_presentation(case)
            response_message = {"role": "assistant", "content": content}
            finish_reason = "stop"

        with self.server.state.lock:
            record["active"] = False
            record["completed_at"] = time.time()
        self.send_json(200, {
            "id": f"chatcmpl-option-d-{sequence}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.get("model", "local-poc-model"),
            "choices": [{"index": 0, "message": response_message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 8, "total_tokens": 16},
        })


if __name__ == "__main__":
    state = State()
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
    server.state = state
    server.daemon_threads = True
    print(json.dumps({
        "event": "fake_provider_ready",
        "bind": "0.0.0.0:8000",
        "external_model": False,
        "report_bytes": [10000, 30000],
    }, separators=(",", ":")), flush=True)
    server.serve_forever()
