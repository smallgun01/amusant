"""P3.1 durable SQLite admission registry plus P3.2 claim/recovery lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Protocol
from uuid import UUID

MAX_TASK_CHARS = 4096
TASK_KINDS = frozenset({"research", "compare", "summarize"})
SCHEMA_VERSION = 6
EXECUTOR_KINDS = frozenset({"openab_acp", "hermes_runs"})
DELIVERY_STATES = frozenset({"pending", "notified", "read", "spoken"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ERROR_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_V2_TASK_STATUSES = "('queued','running','completed','failed','uncertain')"
_V2_OUTBOX_STATES = "('pending','claimed','sent','failed','uncertain')"


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
    executor_kind: str = "openab_acp"

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


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    task_id: str
    client_id: str
    conversation_id: str
    turn_id: int
    call_id: str
    task: str
    task_kind: str
    backend: str
    attempts: int
    executor_kind: str = "openab_acp"
    backend_run_id: str | None = None
    dispatch_started_at: str | None = None
    run_started_at: str | None = None
    run_deadline_at: str | None = None
    next_poll_at: str | None = None
    poll_attempts: int = 0


@dataclass(frozen=True, slots=True)
class RecoveryCounts:
    requeued: int
    uncertain: int


@dataclass(frozen=True, slots=True)
class BoundedTaskResult:
    speak: str
    card: dict[str, object]
    details_md: str


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdmissionUnavailable("invalid_task_time")
    return value.astimezone(timezone.utc).isoformat()


def _sanitize_error(code: str) -> str:
    if not isinstance(code, str) or not _ERROR_RE.fullmatch(code):
        return "dispatch_error"
    return code


class TaskStore(Protocol):
    """Durable Slow-Task ledger + Inbox. SQLite or DynamoDB adapter."""

    def admit(self, request: TaskAdmissionRequest) -> AdmissionReceipt: ...

    def recover_after_restart(self) -> RecoveryCounts: ...

    def claim_due(self, *, now: datetime, lease_seconds: int, limit: int = 1) -> list[ClaimedTask]: ...

    def mark_dispatch_started(self, task_id: str, *, now: datetime) -> None: ...

    def save_backend_run_id(
        self,
        task_id: str,
        run_id: str,
        *,
        run_started_at: datetime | None = None,
        run_deadline_at: datetime | None = None,
        next_poll_at: datetime | None = None,
        poll_attempts: int = 0,
    ) -> None: ...

    def update_hermes_poll_state(
        self, task_id: str, *, next_poll_at: datetime, poll_attempts: int
    ) -> None: ...

    def complete(self, task_id: str, *, result: BoundedTaskResult | None = None) -> None: ...

    def fail(self, task_id: str, *, code: str) -> None: ...

    def mark_uncertain(self, task_id: str, *, code: str = "acp_result_unconfirmed") -> None: ...

    def lookup_inbox(
        self,
        *,
        client_id: str,
        task_id: str | None = None,
        speak: bool = False,
        now: datetime | None = None,
    ) -> dict[str, object] | None: ...

    def mark_inbox_spoken(
        self, *, client_id: str, task_id: str, now: datetime | None = None
    ) -> bool: ...

    def get_by_task_id(self, task_id: str) -> dict[str, object] | None: ...

    def get_inbox(self, task_id: str) -> dict[str, object] | None: ...

    def get_result(self, task_id: str) -> dict[str, object] | None: ...

    def pending_dispatch_count(self) -> int: ...

    def has_client_tasks(self, client_id: str) -> bool: ...


class SQLiteTaskStore:
    """SQLite system-of-record for accepted tasks and transactional dispatch intent.

    No default database path or in-memory fallback is provided. P3.1 commits
    admission + outbox intent atomically. P3.2 claims, recovers, and records
    terminal/uncertain execution state. A container-local file is not claimed
    to survive ECS replacement.
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
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if "slow_tasks" not in tables:
                self._create_v2(connection)
            else:
                if version < 2:
                    self._migrate_v1_to_v2(connection)
                    version = 2
                if version < 3:
                    self._migrate_v2_to_v3(connection)
                    version = 3
                if version < 4:
                    self._migrate_v3_to_v4(connection)
                    version = 4
                if version < 5:
                    self._migrate_v4_to_v5(connection)
                    version = 5
                if version < 6:
                    self._migrate_v5_to_v6(connection)
            os.chmod(self._path, 0o600)
        except (sqlite3.Error, OSError, AdmissionUnavailable):
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def _create_v2(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            f"""
            CREATE TABLE slow_tasks (
                task_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL,
                call_id TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                task_text TEXT NOT NULL,
                task_kind TEXT NOT NULL CHECK(task_kind IN ('research','compare','summarize')),
                backend TEXT NOT NULL,
                executor_kind TEXT NOT NULL DEFAULT 'openab_acp',
                backend_run_id TEXT,
                run_started_at TEXT,
                run_deadline_at TEXT,
                next_poll_at TEXT,
                poll_attempts INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK(status IN {_V2_TASK_STATUSES}),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                UNIQUE(client_id, conversation_id, turn_id, call_id)
            );
            CREATE TABLE dispatch_outbox (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
                state TEXT NOT NULL CHECK(state IN {_V2_OUTBOX_STATES}),
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                claimed_until TEXT,
                dispatch_started_at TEXT,
                last_error TEXT
            );
            CREATE TABLE task_results (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
                speak TEXT NOT NULL,
                card_json TEXT NOT NULL,
                details_md TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE task_inbox (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
                delivery_state TEXT NOT NULL CHECK(delivery_state IN ('pending','notified','read','spoken')),
                updated_at TEXT NOT NULL
            );
            PRAGMA user_version={SCHEMA_VERSION};
            """
        )

    def _migrate_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_results (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
                speak TEXT NOT NULL,
                card_json TEXT NOT NULL,
                details_md TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            PRAGMA user_version=3;
            """
        )

    def _migrate_v3_to_v4(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_inbox (
                task_id TEXT PRIMARY KEY REFERENCES slow_tasks(task_id) ON DELETE CASCADE,
                delivery_state TEXT NOT NULL CHECK(delivery_state IN ('pending','notified','read','spoken')),
                updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO task_inbox(task_id,delivery_state,updated_at)
            SELECT task_id,'pending',created_at FROM task_results;
            INSERT OR IGNORE INTO task_inbox(task_id,delivery_state,updated_at)
            SELECT task_id,'pending',created_at FROM slow_tasks
            WHERE status IN ('completed','failed','uncertain');
            PRAGMA user_version=4;
            """
        )

    def _migrate_v4_to_v5(self, connection: sqlite3.Connection) -> None:
        """Add the selected executor and its optional durable remote Run ID."""
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(slow_tasks)")}
        if "executor_kind" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN executor_kind TEXT NOT NULL DEFAULT 'openab_acp'")
        if "backend_run_id" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN backend_run_id TEXT")
        connection.execute("PRAGMA user_version=5")

    def _migrate_v5_to_v6(self, connection: sqlite3.Connection) -> None:
        """Persist Hermes execution deadline and next-poll state on the existing task."""
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(slow_tasks)")}
        if "run_started_at" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN run_started_at TEXT")
        if "run_deadline_at" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN run_deadline_at TEXT")
        if "next_poll_at" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN next_poll_at TEXT")
        if "poll_attempts" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN poll_attempts INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            "UPDATE slow_tasks SET run_started_at=("
            "SELECT dispatch_started_at FROM dispatch_outbox WHERE dispatch_outbox.task_id=slow_tasks.task_id) "
            "WHERE executor_kind='hermes_runs' AND backend_run_id IS NOT NULL "
            "AND run_started_at IS NULL AND EXISTS ("
            "SELECT 1 FROM dispatch_outbox WHERE dispatch_outbox.task_id=slow_tasks.task_id "
            "AND dispatch_started_at IS NOT NULL)"
        )
        connection.execute("PRAGMA user_version=6")

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                CREATE TABLE slow_tasks_v2 (
                    task_id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    turn_id INTEGER NOT NULL,
                    call_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    task_text TEXT NOT NULL,
                    task_kind TEXT NOT NULL CHECK(task_kind IN ('research','compare','summarize')),
                    backend TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN {_V2_TASK_STATUSES}),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(client_id, conversation_id, turn_id, call_id)
                )
                """
            )
            connection.execute("INSERT INTO slow_tasks_v2 SELECT * FROM slow_tasks")
            connection.execute(
                f"""
                CREATE TABLE dispatch_outbox_v2 (
                    task_id TEXT PRIMARY KEY REFERENCES slow_tasks_v2(task_id) ON DELETE CASCADE,
                    state TEXT NOT NULL CHECK(state IN {_V2_OUTBOX_STATES}),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    claimed_until TEXT,
                    dispatch_started_at TEXT,
                    last_error TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO dispatch_outbox_v2("
                "task_id,state,attempts,created_at,claimed_until,dispatch_started_at,last_error) "
                "SELECT task_id,state,attempts,created_at,NULL,NULL,NULL FROM dispatch_outbox"
            )
            connection.execute("DROP TABLE dispatch_outbox")
            connection.execute("DROP TABLE slow_tasks")
            connection.execute("ALTER TABLE slow_tasks_v2 RENAME TO slow_tasks")
            connection.execute("ALTER TABLE dispatch_outbox_v2 RENAME TO dispatch_outbox")
            connection.execute("PRAGMA user_version=2")
            connection.execute("COMMIT")
        except sqlite3.Error:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

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
        if request.executor_kind not in EXECUTOR_KINDS:
            raise AdmissionUnavailable("invalid_executor_kind")
        if (request.created_at.tzinfo is None or request.created_at.utcoffset() is None
                or request.expires_at.tzinfo is None or request.expires_at.utcoffset() is None):
            raise AdmissionUnavailable("invalid_task_time")
        if request.expires_at <= request.created_at:
            raise AdmissionUnavailable("invalid_task_deadline")

    def admit(self, request: TaskAdmissionRequest) -> AdmissionReceipt:
        """Durably accept one task, deduplicated by trusted identity + call ID."""
        self._validate(request)
        fingerprint = request.fingerprint()
        created = _utc_iso(request.created_at)
        expires = _utc_iso(request.expires_at)
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
                "task_text,task_kind,backend,executor_kind,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request.task_id, request.client_id, request.conversation_id, request.turn_id, request.call_id,
                    fingerprint, request.task, request.task_kind, request.backend, request.executor_kind,
                    "queued", created, expires,
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

    def recover_after_restart(self) -> RecoveryCounts:
        """Requeue unsent work and Hermes Runs with IDs; never replay an ambiguous submission."""
        connection = self._connect()
        transaction_open = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            transaction_open = True
            requeued_runs = connection.execute(
                "UPDATE dispatch_outbox SET state='pending', claimed_until=NULL, last_error=NULL "
                "WHERE state='claimed' AND dispatch_started_at IS NOT NULL AND task_id IN ("
                "SELECT task_id FROM slow_tasks WHERE executor_kind='hermes_runs' "
                "AND backend_run_id IS NOT NULL)"
            ).rowcount
            uncertain = connection.execute(
                "UPDATE dispatch_outbox SET state='uncertain', last_error=CASE "
                "WHEN task_id IN (SELECT task_id FROM slow_tasks WHERE executor_kind='hermes_runs') "
                "THEN 'hermes_submission_unconfirmed' ELSE 'acp_result_unconfirmed' END "
                "WHERE state='claimed' AND dispatch_started_at IS NOT NULL"
            ).rowcount
            connection.execute(
                "UPDATE slow_tasks SET status='uncertain' WHERE task_id IN ("
                "SELECT task_id FROM dispatch_outbox WHERE state='uncertain')"
            )
            requeued_unsent = connection.execute(
                "UPDATE dispatch_outbox SET state='pending', claimed_until=NULL "
                "WHERE state='claimed' AND dispatch_started_at IS NULL"
            ).rowcount
            connection.execute(
                "UPDATE slow_tasks SET status='queued' WHERE task_id IN ("
                "SELECT task_id FROM dispatch_outbox WHERE state='pending')"
                "AND status='running'"
            )
            connection.execute("COMMIT")
            transaction_open = False
            return RecoveryCounts(
                requeued=int(requeued_runs or 0) + int(requeued_unsent or 0),
                uncertain=int(uncertain or 0),
            )
        except sqlite3.Error:
            if transaction_open:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def claim_due(self, *, now: datetime, lease_seconds: int, limit: int = 1) -> list[ClaimedTask]:
        """Claim due pending work for the in-process Dispatcher."""
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        claimed_until = _utc_iso(now + timedelta(seconds=lease_seconds))
        connection = self._connect()
        transaction_open = False
        claimed: list[ClaimedTask] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            transaction_open = True
            rows = connection.execute(
                "SELECT t.task_id,t.client_id,t.conversation_id,t.turn_id,t.call_id,"
                "t.task_text,t.task_kind,t.backend,t.executor_kind,t.backend_run_id,"
                "t.run_started_at,t.run_deadline_at,t.next_poll_at,t.poll_attempts,o.dispatch_started_at,o.attempts "
                "FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) "
                "WHERE o.state='pending' AND t.status='queued' "
                "AND (t.expires_at>? OR (t.executor_kind='hermes_runs' AND t.backend_run_id IS NOT NULL)) "
                "ORDER BY o.created_at ASC LIMIT ?",
                (_utc_iso(now), limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE dispatch_outbox SET state='claimed', attempts=attempts+1, claimed_until=? "
                    "WHERE task_id=? AND state='pending'",
                    (claimed_until, row[0]),
                )
                connection.execute(
                    "UPDATE slow_tasks SET status='running' WHERE task_id=? AND status='queued'",
                    (row[0],),
                )
                claimed.append(
                    ClaimedTask(
                        task_id=row[0],
                        client_id=row[1],
                        conversation_id=row[2],
                        turn_id=row[3],
                        call_id=row[4],
                        task=row[5],
                        task_kind=row[6],
                        backend=row[7],
                        attempts=int(row[15]) + 1,
                        executor_kind=row[8],
                        backend_run_id=row[9],
                        run_started_at=row[10],
                        run_deadline_at=row[11],
                        next_poll_at=row[12],
                        poll_attempts=int(row[13] or 0),
                        dispatch_started_at=row[14],
                    )
                )
            connection.execute("COMMIT")
            transaction_open = False
            return claimed
        except sqlite3.Error:
            if transaction_open:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def mark_dispatch_started(self, task_id: str, *, now: datetime) -> None:
        """Record that remote dispatch has begun; a crash without a Run ID is uncertain."""
        started = _utc_iso(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE dispatch_outbox SET dispatch_started_at=? "
                "WHERE task_id=? AND state='claimed' AND dispatch_started_at IS NULL",
                (started, task_id),
            ).rowcount
            if not updated:
                connection.execute("ROLLBACK")
                raise AdmissionUnavailable("dispatch_not_claimed")
            connection.execute("COMMIT")
        except AdmissionUnavailable:
            raise
        except sqlite3.Error:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def save_backend_run_id(
        self,
        task_id: str,
        run_id: str,
        *,
        run_started_at: datetime | None = None,
        run_deadline_at: datetime | None = None,
        next_poll_at: datetime | None = None,
        poll_attempts: int = 0,
    ) -> None:
        """Persist the one Run ID and its first durable timing state atomically."""
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise AdmissionUnavailable("invalid_backend_run_id")
        if type(poll_attempts) is not int or poll_attempts < 0:
            raise AdmissionUnavailable("invalid_poll_attempts")
        times = (run_started_at, run_deadline_at, next_poll_at)
        if any(value is not None for value in times) and not all(value is not None for value in times):
            raise AdmissionUnavailable("incomplete_hermes_run_timing")
        started_iso = _utc_iso(run_started_at) if run_started_at is not None else None
        deadline_iso = _utc_iso(run_deadline_at) if run_deadline_at is not None else None
        next_poll_iso = _utc_iso(next_poll_at) if next_poll_at is not None else None
        if started_iso is not None and deadline_iso <= started_iso:
            raise AdmissionUnavailable("invalid_hermes_run_deadline")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT t.executor_kind,t.backend_run_id,t.status,o.state,o.dispatch_started_at,"
                "t.run_started_at,t.run_deadline_at,t.next_poll_at "
                "FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) WHERE t.task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise AdmissionUnavailable("task_not_found")
            executor_kind, existing_run_id, task_status, outbox_state, dispatch_started_at = row[:5]
            existing_timing = row[5:8]
            if existing_run_id is not None:
                if existing_run_id != run_id:
                    raise AdmissionConflict("backend_run_id_conflict")
                if started_iso is not None and existing_timing[1] is None:
                    if dispatch_started_at is not None and started_iso != dispatch_started_at:
                        raise AdmissionUnavailable("run_start_anchor_mismatch")
                    connection.execute(
                        "UPDATE slow_tasks SET run_started_at=COALESCE(run_started_at,?), "
                        "run_deadline_at=COALESCE(run_deadline_at,?), "
                        "next_poll_at=COALESCE(next_poll_at,?), poll_attempts=? WHERE task_id=?",
                        (started_iso, deadline_iso, next_poll_iso, poll_attempts, task_id),
                    )
                connection.execute("COMMIT")
                return
            if executor_kind != "hermes_runs":
                raise AdmissionUnavailable("executor_mismatch")
            if task_status != "running" or outbox_state != "claimed" or dispatch_started_at is None:
                raise AdmissionUnavailable("dispatch_not_claimed")
            if started_iso is not None and started_iso != dispatch_started_at:
                raise AdmissionUnavailable("run_start_anchor_mismatch")
            connection.execute(
                "UPDATE slow_tasks SET backend_run_id=?,run_started_at=?,run_deadline_at=?,"
                "next_poll_at=?,poll_attempts=? WHERE task_id=? AND backend_run_id IS NULL",
                (run_id, started_iso, deadline_iso, next_poll_iso, poll_attempts, task_id),
            )
            connection.execute("COMMIT")
        except (AdmissionConflict, AdmissionUnavailable):
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def update_hermes_poll_state(self, task_id: str, *, next_poll_at: datetime, poll_attempts: int) -> None:
        if type(poll_attempts) is not int or poll_attempts < 0:
            raise AdmissionUnavailable("invalid_poll_attempts")
        next_poll_iso = _utc_iso(next_poll_at)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE slow_tasks SET next_poll_at=?,poll_attempts=? "
                "WHERE task_id=? AND executor_kind='hermes_runs' "
                "AND backend_run_id IS NOT NULL AND status='running'",
                (next_poll_iso, poll_attempts, task_id),
            ).rowcount
            if not updated:
                connection.execute("ROLLBACK")
                raise AdmissionUnavailable("hermes_poll_state_unavailable")
            connection.execute("COMMIT")
        except AdmissionUnavailable:
            raise
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def complete(self, task_id: str, *, result: BoundedTaskResult | None = None) -> None:
        self._finish(task_id, task_status="completed", outbox_state="sent", error=None, result=result)

    def fail(self, task_id: str, *, code: str) -> None:
        self._finish(task_id, task_status="failed", outbox_state="failed", error=_sanitize_error(code))

    def mark_uncertain(self, task_id: str, *, code: str = "acp_result_unconfirmed") -> None:
        self._finish(task_id, task_status="uncertain", outbox_state="uncertain", error=_sanitize_error(code))

    def _finish(
        self,
        task_id: str,
        *,
        task_status: str,
        outbox_state: str,
        error: str | None,
        result: BoundedTaskResult | None = None,
    ) -> None:
        if not isinstance(task_id, str) or not task_id:
            raise AdmissionUnavailable("invalid_task_id")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE dispatch_outbox SET state=?, last_error=? "
                "WHERE task_id=? AND state='claimed'",
                (outbox_state, error, task_id),
            ).rowcount
            if not updated:
                connection.execute("ROLLBACK")
                raise AdmissionUnavailable("dispatch_not_claimed")
            connection.execute(
                "UPDATE slow_tasks SET status=?,next_poll_at=NULL WHERE task_id=? AND status='running'",
                (task_status, task_id),
            )
            finished_at = datetime.now(timezone.utc).isoformat()
            if result is not None:
                connection.execute(
                    "INSERT INTO task_results(task_id,speak,card_json,details_md,created_at) VALUES(?,?,?,?,?)",
                    (
                        task_id,
                        result.speak,
                        json.dumps(result.card, separators=(",", ":"), ensure_ascii=False),
                        result.details_md,
                        finished_at,
                    ),
                )
            connection.execute(
                "INSERT OR IGNORE INTO task_inbox(task_id,delivery_state,updated_at) VALUES(?,?,?)",
                (task_id, "pending", finished_at),
            )
            connection.execute("COMMIT")
        except AdmissionUnavailable:
            raise
        except sqlite3.Error:
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

    def get_by_task_id(self, task_id: str) -> dict[str, object] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT t.task_id,t.status,o.state,o.attempts,o.dispatch_started_at,o.last_error, "
                "t.executor_kind,t.backend_run_id,t.run_started_at,t.run_deadline_at,"
                "t.next_poll_at,t.poll_attempts "
                "FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) WHERE t.task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "task_id": row[0],
                "status": row[1],
                "dispatch_state": row[2],
                "attempts": row[3],
                "dispatch_started": row[4] is not None,
                "dispatch_started_at": row[4],
                "last_error": row[5],
                "executor_kind": row[6],
                "backend_run_id": row[7],
                "run_started_at": row[8],
                "run_deadline_at": row[9],
                "next_poll_at": row[10],
                "poll_attempts": int(row[11] or 0),
            }
        except sqlite3.Error:
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def lookup_inbox(
        self,
        *,
        client_id: str,
        task_id: str | None = None,
        speak: bool = False,
        now: datetime | None = None,
    ) -> dict[str, object] | None:
        """Authorized previous-task lookup. Never returns another client's row."""
        if not isinstance(client_id, str) or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        if task_id is not None:
            try:
                parsed = str(UUID(task_id))
            except (ValueError, TypeError, AttributeError):
                return None
            if parsed != task_id.lower():
                return None
        clock = now or datetime.now(timezone.utc)
        now_iso = _utc_iso(clock)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if task_id is None:
                row = connection.execute(
                    "SELECT t.task_id,t.status,t.task_kind,t.expires_at,i.delivery_state,"
                    "r.speak,r.card_json,r.details_md,t.backend_run_id,t.executor_kind,o.last_error "
                    "FROM slow_tasks AS t LEFT JOIN dispatch_outbox AS o USING(task_id) "
                    "LEFT JOIN task_inbox AS i USING(task_id) "
                    "LEFT JOIN task_results AS r USING(task_id) "
                    "WHERE t.client_id=? ORDER BY t.created_at DESC LIMIT 1",
                    (client_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT t.task_id,t.status,t.task_kind,t.expires_at,i.delivery_state,"
                    "r.speak,r.card_json,r.details_md,t.backend_run_id,t.executor_kind,o.last_error "
                    "FROM slow_tasks AS t LEFT JOIN dispatch_outbox AS o USING(task_id) "
                    "LEFT JOIN task_inbox AS i USING(task_id) "
                    "LEFT JOIN task_results AS r USING(task_id) "
                    "WHERE t.client_id=? AND t.task_id=?",
                    (client_id, task_id),
                ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            status = row[1]
            if status == "uncertain" and row[10] == "hermes_run_deadline_exceeded":
                # The Relay deadline is known, but the remote Run may still be active.
                # Expose the policy timeout without claiming remote cancellation.
                status = "timed_out"
            if status in {"queued", "running"} and row[3] <= now_iso:
                # Preserve OpenAB's legacy expiry behavior. For Hermes, this is a queue
                # deadline only; active work and known Runs use the executor/result state.
                if row[9] != "hermes_runs" or (status == "queued" and row[8] is None):
                    status = "expired"
            delivery = row[4]
            result = None
            if row[5] is not None:
                result = {
                    "speak": row[5],
                    "card": json.loads(row[6]),
                    "details_md": row[7],
                }
            next_delivery = delivery
            if status in {"completed", "failed", "uncertain", "timed_out", "expired"}:
                # Retrieval is not proof that audio reached the device.
                if delivery in {None, "pending", "notified"}:
                    next_delivery = "read"
                if next_delivery is not None and next_delivery != delivery:
                    connection.execute(
                        "INSERT INTO task_inbox(task_id,delivery_state,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(task_id) DO UPDATE SET delivery_state=excluded.delivery_state, "
                        "updated_at=excluded.updated_at",
                        (row[0], next_delivery, now_iso),
                    )
                    delivery = next_delivery
            connection.execute("COMMIT")
            payload: dict[str, object] = {
                "task_id": row[0],
                "status": status,
                "task_kind": row[2],
                "delivery_state": delivery,
            }
            if status in {"failed", "uncertain", "timed_out"} and isinstance(row[10], str):
                payload["code"] = row[10]
            if status == "completed" and result is not None:
                payload.update(result)
            if speak and status == "completed" and result is not None:
                payload["speak_now"] = True
            return payload
        except (sqlite3.Error, json.JSONDecodeError):
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def mark_inbox_spoken(
        self,
        *,
        client_id: str,
        task_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Acknowledge a completed result after successful server-side audio delivery."""
        if not isinstance(client_id, str) or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        if not isinstance(task_id, str):
            return False
        try:
            parsed = str(UUID(task_id))
        except (ValueError, TypeError, AttributeError):
            return False
        if parsed != task_id.lower():
            return False
        now_iso = _utc_iso(now or datetime.now(timezone.utc))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT t.status,r.speak,i.delivery_state "
                "FROM slow_tasks AS t "
                "LEFT JOIN task_results AS r USING(task_id) "
                "LEFT JOIN task_inbox AS i USING(task_id) "
                "WHERE t.client_id=? AND t.task_id=?",
                (client_id, task_id),
            ).fetchone()
            if row is None or row[0] != "completed" or row[1] is None:
                connection.execute("COMMIT")
                return False
            if row[2] == "spoken":
                connection.execute("COMMIT")
                return True
            connection.execute(
                "INSERT INTO task_inbox(task_id,delivery_state,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET delivery_state='spoken', updated_at=excluded.updated_at",
                (task_id, "spoken", now_iso),
            )
            connection.execute("COMMIT")
            return True
        except sqlite3.Error:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def get_inbox(self, task_id: str) -> dict[str, object] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT delivery_state,updated_at FROM task_inbox WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            return {"delivery_state": row[0], "updated_at": row[1]}
        except sqlite3.Error:
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def get_result(self, task_id: str) -> dict[str, object] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT speak,card_json,details_md FROM task_results WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            return {"speak": row[0], "card": json.loads(row[1]), "details_md": row[2]}
        except (sqlite3.Error, json.JSONDecodeError):
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

    def has_client_tasks(self, client_id: str) -> bool:
        if not isinstance(client_id, str) or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM slow_tasks WHERE client_id=? LIMIT 1", (client_id,)
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()
