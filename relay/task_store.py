"""P3.1 durable SQLite admission registry; worker execution is a later slice."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID

MAX_TASK_CHARS = 4096
TASK_KINDS = frozenset({"research", "compare", "summarize"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AdmissionError(RuntimeError):
    """Base class for sanitized durable-admission failures."""


class AdmissionConflict(AdmissionError):
    """A provider call ID was reused with different task content."""


class AdmissionCapacity(AdmissionError):
    """The bounded pending-task registry is full."""


class AdmissionUnavailable(AdmissionError):
    """The store is known not to have durably accepted the task."""


class AdmissionUnknown(AdmissionError):
    """The commit result is ambiguous; callers must not claim acceptance."""


@dataclass(frozen=True, slots=True)
class TaskAdmissionRequest:
    task_id: str
    client_id: str
    conversation_id: str
    turn_id: int
    call_id: str
    task: str
    task_kind: str
    backend: str
    created_at: datetime
    expires_at: datetime

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"task": self.task, "task_kind": self.task_kind, "backend": self.backend},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    task_id: str
    duplicate: bool


class SQLiteTaskStore:
    """SQLite system-of-record for accepted tasks and transactional dispatch intent.

    No default database path or in-memory fallback is provided. P3.1 commits
    admission + outbox intent atomically; P3.2 owns claiming and worker recovery.
    """

    def __init__(self, *, database_path: str | Path, max_pending_tasks: int) -> None:
        raw_path = str(database_path)
        if raw_path == ":memory:":
            raise ValueError("P3.1 requires a persistent SQLite file, not :memory:")
        path = Path(database_path).expanduser()
        if not path.is_absolute() or not path.parent.is_dir():
            raise ValueError("database_path must be absolute and its parent directory must exist")
        if path.is_symlink():
            raise ValueError("database_path must not be a symlink")
        if type(max_pending_tasks) is not int or max_pending_tasks <= 0:
            raise ValueError("max_pending_tasks must be a positive integer")
        self._path = path
        self._max_pending_tasks = max_pending_tasks
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(str(self._path), timeout=5.0, isolation_level=None)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except sqlite3.Error:
            if connection is not None:
                connection.close()
            raise AdmissionUnavailable("task_store_unavailable") from None

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS slow_tasks (
                    task_id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    turn_id INTEGER NOT NULL,
                    call_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    task_text TEXT NOT NULL,
                    task_kind TEXT NOT NULL CHECK(task_kind IN ('research','compare','summarize')),
                    backend TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status = 'queued'),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(client_id, conversation_id, turn_id, call_id)
                );
                CREATE TABLE IF NOT EXISTS dispatch_outbox (
                    task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
                    state TEXT NOT NULL CHECK(state IN ('pending','sent')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )
            os.chmod(self._path, 0o600)
        except (sqlite3.Error, OSError):
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    @staticmethod
    def _validate(request: TaskAdmissionRequest) -> None:
        try:
            parsed = str(UUID(request.task_id))
        except (ValueError, TypeError, AttributeError):
            raise AdmissionUnavailable("invalid_task_id") from None
        if parsed != request.task_id.lower():
            raise AdmissionUnavailable("invalid_task_id")
        if not _ID_RE.fullmatch(request.client_id) or not _ID_RE.fullmatch(request.conversation_id):
            raise AdmissionUnavailable("invalid_identity")
        if type(request.turn_id) is not int or request.turn_id <= 0:
            raise AdmissionUnavailable("invalid_turn_id")
        if not isinstance(request.call_id, str) or not _ID_RE.fullmatch(request.call_id):
            raise AdmissionUnavailable("invalid_call_id")
        if not isinstance(request.task, str) or not request.task.strip() or len(request.task) > MAX_TASK_CHARS:
            raise AdmissionUnavailable("invalid_task")
        if request.task_kind not in TASK_KINDS:
            raise AdmissionUnavailable("invalid_task_kind")
        if not isinstance(request.backend, str) or not request.backend or len(request.backend) > 64:
            raise AdmissionUnavailable("invalid_backend")
        if (request.created_at.tzinfo is None or request.created_at.utcoffset() is None
                or request.expires_at.tzinfo is None or request.expires_at.utcoffset() is None):
            raise AdmissionUnavailable("invalid_task_time")
        if request.expires_at <= request.created_at:
            raise AdmissionUnavailable("invalid_task_deadline")

    def admit(self, request: TaskAdmissionRequest) -> AdmissionReceipt:
        """Durably accept one task, deduplicated by trusted identity + call ID."""
        self._validate(request)
        fingerprint = request.fingerprint()
        created = request.created_at.astimezone(timezone.utc).isoformat()
        expires = request.expires_at.astimezone(timezone.utc).isoformat()
        connection = self._connect()
        transaction_open = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            transaction_open = True
            row = connection.execute(
                "SELECT task_id,payload_sha256 FROM slow_tasks "
                "WHERE client_id=? AND conversation_id=? AND turn_id=? AND call_id=?",
                (request.client_id, request.conversation_id, request.turn_id, request.call_id),
            ).fetchone()
            if row is not None:
                if row[1] != fingerprint:
                    raise AdmissionConflict("call_id_conflict")
                connection.execute("COMMIT")
                transaction_open = False
                return AdmissionReceipt(task_id=row[0], duplicate=True)

            count = connection.execute(
                "SELECT COUNT(*) FROM slow_tasks WHERE status='queued'"
            ).fetchone()[0]
            if count >= self._max_pending_tasks:
                raise AdmissionCapacity("task_capacity")

            connection.execute(
                "INSERT INTO slow_tasks(task_id,client_id,conversation_id,turn_id,call_id,payload_sha256,"
                "task_text,task_kind,backend,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request.task_id, request.client_id, request.conversation_id, request.turn_id, request.call_id,
                    fingerprint, request.task, request.task_kind, request.backend, "queued", created, expires,
                ),
            )
            connection.execute(
                "INSERT INTO dispatch_outbox(task_id,state,attempts,created_at) VALUES(?,?,?,?)",
                (request.task_id, "pending", 0, created),
            )
            try:
                connection.execute("COMMIT")
            except sqlite3.Error:
                transaction_open = False
                raise AdmissionUnknown("acceptance_unconfirmed") from None
            transaction_open = False
            return AdmissionReceipt(task_id=request.task_id, duplicate=False)
        except (AdmissionConflict, AdmissionCapacity, AdmissionUnknown):
            if transaction_open:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        except sqlite3.Error:
            if transaction_open:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def get_by_call(self, *, client_id: str, conversation_id: str, turn_id: int, call_id: str) -> dict[str, object] | None:
        """Read-only diagnostics helper used by local acceptance tests."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT t.task_id,t.task_text,t.task_kind,t.backend,t.status,o.state "
                "FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) "
                "WHERE t.client_id=? AND t.conversation_id=? AND t.turn_id=? AND t.call_id=?",
                (client_id, conversation_id, turn_id, call_id),
            ).fetchone()
            if row is None:
                return None
            return {
                "task_id": row[0], "task": row[1], "task_kind": row[2],
                "backend": row[3], "status": row[4], "dispatch_state": row[5],
            }
        except sqlite3.Error:
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def pending_dispatch_count(self) -> int:
        connection = self._connect()
        try:
            row = connection.execute("SELECT COUNT(*) FROM dispatch_outbox WHERE state='pending'").fetchone()
            return int(row[0])
        except sqlite3.Error:
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()
