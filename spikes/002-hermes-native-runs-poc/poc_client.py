#!/usr/bin/env python3
"""Minimal stdlib Hermes Runs API client, run as a disposable container on the internal network."""
import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_BASE = os.environ.get("POC_API_BASE", "http://hermes:8642").rstrip("/")
API_KEY = os.environ.get("POC_API_KEY")
FAKE_BASE = os.environ.get("POC_FAKE_BASE", "http://fake-provider:8000").rstrip("/")
SESSION_KEY = "amusant-phase2a-local-owner"
MARKER = "POC_RESULT: deterministic-local-stub"
TERMINAL = {"completed", "failed", "cancelled", "interrupted", "expired"}


def request_json(base, method, path, body=None, idempotency_key=None, auth=True):
    headers = {}
    if auth:
        if not API_KEY:
            raise RuntimeError("POC_API_KEY not set")
        headers["Authorization"] = "Bearer " + API_KEY
        headers["X-Hermes-Session-Key"] = SESSION_KEY
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw or b"{}")
    except HTTPError as exc:
        raw = exc.read()
        try:
            value = json.loads(raw or b"{}")
        except Exception:
            value = {}
        return exc.code, value
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("local HTTP request failed: " + type(exc).__name__) from None


def run_status(run_id):
    return request_json(API_BASE, "GET", "/v1/runs/" + quote(str(run_id), safe=""))


def marker_seen(value):
    return MARKER in json.dumps(value, ensure_ascii=False)


def summary(http_status, value):
    result = {"http_status": http_status}
    if isinstance(value, dict):
        for key in ("run_id", "status", "object"):
            if key in value:
                result[key] = value[key]
        error = value.get("error")
        if isinstance(error, dict) and error.get("code") is not None:
            result["error_code"] = error["code"]
        result["result_marker_seen"] = marker_seen(value)
    return result


def poll(run_id, timeout):
    deadline = time.monotonic() + timeout
    last_code, last = 0, {}
    while time.monotonic() < deadline:
        last_code, last = run_status(run_id)
        if last_code >= 400:
            return last_code, last
        if str(last.get("status", "")).lower() in TERMINAL:
            return last_code, last
        time.sleep(0.25)
    return last_code, last


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    health = sub.add_parser("health")
    submit = sub.add_parser("submit")
    submit.add_argument("--key", required=True)
    submit.add_argument("--input", required=True)
    submit.add_argument("--wait", action="store_true")
    submit.add_argument("--timeout", type=float, default=60.0)
    get = sub.add_parser("get")
    get.add_argument("--run-id", required=True)
    get.add_argument("--wait", action="store_true")
    get.add_argument("--timeout", type=float, default=60.0)
    sub.add_parser("stats")
    args = parser.parse_args()
    if args.command == "health":
        api_code, _ = request_json(API_BASE, "GET", "/health")
        fake_code, _ = request_json(FAKE_BASE, "GET", "/health", auth=False)
        print(json.dumps({"api_http_status": api_code, "fake_http_status": fake_code}, separators=(",", ":")))
        return 0
    if args.command == "stats":
        code, value = request_json(FAKE_BASE, "GET", "/_poc/requests", auth=False)
        print(json.dumps({"http_status": code, **(value if isinstance(value, dict) else {})}, separators=(",", ":")))
        return 0
    if args.command == "submit":
        code, value = request_json(API_BASE, "POST", "/v1/runs", {"input": args.input}, args.key)
        run_id = value.get("run_id") if isinstance(value, dict) else None
        if code < 400 and run_id and args.wait:
            code, value = poll(run_id, args.timeout)
        print(json.dumps(summary(code, value), separators=(",", ":")))
        return 0
    code, value = run_status(args.run_id)
    if code < 400 and args.wait:
        code, value = poll(args.run_id, args.timeout)
    print(json.dumps(summary(code, value), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"client_error": type(exc).__name__}), file=sys.stderr)
        sys.exit(2)
