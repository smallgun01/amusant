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


def tool_names(payload):
    values = payload.get("tools")
    if not isinstance(values, list):
        values = payload.get("functions", [])
    result = []
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else item
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            result.append(name)
    return result


def called_tools(messages):
    result = set()
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool" and isinstance(message.get("name"), str):
            result.add(message["name"])
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and isinstance(function.get("name"), str):
                    result.add(function["name"])
    return result


def called_tool_count(messages, wanted):
    count = 0
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        calls = message.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and function.get("name") == wanted:
                count += 1
    return count


def report_tool_result_codes(messages):
    allowed = {
        "committed", "invalid_arguments", "report_invalid", "report_over_cap",
        "trusted_request_context_unavailable", "artifact_write_failed",
        "artifact_write_timeout", "artifact_conflict", "artifact_commit_failed",
    }
    results = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "tool" or message.get("name") != "poc_save_report":
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
        called = called_tools(messages)
        is_late_case = case == "late_mutation"
        next_tool = None
        arguments = None

        writer_calls = called_tool_count(messages, "poc_save_report")
        should_write = case != "no_writer" and (writer_calls == 0 or (case == "writer_redelivery" and writer_calls == 1))
        if should_write:
            next_tool = "poc_save_report"
            report = report_body(case)
            arguments = {"report": report}
            if case == "wrong_binding":
                arguments.update({
                    "owner_scope": "model-spoofed-owner",
                    "task_id": "model-spoofed-task",
                    "run_id": "model-spoofed-run",
                    "artifact_ref": "model-spoofed-reference",
                })
        elif is_late_case and "write_file" not in called:
            next_tool = "write_file"
            arguments = {
                "path": "/proc/amusant-option-d-poc-denied.txt",
                "content": f"POC_TOOL_SECRET_{RUN_TAG}_{case}_This deterministic write must fail.",
            }

        delay = SLOW_DELAY if case in {"restart_10k", "ambiguous_10k", "stop_10k"} and not called else 0.0
        with self.server.state.lock:
            self.server.state.sequence += 1
            sequence = self.server.state.sequence
            self.server.state.requests.append({
                "sequence": sequence,
                "case": case,
                "active": True,
                "tool_names": tools,
                "called_tools": sorted(called),
                "report_tool_result_codes": report_tool_result_codes(messages),
                "next_tool": next_tool,
                "spoofed_identity_fields_included": bool(case == "wrong_binding" and next_tool == "poc_save_report" and isinstance(arguments, dict) and set(arguments) > {"report"}),
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
