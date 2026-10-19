"""Strictly classify the pinned Hermes active-stop outcomes accepted by B2."""
from __future__ import annotations

from typing import Literal


StopOutcome = Literal["cancelled", "interrupt_failed"]


def classify_stop_outcome(
    *,
    remote_status: object,
    remote_error_category: object,
    remote_interrupted: object,
    ledger_status: object,
    ledger_code: object,
    visible_status: object,
    visible_code: object,
) -> StopOutcome | None:
    """Return only the confirmed cancellation or the pinned interrupt failure.

    A failed Hermes Run is never treated as cancellation. The only accepted
    failure is the observed interrupt-class result, honestly surfaced by Relay
    and its owner-visible Inbox as ``failed / hermes_failed``.
    """
    if (
        remote_status == "failed"
        and remote_error_category == "interrupt"
        and ledger_status == "failed"
        and ledger_code == "hermes_failed"
        and visible_status == "failed"
        and visible_code == "hermes_failed"
    ):
        return "interrupt_failed"

    if (
        remote_status in {"cancelled", "interrupted"}
        and remote_interrupted in {"true", "unknown"}
        and ledger_status == "failed"
        and ledger_code == "hermes_cancelled"
        and visible_status == "cancelled"
        and visible_code == "hermes_cancelled"
    ):
        return "cancelled"

    return None
