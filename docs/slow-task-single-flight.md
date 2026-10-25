# Amusant-channel single-flight, cancellation and recovery

Aligned 2026-10-25. [SPEC](../SPEC.md) / [ADR-0004](adr/0004-owner-managed-hermes.md) define scope. Runtime baseline is #116; #118 remains a separate reviewed candidate, not assumed deployed. [Original implementation and fixture record](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/docs/slow-task-single-flight.md) is retained unchanged in history.

## Policy and state model

Accept a new task only when its authenticated Owner has no outstanding **Amusant-admitted Run**. The reservation starts atomically with durable admission and includes first dispatch, POST-in-flight, known/deferred execution, stop requests and unresolved remote activity. A deferred poll frees the local Dispatcher claim, not the Owner's channel slot.

| State | Meaning | Amusant slot |
| --- | --- | --- |
| idle | No outstanding channel task | Available |
| queued / running | Accepted, dispatch or execution/polling pending | Held |
| stopping | Owner/deadline stop intent recorded | Held |
| uncertain | Remote or persistence outcome unconfirmed | Held; use the implemented reviewed recovery/reconciliation path |
| terminal | Trusted terminal confirmation or known pre-POST cancellation/rejection | Released |

Native Telegram and Hermes Cron work is outside this Relay slot. Do not create a global Hermes queue/lock or cancel unrelated native work. Resource contention is managed and measured at Hermes/host boundaries.

A distinct busy admission returns `slow_busy` without a new task, outbox, Inbox or Hermes POST. Identical trusted call identity/payload returns the original receipt before the busy check; changed payload conflicts. FAST conversation, status lookup and cancel remain available while busy.

## Execution versus result delivery

Authenticated exact-Run GET can confirm native `completed` even when presentation is missing/invalid. Preserve that fact and record a fixed bounded delivery failure; never store or speak the malformed source. Keep already trusted, recoverable artifact metadata when available, but do not salvage a reference from an invalid/untrusted envelope. A completed Run is not proof an external action succeeded.

Transport failures, mismatched owner/Run IDs and unresolved/expired state do not release the slot. Historical v1-pinned tasks are not submitted or reinterpreted as new v2 work; follow the [cutover procedure](result-contract-v2-cutover.md).

## Cancellation and timing

`cancel_task(task_id)` derives Owner identity from authenticated context. For a bound Run it durably requests STOP, then GETs for confirmation. STOP Accepted is not Terminal Confirmed. No slot release merely because a timer expired, no blind Run re-POST, and no cancellation of the whole Gateway.

GET-only recovery means no new Run submission; it must not suppress a first authorized Owner/deadline STOP. Concurrent Cancel intent must survive a recovery GET completing. These are explicit [#118 review regressions](https://github.com/smallgun01/amusant/pull/118#pullrequestreview-5641201516), not claims the candidate has already fixed them.

A Run stop does not remove a Cron job, cancel a booking, undo a Calendar event or settle provider billing. Those are separate Hermes operations requiring appropriate intent/authorization.

Keep existing explicit `AMUSANT_HERMES_*` deadline, poll, HTTP and STOP-grace/retry settings. This document selects no new product timing defaults; test values are fixtures. Pin the actual Hermes image/source before claiming API stop compatibility; dated local fake-provider outcomes are not live cost/settlement proof.

## Storage and compatibility

SQLite and DynamoDB adapters implement the same admission/stop/dedup invariants. Historical migration steps (including the v7 lifecycle and v8 result-contract metadata) remain history, not instructions to reset the current schema. Preserve old fingerprints, existing Runs and unresolved slots during later generic-task migrations.

Known Runs are recovered without new Run POSTs. Legacy unsubmitted work cannot overtake an unresolved known Run or ambiguous submission. ACP remains a separately configured legacy path, not automatic failure recovery or a user-facing FIFO promise.

## Operator reconciliation

Never reconcile from timeout alone. Independently establish the bound remote terminal state, or demonstrate no remote Run existed for an ambiguous submission; retain a sanitized evidence reference. The existing operator CLI requires an explicit release flag:

```sh
python -m relay.operator_reconcile --help
```

The existing manual reconciliation tool records confirmed completed-without-retrieved-result as `failed/no-result`; it does not invent result content. This special manual disposition must not erase the native execution fact in evidence or become a reason for normal result polling to discard a verified result. Never use `no_remote_run` when a durable Run ID exists.

Any live reconciliation is a separately approved mutation. Documentation, fake-clock tests, a green Draft gate or an elapsed grace period do not authorize it. See [Codex C1](handoffs/20261025-codex.md) and [OpenClaw gates](handoffs/20261025-openclaw.md).
