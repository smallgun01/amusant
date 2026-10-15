# Gemini 4 Argon review — remediation implementation brief

> Planning only, 2026-10-15. This is a docs-only review artifact, not evidence of fixes, test passes, deployment approval, or cloud/live test authorization.
> Canonical requirements: [SPEC.md](../../SPEC.md). Sequencing: [PLAN.md](../../PLAN.md). Verified results: [STATUS.md](../../STATUS.md).

## Baseline and scope

External architecture review verified against `main` on 2026-10-15. The findings are primarily integrated functionality, durability/cost, and packaging issues; they do **not** authorize a third ECS Service, SQS, GSI, extra agent workers, or a FAST/SLOW redesign.

- Existing tracking: [#30](https://github.com/smallgun01/amusant/issues/30) A1 DynamoDB history / A2 bounded shutdown / A3 offline CI (A3 merged); [#35](https://github.com/smallgun01/amusant/issues/35) persistent Relay A2; [#31](https://github.com/smallgun01/amusant/issues/31) local real-StickS3 G0; [#27](https://github.com/smallgun01/amusant/issues/27) device edge security.
- **G0 local FAST physical proof may proceed independently** with pinned firmware and stable Relay baseline. Do not mix software refactor into an ongoing G0 hardware test.
- **G3 spoken Inbox retrieval is blocked on PR-A acceptance.** The persistent ECS A2 live canary must address PR-B, PR-C, bounded shutdown and runtime packaging before execution. A2.0 read-only planning may continue.
- Work in independently reviewable code PRs, with offline tests/CI; no changes to shared cloudflared/Gateway, production tables or public ingress.

## PR-A — P0 Inbox voice retrieval, tool boundary, delivery acknowledgement

**Problem (confirmed source-level):** `relay/live_bridge.py` currently coerces `check_task_status` values such as `completed` to admission-only `unknown`, then suppresses post-tool audio/caption, and emits an admission-related fallback card. Separately, `lookup_inbox(..., speak=True)` currently marks a completed result `spoken` before any speech has successfully completed. Tool declarations remain available in the Live session when result text is returned; `inject_text()` by itself is not proof that tools are disabled.

**Implementation boundaries:**

1. Model tool result as a typed/explicit *tool kind* and outcome, not a single overloaded `tool_result_status`; distinguish `delegate_task` admission (`accepted/rejected/unavailable/unknown`) from `check_task_status` lookup (`queued/running/completed/failed/expired/uncertain/not_found`, plus sanitized rejected/unavailable/unknown). Use proper tool-specific timeout/fallback cards and preserve prior rejection/muted-admission behavior.
2. Admit at most the authorized tool action(s) per turn, correlating each `call_id` to its response. Once lookup returns untrusted Agent result text, reject any subsequent side-effecting or duplicate `delegate_task` in that turn **before calling the slow router**, including same-message multi-function-call cases. Verify how the Gemini SDK handles blocking ordering and duplicated/stale call IDs. The final product spoken result must be emitted through an actually tool-disabled Gemini turn/session or an equivalently enforced server-side no-tool boundary; `LiveProvider.inject_text()` does **not** revoke predeclared tools.
3. Separate *result lookup* from *speech-delivery confirmation*: `speak=True` requests speech but does not immediately imply `delivery_state=spoken`. Commit `spoken` only after server-observable delivery success (e.g. provider completion **and** successful outbound audio path / final device flush); this is not a guarantee the human actually heard it. Retain a retry-safe state when audio is suppressed, interrupted, failed, dropped, or client disconnected. Ensure read-vs-spoken transitions are atomic, owner scoped, non-regressing, and idempotent for SQLite **and** DynamoDB.
4. Keep backend `speak/card/details_md` as untrusted content with strict size limits and no capability escalation. Do not replay or re-admit an earlier ACP prompt to speak a saved result. Preserve provider-epoch fencing, barge-in and 3s/15s bounded post-tool completion behavior, with lookup-specific outcomes.
5. Verify against SPEC §§8, 9, 12–13 (new-session, tool-disabled spoken turn). If product contract changes, propose a focused SPEC change; do not quietly assert completion.

**Offline acceptance (required):**

- Full `check_task_status` fake-provider roundtrip: queued/completed/failed/uncertain/expired/not_found → correlated FunctionResponse → appropriate audio and output caption (where offered) → exactly one `turn_complete` and no incorrect admission card.
- Regression: original `delegate_task` accepted/rejected/unknown paths; failed/oversized lookup responses; mid-turn supersession, disconnect, stalled/partial audio and late callbacks; repeated/multiple tool calls, prompt-injection-like `details_md`, and an attempted post-lookup `delegate_task` never result in unauthorized admission.
- SQLite + in-memory/moto DynamoDB: result remains retrievable after aborted speech, only transitions to `spoken` on verified boundary, no `spoken` downgrade, other-client access rejected.
- Do not mark real Gemini tool-disabled voice behavior PASS on offline fakes alone; a later separately approved real model/physical test is required.

**Likely files:** `relay/live_bridge.py`, `relay/slow_path.py`, `relay/task_store.py`, `relay/dynamodb_task_store.py`, `fast_voice/gemini.py` or adapter contract if necessary; `fast_voice/tests/test_live_bridge.py`, `relay/tests/test_inbox.py`, `relay/tests/test_dynamodb_task_store.py`.

## PR-B — P1 admission wakeup and bounded polling

**Problem (confirmed):** `AsyncDispatcher.poll_interval_seconds=0.05` and its `notify()` is never wired by `build_supported_relay_runtime()`. DynamoDB scans over all `TASK#` rows can therefore run ~20 times/sec while idle.

**Implementation boundaries:**

1. Compose Dispatcher before/alongside each session router. Add optional `on_admitted` callback so *confirmed or deduplicated* durable admission wakes the dispatcher, while rejected admission does not. For acceptance timeout/commit-unknown, note an underlying `asyncio.to_thread()` call may commit later; preserve bounded periodic recovery so late commits are not stranded.
2. Wake `asyncio.Event` only on its owning loop, or through `loop.call_soon_threadsafe`. Fix the wakeup race in `_run_loop`: notification between the last drain and `_wakeup.clear()` must not be lost.
3. Keep startup `recover_after_restart()` and bounded backup scan. Suggested *initial*, configurable defaults: SQLite ~1s, DynamoDB ~15–30s (choose and document an operational default), with small explicit test intervals. Measure idle Query count and wake-to-claim latency offline; do not claim a cost estimate without measurement.
4. Keep exactly one concurrent ACP execution, avoid shared `OpenAbAcpExecutor._deadline` overlap, do not block Gemini FAST callbacks with ledger/network operations, and do not introduce additional ECS/SQS/GSI services.
5. Keep Dispatcher shutdown/recovery fixes tracked in #30 A2 / #35, not silently bundled without tests: no unsafe post-send replay.

**Offline acceptance (required):**

- Admission wakes due work without waiting for fallback interval; late-commit-after-timeout eventually recovered by polling; duplicate/no-op and rejected admission do not produce unintended execution.
- Inject deterministic wakeup-at-clear race, startup backlog and successive admitted tasks; all process exactly as allowed, without duplicate ACP execution.
- Idle DynamoDB ledger does not poll at 20 Query/s. Configuration validation rejects invalid intervals; fake executor and existing CI remain green.

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
