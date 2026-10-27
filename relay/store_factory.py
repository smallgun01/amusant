"""Build the process TaskStore from environment. Never creates AWS tables."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from .task_store import SQLiteTaskStore, TaskStore, _ID_RE

TASK_STORE_ENV = "AMUSANT_TASK_STORE"
TASK_DB_ENV = "AMUSANT_TASK_DB"
DDB_TABLE_ENV = "AMUSANT_DDB_TABLE"
DDB_REGION_ENV = "AMUSANT_DDB_REGION"
DDB_ENDPOINT_ENV = "AMUSANT_DDB_ENDPOINT"
CLIENT_ID_ENV = "AMUSANT_CLIENT_ID"
MAX_PENDING_TASKS_ENV = "AMUSANT_MAX_PENDING_TASKS"
DEFAULT_MAX_PENDING_TASKS = 8


def _require(environ: Mapping[str, str], name: str) -> str:
    raw = environ.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be set")
    return raw.strip()


def _max_pending(environ: Mapping[str, str], default: int) -> int:
    raw = environ.get(MAX_PENDING_TASKS_ENV)
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str) or not re.fullmatch(r"[1-9][0-9]{0,8}", raw):
        raise ValueError("AMUSANT_MAX_PENDING_TASKS must be a positive integer")
    return int(raw)


def build_task_store(
    environ: Mapping[str, str],
    *,
    max_pending_tasks: int | None = None,
    preflight: bool = False,
) -> TaskStore:
    kind = (environ.get(TASK_STORE_ENV) or "sqlite").strip().lower()
    if max_pending_tasks is None:
        pending = _max_pending(environ, DEFAULT_MAX_PENDING_TASKS)
    else:
        pending = max_pending_tasks
    if kind in ("", "sqlite"):
        path = Path(_require(environ, TASK_DB_ENV))
        return SQLiteTaskStore(database_path=path, max_pending_tasks=pending)
    if kind != "dynamodb":
        raise ValueError("AMUSANT_TASK_STORE must be sqlite or dynamodb")
    client_id = _require(environ, CLIENT_ID_ENV)
    if not _ID_RE.fullmatch(client_id):
        raise ValueError("AMUSANT_CLIENT_ID must be a bounded authenticated identity")
    table = _require(environ, DDB_TABLE_ENV)
    region = (
        (environ.get(DDB_REGION_ENV) or "").strip()
        or (environ.get("AWS_REGION") or "").strip()
        or (environ.get("AWS_DEFAULT_REGION") or "").strip()
    )
    if not region:
        raise ValueError("AMUSANT_DDB_REGION or AWS_REGION must be set for DynamoDB")
    endpoint = (environ.get(DDB_ENDPOINT_ENV) or "").strip() or None
    if preflight and endpoint:
        raise ValueError("custom DynamoDB endpoints are forbidden for preflight")
    try:
        import boto3
    except ImportError as exc:
        raise ValueError("boto3 is required for AMUSANT_TASK_STORE=dynamodb") from exc
    from .boto3_dynamodb_ledger import Boto3DynamoLedger
    from .dynamodb_task_store import DynamoDbTaskStore

    kwargs: dict[str, object] = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if preflight:
        from botocore.config import Config

        kwargs["config"] = Config(
            connect_timeout=2,
            read_timeout=2,
            retries={"total_max_attempts": 1, "mode": "standard"},
            ignore_configured_endpoint_urls=True,
        )
    client = boto3.client("dynamodb", **kwargs)
    return DynamoDbTaskStore(
        Boto3DynamoLedger(client, table),
        client_id=client_id,
        max_pending_tasks=pending,
    )
