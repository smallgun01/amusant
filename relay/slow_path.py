"""P3.1 backend-neutral Live tool admission adapter backed by durable SQLite."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Protocol
from uuid import uuid4

from fast_voice.contracts import JSONValue, ToolCallEvent
from .task_store import (
    MAX_TASK_CHARS,
    TASK_KINDS,
    AdmissionCapacity,
    AdmissionConflict,
    AdmissionReceipt,
    AdmissionUnknown,
    AdmissionUnavailable,
    TaskAdmissionRequest,
)

TOOL_NAME = "delegate_task"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TaskAdmissionStore(Protocol):
    def admit(self, request: TaskAdmissionRequest) -> AdmissionReceipt:
        """Return only after task + dispatch intent are durably committed."""
        ...


class SlowPathToolRouter:
    """Validate a Live tool call and perform short, durable backend-neutral admission.

    Identity, conversation, and backend are supplied by trusted server code.
    The model can provide only task text and an optional bounded task-kind hint.
    """

    def __init__(
        self,
        *,
        store: TaskAdmissionStore,
        client_id: str,
        conversation_id: str,
        backend_id: str,
        default_task_kind: str,
        max_task_ttl_seconds: int,
        acceptance_timeout_seconds: float,
        now=None,
        new_task_id=None,
    ) -> None:
        if not isinstance(client_id, str) or not _ID_RE.fullmatch(client_id):
            raise ValueError("client_id must be a bounded authenticated identity")
        if not isinstance(conversation_id, str) or not _ID_RE.fullmatch(conversation_id):
            raise ValueError("conversation_id must be a bounded server identity")
        if not isinstance(backend_id, str) or not backend_id or len(backend_id) > 64:
            raise ValueError("backend_id must be explicitly server-configured")
        if default_task_kind not in TASK_KINDS:
            raise ValueError("default_task_kind must be an allowed task kind")
        if type(max_task_ttl_seconds) is not int or max_task_ttl_seconds <= 0:
            raise ValueError("max_task_ttl_seconds must be positive")
        if (not isinstance(acceptance_timeout_seconds, (int, float))
                or not math.isfinite(acceptance_timeout_seconds)
                or acceptance_timeout_seconds <= 0):
            raise ValueError("acceptance_timeout_seconds must be finite and positive")
        self._store = store
        self._client_id = client_id
        self._conversation_id = conversation_id
        self._backend_id = backend_id
        self._default_task_kind = default_task_kind
        self._ttl = max_task_ttl_seconds
        self._timeout = float(acceptance_timeout_seconds)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._new_task_id = new_task_id or (lambda: str(uuid4()))

    async def __call__(self, event: ToolCallEvent, turn_id: int) -> Mapping[str, JSONValue]:
        if event.name != TOOL_NAME:
            return {"status": "rejected", "code": "unsupported_tool"}
        if type(turn_id) is not int or turn_id <= 0:
            return {"status": "rejected", "code": "invalid_turn_id"}
        call_id = event.call_id
        if not isinstance(call_id, str) or not _ID_RE.fullmatch(call_id):
            return {"status": "rejected", "code": "invalid_call_id"}
        request, validation_error = self._build_request(event.arguments, call_id, turn_id)
        if validation_error is not None:
            return validation_error
        assert request is not None
        try:
            receipt = await asyncio.wait_for(
                asyncio.to_thread(self._store.admit, request),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            # The SQLite thread may still commit. A same-call retry is deduplicated.
            return {"status": "unknown", "task_id": request.task_id, "code": "acceptance_unconfirmed"}
        except AdmissionConflict:
            return {"status": "rejected", "code": "call_id_conflict"}
        except AdmissionCapacity:
            return {"status": "unavailable", "code": "task_capacity"}
        except AdmissionUnavailable:
            return {"status": "unavailable", "code": "task_store_unavailable"}
        except AdmissionUnknown:
            return {"status": "unknown", "task_id": request.task_id, "code": "acceptance_unconfirmed"}
        except Exception:
            # A storage/transport failure can be ambiguous; never invent acceptance.
            return {"status": "unknown", "task_id": request.task_id, "code": "acceptance_unconfirmed"}
        return {
            "status": "accepted",
            "task_id": receipt.task_id,
            "duplicate": receipt.duplicate,
        }

    def _build_request(
        self,
        arguments: Mapping[str, object],
        call_id: str,
        turn_id: int,
    ) -> tuple[TaskAdmissionRequest | None, dict[str, JSONValue] | None]:
        if not isinstance(arguments, Mapping) or set(arguments) - {"task", "task_kind"}:
            return None, {"status": "rejected", "code": "invalid_arguments"}
        task = arguments.get("task")
        raw_kind = arguments.get("task_kind")
        task_kind = self._default_task_kind if raw_kind is None else raw_kind
        if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK_CHARS:
            return None, {"status": "rejected", "code": "invalid_task"}
        if not isinstance(task_kind, str) or task_kind not in TASK_KINDS:
            return None, {"status": "rejected", "code": "invalid_task_kind"}
        try:
            created = self._now()
            if created.tzinfo is None or created.utcoffset() is None:
                return None, {"status": "rejected", "code": "invalid_clock"}
            created = created.astimezone(timezone.utc)
            task_id = self._new_task_id()
            request = TaskAdmissionRequest(
                task_id=task_id,
                client_id=self._client_id,
                conversation_id=self._conversation_id,
                turn_id=turn_id,
                call_id=call_id,
                task=task,
                task_kind=task_kind,
                backend=self._backend_id,
                created_at=created,
                expires_at=created + timedelta(seconds=self._ttl),
            )
        except Exception:
            return None, {"status": "rejected", "code": "invalid_task_envelope"}
        return request, None


def build_delegate_task_tool(sdk_types):
    """Build Gemini's backend-neutral P3.1 tool declaration."""
    return sdk_types.Tool(
        function_declarations=[
            sdk_types.FunctionDeclaration(
                name=TOOL_NAME,
                description=(
                    "Admit bounded background research, comparison, or summarization. "
                    "Acceptance confirms durable admission only, not task completion."
                ),
                parameters=sdk_types.Schema(
                    type="OBJECT",
                    properties={
                        "task": sdk_types.Schema(type="STRING", description="The requested background task."),
                        "task_kind": sdk_types.Schema(
                            type="STRING",
                            enum=["research", "compare", "summarize"],
                            description="Optional task category hint; backend and permissions are server-selected.",
                        ),
                    },
                    required=["task"],
                ),
            )
        ]
    )
