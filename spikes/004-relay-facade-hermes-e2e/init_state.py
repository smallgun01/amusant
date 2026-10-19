#!/usr/bin/env python3
"""Prepare fresh named test volumes for the pinned offline Compose project."""
import os
from pathlib import Path

for raw, uid, gid in (
    ("/opt/data", 10000, 10000),
    ("/var/lib/amusant-results", 10003, 10003),
    ("/var/lib/amusant", 10001, 10001),
):
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid)
    os.chmod(path, 0o700)
