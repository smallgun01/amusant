"""Listener-only liveness probe; it does not claim provider or task-store health."""
from __future__ import annotations

import os
import http.client
import socket
import sys
from urllib.parse import urlsplit


def main() -> int:
    host = os.environ.get("RELAY_BIND_IP", "")
    raw_port = os.environ.get("RELAY_LISTEN_PORT", "8443")
    try:
        port = int(raw_port)
        with socket.create_connection((host, port), timeout=2):
            pass
        if os.environ.get("AMUSANT_SLOW_EXECUTOR") == "grok_callback":
            callback_host = os.environ["RELAY_CALLBACK_BIND_IP"]
            callback_port = int(os.environ.get("RELAY_CALLBACK_LISTEN_PORT", "8645"))
            external = urlsplit(os.environ["AMUSANT_GROK_CALLBACK_URL"])
            connection = http.client.HTTPConnection(callback_host, callback_port, timeout=1)
            try:
                connection.putrequest("GET", "/callback/results", skip_host=True)
                connection.putheader("Host", external.netloc)
                connection.endheaders()
                response = connection.getresponse()
                response.read(512)
                if response.status != 405:
                    return 1
            finally:
                connection.close()
        return 0
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
