"""Independent SQLite ledger for one-shot pilot submissions and immutable receipts."""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .contract import SCHEMA_VERSION, ValidatedReport, validate_routine_payload

MAX_RESULT_TOKEN_TTL_SECONDS = 86_400


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


def pid_is_alive(pid: int) -> bool:
    """Return false only when the OS confirms that a local process is absent."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH or getattr(exc, "winerror", None) in {87, 1168}:
            return False
        # Unknown errors and access restrictions fail closed: do not recover/resend.
        return True
    return True


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    task_id: str
    received_at: str
    late: bool
    duplicate: bool


class PilotStore:
    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None):
        self.path = Path(path).expanduser().absolute()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._prepare_private_storage()
        self._initialize()

    def _prepare_private_storage(self) -> None:
        try:
            if os.name == "nt":
                # Windows inherits the current user's ACL; deployment guidance requires
                # a per-user LocalAppData directory and a separate ACL review for G3.
                self.path.parent.mkdir(parents=True, exist_ok=True)
                return

            parent = self.path.parent
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if parent.is_symlink() or not parent.is_dir():
                raise StoreError("SQLite parent must be a private real directory")
            parent_mode = stat.S_IMODE(parent.stat().st_mode)
            if parent_mode & 0o077 or parent_mode & 0o700 != 0o700:
                raise StoreError("SQLite parent must have POSIX mode 0700")

            self._secure_existing_file(self.path, required_mode=0o600, reject_broad_mode=True)
            for suffix in ("-wal", "-shm", "-journal"):
                self._secure_existing_file(Path(f"{self.path}{suffix}"), required_mode=0o600)
        except StoreError:
            raise
        except OSError as exc:
            raise StoreError("could not create private SQLite storage") from exc

    @staticmethod
    def _secure_existing_file(path: Path, *, required_mode: int, reject_broad_mode: bool = False) -> None:
        try:
            details = path.lstat()
        except FileNotFoundError:
            if reject_broad_mode:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, required_mode)
                os.close(descriptor)
                details = path.lstat()
            else:
                return
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise StoreError("SQLite files and sidecars must be regular files")
        current_mode = stat.S_IMODE(details.st_mode)
        if reject_broad_mode and (current_mode & 0o077 or current_mode & required_mode != required_mode):
            raise StoreError("existing SQLite database must have POSIX mode 0600")
        if not reject_broad_mode and current_mode & 0o077:
            os.chmod(path, required_mode)
        final_mode = stat.S_IMODE(path.lstat().st_mode)
        if final_mode != required_mode:
            os.chmod(path, required_mode)
            final_mode = stat.S_IMODE(path.lstat().st_mode)
        if final_mode != required_mode:
            raise StoreError("could not enforce private SQLite file mode")

    def _secure_sqlite_artifacts(self) -> None:
        if os.name == "nt":
            return
        for suffix in ("", "-wal", "-shm", "-journal"):
            self._secure_existing_file(
                Path(f"{self.path}{suffix}"),
                required_mode=0o600,
                reject_broad_mode=(suffix == ""),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            self._secure_sqlite_artifacts()
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            try:
                self._secure_sqlite_artifacts()
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
                        sender_pid INTEGER,
                        native_state TEXT NOT NULL DEFAULT 'unknown' CHECK(native_state = 'unknown'),
                        created_at TEXT NOT NULL,
                        result_token_expires_at TEXT,
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
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
                if "sender_pid" not in columns:
                    connection.execute("ALTER TABLE tasks ADD COLUMN sender_pid INTEGER")
                if "result_token_expires_at" not in columns:
                    connection.execute("ALTER TABLE tasks ADD COLUMN result_token_expires_at TEXT")
        except sqlite3.Error as exc:
            raise StoreError("could not initialize pilot database") from exc

    def _now_datetime(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("store clock must return a timezone-aware datetime")
        return current.astimezone(timezone.utc).replace(microsecond=0)

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _now(self) -> str:
        return self._format_datetime(self._now_datetime())

    @staticmethod
    def _parse_expiry(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise Unauthorized("task result capability expiry is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise Unauthorized("task result capability expiry is invalid")
        return parsed.astimezone(timezone.utc).replace(microsecond=0)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def prepare(
        self,
        owner_id: str,
        label: str,
        instruction: str,
        *,
        result_token_ttl_seconds: int | None = None,
    ) -> tuple[str, str]:
        if result_token_ttl_seconds is not None and (
            isinstance(result_token_ttl_seconds, bool)
            or not isinstance(result_token_ttl_seconds, int)
            or not 1 <= result_token_ttl_seconds <= MAX_RESULT_TOKEN_TTL_SECONDS
        ):
            raise ValueError(f"result token TTL must be 1..{MAX_RESULT_TOKEN_TTL_SECONDS} seconds")
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
        created_at = self._now_datetime()
        expires_at = (
            self._format_datetime(created_at + timedelta(seconds=result_token_ttl_seconds))
            if result_token_ttl_seconds is not None
            else None
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    """INSERT INTO tasks(task_id, owner_id, task_label, instruction,
                       result_token_sha256, submission_state, created_at, result_token_expires_at)
                       VALUES(?, ?, ?, ?, ?, 'prepared', ?, ?)""",
                    (
                        task_id,
                        owner_id,
                        label,
                        instruction,
                        self._token_hash(token),
                        self._format_datetime(created_at),
                        expires_at,
                    ),
                )
        except sqlite3.Error as exc:
            raise StoreError("could not prepare task") from exc
        return task_id, token

    def get_task(self, task_id: str) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """SELECT task_id, owner_id, task_label, instruction, submission_state,
                       native_state, created_at, result_token_expires_at, closed_at, close_reason, token_revoked
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
                    "SELECT result_token_sha256, token_revoked, result_token_expires_at FROM tasks WHERE task_id=?",
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError("could not verify task capability") from exc
        token_valid = bool(
            row
            and not row["token_revoked"]
            and row["result_token_sha256"]
            and hmac.compare_digest(row["result_token_sha256"], self._token_hash(token))
        )
        if not token_valid:
            return False
        if row["result_token_expires_at"] is None:
            return True
        try:
            return self._now_datetime() < self._parse_expiry(row["result_token_expires_at"])
        except (Unauthorized, ValueError):
            return False

    def begin_submission(
        self,
        task_id: str,
        *,
        sender_pid: int | None = None,
        result_token: str | None = None,
        require_result_token_expiry: bool = False,
    ) -> dict[str, Any]:
        sender_pid = os.getpid() if sender_pid is None else sender_pid
        if sender_pid <= 0:
            raise ValueError("sender_pid must be a positive process id")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """SELECT submission_state, result_token_sha256, token_revoked,
                       result_token_expires_at FROM tasks WHERE task_id=?""",
                    (task_id,),
                ).fetchone()
                if row is None:
                    raise NotFound("task not found")
                if row["submission_state"] != "prepared":
                    raise InvalidTransition("task is not in prepared state; no resend is allowed")
                if require_result_token_expiry:
                    expiry = row["result_token_expires_at"]
                    token_matches = bool(
                        result_token
                        and row["result_token_sha256"]
                        and hmac.compare_digest(
                            row["result_token_sha256"], self._token_hash(result_token)
                        )
                    )
                    if expiry is None or row["token_revoked"] or not token_matches:
                        raise Unauthorized("callback task capability is not valid")
                    try:
                        if self._now_datetime() >= self._parse_expiry(expiry):
                            raise Unauthorized("callback task capability is not valid")
                    except (ValueError, Unauthorized) as exc:
                        raise Unauthorized("callback task capability is not valid") from exc
                connection.execute(
                    "UPDATE tasks SET submission_state='sending', sender_pid=? WHERE task_id=?",
                    (sender_pid, task_id),
                )
                connection.commit()
        except StoreError:
            raise
        except sqlite3.Error as exc:
            raise StoreError("could not persist sending state") from exc
        return self.get_task(task_id)

    def recover_submission(self, task_id: str) -> dict[str, Any]:
        """Explicitly mark a crashed send unknown, only after its recorded PID is gone."""
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT submission_state, sender_pid FROM tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if row is None:
                    raise NotFound("task not found")
                if row["submission_state"] != "sending":
                    raise InvalidTransition("task is not in sending state")
                sender_pid = row["sender_pid"]
                if sender_pid is None:
                    raise InvalidTransition("sender identity is missing; recovery refused")
                if pid_is_alive(int(sender_pid)):
                    raise InvalidTransition("sender process is still present; recovery refused")
                changed = connection.execute(
                    """UPDATE tasks SET submission_state='acceptance_unknown', sender_pid=NULL
                       WHERE task_id=? AND submission_state='sending' AND sender_pid=?""",
                    (task_id, sender_pid),
                ).rowcount
                if changed != 1:
                    raise InvalidTransition("task changed during recovery")
                connection.commit()
        except StoreError:
            raise
        except sqlite3.Error as exc:
            raise StoreError("could not recover sending state") from exc
        return self.get_task(task_id)

    def finish_submission(self, task_id: str, state: str) -> None:
        if state not in {"accepted", "rejected", "acceptance_unknown"}:
            raise ValueError("invalid submission state")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                changed = connection.execute(
                    """UPDATE tasks SET submission_state=?, sender_pid=NULL
                       WHERE task_id=? AND submission_state='sending'""",
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

    def submit_report(
        self,
        owner_id: str,
        validated: ValidatedReport,
        *,
        require_token_expiry: bool = False,
    ) -> Receipt:
        """Atomically validate Owner/capability/expiry and commit one immutable receipt."""
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                task = connection.execute(
                    """SELECT owner_id, result_token_sha256, token_revoked, result_token_expires_at, closed_at
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

                expires_at = task["result_token_expires_at"]
                if require_token_expiry and expires_at is None:
                    raise Unauthorized("task result capability has no expiry")
                current_time = self._now_datetime()
                if expires_at is not None and current_time >= self._parse_expiry(expires_at):
                    raise Unauthorized("task result capability has expired")

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
                received_at = self._format_datetime(current_time)
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
                       t.created_at, t.result_token_expires_at, t.closed_at, t.close_reason,
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
