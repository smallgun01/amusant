"""Listener-only liveness probe; it does not claim provider or task-store health."""
from __future__ import annotations

import os
import socket
import sys


def main() -> int:
    host = os.environ.get("RELAY_BIND_IP", "")
    raw_port = os.environ.get("RELAY_LISTEN_PORT", "8443")
    try:
        port = int(raw_port)
        with socket.create_connection((host, port), timeout=2):
            return 0
    except (OSError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
