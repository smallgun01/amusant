#!/usr/bin/env python3
"""Seed only the fresh named Hermes POC volume; refuse to overwrite changed state."""
import hashlib
import json
import os
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
source = Path("/poc/hermes-config.yaml")
destination = home / "config.yaml"
expected = source.read_bytes()
home.mkdir(parents=True, exist_ok=True)
if destination.exists():
    current = destination.read_bytes()
    required = (
        b"provider: custom",
        b"default: local-poc-model",
        b"base_url: http://fake-provider:8000/v1",
        b"api_mode: chat_completions",
        b"streaming: false",
        b"context_length: 65536",
        b"agent:\n  disabled_toolsets:\n    - terminal\n    - file\n    - code_execution\n    - browser\n    - computer_use\n    - image_gen\n    - vision\n    - tts\n    - clarify\n    - video\n    - video_gen\n    - homeassistant\n    - spotify\n    - kanban",
        b"approvals:\n  mode: manual\n  cron_mode: deny\n  unattended_mode: deny\n  single_query_mode: deny",
    )
    if not all(item in current for item in required):
        raise SystemExit("Refusing to overwrite Hermes config: existing persistent POC config is incompatible")
    action = "existing-compatible-preserved" if current != expected else "already-present-and-matching"
    actual = current
else:
    destination.write_bytes(expected)
    action = "seeded"
    actual = expected
print(json.dumps({"action": action, "config_sha256": hashlib.sha256(actual).hexdigest(), "bytes": len(actual)}))
