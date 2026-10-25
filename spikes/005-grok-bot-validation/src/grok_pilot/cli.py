"""Small operator CLI for the isolated Grok pilot ledger."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from .contract import MAX_INSTRUCTION_CHARS, MAX_LABEL_CHARS
from .routine import RoutineSendError, submit_once
from .store import MAX_RESULT_TOKEN_TTL_SECONDS, PilotStore, StoreError

DEFAULT_OWNER_ID = "offline-owner"


def _emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _add_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", required=True, help="path to the independent pilot SQLite file")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grok-pilot")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="durably create one task before a Routine POST")
    _add_db(prepare)
    prepare.add_argument("--label", required=True)
    prepare.add_argument("--instruction", required=True)
    prepare.add_argument("--owner-id", default=DEFAULT_OWNER_ID)
    prepare.add_argument(
        "--result-token-ttl-seconds",
        type=int,
        help=f"optional finite result capability TTL (1..{MAX_RESULT_TOKEN_TTL_SECONDS}); no default",
    )

    submit = commands.add_parser("submit", help="preview by default; POST only with explicit --live")
    _add_db(submit)
    submit.add_argument("--task-id", required=True)
    submit.add_argument("--routine-url", help="Owner-provided HTTPS Routine Webhook URL")
    submit.add_argument("--live", action="store_true", help="make exactly one Routine POST")

    list_command = commands.add_parser("list", help="list pilot task states without private instructions")
    _add_db(list_command)

    readback = commands.add_parser("readback", help="read a task and its durable report receipt")
    _add_db(readback)
    readback.add_argument("--task-id", required=True)

    recover = commands.add_parser(
        "recover", help="mark a crashed Routine send unknown after its local sender process exits"
    )
    _add_db(recover)
    recover.add_argument("--task-id", required=True)

    close = commands.add_parser("close", help="manually close a local experiment task")
    _add_db(close)
    close.add_argument("--task-id", required=True)
    close.add_argument("--reason", required=True)

    revoke = commands.add_parser("revoke", help="revoke a task result token")
    _add_db(revoke)
    revoke.add_argument("--task-id", required=True)

    evidence = commands.add_parser("evidence", help="print a content-free evidence summary")
    _add_db(evidence)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        store = PilotStore(args.db)
        if args.command == "prepare":
            if not 1 <= len(args.label) <= MAX_LABEL_CHARS:
                raise ValueError("label length must be 1..80 characters")
            if not 1 <= len(args.instruction) <= MAX_INSTRUCTION_CHARS:
                raise ValueError("instruction length must be 1..4096 characters")
            task_id, result_token = store.prepare(
                args.owner_id,
                args.label,
                args.instruction,
                result_token_ttl_seconds=args.result_token_ttl_seconds,
            )
            # The capability is printed once for the operator to place in a private environment.
            prepared = {
                "task_id": task_id,
                "task_label": args.label,
                "submission_state": "prepared",
                "result_token": result_token,
                "token_storage": "sha256 only in SQLite",
                "native_state": "unknown",
            }
            if args.result_token_ttl_seconds is not None:
                prepared["result_token_expires_at"] = store.get_task(task_id)["result_token_expires_at"]
            _emit(prepared)
        elif args.command == "submit":
            task = store.get_task(args.task_id)
            if not args.live:
                _emit({
                    "task_id": task["task_id"],
                    "submission_state": task["submission_state"],
                    "mode": "dry-run",
                    "would_post": bool(args.routine_url),
                    "routine_url_set": bool(args.routine_url),
                    "result_token_configured": bool(os.environ.get("GROK_PILOT_RESULT_TOKEN")),
                    "routine_credential_configured": bool(os.environ.get("GROK_PILOT_ROUTINE_BEARER_TOKEN")),
                    "native_state": task["native_state"],
                })
            else:
                if not args.routine_url:
                    raise RoutineSendError("--routine-url is required with --live")
                result_token = os.environ.get("GROK_PILOT_RESULT_TOKEN", "")
                routine_token = os.environ.get("GROK_PILOT_ROUTINE_BEARER_TOKEN", "")
                state = submit_once(store, args.task_id, result_token, args.routine_url, routine_token)
                _emit({
                    "task_id": args.task_id,
                    "submission_state": state,
                    "native_state": "unknown",
                    "note": "one POST attempted; this command will not retry",
                })
                return 0 if state == "accepted" else 3
        elif args.command == "list":
            _emit(store.list_tasks())
        elif args.command == "readback":
            _emit(store.readback(args.task_id))
        elif args.command == "recover":
            _emit(store.recover_submission(args.task_id))
        elif args.command == "close":
            store.close_task(args.task_id, args.reason)
            _emit({"task_id": args.task_id, "closed": True, "native_state": "unknown"})
        elif args.command == "revoke":
            store.revoke_token(args.task_id)
            _emit({"task_id": args.task_id, "result_token_revoked": True})
        elif args.command == "evidence":
            _emit(store.evidence())
        return 0
    except (StoreError, RoutineSendError, ValueError) as exc:
        print(f"grok-pilot: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
