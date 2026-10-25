"""Independent SQLite ledger for one-shot pilot submissions and immutable receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contract import SCHEMA_VERSION, ValidatedReport, validate_routine_payload


class StoreError(RuntimeError):
    """A safe, non-sensitive store error."""


class NotFound(StoreError):
    pass


class Unauthorized(StoreError):
    pass


class Conflict(StoreError):
    pass


class InvalidTransition(StoreError):
    pass


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    task_id: str
    received_at: str
    late: bool
    duplicate: bool


class PilotStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        task_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        task_label TEXT NOT NULL,
                        instruction TEXT NOT NULL,
                        result_token_sha256 TEXT,
                        token_revoked INTEGER NOT NULL DEFAULT 0 CHECK(token_revoked IN (0, 1)),
                        submission_state TEXT NOT NULL CHECK(submission_state IN
                            ('prepared', 'sending', 'accepted', 'rejected', 'acceptance_unknown')),
                        native_state TEXT NOT NULL DEFAULT 'unknown' CHECK(native_state = 'unknown'),
                        created_at TEXT NOT NULL,
                        closed_at TEXT,
                        close_reason TEXT
                    );
                    CREATE TABLE IF NOT EXISTS receipts (
                        task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
                        receipt_id TEXT NOT NULL UNIQUE,
                        digest TEXT NOT NULL,
                        report_json TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        late INTEGER NOT NULL CHECK(late IN (0, 1))
                    );
                    CREATE INDEX IF NOT EXISTS tasks_created_at_idx ON tasks(created_at);
                    """
                )
                # A crash after durable 'sending' is ambiguous. Never silently resend.
                connection.execute(
                    "UPDATE tasks SET submission_state='acceptance_unknown' WHERE submission_state='sending'"
                )
        except sqlite3.Error as exc:
            raise StoreError("could not initialize pilot database") from exc

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def prepare(self, owner_id: str, label: str, instruction: str) -> tuple[str, str]:
        task_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        validate_routine_payload(
            {
                "schema_version": SCHEMA_VERSION,
                "task_id": task_id,
                "result_token": token,
                "task_label": label,
                "instruction": instruction,
            }
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    """INSERT INTO tasks(task_id, owner_id, task_label, instruction,
                       result_token_sha256, submission_state, created_at)
                       VALUES(?, ?, ?, ?, ?, 'prepared', ?)""",
                    (task_id, owner_id, label, instruction, self._token_hash(token), self._now()),
                )
        except sqlite3.Error as exc:
            raise StoreError("could not prepare task") from exc
        return task_id, token

    def get_task(self, task_id: str) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """SELECT task_id, owner_id, task_label, instruction, submission_state,
                       native_state, created_at, closed_at, close_reason, token_revoked
                       FROM tasks WHERE task_id=?""",
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError("could not read task") from exc
        if row is None:
            raise NotFound("task not found")
        return dict(row)

    def list_tasks(self) -> list[dict[str, Any]]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """SELECT t.task_id, t.task_label, t.submission_state, t.native_state,
                       t.created_at, t.closed_at, r.receipt_id, r.received_at, r.late
                       FROM tasks AS t LEFT JOIN receipts AS r USING(task_id)
                       ORDER BY t.created_at, t.task_id"""
                ).fetchall()
        except sqlite3.Error as exc:
            raise StoreError("could not list tasks") from exc
        return [dict(row) for row in rows]

    def has_task_token(self, task_id: str, token: str) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT result_token_sha256, token_revoked FROM tasks WHERE task_id=?", (task_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError("could not verify task capability") from exc
        return bool(
            row
            and not row["token_revoked"]
            and row["result_token_sha256"]
            and hmac.compare_digest(row["result_token_sha256"], self._token_hash(token))
        )

    def begin_submission(self, task_id: str) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT submission_state FROM tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound("task not found")
                if row["submission_state"] != "prepared":
                    raise InvalidTransition("task is not in prepared state; no resend is allowed")
                connection.execute(
                    "UPDATE tasks SET submission_state='sending' WHERE task_id=?", (task_id,)
                )
                connection.commit()
        except StoreError:
            raise
        except sqlite3.Error as exc:
            raise StoreError("could not persist sending state") from exc
        return self.get_task(task_id)

    def finish_submission(self, task_id: str, state: str) -> None:
        if state not in {"accepted", "rejected", "acceptance_unknown"}:
            raise ValueError("invalid submission state")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                changed = connection.execute(
                    "UPDATE tasks SET submission_state=? WHERE task_id=? AND submission_state='sending'",
                    (state, task_id),
                ).rowcount
                if changed != 1:
                    raise InvalidTransition("task left sending state")
                connection.commit()
        except StoreError:
            raise
        except sqlite3.Error as exc:
            raise StoreError("could not persist submission result") from exc

    def mark_acceptance_unknown(self, task_id: str) -> None:
        self.finish_submission(task_id, "acceptance_unknown")

    def submit_report(self, owner_id: str, validated: ValidatedReport) -> Receipt:
        """Atomically commit first report and receipt before any caller can ACK."""
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                task = connection.execute(
                    """SELECT owner_id, result_token_sha256, token_revoked, closed_at
                       FROM tasks WHERE task_id=?""",
                    (validated.task_id,),
                ).fetchone()
                if task is None:
                    raise NotFound("task not found")
                if task["owner_id"] != owner_id:
                    raise Unauthorized("authenticated Owner does not own this task")
                if task["token_revoked"] or not task["result_token_sha256"]:
                    raise Unauthorized("task result capability is no longer valid")
                if not hmac.compare_digest(task["result_token_sha256"], self._token_hash(validated.result_token)):
                    raise Unauthorized("invalid task result capability")

                current = connection.execute(
                    "SELECT receipt_id, digest, received_at, late FROM receipts WHERE task_id=?",
                    (validated.task_id,),
                ).fetchone()
                if current is not None:
                    if not hmac.compare_digest(current["digest"], validated.digest):
                        raise Conflict("a different report already has a durable receipt")
                    connection.commit()
                    return Receipt(
                        current["receipt_id"], validated.task_id, current["received_at"], bool(current["late"]), True
                    )

                receipt_id = f"rcpt_{uuid4().hex}"
                received_at = self._now()
                late = task["closed_at"] is not None
                connection.execute(
                    """INSERT INTO receipts(task_id, receipt_id, digest, report_json, received_at, late)
                       VALUES(?, ?, ?, ?, ?, ?)""",
                    (validated.task_id, receipt_id, validated.digest, validated.canonical_json, received_at, int(late)),
                )
                connection.commit()
                return Receipt(receipt_id, validated.task_id, received_at, late, False)
        except StoreError:
            raise
        except sqlite3.Error as exc:
            raise StoreError("could not persist result receipt") from exc

    def readback(self, task_id: str) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """SELECT t.task_id, t.task_label, t.submission_state, t.native_state,
                       t.created_at, t.closed_at, t.close_reason,
                       r.receipt_id, r.received_at, r.late, r.report_json
                       FROM tasks AS t LEFT JOIN receipts AS r USING(task_id) WHERE t.task_id=?""",
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError("could not read task receipt") from exc
        if row is None:
            raise NotFound("task not found")
        result = dict(row)
        report_json = result.pop("report_json")
        result["report"] = json.loads(report_json) if report_json is not None else None
        return result

    def close_task(self, task_id: str, reason: str) -> None:
        try:
            with self._connection() as connection:
                changed = connection.execute(
                    "UPDATE tasks SET closed_at=?, close_reason=? WHERE task_id=? AND closed_at IS NULL",
                    (self._now(), reason, task_id),
                ).rowcount
        except sqlite3.Error as exc:
            raise StoreError("could not close task") from exc
        if changed != 1:
            raise NotFound("open task not found")

    def revoke_token(self, task_id: str) -> None:
        try:
            with self._connection() as connection:
                changed = connection.execute(
                    """UPDATE tasks SET token_revoked=1, result_token_sha256=NULL
                       WHERE task_id=? AND token_revoked=0""",
                    (task_id,),
                ).rowcount
        except sqlite3.Error as exc:
            raise StoreError("could not revoke task capability") from exc
        if changed != 1:
            raise NotFound("active task capability not found")

    def evidence(self) -> dict[str, Any]:
        """Return a redacted, content-free local summary."""
        rows = self.list_tasks()
        return {
            "schema_version": SCHEMA_VERSION,
            "task_count": len(rows),
            "tasks": [
                {
                    "case_alias": f"task-{row['task_id'][:8]}",
                    "submission_state": row["submission_state"],
                    "report_received": row["receipt_id"] is not None,
                    "receipt_id": row["receipt_id"],
                    "received_at": row["received_at"],
                    "late": bool(row["late"]) if row["late"] is not None else False,
                    "native_state": row["native_state"],
                }
                for row in rows
            ],
        }
