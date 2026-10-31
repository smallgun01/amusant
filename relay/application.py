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
from .dispatcher import AsyncDispatcher, ExecutionOutcome, MAX_POLL_INTERVAL_SECONDS
from .grok_callback import create_grok_callback_app
from .grok_callback_executor import GrokConfigError, load_grok_callback_executor
from .store_factory import build_task_store
from .task_store import TASK_KINDS
from .task_store import SQLiteTaskStore

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
# Retained for legacy ACP task-only calls; Hermes task-only calls force `agent` in the router.
DEFAULT_TASK_KIND = "agent"
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


def load_acp_executor(environ):
    """Import the legacy ACP adapter only when explicitly selected."""
    from .acp_client import load_acp_executor as load

    return load(environ)


def load_hermes_runs_executor(environ, *, save_backend_run_id, update_poll_state, update_stop_state):
    """Import Hermes only when explicitly selected as the rollback executor."""
    from .hermes_runs_executor import load_hermes_runs_executor as load

    return load(
        environ,
        save_backend_run_id=save_backend_run_id,
        update_poll_state=update_poll_state,
        update_stop_state=update_stop_state,
    )


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
        raise ValueError("AMUSANT_DEFAULT_TASK_KIND must be agent or a legacy task-kind hint (ACP compatibility)")
    default_task_kind = default_task_kind.strip()
    if default_task_kind not in TASK_KINDS:
        raise ValueError("AMUSANT_DEFAULT_TASK_KIND must be agent or a legacy task-kind hint (ACP compatibility)")
    slow_executor_kind = (environ.get(SLOW_EXECUTOR_ENV) or DEFAULT_SLOW_EXECUTOR).strip().lower() or DEFAULT_SLOW_EXECUTOR
    if slow_executor_kind not in {"openab_acp", "hermes_runs", "grok_callback"}:
        raise ValueError(f"{SLOW_EXECUTOR_ENV} must be openab_acp, hermes_runs, or grok_callback")
    # Older releases used this variable as a v1/v2 selector. Keep only a
    # fail-closed guard for stale deployment files; the runtime always uses v2
    # for Hermes Runs and the value no longer selects behavior.
    legacy_contract_setting = environ.get("AMUSANT_RESULT_CONTRACT_VERSION")
    if legacy_contract_setting is not None and legacy_contract_setting.strip() not in {"", "2"}:
        raise ValueError("AMUSANT_RESULT_CONTRACT_VERSION only accepts 2; unset it to use the v2 default")
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
    admission_available: bool = True,
    inbox_badges=None,
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

    live_tools = build_relay_tools(sdk_types, executor_kind=executor_kind)
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
            admission_available=admission_available,
            on_inbox_change=None if inbox_badges is None else inbox_badges.notify,
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
        inbox_badges=inbox_badges,
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
    grok_listener_available: bool = True,
    inbox_badges=None,
):
    """Build the selected executor dispatcher and Live handler.

    Do not switch executor kinds on a ledger with queued, running, or uncertain
    tasks. This runtime selects one executor and does not recover another
    backend's rows; such rows can be finalized as executor_unavailable. A
    same-ledger switch guard/recovery policy is a separate #135 follow-up.
    The Grok profile uses a separate fresh SQLite v10 ledger.
    """
    config = load_relay_process_config(environ, expected_device_token=expected_device_token)
    if config.slow_executor_kind == "grok_callback" and config.task_store_kind != "sqlite":
        raise ValueError("grok_callback requires the existing SQLite TaskStore")
    store = build_task_store(environ, max_pending_tasks=config.max_pending_tasks)
    if inbox_badges is not None:
        if config.slow_executor_kind != "grok_callback":
            raise ValueError("Inbox badges require the SQLite Grok profile")
        inbox_badges.store = store
    selected_executor = None
    if config.slow_executor_kind == "openab_acp":
        selected_executor = load_acp_executor(environ)
        if selected_executor is None:
            raise ValueError(
                "ACP selection requires AMUSANT_ACP_URL, AMUSANT_ACP_AUTH_KEY, and AMUSANT_ACP_CWD"
            )
    elif config.slow_executor_kind == "hermes_runs":
        selected_executor = load_hermes_runs_executor(
            environ,
            save_backend_run_id=store.save_backend_run_id,
            update_poll_state=store.update_hermes_poll_state,
            update_stop_state=store.update_hermes_stop_state,
        )
        if selected_executor is None:
            raise ValueError("Hermes selection requires AMUSANT_HERMES_RUNS_URL and AMUSANT_FACADE_API_KEY")
    else:
        if config.task_store_kind != "sqlite" or not isinstance(store, SQLiteTaskStore):
            raise ValueError("grok_callback requires the existing SQLite TaskStore")
        if grok_listener_available:
            try:
                selected_executor = load_grok_callback_executor(
                    environ,
                    begin_submission=store.begin_grok_submission,
                    finish_submission=store.finish_grok_submission,
                    fail_before_send=store.fail,
                )
            except GrokConfigError:
                # Keep FAST and status/cancel available; do not admit new work.
                selected_executor = None
    active_executors = (
        {config.slow_executor_kind: selected_executor}
        if selected_executor is not None
        else {}
    )

    class _ExecutorRouter:
        async def execute(self, task):
            selected = active_executors.get(task.executor_kind)
            if selected is None:
                state = "uncertain" if task.backend_run_id or task.executor_kind == "grok_callback" else "failed"
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
        admission_available=selected_executor is not None,
        inbox_badges=inbox_badges,
        client_factory=client_factory,
        sdk_types=sdk_types,
        conversation_id_factory=conversation_id_factory,
        post_tool_ack_timeout_seconds=post_tool_ack_timeout_seconds,
        post_tool_ack_max_duration_seconds=post_tool_ack_max_duration_seconds,
    )
    return handler, dispatcher, store


def build_grok_callback_receiver_app(
    *,
    store,
    authenticated_client_id: str,
    environ: Mapping[str, str],
    on_committed=None,
):
    """Build the restricted ASGI app without binding a public or local listener.

    A future HTTPS proxy/server must mount this app only after D2 review. This
    factory deliberately does not consume forwarded headers or start a server.
    """
    if not isinstance(store, SQLiteTaskStore):
        raise ValueError("grok_callback requires the existing SQLite TaskStore")
    raw_hosts = environ.get("AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS", "")
    hosts = [host.strip() for host in raw_hosts.split(",") if host.strip()]
    if not hosts:
        raise ValueError("AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS must be set before mounting the app")
    raw_port = environ.get("AMUSANT_GROK_CALLBACK_SERVICE_PORT", "443")
    if not isinstance(raw_port, str) or not raw_port.isascii() or not raw_port.isdigit():
        raise ValueError("AMUSANT_GROK_CALLBACK_SERVICE_PORT must be an integer")
    port = int(raw_port)
    raw_limit = environ.get("AMUSANT_GROK_CALLBACK_MAX_INFLIGHT", "8")
    if not isinstance(raw_limit, str) or not raw_limit.isascii() or not raw_limit.isdigit():
        raise ValueError("AMUSANT_GROK_CALLBACK_MAX_INFLIGHT must be an integer")
    return create_grok_callback_app(
        store,
        authenticated_client_id,
        trusted_hosts=hosts,
        expected_port=port,
        max_inflight=int(raw_limit),
        on_committed=on_committed,
    )


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
