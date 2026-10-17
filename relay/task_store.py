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
SCHEMA_VERSION = 7
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


class AdmissionBusy(AdmissionError):
    """This authenticated owner already has an outstanding Slow Task."""

    def __init__(self, active_task_id: str | None = None):
        super().__init__("slow_busy")
        self.active_task_id = active_task_id


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
    stop_requested_at: str | None = None
    stop_reason: str | None = None
    stop_attempts: int = 0
    stop_accepted_at: str | None = None


@dataclass(frozen=True, slots=True)
class CancelReceipt:
    task_id: str
    status: str
    code: str = "ok"
    duplicate: bool = False


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

    def defer_hermes_run(
        self,
        task_id: str,
        *,
        backend_run_id: str,
        run_started_at: datetime,
        run_deadline_at: datetime,
        next_poll_at: datetime,
        poll_attempts: int,
    ) -> None: ...

    def next_hermes_wake_at(self) -> datetime | None: ...

    def complete(self, task_id: str, *, result: BoundedTaskResult | None = None) -> None: ...

    def fail(self, task_id: str, *, code: str) -> None: ...

    def mark_uncertain(self, task_id: str, *, code: str = "acp_result_unconfirmed") -> None: ...

    def request_cancel(self, *, client_id: str, task_id: str, now: datetime) -> CancelReceipt: ...

    def request_stop(self, task_id: str, *, reason: str, now: datetime) -> CancelReceipt: ...

    def update_hermes_stop_state(
        self,
        task_id: str,
        *,
        stop_attempts: int,
        stop_accepted_at: datetime | None,
        next_poll_at: datetime,
    ) -> None: ...

    def operator_reconcile_uncertain(
        self,
        *,
        client_id: str,
        task_id: str,
        terminal_state: str,
        verified_run_id: str | None,
        operator_id: str,
        evidence_ref: str,
        now: datetime,
    ) -> CancelReceipt: ...

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
                    version = 6
                if version < 7:
                    self._migrate_v6_to_v7(connection)
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
                stop_requested_at TEXT,
                stop_reason TEXT,
                stop_attempts INTEGER NOT NULL DEFAULT 0,
                stop_accepted_at TEXT,
                manual_reconciled_at TEXT,
                manual_reconciled_by TEXT,
                manual_reconciliation_ref TEXT,
                manual_reconciled_state TEXT,
                manual_reconciled_run_id TEXT,
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

    def _migrate_v6_to_v7(self, connection: sqlite3.Connection) -> None:
        """Persist owner cancellation/stop state on the existing task row."""
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(slow_tasks)")}
        if "stop_requested_at" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN stop_requested_at TEXT")
        if "stop_reason" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN stop_reason TEXT")
        if "stop_attempts" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN stop_attempts INTEGER NOT NULL DEFAULT 0")
        if "stop_accepted_at" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN stop_accepted_at TEXT")
        if "manual_reconciled_at" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN manual_reconciled_at TEXT")
        if "manual_reconciled_by" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN manual_reconciled_by TEXT")
        if "manual_reconciliation_ref" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN manual_reconciliation_ref TEXT")
        if "manual_reconciled_state" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN manual_reconciled_state TEXT")
        if "manual_reconciled_run_id" not in columns:
            connection.execute("ALTER TABLE slow_tasks ADD COLUMN manual_reconciled_run_id TEXT")
        connection.execute("PRAGMA user_version=7")

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

            active = connection.execute(
                "SELECT t.task_id FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) "
                "WHERE t.client_id=? AND (t.status IN ('running','uncertain') OR "
                "(t.status='queued' AND (t.backend_run_id IS NOT NULL OR o.dispatch_started_at IS NOT NULL OR t.expires_at>?))) "
                "ORDER BY t.created_at ASC LIMIT 1",
                (request.client_id, created),
            ).fetchone()
            if active is not None:
                raise AdmissionBusy(str(active[0]))

            count = connection.execute(
                "SELECT COUNT(*) FROM slow_tasks WHERE status='queued' AND expires_at>?",
                (created,),
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
        except (AdmissionConflict, AdmissionCapacity, AdmissionBusy, AdmissionUnknown):
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
                "AND status='running' AND NOT (executor_kind='hermes_runs' AND backend_run_id IS NOT NULL)"
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

    def next_hermes_wake_at(self) -> datetime | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT MIN(CASE "
                "WHEN t.next_poll_at IS NULL THEN t.run_deadline_at "
                "WHEN t.run_deadline_at IS NULL THEN t.next_poll_at "
                "WHEN t.next_poll_at <= t.run_deadline_at THEN t.next_poll_at "
                "ELSE t.run_deadline_at END) "
                "FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) "
                "WHERE o.state='pending' AND t.status IN ('queued','running') "
                "AND t.executor_kind='hermes_runs' AND t.backend_run_id IS NOT NULL"
            ).fetchone()
            value = row[0] if row else None
            if value is None:
                return None
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Hermes wake timestamp must include timezone")
            return parsed.astimezone(timezone.utc)
        except (sqlite3.Error, TypeError, ValueError):
            raise AdmissionUnavailable("hermes_poll_schedule_unavailable") from None
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
                "t.run_started_at,t.run_deadline_at,t.next_poll_at,t.poll_attempts,o.dispatch_started_at,o.attempts,"
                "t.stop_requested_at,t.stop_reason,t.stop_attempts,t.stop_accepted_at "
                "FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) "
                "WHERE o.state='pending' AND ("
                "(t.status='queued' AND t.expires_at>? "
                "AND NOT (t.executor_kind='hermes_runs' AND t.backend_run_id IS NOT NULL) "
                "AND NOT EXISTS (SELECT 1 FROM slow_tasks AS active "
                "JOIN dispatch_outbox AS active_outbox USING(task_id) "
                "WHERE active.client_id=t.client_id AND active.task_id<>t.task_id "
                "AND (active.status IN ('running','uncertain') OR "
                "(active.status='queued' AND (active.backend_run_id IS NOT NULL "
                "OR active_outbox.dispatch_started_at IS NOT NULL)))) "
                "AND NOT EXISTS (SELECT 1 FROM slow_tasks AS earlier "
                "JOIN dispatch_outbox AS earlier_outbox USING(task_id) "
                "WHERE earlier.client_id=t.client_id AND earlier.task_id<>t.task_id "
                "AND earlier.status='queued' AND earlier.expires_at>? "
                "AND earlier.backend_run_id IS NULL AND earlier_outbox.dispatch_started_at IS NULL "
                "AND (earlier.created_at<t.created_at OR "
                "(earlier.created_at=t.created_at AND earlier.task_id<t.task_id)))) "
                "OR (t.executor_kind='hermes_runs' AND t.backend_run_id IS NOT NULL "
                "AND t.status IN ('queued','running') AND (t.next_poll_at IS NULL OR t.next_poll_at<=? "
                "OR (t.run_deadline_at IS NOT NULL AND t.run_deadline_at<=?)))) "
                "ORDER BY o.created_at ASC LIMIT ?",
                (_utc_iso(now), _utc_iso(now), _utc_iso(now), _utc_iso(now), limit),
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
                        stop_requested_at=row[16],
                        stop_reason=row[17],
                        stop_attempts=int(row[18] or 0),
                        stop_accepted_at=row[19],
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

    def defer_hermes_run(
        self,
        task_id: str,
        *,
        backend_run_id: str,
        run_started_at: datetime,
        run_deadline_at: datetime,
        next_poll_at: datetime,
        poll_attempts: int,
    ) -> None:
        if not isinstance(backend_run_id, str) or not _RUN_ID_RE.fullmatch(backend_run_id):
            raise AdmissionUnavailable("invalid_backend_run_id")
        if type(poll_attempts) is not int or poll_attempts < 0:
            raise AdmissionUnavailable("invalid_poll_attempts")
        started_iso = _utc_iso(run_started_at)
        deadline_iso = _utc_iso(run_deadline_at)
        next_poll_iso = _utc_iso(next_poll_at)
        if deadline_iso <= started_iso:
            raise AdmissionUnavailable("invalid_hermes_run_deadline")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT backend_run_id,run_started_at,run_deadline_at,status,stop_requested_at FROM slow_tasks "
                "WHERE task_id=? AND executor_kind='hermes_runs'",
                (task_id,),
            ).fetchone()
            if (
                not task
                or task[0] != backend_run_id
                or task[1] != started_iso
                or task[2] != deadline_iso
                or task[3] != "running"
            ):
                connection.execute("ROLLBACK")
                raise AdmissionUnavailable("hermes_defer_state_unavailable")
            effective_poll_iso = min(next_poll_iso, task[4]) if task[4] else next_poll_iso
            updated = connection.execute(
                "UPDATE slow_tasks SET next_poll_at=?,poll_attempts=? "
                "WHERE task_id=? AND executor_kind='hermes_runs' AND backend_run_id=? "
                "AND run_started_at=? AND run_deadline_at=? AND status='running'",
                (effective_poll_iso, poll_attempts, task_id, backend_run_id, started_iso, deadline_iso),
            ).rowcount
            released = connection.execute(
                "UPDATE dispatch_outbox SET state='pending',claimed_until=NULL,last_error=NULL "
                "WHERE task_id=? AND state='claimed' AND dispatch_started_at IS NOT NULL",
                (task_id,),
            ).rowcount
            if not updated or not released:
                connection.execute("ROLLBACK")
                raise AdmissionUnavailable("hermes_defer_state_unavailable")
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

    def request_cancel(self, *, client_id: str, task_id: str, now: datetime) -> CancelReceipt:
        if not isinstance(client_id, str) or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        return self._request_stop(task_id, reason="owner_cancel", now=now, client_id=client_id)

    def request_stop(self, task_id: str, *, reason: str, now: datetime) -> CancelReceipt:
        if reason not in {"owner_cancel", "run_deadline"}:
            raise AdmissionUnavailable("invalid_stop_reason")
        return self._request_stop(task_id, reason=reason, now=now, client_id=None)

    def _request_stop(
        self, task_id: str, *, reason: str, now: datetime, client_id: str | None
    ) -> CancelReceipt:
        if not isinstance(task_id, str) or not task_id:
            raise AdmissionUnavailable("invalid_task_id")
        now_iso = _utc_iso(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            sql = (
                "SELECT t.client_id,t.status,t.executor_kind,t.backend_run_id,t.stop_requested_at,"
                "t.stop_reason,t.stop_attempts,t.stop_accepted_at,o.state,o.dispatch_started_at,o.last_error "
                "FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) WHERE t.task_id=?"
            )
            params: tuple[object, ...] = (task_id,)
            if client_id is not None:
                sql += " AND t.client_id=?"
                params += (client_id,)
            row = connection.execute(sql, params).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return CancelReceipt(task_id, "not_found", "task_not_found")
            _owner, status, executor_kind, run_id, stop_at, stop_reason, stop_attempts, stop_accepted, outbox_state, dispatch_started, last_error = row
            if status == "completed":
                connection.execute("COMMIT")
                return CancelReceipt(task_id, "completed", "already_terminal")
            if status == "failed":
                code = str(last_error or "already_terminal")
                visible = "cancelled" if code in {"slow_cancelled_before_dispatch", "hermes_cancelled"} else ("timed_out" if code == "hermes_run_timed_out" else "failed")
                connection.execute("COMMIT")
                return CancelReceipt(task_id, visible, code)
            if stop_at is not None:
                if status == "uncertain" and last_error == "hermes_stop_unconfirmed":
                    connection.execute("COMMIT")
                    return CancelReceipt(task_id, "uncertain", "hermes_stop_unconfirmed", duplicate=True)
                waiting_for_id = executor_kind == "hermes_runs" and not run_id and dispatch_started is not None and outbox_state == "claimed"
                connection.execute("COMMIT")
                return CancelReceipt(
                    task_id,
                    "stopping" if run_id or waiting_for_id else "uncertain",
                    "hermes_stop_pending" if run_id else ("hermes_stop_waiting_run_id" if waiting_for_id else str(last_error or "hermes_submission_unconfirmed")),
                    duplicate=True,
                )
            if run_id and executor_kind == "hermes_runs":
                connection.execute(
                    "UPDATE slow_tasks SET status='running',stop_requested_at=?,stop_reason=?,"
                    "stop_attempts=0,stop_accepted_at=NULL,next_poll_at=? "
                    "WHERE task_id=? AND executor_kind='hermes_runs' AND backend_run_id=? "
                    "AND status IN ('queued','running','uncertain')",
                    (now_iso, reason, now_iso, task_id, run_id),
                )
                if outbox_state == "uncertain":
                    connection.execute(
                        "UPDATE dispatch_outbox SET state='pending',claimed_until=NULL,last_error=NULL "
                        "WHERE task_id=? AND state='uncertain'",
                        (task_id,),
                    )
                connection.execute("COMMIT")
                return CancelReceipt(task_id, "stopping", "hermes_stop_requested")
            if (
                executor_kind == "hermes_runs"
                and run_id is None
                and dispatch_started is not None
                and outbox_state == "claimed"
                and status == "running"
            ):
                # POST is in flight. Record the cancel intent but keep the claim
                # so a successful response can still persist the write-once Run ID.
                connection.execute(
                    "UPDATE slow_tasks SET stop_requested_at=?,stop_reason=?,stop_attempts=0,"
                    "stop_accepted_at=NULL,next_poll_at=? WHERE task_id=? AND status='running'",
                    (now_iso, reason, now_iso, task_id),
                )
                connection.execute("COMMIT")
                return CancelReceipt(task_id, "stopping", "hermes_stop_waiting_run_id")
            if dispatch_started is None and status in {"queued", "running"}:
                code = "slow_cancelled_before_dispatch" if reason == "owner_cancel" else "hermes_deadline_without_run"
                released = connection.execute(
                    "UPDATE dispatch_outbox SET state='failed',claimed_until=NULL,last_error=? "
                    "WHERE task_id=? AND state IN ('pending','claimed') AND dispatch_started_at IS NULL",
                    (code, task_id),
                ).rowcount
                if not released:
                    connection.execute("ROLLBACK")
                    raise AdmissionUnavailable("cancel_state_unavailable")
                connection.execute(
                    "UPDATE slow_tasks SET status='failed',stop_requested_at=?,stop_reason=?,"
                    "stop_accepted_at=NULL,next_poll_at=NULL WHERE task_id=?",
                    (now_iso, reason, task_id),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO task_inbox(task_id,delivery_state,updated_at) VALUES(?,?,?)",
                    (task_id, "pending", now_iso),
                )
                connection.execute("COMMIT")
                return CancelReceipt(task_id, "cancelled", code)
            # A prompt may have reached a legacy ACP or Hermes before the response
            # was lost. Keep the owner slot occupied; never infer remote cancellation.
            code = "hermes_submission_unconfirmed" if executor_kind == "hermes_runs" else "acp_cancel_unconfirmed"
            connection.execute(
                "UPDATE dispatch_outbox SET state='uncertain',last_error=? "
                "WHERE task_id=? AND state IN ('pending','claimed','uncertain')",
                (code, task_id),
            )
            connection.execute(
                "UPDATE slow_tasks SET status='uncertain',stop_requested_at=?,stop_reason=?,next_poll_at=NULL "
                "WHERE task_id=? AND status IN ('queued','running','uncertain')",
                (now_iso, reason, task_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO task_inbox(task_id,delivery_state,updated_at) VALUES(?,?,?)",
                (task_id, "pending", now_iso),
            )
            connection.execute("COMMIT")
            return CancelReceipt(task_id, "uncertain", code)
        except AdmissionUnavailable:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def update_hermes_stop_state(
        self,
        task_id: str,
        *,
        stop_attempts: int,
        stop_accepted_at: datetime | None,
        next_poll_at: datetime,
    ) -> None:
        if type(stop_attempts) is not int or stop_attempts < 0:
            raise AdmissionUnavailable("invalid_stop_attempts")
        accepted_iso = _utc_iso(stop_accepted_at) if stop_accepted_at is not None else None
        next_poll_iso = _utc_iso(next_poll_at)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE slow_tasks SET stop_attempts=?,stop_accepted_at=COALESCE(?,stop_accepted_at),next_poll_at=? "
                "WHERE task_id=? AND executor_kind='hermes_runs' AND backend_run_id IS NOT NULL "
                "AND stop_requested_at IS NOT NULL AND status='running'",
                (stop_attempts, accepted_iso, next_poll_iso, task_id),
            ).rowcount
            if not updated:
                connection.execute("ROLLBACK")
                raise AdmissionUnavailable("hermes_stop_state_unavailable")
            connection.execute("COMMIT")
        except AdmissionUnavailable:
            raise
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

    def operator_reconcile_uncertain(
        self,
        *,
        client_id: str,
        task_id: str,
        terminal_state: str,
        verified_run_id: str | None,
        operator_id: str,
        evidence_ref: str,
        now: datetime,
    ) -> CancelReceipt:
        if not isinstance(client_id, str) or not _ID_RE.fullmatch(client_id):
            raise AdmissionUnavailable("invalid_identity")
        if terminal_state not in {"completed", "failed", "cancelled", "interrupted", "no_remote_run"}:
            raise AdmissionUnavailable("invalid_reconciliation_state")
        if not isinstance(operator_id, str) or not _ID_RE.fullmatch(operator_id):
            raise AdmissionUnavailable("invalid_operator_id")
        if not isinstance(evidence_ref, str) or not evidence_ref.strip() or len(evidence_ref) > 512:
            raise AdmissionUnavailable("reconciliation_evidence_required")
        if verified_run_id is not None and (not isinstance(verified_run_id, str) or not _RUN_ID_RE.fullmatch(verified_run_id)):
            raise AdmissionUnavailable("invalid_reconciled_run_id")
        now_iso = _utc_iso(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT t.status,t.backend_run_id,t.stop_reason,o.state FROM slow_tasks AS t "
                "JOIN dispatch_outbox AS o USING(task_id) WHERE t.client_id=? AND t.task_id=?",
                (client_id, task_id),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return CancelReceipt(task_id, "not_found", "task_not_found")
            status, saved_run_id, stop_reason, outbox_state = row
            if status != "uncertain" or outbox_state != "uncertain":
                connection.execute("ROLLBACK")
                raise AdmissionUnavailable("task_not_in_manual_reconciliation")
            if saved_run_id:
                if verified_run_id != saved_run_id or terminal_state == "no_remote_run":
                    connection.execute("ROLLBACK")
                    raise AdmissionUnavailable("reconciliation_run_id_mismatch")
            elif terminal_state == "no_remote_run":
                if verified_run_id is not None:
                    connection.execute("ROLLBACK")
                    raise AdmissionUnavailable("reconciliation_run_id_mismatch")
            elif verified_run_id is None:
                connection.execute("ROLLBACK")
                raise AdmissionUnavailable("reconciliation_run_id_required")
            if terminal_state == "completed":
                code = "hermes_operator_reconciled_completed_no_result"
                visible = "failed"
            elif terminal_state in {"cancelled", "interrupted"}:
                code = "hermes_run_timed_out" if stop_reason == "run_deadline" else "hermes_cancelled"
                visible = "timed_out" if stop_reason == "run_deadline" else "cancelled"
            elif terminal_state == "no_remote_run":
                code = "hermes_operator_confirmed_no_remote_run"
                visible = "cancelled" if stop_reason == "owner_cancel" else "failed"
            else:
                code = "hermes_failed"
                visible = "failed"
            connection.execute(
                "UPDATE dispatch_outbox SET state='failed',claimed_until=NULL,last_error=? "
                "WHERE task_id=? AND state='uncertain'",
                (code, task_id),
            )
            connection.execute(
                "UPDATE slow_tasks SET status='failed',next_poll_at=NULL,manual_reconciled_at=?,"
                "manual_reconciled_by=?,manual_reconciliation_ref=?,manual_reconciled_state=?,"
                "manual_reconciled_run_id=? WHERE task_id=? AND client_id=? AND status='uncertain'",
                (now_iso, operator_id, evidence_ref.strip(), terminal_state, verified_run_id, task_id, client_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO task_inbox(task_id,delivery_state,updated_at) VALUES(?,?,?)",
                (task_id, "pending", now_iso),
            )
            connection.execute("COMMIT")
            return CancelReceipt(task_id, visible, code)
        except AdmissionUnavailable:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise AdmissionUnavailable("task_store_unavailable") from None
        finally:
            connection.close()

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
                "t.next_poll_at,t.poll_attempts,t.stop_requested_at,t.stop_reason,"
                "t.stop_attempts,t.stop_accepted_at "
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
                "stop_requested_at": row[12],
                "stop_reason": row[13],
                "stop_attempts": int(row[14] or 0),
                "stop_accepted_at": row[15],
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
                    "r.speak,r.card_json,r.details_md,t.backend_run_id,t.executor_kind,o.last_error,"
                    "t.stop_requested_at,t.stop_reason,t.stop_attempts,t.stop_accepted_at "
                    "FROM slow_tasks AS t LEFT JOIN dispatch_outbox AS o USING(task_id) "
                    "LEFT JOIN task_inbox AS i USING(task_id) "
                    "LEFT JOIN task_results AS r USING(task_id) "
                    "WHERE t.client_id=? ORDER BY t.created_at DESC LIMIT 1",
                    (client_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT t.task_id,t.status,t.task_kind,t.expires_at,i.delivery_state,"
                    "r.speak,r.card_json,r.details_md,t.backend_run_id,t.executor_kind,o.last_error,"
                    "t.stop_requested_at,t.stop_reason,t.stop_attempts,t.stop_accepted_at "
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
            stop_requested_at = row[11]
            stop_reason = row[12]
            if status == "uncertain" and (
                row[10] in {"hermes_run_deadline_exceeded", "hermes_run_timed_out"}
                or (stop_reason == "run_deadline" and row[10] == "hermes_stop_unconfirmed")
            ):
                # The deadline is known but remote activity may continue.
                status = "timed_out"
            elif status == "failed" and row[10] in {"hermes_cancelled", "slow_cancelled_before_dispatch"}:
                status = "cancelled"
            if status in {"queued", "running"} and row[3] <= now_iso:
                # Preserve OpenAB's legacy expiry behavior. For Hermes, this is a queue
                # deadline only; active work and known Runs use the executor/result state.
                if row[9] != "hermes_runs" or (status == "queued" and row[8] is None):
                    status = "expired"
            if status == "running" and stop_requested_at is not None:
                status = "stopping"
            delivery = row[4]
            result = None
            if row[5] is not None:
                result = {
                    "speak": row[5],
                    "card": json.loads(row[6]),
                    "details_md": row[7],
                }
            next_delivery = delivery
            if status in {"completed", "failed", "uncertain", "timed_out", "cancelled", "expired"}:
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
            if status in {"failed", "uncertain", "timed_out", "cancelled"} and isinstance(row[10], str):
                payload["code"] = row[10]
            if status == "stopping":
                payload["code"] = "hermes_stop_pending"
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
