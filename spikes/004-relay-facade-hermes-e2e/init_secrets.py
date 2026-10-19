#!/usr/bin/env python3
"""Create distinct synthetic shared-group credentials in an ephemeral volume."""
import os
from pathlib import Path

values = {
    "facade_api_key": os.environ["POC_FACADE_KEY"],
    "hermes_api_key": os.environ["POC_HERMES_KEY"],
    "facade_writer_api_key": os.environ["POC_WRITER_KEY"],
}
if len(set(values.values())) != len(values) or any(len(value) < 32 for value in values.values()):
    raise SystemExit("test credentials are not distinct and bounded")
root = Path("/run/test-secrets")
root.mkdir(parents=True, exist_ok=True)
os.chown(root, 0, 20000)
os.chmod(root, 0o750)
for name, value in values.items():
    path = root / name
    path.write_text(value + "\n", encoding="utf-8")
    os.chown(path, 0, 20000)
    os.chmod(path, 0o440)
