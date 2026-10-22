"""Offline TCP route assertion, with no DNS or external destinations."""
from __future__ import annotations

import argparse
import socket
import sys


def reachable(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--deny", action="append", default=[])
    args = parser.parse_args()
    for expected, values in ((True, args.allow), (False, args.deny)):
        for value in values:
            host, separator, raw_port = value.rpartition(":")
            if not separator:
                return 2
            actual = reachable(host, int(raw_port))
            if actual is not expected:
                print("NETWORK_PROBE_FAIL")
                return 1
    print("NETWORK_ISOLATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

