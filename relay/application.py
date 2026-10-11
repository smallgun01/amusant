"""Container-neutral composition for one Amusant Relay application process.

An ECS A entrypoint can attach the returned authenticated WebSocket handler to its
chosen listener. This module does not provision ECS, configure ingress, or claim
that a container-local SQLite file survives task replacement.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

from fast_voice import LiveProvider, build_live_provider
from .live_bridge import (
    POST_TOOL_ACK_TIMEOUT_SECONDS,
    ConversationIdFactory,
    SlowPathToolHandler,
    make_handler,
)
from .slow_path import (
    SlowPathToolRouter,
    TaskAdmissionStore,
    build_delegate_task_tool,
)

ProviderClientFactory = Callable[..., Any]


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
    client_factory: ProviderClientFactory | None = None,
    sdk_types: Any | None = None,
    conversation_id_factory: ConversationIdFactory | None = None,
    post_tool_ack_timeout_seconds: float = POST_TOOL_ACK_TIMEOUT_SECONDS,
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

    delegate_tool = build_delegate_task_tool(sdk_types)

    def provider_factory() -> LiveProvider:
        return build_live_provider(
            provider_environ,
            client_factory=client_factory,
            sdk_types=sdk_types,
            tool_declarations=[delegate_tool],
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
        )

    return make_handler(
        expected_device_token,
        provider_factory,
        slow_path_tool_handler_factory=tool_handler_factory,
        authenticated_client_id=authenticated_client_id,
        conversation_id_factory=conversation_id_factory,
        post_tool_ack_timeout_seconds=post_tool_ack_timeout_seconds,
    )
