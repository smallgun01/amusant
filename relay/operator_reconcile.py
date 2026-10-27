"""Operator-only reconciliation for an uncertain Slow Task owner reservation.

This CLI is not exposed as a Live/Gemini tool. For Hermes, run only after
independently verifying native Run state. For Grok, ``abandoned`` is an explicit
local Owner decision that releases Relay's slot; it does not send or imply a
remote Stop. Late authenticated Callback metadata cannot reopen an abandoned task.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys

from .store_factory import build_task_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True, help="Authenticated owner partition")
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--terminal-state",
        required=True,
        choices=("completed", "failed", "cancelled", "interrupted", "no_remote_run", "abandoned"),
    )
    parser.add_argument("--verified-run-id", help="Required for a located Hermes Run; omit for no_remote_run or Grok abandoned")
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--evidence-ref", required=True, help="Ticket/log reference for the independent verification")
    parser.add_argument("--confirm-slot-release", action="store_true", help="Explicitly authorize releasing this owner's slot")
    parser.add_argument(
        "--revoke-grok-callback-token", action="store_true",
        help="For Grok abandon only, immediately reject any later Callback; without this flag, valid late reports are recorded as metadata until TTL",
    )
    args = parser.parse_args(argv)
    if not args.confirm_slot_release:
        parser.error("--confirm-slot-release is required; verify Hermes state or explicitly authorize a local Grok abandon")
    try:
        store = build_task_store(os.environ)
        receipt = store.operator_reconcile_uncertain(
            client_id=args.client_id,
            task_id=args.task_id,
            terminal_state=args.terminal_state,
            verified_run_id=args.verified_run_id,
            operator_id=args.operator_id,
            evidence_ref=args.evidence_ref,
            now=datetime.now(timezone.utc),
            revoke_callback_token=args.revoke_grok_callback_token,
        )
    except Exception as exc:
        print(json.dumps({"status": "unavailable", "error_type": type(exc).__name__}, separators=(",", ":")))
        return 2
    print(json.dumps({"status": receipt.status, "task_id": receipt.task_id, "code": receipt.code}, separators=(",", ":")))
    return 0 if receipt.status in {"failed", "cancelled", "timed_out", "abandoned"} else 1


if __name__ == "__main__":
    sys.exit(main())
