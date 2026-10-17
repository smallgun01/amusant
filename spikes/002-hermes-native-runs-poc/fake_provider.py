#!/usr/bin/env python3
"""Deterministic, local-only OpenAI Chat Completions stub. Never records prompt text."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLIENT_DELAY = float(os.environ.get("FAKE_CLIENT_DELAY_SECONDS", "4"))
RESTART_DELAY = float(os.environ.get("FAKE_RESTART_DELAY_SECONDS", "40"))
MARKER = "POC_RESULT: deterministic-local-stub"


def json_bytes(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def text_parts(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.extend(text_parts(item.get("text", "")))
        return out
    return []


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.sequence = 0
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalHermesPOC/1"

    def log_message(self, _format, *_args):
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
            self.send_json(200, {"object": "list", "data": [{"id": "local-poc-model", "object": "model", "created": 0, "owned_by": "poc"}]})
            return
        if self.path == "/_poc/requests":
            with self.server.state.lock:
                snapshot = [dict(item) for item in self.server.state.requests]
            self.send_json(200, {"count": len(snapshot), "requests": snapshot})
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
        streaming = bool(payload.get("stream", False))
        if streaming:
            self.send_json(400, {"error": {"message": "POC requires non-streaming requests"}})
            return
        messages = payload.get("messages", [])
        latest_user_text = ""
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "user":
                    latest_user_text = "\n".join(text_parts(message.get("content", "")))
        slow_case = "none"
        delay = 0.0
        if "POC_HERMES_RESTART" in latest_user_text:
            slow_case = "hermes_restart"
            delay = RESTART_DELAY
        elif "POC_CLIENT_RESTART" in latest_user_text:
            slow_case = "client_restart"
            delay = CLIENT_DELAY
        now = time.time()
        with self.server.state.lock:
            self.server.state.sequence += 1
            record = {"sequence": self.server.state.sequence, "started_at": now, "completed_at": None, "active": True, "slow_case": slow_case, "stream": streaming, "model": payload.get("model", "")}
            self.server.state.requests.append(record)
        print(json.dumps({"event": "fake_completion_started", "sequence": record["sequence"], "slow_case": slow_case, "stream": streaming}), flush=True)
        if delay > 0:
            time.sleep(delay)
        with self.server.state.lock:
            record["active"] = False
            record["completed_at"] = time.time()
            completed_at = record["completed_at"]
        print(json.dumps({"event": "fake_completion_finished", "sequence": record["sequence"], "slow_case": slow_case}), flush=True)
        self.send_json(200, {
            "id": "chatcmpl-poc-%d" % record["sequence"],
            "object": "chat.completion",
            "created": int(completed_at),
            "model": payload.get("model", "local-poc-model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": MARKER}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
        })


if __name__ == "__main__":
    state = State()
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
    server.state = state
    server.daemon_threads = True
    print(json.dumps({"event": "fake_provider_ready", "bind": "0.0.0.0:8000", "external_model": False}), flush=True)
    server.serve_forever()
