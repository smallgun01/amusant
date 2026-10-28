"""Strict, bounded projection of a Grok Bot final Callback into Relay v2."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .task_store import BoundedTaskResult

GROK_RESULT_SCHEMA = "amusant-grok-pilot-0"
MAX_GROK_ARGUMENT_BYTES = 3_000
MAX_SPEAK_CHARS = 240
MAX_CARD_TITLE_CHARS = 80
MAX_CARD_LINE_CHARS = 120


class GrokResultError(ValueError):
    """A Grok Callback did not satisfy the bounded v2-compatible contract."""


@dataclass(frozen=True, slots=True)
class ValidatedGrokResult:
    task_id: str
    reported_outcome: str
    digest: str
    result: BoundedTaskResult


def _text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GrokResultError(f"{label} length is outside the allowed range")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise GrokResultError(f"{label} contains control characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise GrokResultError(f"{label} is not valid Unicode") from exc
    return value


def validate_grok_callback(payload: Any, *, result_token: str) -> ValidatedGrokResult:
    """Validate a token-free report; the capability arrives only in Authorization."""
    if not isinstance(result_token, str) or not result_token or len(result_token) > 128:
        raise GrokResultError("invalid task capability")
    if not isinstance(payload, dict):
        raise GrokResultError("report must be an object")
    allowed = {
        "schema_version", "task_id", "reported_outcome", "speak", "card",
        "native_result_locator",
    }
    if set(payload) - allowed or allowed - {"native_result_locator"} - set(payload):
        raise GrokResultError("report fields do not match the callback contract")
    if payload.get("schema_version") != GROK_RESULT_SCHEMA:
        raise GrokResultError("unsupported callback schema")
    task_id = _text(payload.get("task_id"), "task_id", 36)
    try:
        parsed = UUID(task_id)
    except (ValueError, AttributeError) as exc:
        raise GrokResultError("task_id must be a UUID") from exc
    if str(parsed) != task_id:
        raise GrokResultError("task_id must use canonical UUID form")
    outcome = payload.get("reported_outcome")
    if not isinstance(outcome, str) or outcome not in {"completed", "not_completed"}:
        raise GrokResultError("reported_outcome must be completed or not_completed")
    speak = _text(payload.get("speak"), "speak", MAX_SPEAK_CHARS)
    card = payload.get("card")
    if not isinstance(card, dict) or set(card) != {"title", "lines"}:
        raise GrokResultError("card must contain title and lines")
    title = _text(card.get("title"), "card.title", MAX_CARD_TITLE_CHARS)
    lines = card.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 4:
        raise GrokResultError("card.lines must contain one to four entries")
    bounded_lines = [_text(line, f"card.lines[{index}]", MAX_CARD_LINE_CHARS) for index, line in enumerate(lines)]
    locator = payload.get("native_result_locator")
    if locator is not None:
        _text(locator, "native_result_locator", 512)

    # The callback is an Agent report, not proof of native Run completion.
    # Prefix speech server-side so an untrusted report cannot present the
    # channel result as independently verified or claim success for failure.
    # The machine-readable native_state remains unknown. Avoid reading the
    # repetitive English engineering disclaimer aloud to the Taiwanese user.
    # Frame the answer explicitly as Grok's report, never independent proof.
    prefix = "Grok 回報：" if outcome == "completed" else "Grok 回報這項工作未完成："
    bounded_speak = prefix + speak[: MAX_SPEAK_CHARS - len(prefix)].rstrip()
    outcome_line = (
        "Grok 回報任務完成；原生執行狀態尚未獨立驗證。"
        if outcome == "completed"
        else "Grok 回報任務未完成；原生執行狀態尚未獨立驗證。"
    )
    result_lines = [outcome_line, *bounded_lines[:3]]
    normalized = {
        "schema_version": GROK_RESULT_SCHEMA,
        "task_id": task_id,
        "reported_outcome": outcome,
        "speak": speak,
        "card": {"title": title, "lines": bounded_lines},
        "native_result_locator": locator,
    }
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    argument_size = len(json.dumps(
        {**normalized, "result_token": result_token},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8"))
    if argument_size > MAX_GROK_ARGUMENT_BYTES:
        raise GrokResultError("callback report exceeds the v2 argument byte limit")
    return ValidatedGrokResult(
        task_id=task_id,
        reported_outcome=outcome,
        digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        result=BoundedTaskResult(
            speak=bounded_speak,
            card={"title": title, "lines": result_lines},
            details_md="\n".join([f"# {title}", "", *[f"- {line}" for line in result_lines]]),
            artifact_status=None,
            artifact_ref=None,
            presentation_status=None,
            delivery_error=None,
        ),
    )
