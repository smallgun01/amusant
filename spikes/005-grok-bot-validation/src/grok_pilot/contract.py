"""Bounded, transport-neutral Grok report and Routine payload contracts."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID

SCHEMA_VERSION = "amusant-grok-pilot-0"
MAX_ARGUMENT_BYTES = 3_000
MAX_INSTRUCTION_CHARS = 4_096
MAX_LABEL_CHARS = 80
MAX_TOKEN_CHARS = 128


class ContractError(ValueError):
    """Input did not satisfy the bounded pilot contract."""


@dataclass(frozen=True)
class ValidatedReport:
    task_id: str
    result_token: str
    report: dict[str, Any]
    canonical_json: str
    digest: str


def _object(
    value: Any, label: str, keys: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    optional = optional or set()
    unknown = set(value) - keys
    missing = keys - set(value) - optional
    if unknown:
        raise ContractError(f"{label} has unknown fields")
    if missing:
        raise ContractError(f"{label} is missing required fields")
    return value


def _text(
    value: Any,
    label: str,
    maximum: int,
    *,
    allow_empty: bool = False,
    allow_line_breaks: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be text")
    if (not allow_empty and not value.strip()) or len(value) > maximum:
        raise ContractError(f"{label} length is outside the allowed range")
    if any(
        unicodedata.category(char) == "Cc"
        and not (allow_line_breaks and char in "\r\n\t")
        for char in value
    ):
        raise ContractError(f"{label} must not contain control characters")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ContractError(f"{label} is not valid Unicode") from exc
    return value


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ContractError("payload cannot be encoded as UTF-8 JSON") from exc


def validate_report(payload: Any) -> ValidatedReport:
    """Validate a complete submit_result payload and compute its token-free digest."""
    top = _object(
        payload,
        "report",
        {
            "schema_version",
            "task_id",
            "result_token",
            "reported_outcome",
            "speak",
            "card",
            "native_result_locator",
        },
        optional={"native_result_locator"},
    )
    if top["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported schema_version")
    task_id = _text(top["task_id"], "task_id", 36)
    try:
        parsed_id = UUID(task_id)
    except (ValueError, AttributeError) as exc:
        raise ContractError("task_id must be a UUID") from exc
    if str(parsed_id) != task_id:
        raise ContractError("task_id must use canonical UUID form")
    token = _text(top["result_token"], "result_token", MAX_TOKEN_CHARS)
    if top["reported_outcome"] not in ("completed", "not_completed"):
        raise ContractError("reported_outcome must be completed or not_completed")
    speak = _text(top["speak"], "speak", 240)
    card = _object(top["card"], "card", {"title", "lines"})
    title = _text(card["title"], "card.title", 80)
    lines = card["lines"]
    if not isinstance(lines, list) or not 1 <= len(lines) <= 4:
        raise ContractError("card.lines must contain one to four lines")
    bounded_lines = [_text(line, f"card.lines[{index}]", 120) for index, line in enumerate(lines)]
    locator = top.get("native_result_locator")
    if locator is not None:
        locator = _text(locator, "native_result_locator", 512)

    report = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "reported_outcome": top["reported_outcome"],
        "speak": speak,
        "card": {"title": title, "lines": bounded_lines},
        "native_result_locator": locator,
    }
    # Bound the actual MCP tool arguments, including the auth capability.
    arguments_json = canonical_json({**report, "result_token": token})
    if len(arguments_json.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ContractError("tool arguments exceed 3000 UTF-8 bytes")
    content_json = canonical_json(report)
    digest = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
    return ValidatedReport(task_id, token, report, content_json, digest)


def validate_routine_payload(payload: Any) -> dict[str, str]:
    """Validate the initial Routine Webhook request (not the result report)."""
    body = _object(payload, "Routine payload", {"schema_version", "task_id", "result_token", "task_label", "instruction"})
    if body["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported schema_version")
    task_id = _text(body["task_id"], "task_id", 36)
    try:
        parsed_id = UUID(task_id)
    except (ValueError, AttributeError) as exc:
        raise ContractError("task_id must be a UUID") from exc
    if str(parsed_id) != task_id:
        raise ContractError("task_id must use canonical UUID form")
    token = _text(body["result_token"], "result_token", MAX_TOKEN_CHARS)
    label = _text(body["task_label"], "task_label", MAX_LABEL_CHARS)
    instruction = _text(
        body["instruction"], "instruction", MAX_INSTRUCTION_CHARS, allow_line_breaks=True
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "result_token": token,
        "task_label": label,
        "instruction": instruction,
    }
