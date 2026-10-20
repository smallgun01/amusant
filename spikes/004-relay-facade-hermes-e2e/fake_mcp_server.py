#!/usr/bin/env python3
"""Harmless MCP canary: its startup/call markers prove whether no_mcp leaked."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
(home / ".h12-preflight-mcp-started").write_text("started\n", encoding="ascii")


def respond(message_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        message = json.loads(line)
    except (ValueError, TypeError):
        continue
    method = message.get("method") if isinstance(message, dict) else None
    message_id = message.get("id") if isinstance(message, dict) else None
    if method == "initialize":
        respond(message_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "h12-preflight-canary", "version": "1"},
        })
    elif method == "tools/list":
        respond(message_id, {"tools": [{
            "name": "preflight_probe",
            "description": "Harmless offline MCP canary; must be suppressed by no_mcp.",
            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }]})
    elif method == "tools/call":
        (home / ".h12-preflight-mcp-called").write_text("called\n", encoding="ascii")
        respond(message_id, {"content": [{"type": "text", "text": "offline MCP canary called"}]})
    elif method == "ping":
        respond(message_id, {})
