#!/usr/bin/env python3
"""Seed only the unique Hermes proof volume; never overwrite incompatible persistent state."""
import hashlib
import json
import os
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
source = Path("/poc/hermes-config.yaml")
destination = home / "config.yaml"
expected = source.read_bytes()
home.mkdir(parents=True, exist_ok=True)
required = (
    b"provider: custom",
    b"default: local-poc-model",
    b"base_url: http://fake-provider:8000/v1",
    b"api_mode: chat_completions",
    b"streaming: false",
    b"context_length: 65536",
    b"unattended_mode: deny",
    b"single_query_mode: deny",
    b"amusant-option-d-poc",
    b"hook_callback_timeout: 0.15",
    b"platform_toolsets:",
    b"api_server:",
    b"amusant_option_d",
)
if destination.exists():
    current = destination.read_bytes()
    if not all(item in current for item in required):
        raise SystemExit("Refusing to overwrite Hermes config: existing proof volume is incompatible")
    action = "existing-compatible-preserved" if current != expected else "already-present-and-matching"
    actual = current
else:
    destination.write_bytes(expected)
    action = "seeded"
    actual = expected
print(json.dumps({
    "action": action,
    "config_sha256": hashlib.sha256(actual).hexdigest(),
    "bytes": len(actual),
}))
