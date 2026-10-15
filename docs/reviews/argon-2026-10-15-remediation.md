# Gemini 4 Argon review — remediation implementation brief

> Planning only, 2026-10-15. This is a docs-only review artifact, not evidence of fixes, test passes, deployment approval, or cloud/live test authorization.
> Canonical requirements: [SPEC.md](../../SPEC.md). Sequencing: [PLAN.md](../../PLAN.md). Verified results: [STATUS.md](../../STATUS.md).

## Baseline and scope

External architecture review verified against `main` on 2026-10-15. The findings are primarily integrated functionality, durability/cost, and packaging issues; they do **not** authorize a third ECS Service, SQS, GSI, extra agent workers, or a FAST/SLOW redesign.

- Existing tracking: [#30](https://github.com/smallgun01/amusant/issues/30) A1 DynamoDB history / A2 bounded shutdown / A3 offline CI (A3 merged); [#35](https://github.com/smallgun01/amusant/issues/35) persistent Relay A2; [#31](https://github.com/smallgun01/amusant/issues/31) local real-StickS3 G0; [#27](https://github.com/smallgun01/amusant/issues/27) device edge security.
- **G0 local FAST physical proof may proceed independently** with pinned firmware and stable Relay baseline. Do not mix software refactor into an ongoing G0 hardware test.
- **G3 spoken Inbox retrieval is blocked on PR-A acceptance.** The persistent ECS A2 live canary must address PR-B, PR-C, bounded shutdown and runtime packaging before execution. A2.0 read-only planning may continue.
- Work in independently reviewable code PRs, with offline tests/CI; no changes to shared cloudflared/Gateway, production tables or public ingress.
- **Review checkpoint (2026-10-15):** [Agent source review on PR #39](https://github.com/smallgun01/amusant/pull/39#issuecomment-6189482286) returned **PR-A: Changes Requested**, **PR-B: LGTM with required clarifications**. This document incorporates those *planning* corrections; implementation and tests remain unproven. PR-C/D/E were **not reviewed** and must not be represented as approved designs.

## PR-A — P0 Inbox voice retrieval, tool boundary, delivery acknowledgement

**Problem (confirmed source-level):** `relay/live_bridge.py` currently coerces `check_task_status` values such as `completed` to admission-only `unknown`, then suppresses post-tool audio/caption, and emits an admission-related fallback card. Separately, `lookup_inbox(..., speak=True)` currently marks a completed result `spoken` before any speech has successfully completed. Tool declarations remain available in the Live session when result text is returned; `inject_text()` by itself is not proof that tools are disabled.

**Implementation boundaries:**

1. Model tool result as a typed/explicit *tool kind* and outcome, not a single overloaded `tool_result_status`; distinguish `delegate_task` admission (`accepted/rejected/unavailable/unknown`) from `check_task_status` lookup (`queued/running/completed/failed/expired/uncertain/not_found`, plus sanitized rejected/unavailable/unknown). **Only admission outcomes** may drive existing muted/rejected admission behavior. Lookup outcomes must have their own audio/caption and timeout/fallback paths, including `running` and errors; never display an `Admission could not be confirmed` card for a status lookup.
2. Apply a **minimal fail-closed per-turn tool authorization gate before `SlowPathToolRouter`**. The first authorized logical tool action reserves that turn: **at most one logical action per turn** for this iteration; later *distinct* tool calls (including mixed `delegate_task` and `check_task_status` in either order, and same-message function-call batches) get a sanitized rejection with their own `call_id` and **no router/store side effects**. A repeated/stale `call_id` must never execute twice; retain safe idempotent/correlated response handling consistent with the Gemini SDK, and ensure each distinct call ID receives one appropriate FunctionResponse. The provider currently dispatches calls sequentially: checking only the next model turn is insufficient. More permissive multi-action semantics require a separate explicit contract and regression tests, not implicit support.
3. **Honor SPEC §13:** saved-result speech must run in a **new tool-disabled Live turn/session** with a config that really omits tool declarations; `LiveProvider.inject_text()` is only `send_client_content` and does **not** revoke the active session's tools. Enforce a Relay-side pre-router gate as defense-in-depth regardless. If the implementation proposes a same-session alternative, change/review the SPEC explicitly **before** calling it equivalent; do not treat instructions to the model as a security boundary. The adapter does not currently support standalone `cancel_response()`.
4. Separate *result lookup* from *speech-delivery confirmation*: `speak=True` **requests** speech and must not mark `delivery_state=spoken` during lookup. Provide an explicit owner-scoped, atomic, idempotent, non-regressing delivery transition for both SQLite and DynamoDB. Mark `spoken` only after a **server-observable successful complete voice send**: genuine provider `turn_complete(cancelled=false)`, at least one output audio frame, successful forwarding of every frame, and successful final Opus/PCM output and control/turn-complete flush for that device mode. Provider cancellation, barge-in, disconnection, no/partial audio, failed writes/encoding, transport flush failure, or stale provider epoch must leave the result retrievable for retry. There is currently **no physical-device playback ACK**; `spoken` means Relay successfully transmitted the complete output, *not* proof that the human heard it.
5. Keep backend `speak/card/details_md` as untrusted content with strict size limits and no capability escalation. Do not replay or re-admit an earlier ACP prompt to speak a saved result. Preserve provider-epoch fencing, barge-in and 3s/15s bounded post-tool completion behavior, with lookup-specific outcomes. Confirm any speech request applies to a completed task owned by the requesting client.
6. Verify against SPEC §§8, 9, 12–13 (new-session, tool-disabled spoken turn). If product contract changes, propose a focused SPEC change; do not quietly assert completion.

**Offline acceptance (required):**

- Full `check_task_status` fake-provider → real bridge/router/store roundtrip: `queued/running/completed/failed/uncertain/expired/not_found` and lookup `rejected/unavailable/unknown` → correctly correlated FunctionResponse → appropriate audio and output caption (where offered) → properly finalized turn and no incorrect admission card. Verify tool declaration absent from the new spoken-result Live configuration and the Relay gate blocks unsafe calls even if the model/adapter tries one.
- Regression: original `delegate_task` accepted/rejected/unavailable/unknown paths; failed/oversized lookup responses; mid-turn supersession, disconnect, zero/stalled/partial audio, cancelled turn and late callbacks. Cover **both** same-batch orders (`lookup` → `delegate`, `delegate` → `lookup`), later-turn-internal attempts, multiple distinct call IDs, duplicate/stale/conflicting call IDs, malformed/prompt-injection-like `details_md`; every rejected call must get its correct response without unauthorized admission. Regress a legitimate duplicate receipt without a second task.
- SQLite + **existing in-memory DynamoDB ledger** parity (no new moto/AWS dependency required for this slice): lookup alone leaves `delivery_state` unchanged, completed full output sets `spoken` once, interruption/disconnect/zero/partial audio/final flush failures do not; no downgrade, repeated confirmation is idempotent, other-client access rejected.
- Do not mark real Gemini tool-disabled voice behavior PASS on offline fakes alone; a later separately approved real model/physical test is required.

**Likely files:** `relay/live_bridge.py`, `relay/slow_path.py`, `relay/task_store.py`, `relay/dynamodb_task_store.py`, `fast_voice/gemini.py` or adapter contract if necessary; `fast_voice/tests/test_live_bridge.py`, `relay/tests/test_inbox.py`, `relay/tests/test_dynamodb_task_store.py`.

## PR-B — P1 admission wakeup and bounded polling

**Problem (confirmed):** `AsyncDispatcher.poll_interval_seconds=0.05` and its `notify()` is never wired by `build_supported_relay_runtime()`. DynamoDB `claim_due()` reads `_task_rows()` using a **strongly consistent `Query`** on `TASK#` rows; this can run ~20 times/sec while idle (and each poll may page through more than one Query response). It is **not** a DynamoDB `Scan` operation.

**Implementation boundaries:**

1. Compose Dispatcher before/alongside each session router. Add optional `on_admitted` callback so **confirmed and deduplicated accepted** durable admission both wake the dispatcher: a duplicate receipt may reference still-pending work. Rejected/unavailable admission must **not** notify. Only notify **after** `asyncio.to_thread(store.admit)` resolves back on the owning event loop; never call `asyncio.Event.set()` directly from a worker thread. For acceptance timeout/commit-unknown, an underlying worker may still commit later (and DynamoDB can be ambiguous before/after commit): keep bounded periodic recovery so pending rows cannot be stranded.
2. Fix the lost-wakeup race in `_run_loop`: notification between the last empty drain and `_wakeup.clear()` must not be lost. Clear *before* draining, or use a verified equivalent generation/handshake pattern. Keep exactly one active event-loop owner for the Event; any future cross-thread caller must use `loop.call_soon_threadsafe`.
3. Keep startup `recover_after_restart()` with a **single explicit owner**: `build_supported_relay_runtime()` currently invokes it synchronously **and** `dispatcher.start()` invokes it again. When Dispatcher is enabled, choose one recovery path and do not duplicate or synchronously block the Live startup path. Maintain a bounded fallback poll. Suggested configurable backend-aware defaults: SQLite ~1s, DynamoDB ~15–30s; document worst-case eventual recovery latency, with small explicit test intervals. Measure idle strong-read Query count and wake-to-claim latency offline; do not claim production cost estimates from mock counters. Validate each polling interval as a **finite positive real number**, explicitly reject bool, NaN and ±Infinity (the current constructor lets such values through).
4. Keep exactly one concurrent ACP execution, avoid shared `OpenAbAcpExecutor._deadline` overlap, do not block Gemini FAST callbacks with ledger/network operations, and do not introduce additional ECS/SQS/GSI services.
5. Keep Dispatcher shutdown/recovery fixes tracked in #30 A2 / #35, not silently bundled without tests: no unsafe post-send replay.

**Offline acceptance (required):**

- Integrate `SlowPathToolRouter → durable admit → on_admitted → Dispatcher → fake executor`: idle Dispatcher wakes well before fallback timeout; **confirmed duplicate** also wakes but executes at most once; rejected/unavailable do not trigger execution or notify. Preserve single concurrent ACP execution.
- Deterministic barrier reproduces the empty-drain → wait/clear race; no lost notifications. Test startup backlog, consecutive admits, stop/restart, and a single owner of startup recovery. Already-sent ACP with unknown outcome must not be blindly replayed.
- Simulate an admission timeout while `to_thread()` commits *after* timeout, and DynamoDB ambiguous-before/after-commit with strong-read confirms/does not confirm; pending work discovered within the **documented bounded fallback**.
- Fake ledger **Query counter** shows idle calls far below 20/s (not an AWS cost benchmark). Interval validation rejects bool, NaN, ±Infinity and nonpositive values. Fake executor and existing offline CI remain green.

**Likely files:** `relay/slow_path.py`, `relay/dispatcher.py`, `relay/application.py`, `relay/tests/test_dispatcher.py`, `relay/tests/test_slow_path.py`, application integration tests.

## PR-C — P1 DynamoDB history >100 (existing #30 A1)

**Confirmed:** `DynamoDbTaskStore._task_rows()` raises `AdmissionCapacity` if Query reaches `QUERY_ITEM_BOUND=100`, even with zero queued tasks. It can stop new admission, recovery, latest Inbox and dispatch. `retain_until` is written, but the version-controlled table JSON does not enable TTL; verify the deployed table read-only before any TTL design/change.

**Design decision before coding:** Prefer the smallest bounded single-owner fix that preserves historical and pending correctness. A DynamoDB `FilterExpression` is **post-read**, consumes read units for scanned items and can yield empty pages with `LastEvaluatedKey`; filtering plus a 1-MB/page-count cap cannot silently omit pending or latest tasks. Do not treat `ScanIndexForward=False, Limit=1` on existing UUID sort keys as newest. A time-key migration requires compatible `TASK#id` point lookups, conditional writes and DEDUP reconciliation, and existing-data migration/read path; no silent key change.

**Offline acceptance:** >100 / 500+ completed rows with pending slots available: admission, oldest-due claim, startup recovery, correct latest result and owner isolation; pending cap; TTL non-immediacy/retention and dedup; bounded request units/Query pages; moto + in-memory parity. Do not create AWS resources in this PR without explicit approval.

## PR-D — P2 persistent runtime packaging (existing #35 A2.1 and #30 A2)

- Move production runtime from Python 3.12 to Python 3.13 for native TLS-PSK support (PSK callback availability also depends on compiled OpenSSL).
- Make Docker default process the real controlled Relay service, **not** `relay.wss_preflight_smoke`. Keep one-off smoke commands explicit overrides; include health and bounded signal/shutdown behavior, non-root where appropriate, and maintain supported PCM versus StickS3 mode isolation.
- Update `relay/tests/test_packaging.py` pins and ECS taskdef/entrypoint evidence. Existing RunTask overrides must be reviewed to avoid accidentally starting live Gemini/daemon during a one-off smoke.
- #30 A2: bounded drain/stop of an in-flight ACP prompt; post-send ambiguous outcome remains `uncertain`, no blind replay. Coordinate with ECS stopTimeout. No rollout before separate owner approval.

## PR-E — P2/P3 maintenance (defer, non-gate)

- Consolidate duplicate `relay/pyproject.toml` and `relay/uv.lock` only after checking historical developer CLI usage and locked smoke reproducibility; avoid unnecessary image context/CI changes.
- Move `fast_voice/tests/test_live_bridge.py` under `relay/tests`; remove `fast_voice/tests/test_factory.py` import from `relay.slow_path`; make imports consistent and drop legacy `PYTHONPATH=.:relay` only when all test entrypoints pass.
- Decouple `relay/devtools/nginx_sidecar_smoke.py` from `relay.tests.test_wss_proxy`; move fixtures/test doubles to suitable testing-only modules, not production dependency imports.
- Extract shared `validate_admission_request` and Inbox projection *after* PR-A delivery semantics are fixed and tested across both stores.

## Merge and acceptance discipline

Proposed implementation order: **PR-A → PR-B → PR-C → PR-D** (PR-B/C can be developed in parallel once contract edges are agreed); **PR-E deferred**. One concern per PR, explicit source baseline, regression tests, CI PASS and reviewer signoff before merge.

Do not automatically close #30 or #35 through `Closes/Fixes` directives in unrelated PRs; refer to issues using `Refs #30` and `Refs #35`. Existing #30 A3 is already done; retain its A1/A2 tasks. G0 may proceed on pinned code independently of these changes. Later G3 requires real voice/tool-disabled proof and separately approved provider/cloud/device tests.

**No authorization from this plan:** AWS deployments or IAM/table/TTL mutations, Gemini paid calls, device flashing, external ingress, shared Gateway/Cloudflare modifications, PR merge, or any claim of live verification.
