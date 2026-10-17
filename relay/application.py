"""Container-neutral composition for one Amusant Relay application process.

An ECS A entrypoint can attach the returned authenticated WebSocket handler to its
chosen listener. This module does not provision ECS, configure ingress, or claim
that a container-local SQLite file survives task replacement.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from fast_voice import LiveProvider, build_live_provider
from .companion_persona import resolve_persona
from .live_bridge import (
    POST_TOOL_ACK_MAX_DURATION_SECONDS,
    POST_TOOL_ACK_TIMEOUT_SECONDS,
    ConversationIdFactory,
    SlowPathToolHandler,
    make_handler,
)
from .slow_path import (
    SlowPathToolRouter,
    TaskAdmissionStore,
    build_relay_tools,
)
from .acp_client import load_acp_executor
from .dispatcher import AsyncDispatcher, ExecutionOutcome, MAX_POLL_INTERVAL_SECONDS
from .hermes_runs_executor import HermesConfigError, load_hermes_runs_executor
from .store_factory import build_task_store
from .task_store import TASK_KINDS

ProviderClientFactory = Callable[..., Any]
CLIENT_ID_ENV = "AMUSANT_CLIENT_ID"
BACKEND_ID_ENV = "AMUSANT_BACKEND_ID"
TASK_DB_ENV = "AMUSANT_TASK_DB"
TASK_STORE_ENV = "AMUSANT_TASK_STORE"
DDB_TABLE_ENV = "AMUSANT_DDB_TABLE"
DEFAULT_TASK_KIND_ENV = "AMUSANT_DEFAULT_TASK_KIND"
MAX_TASK_TTL_ENV = "AMUSANT_MAX_TASK_TTL_SECONDS"
MAX_PENDING_TASKS_ENV = "AMUSANT_MAX_PENDING_TASKS"
ACCEPTANCE_TIMEOUT_ENV = "AMUSANT_ACCEPTANCE_TIMEOUT_SECONDS"
DISPATCHER_POLL_INTERVAL_ENV = "AMUSANT_DISPATCHER_POLL_INTERVAL_SECONDS"
SLOW_EXECUTOR_ENV = "AMUSANT_SLOW_EXECUTOR"
# Product default: Hermes Native Runs. OpenAB ACP is an explicit legacy/rollback choice.
DEFAULT_SLOW_EXECUTOR = "hermes_runs"
DEFAULT_TASK_KIND = "research"
DEFAULT_MAX_TASK_TTL_SECONDS = 600
DEFAULT_MAX_PENDING_TASKS = 8
DEFAULT_ACCEPTANCE_TIMEOUT_SECONDS = 2.0
DEFAULT_SQLITE_DISPATCHER_POLL_SECONDS = 1.0
DEFAULT_DYNAMODB_DISPATCHER_POLL_SECONDS = 15.0
_PROCESS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class RelayProcessConfig:
    expected_device_token: str
    authenticated_client_id: str
    task_store_kind: str
    database_path: Path | None
    backend_id: str
    default_task_kind: str
    max_task_ttl_seconds: int
    max_pending_tasks: int
    acceptance_timeout_seconds: float
    dispatcher_poll_interval_seconds: float
    slow_executor_kind: str


def _require_env(environ: Mapping[str, str], name: str) -> str:
    raw = environ.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be set for Relay admission startup")
    return raw.strip()


def _optional_positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str) or not re.fullmatch(r"[1-9][0-9]{0,8}", raw):
        raise ValueError(f"{name} must be a positive integer")
    return int(raw)


def _optional_positive_float(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a finite positive number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def load_relay_process_config(
    environ: Mapping[str, str],
    *,
    expected_device_token: str,
) -> RelayProcessConfig:
    """Fail closed unless server-owned identity, backend, and store config are explicit."""
    if not isinstance(expected_device_token, str) or len(expected_device_token) < 12:
        raise ValueError("AMUSANT_DEVICE_TOKEN must contain at least 12 characters")
    client_id = _require_env(environ, CLIENT_ID_ENV)
    if not _PROCESS_ID_RE.fullmatch(client_id):
        raise ValueError("AMUSANT_CLIENT_ID must be a bounded authenticated identity")
    backend_id = _require_env(environ, BACKEND_ID_ENV)
    if len(backend_id) > 64:
        raise ValueError("AMUSANT_BACKEND_ID must be at most 64 characters")
    default_task_kind = environ.get(DEFAULT_TASK_KIND_ENV, DEFAULT_TASK_KIND)
    if not isinstance(default_task_kind, str) or not default_task_kind.strip():
        raise ValueError("AMUSANT_DEFAULT_TASK_KIND must be research, compare, or summarize")
    default_task_kind = default_task_kind.strip()
    if default_task_kind not in TASK_KINDS:
        raise ValueError("AMUSANT_DEFAULT_TASK_KIND must be research, compare, or summarize")
    slow_executor_kind = (environ.get(SLOW_EXECUTOR_ENV) or DEFAULT_SLOW_EXECUTOR).strip().lower() or DEFAULT_SLOW_EXECUTOR
    if slow_executor_kind not in {"openab_acp", "hermes_runs"}:
        raise ValueError(f"{SLOW_EXECUTOR_ENV} must be openab_acp or hermes_runs")
    store_kind = (environ.get(TASK_STORE_ENV) or "sqlite").strip().lower() or "sqlite"
    if store_kind not in {"sqlite", "dynamodb"}:
        raise ValueError("AMUSANT_TASK_STORE must be sqlite or dynamodb")
    database_path: Path | None
    if store_kind == "sqlite":
        database_path = Path(_require_env(environ, TASK_DB_ENV))
    else:
        database_path = None
        if not isinstance(environ.get(DDB_TABLE_ENV), str) or not str(environ.get(DDB_TABLE_ENV)).strip():
            raise ValueError("AMUSANT_DDB_TABLE must be set for DynamoDB")
    default_dispatcher_poll = (
        DEFAULT_SQLITE_DISPATCHER_POLL_SECONDS
        if store_kind == "sqlite"
        else DEFAULT_DYNAMODB_DISPATCHER_POLL_SECONDS
    )
    dispatcher_poll = _optional_positive_float(
        environ, DISPATCHER_POLL_INTERVAL_ENV, default_dispatcher_poll
    )
    if dispatcher_poll > MAX_POLL_INTERVAL_SECONDS:
        raise ValueError(
            f"{DISPATCHER_POLL_INTERVAL_ENV} must be at most {MAX_POLL_INTERVAL_SECONDS:g} seconds"
        )
    return RelayProcessConfig(
        expected_device_token=expected_device_token,
        authenticated_client_id=client_id,
        task_store_kind=store_kind,
        database_path=database_path,
        backend_id=backend_id,
        default_task_kind=default_task_kind,
        max_task_ttl_seconds=_optional_positive_int(
            environ, MAX_TASK_TTL_ENV, DEFAULT_MAX_TASK_TTL_SECONDS
        ),
        max_pending_tasks=_optional_positive_int(
            environ, MAX_PENDING_TASKS_ENV, DEFAULT_MAX_PENDING_TASKS
        ),
        acceptance_timeout_seconds=_optional_positive_float(
            environ, ACCEPTANCE_TIMEOUT_ENV, DEFAULT_ACCEPTANCE_TIMEOUT_SECONDS
        ),
        dispatcher_poll_interval_seconds=dispatcher_poll,
        slow_executor_kind=slow_executor_kind,
    )


def build_relay_handler(
    *,
    expected_device_token: str,
    authenticated_client_id: str,
    task_store: TaskAdmissionStore,
    provider_environ: Mapping[str, str],
    backend_id: str,
    default_task_kind: str,
    max_task_ttl_seconds: int,
    acceptance_timeout_seconds: float,
    on_admitted: Callable[[], None] | None = None,
    client_factory: ProviderClientFactory | None = None,
    sdk_types: Any | None = None,
    conversation_id_factory: ConversationIdFactory | None = None,
    post_tool_ack_timeout_seconds: float = POST_TOOL_ACK_TIMEOUT_SECONDS,
    post_tool_ack_max_duration_seconds: float = POST_TOOL_ACK_MAX_DURATION_SECONDS,
    executor_kind: str = DEFAULT_SLOW_EXECUTOR,
):
    """Compose Live tool declaration + per-authenticated-session admission.

    ``authenticated_client_id`` is server-owned and must come from the auth
    mapping for ``expected_device_token``; never derive it from a client hello
    or Gemini tool arguments. A fresh conversation ID is generated per
    authenticated WebSocket session unless a deterministic factory is injected
    by an offline test. The backend is fixed here, outside model control.

    The returned handler can be hosted by the Relay ECS process. ECS task
    provisioning, public ingress, and external durable-store selection are
    intentionally outside this P3.1 composition slice.
    """
    if sdk_types is None:
        try:
            from google.genai import types as sdk_types
        except ImportError:
            raise RuntimeError("google-genai SDK types are required for Relay tool composition") from None

    if conversation_id_factory is None:
        conversation_id_factory = lambda: str(uuid4())

    live_tools = build_relay_tools(sdk_types)
    system_instruction = resolve_persona(provider_environ)

    def provider_factory() -> LiveProvider:
        return build_live_provider(
            provider_environ,
            client_factory=client_factory,
            sdk_types=sdk_types,
            tool_declarations=[live_tools],
            system_instruction=system_instruction,
        )

    def spoken_provider_factory() -> LiveProvider:
        # Saved Inbox content gets its own Live session with no tool access,
        # including Gemini-managed Google Search grounding.
        return build_live_provider(
            provider_environ,
            client_factory=client_factory,
            sdk_types=sdk_types,
            system_instruction=system_instruction,
            allow_google_search=False,
        )

    def tool_handler_factory(client_id: str, conversation_id: str) -> SlowPathToolHandler:
        return SlowPathToolRouter(
            store=task_store,
            client_id=client_id,
            conversation_id=conversation_id,
            backend_id=backend_id,
            default_task_kind=default_task_kind,
            max_task_ttl_seconds=max_task_ttl_seconds,
            acceptance_timeout_seconds=acceptance_timeout_seconds,
            on_admitted=on_admitted,
            executor_kind=executor_kind,
        )

    return make_handler(
        expected_device_token,
        provider_factory,
        slow_path_tool_handler_factory=tool_handler_factory,
        spoken_provider_factory=spoken_provider_factory,
        authenticated_client_id=authenticated_client_id,
        conversation_id_factory=conversation_id_factory,
        post_tool_ack_timeout_seconds=post_tool_ack_timeout_seconds,
        post_tool_ack_max_duration_seconds=post_tool_ack_max_duration_seconds,
    )


def build_supported_relay_runtime(
    *,
    expected_device_token: str,
    environ: Mapping[str, str],
    client_factory: ProviderClientFactory | None = None,
    sdk_types: Any | None = None,
    conversation_id_factory: ConversationIdFactory | None = None,
    post_tool_ack_timeout_seconds: float = POST_TOOL_ACK_TIMEOUT_SECONDS,
    post_tool_ack_max_duration_seconds: float = POST_TOOL_ACK_MAX_DURATION_SECONDS,
):
    """Build handler plus optional executor dispatcher; no configured executor skips dispatch."""
    config = load_relay_process_config(environ, expected_device_token=expected_device_token)
    store = build_task_store(environ, max_pending_tasks=config.max_pending_tasks)
    acp_executor = load_acp_executor(environ)
    try:
        hermes_executor = load_hermes_runs_executor(
            environ,
            save_backend_run_id=store.save_backend_run_id,
            update_poll_state=store.update_hermes_poll_state,
            update_stop_state=store.update_hermes_stop_state,
        )
    except HermesConfigError:
        if config.slow_executor_kind != "openab_acp":
            raise
        # Explicit legacy rollback must start even when stale Hermes settings
        # remain. An unavailable Hermes executor is never replaced with ACP for
        # existing Hermes tasks; the executor router fails those closed.
        hermes_executor = None
    configured_executors = {
        "openab_acp": acp_executor,
        "hermes_runs": hermes_executor,
    }
    selected_executor = configured_executors[config.slow_executor_kind]
    if config.slow_executor_kind == "hermes_runs" and selected_executor is None:
        raise ValueError("Hermes selection requires AMUSANT_HERMES_RUNS_URL and AMUSANT_HERMES_API_KEY")
    if selected_executor is None and any(configured_executors.values()):
        raise ValueError(f"{SLOW_EXECUTOR_ENV} selects an executor without complete server configuration")
    active_executors = {kind: executor for kind, executor in configured_executors.items() if executor is not None}

    class _ExecutorRouter:
        async def execute(self, task):
            selected = active_executors.get(task.executor_kind)
            if selected is None:
                state = "uncertain" if task.backend_run_id else "failed"
                return ExecutionOutcome(status=state, code="executor_unavailable")
            return await selected.execute(task)

    dispatcher = (
        None
        if not active_executors
        else AsyncDispatcher(
            store,
            _ExecutorRouter(),
            poll_interval_seconds=config.dispatcher_poll_interval_seconds,
        )
    )
    # The running Dispatcher owns startup recovery. If no executor is configured,
    # still recover once so status lookups report interrupted work honestly.
    if dispatcher is None:
        store.recover_after_restart()
    handler = build_relay_handler(
        expected_device_token=config.expected_device_token,
        authenticated_client_id=config.authenticated_client_id,
        task_store=store,
        provider_environ=environ,
        backend_id=config.backend_id,
        default_task_kind=config.default_task_kind,
        max_task_ttl_seconds=config.max_task_ttl_seconds,
        acceptance_timeout_seconds=config.acceptance_timeout_seconds,
        on_admitted=None if dispatcher is None else dispatcher.notify,
        executor_kind=config.slow_executor_kind,
        client_factory=client_factory,
        sdk_types=sdk_types,
        conversation_id_factory=conversation_id_factory,
        post_tool_ack_timeout_seconds=post_tool_ack_timeout_seconds,
        post_tool_ack_max_duration_seconds=post_tool_ack_max_duration_seconds,
    )
    return handler, dispatcher, store


def build_supported_relay_handler(
    *,
    expected_device_token: str,
    environ: Mapping[str, str],
    client_factory: ProviderClientFactory | None = None,
    sdk_types: Any | None = None,
    conversation_id_factory: ConversationIdFactory | None = None,
    post_tool_ack_timeout_seconds: float = POST_TOOL_ACK_TIMEOUT_SECONDS,
    post_tool_ack_max_duration_seconds: float = POST_TOOL_ACK_MAX_DURATION_SECONDS,
):
    """Build the supported Live handler: FAST audio plus durable SLOW admission.

    This is the process-level composition used by `relay.live_bridge.run()`.
    It still does not provision ECS, public ingress, or a replacement-safe store.
    """
    handler, _dispatcher, _store = build_supported_relay_runtime(
        expected_device_token=expected_device_token,
        environ=environ,
        client_factory=client_factory,
        sdk_types=sdk_types,
        conversation_id_factory=conversation_id_factory,
        post_tool_ack_timeout_seconds=post_tool_ack_timeout_seconds,
        post_tool_ack_max_duration_seconds=post_tool_ack_max_duration_seconds,
    )
    return handler
