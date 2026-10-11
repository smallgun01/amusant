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
from uuid import UUID

MAX_TASK_CHARS = 4096
TASK_KINDS = frozenset({"research", "compare", "summarize"})
SCHEMA_VERSION = 4
DELIVERY_STATES = frozenset({"pending", "notified", "read", "spoken"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ERROR_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
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

    def recover_after_restart(self) -> RecoveryCounts:
        """Single-Relay crash recovery: unsent claims are requeued; started ACP is uncertain."""
        connection = self._connect()
        transaction_open = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            transaction_open = True
            uncertain = connection.execute(
                "UPDATE dispatch_outbox SET state='uncertain', last_error='acp_result_unconfirmed' "
                "WHERE state='claimed' AND dispatch_started_at IS NOT NULL"
            ).rowcount
            connection.execute(
                "UPDATE slow_tasks SET status='uncertain' WHERE task_id IN ("
                "SELECT task_id FROM dispatch_outbox WHERE state='uncertain')"
            )
            requeued = connection.execute(
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
            return RecoveryCounts(requeued=int(requeued or 0), uncertain=int(uncertain or 0))
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
                "t.task_text,t.task_kind,t.backend,o.attempts "
                "FROM slow_tasks AS t JOIN dispatch_outbox AS o USING(task_id) "
                "WHERE o.state='pending' AND t.status='queued' AND t.expires_at>? "
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
                        attempts=int(row[8]) + 1,
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
        """Record that ACP/send has begun; a crash after this is uncertain, not retryable."""
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
                "UPDATE slow_tasks SET status=? WHERE task_id=? AND status='running'",
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
                "SELECT t.task_id,t.status,o.state,o.attempts,o.dispatch_started_at,o.last_error "
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
                "last_error": row[5],
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
                    "r.speak,r.card_json,r.details_md "
                    "FROM slow_tasks AS t LEFT JOIN task_inbox AS i USING(task_id) "
                    "LEFT JOIN task_results AS r USING(task_id) "
                    "WHERE t.client_id=? ORDER BY t.created_at DESC LIMIT 1",
                    (client_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT t.task_id,t.status,t.task_kind,t.expires_at,i.delivery_state,"
                    "r.speak,r.card_json,r.details_md "
                    "FROM slow_tasks AS t LEFT JOIN task_inbox AS i USING(task_id) "
                    "LEFT JOIN task_results AS r USING(task_id) "
                    "WHERE t.client_id=? AND t.task_id=?",
                    (client_id, task_id),
                ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            status = row[1]
            if status in {"queued", "running"} and row[3] <= now_iso:
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
            if status in {"completed", "failed", "uncertain", "expired"}:
                if speak and status == "completed" and result is not None:
                    next_delivery = "spoken"
                elif delivery in {None, "pending", "notified"}:
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
