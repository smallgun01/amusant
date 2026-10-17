# Slow Task single-flight and recovery — Issue #61 draft

> Draft implementation notes for review. This document does not approve production timing values, deployment, or a Hermes server contract change.

## Policy and state model

A new Slow Task is accepted only when its authenticated owner has no outstanding Slow Task. The reservation begins atomically with durable admission and includes the internal first-dispatch state, POST-in-flight, a known/deferred Hermes Run, a stop request, and unresolved remote activity. A deferred poll frees the Relay's local Dispatcher claim, **not** the owner's Slow Task slot.

| State | Meaning | Owner slot |
| --- | --- | --- |
| idle | No outstanding task | Available |
| queued / running | Accepted; first dispatch or execution/polling is pending | Held |
| stopping | Owner cancellation or Run-deadline stop is recorded | Held |
| uncertain | Remote activity or persistence outcome cannot be confirmed | Held; operator reconciliation required |
| terminal | Confirmed result/failure/stop, or a known pre-POST rejection/cancel | Released |

A Hermes GET that confirms remote `completed` but has missing, partial, malformed, or unsupported output becomes failed with `hermes_invalid_result`; the untrusted payload is never stored or spoken, and the confirmed terminal Run releases the owner slot. A transport failure or expired/unresolved Run remains uncertain and busy.

A distinct admission while busy returns structured slow_busy and may include active_task_id; it creates no task, outbox, or Inbox row and sends no Hermes POST. Duplicate trusted call identity plus identical payload is resolved before the busy check and returns its original receipt; a changed payload remains a conflict. Busy applies to new Slow Tasks, not FAST conversation, status lookup, or cancel.

## Cancellation and Hermes contract boundary

FAST exposes an owner-scoped cancel_task tool with only task_id as model-supplied input. Relay derives the owner from authenticated server identity. The tool records cancellation durably and reports stopping; it does not assert that the remote Run has stopped.

For a known Run, Relay sends POST /v1/runs/{run_id}/stop with the existing bearer/session headers, then polls GET /v1/runs/{run_id}. Source was checked at Hermes tag v2026.9.24, commit f97608f178d1ffeca59860195ab7da295f7c8e5f (pinned image docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7; local OCI revision label matches the source commit, but a signed build attestation was not independently checked). In gateway/platforms/api_server_runs.py, the active Run stop handler returns HTTP 200 with status=stopping; already-terminal Runs return their current status, and a nonterminal Run without an active in-process agent/task returns 409 run_not_active. Any 2xx is persisted only as stop-request acknowledgement, including an empty 202; only a terminal GET releases the reservation. The focused Phase 2B gate exercises the pinned image; until that gate passes, runtime compatibility remains pending. No firmware/client change is included.

Timeout follows the same path: retain the #58A persisted Run deadline, record run_deadline as the stop reason, request stop, and keep the slot through bounded retry/backoff and GET confirmation. If stop grace/retry bounds are exhausted, persist uncertain and require operator action; elapsed time alone never unlocks the owner.

Hermes requires explicit values for AMUSANT_HERMES_RUN_DEADLINE_SECONDS, AMUSANT_HERMES_POLL_CYCLE_SECONDS, AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS, AMUSANT_HERMES_POLL_INTERVAL_SECONDS, AMUSANT_HERMES_STOP_GRACE_SECONDS, AMUSANT_HERMES_STOP_MAX_ATTEMPTS, AMUSANT_HERMES_STOP_RETRY_BASE_SECONDS, and AMUSANT_HERMES_STOP_RETRY_MAX_SECONDS. No product defaults are selected here; values used in tests are fixtures only.

## Existing data and migration

- SQLite advances its schema to v7 by adding stop/reconciliation fields to the existing task row. Admission is serialized in the existing transaction and checks earlier active rows; it does not create a second store.
- DynamoDB uses the existing owner partition and a conditional SLOT#SLOW reservation item. Before enabling the marker META#SLOW_SINGLE_FLIGHT_V1, a strongly consistent, paginated legacy-task query is used. More than 100 retained terminal history rows do not cause a false busy result. More than 100 genuinely active legacy rows remains fail-closed.
- Existing multiple known Run IDs are recovered by GET only; unsubmitted legacy work is not posted ahead of a known Run. An ambiguous row without a Run ID keeps the owner busy and prevents another legacy POST. Once known Runs reach terminal state, the oldest still-unsubmitted legacy item may proceed. Rows are not silently discarded or auto-cancelled.
- Explicit OpenAB ACP execution remains available for the existing rollback path. New Slow Task admission still follows the owner single-flight policy; no user-facing FIFO promise is made.

## Deliberate operator reconciliation

Never reconcile based only on a Relay timeout or a failed stop request. Independently verify the Hermes Run's terminal state, or verify that an ambiguous POST created no remote Run; attach the evidence reference to the task record. The CLI requires an explicit release flag.

~~~sh
python -m relay.operator_reconcile --help
~~~

For a known Run, the supplied verified Run ID must match the durable ID and the evidence reference is persisted with operator identity, state, and timestamp. A manually confirmed completed Run is marked failed/no-result because this tool cannot retrieve a result; it does not synthesize completion content. Never use no_remote_run when a durable Run ID exists.

## Verification boundary

Automated acceptance tests use SQLite, Moto/InMemory DynamoDB, and deterministic fake providers. They prove storage transitions and protocol handling, not Hermes server stop support, paid-model behavior, AWS/N100 behavior, or deployment acceptance. Production integration and timing values remain separate review gates.
