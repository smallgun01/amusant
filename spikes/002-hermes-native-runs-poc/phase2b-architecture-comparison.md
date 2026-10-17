# Phase 2B — source comparison and local POC verdict

**Verdict: PARTIAL — the adapter path is validated locally; operational simplification and cloud feasibility are not.** This is a spike report, not a SPEC/ADR change, production acceptance, or cutover approval.

## Scope and evidence boundary

- Existing implementation baseline inspected at the Phase 1 source snapshot aaf7c80a5791f279f491ab3a0a8e372d77b22bf2. Phase 2A harness/evidence was preserved in commit 69d23965747ccf4eae22371fa56fcb6f9ca19e89 and Draft PR #45.
- Phase 2B validation used the actual working-tree Relay code, local SQLite, deterministic fake provider, and pinned Hermes image v2026.9.24 (sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7). The sanitized run summary is evidence/phase2b-gates-sanitized.json.
- No AWS resources, real model API, Gemini Live, production Relay, SPEC/ADR, deployment configuration, or merge were changed or exercised.
- Full Relay test discovery: 203 tests run, all passed; 3 optional hardware-Opus tests skipped by their existing marker. The local Phase 2B Docker gates all passed.

## Before / after architecture

### Existing OpenAB ACP baseline

Live tool call → SlowPathToolRouter validates the bounded task and persists the server-owned identity/task/call-id and dispatch intent → AsyncDispatcher claims and starts the task → OpenAbAcpExecutor performs initialize → session/new → session/prompt and collects session-update text → TaskStore persists terminal result or uncertainty → existing owner-scoped Inbox serves check_task_status.

Facts from source: Relay owns admission, call-id deduplication, task ledger, dispatch/recovery, result persistence and Inbox. OpenAB ACP exposes a session/prompt exchange, not a Hermes Native Run ID/status API. A lost ACP terminal response is uncertain; the existing Dispatcher is the lifecycle owner.

### Opt-in Hermes adapter path implemented in this POC

Live tool call → same SlowPathToolRouter and ledger → same AsyncDispatcher routes by persisted executor_kind → HermesRunsExecutor sends a bounded POST /v1/runs with the server task_id as Idempotency-Key and an opaque owner-derived X-Hermes-Session-Key → write-once task_id↔run_id mapping is persisted on the existing task item → GET /v1/runs/{run_id} polling → the same bounded result and owner-scoped Inbox.

AMUSANT_SLOW_EXECUTOR defaults to openab_acp. Selecting hermes_runs requires server-configured AMUSANT_HERMES_RUNS_URL and AMUSANT_HERMES_API_KEY. The model still cannot choose the executor or endpoint. Existing ACP configuration and executor remain available as a baseline; this POC does not remove them.

SQLite schema v4→v5 adds executor_kind and backend_run_id. DynamoDB adds those attributes to the existing owner-scoped TASK item, with conditional write-once mapping; no new table/key/GSI or AWS operation was performed. Existing backend_id remains the server-owned backend identity and is not reused as Hermes run_id.

## Local Phase 2B gates

1. **Relay submit + mapping — PASS.** One Relay POST was observed. The returned Run ID was durably recorded while the fake-provider request was active; the disposable Relay process then exited.
2. **Relay process restart + result recovery — PASS.** A new process opened the same SQLite volume; Dispatcher recovery requeued the task with its persisted Run ID. The task reached completed and the result was available from the Relay TaskStore. Recorded Relay calls after restart were GET only (7 GETs, 0 POSTs). The fake provider recorded 2 completion requests while the one Hermes Run settled; these are provider-level requests, not Relay submissions.
3. **Accepted POST with lost response — PASS, fail-closed.** The test let Hermes accept one POST and then deliberately discarded its response. Relay recorded no Run ID, marked the task uncertain, retained uncertainty through recovery, and a subsequent Dispatcher drain handled 0 tasks. The trace contained one POST and no retry. The fake provider count increased from 2 to 4; the evidence does not claim a single provider call per Hermes Run.
4. **Unit/regression coverage — PASS.** 203 tests passed; 3 optional hardware-Opus tests were skipped. Coverage includes v4→v5 migration, SQLite reopen/recovery, InMemoryDynamoLedger mapping/recovery, owner-scoped opaque session keys, write-once Run ID, terminal failure vs interrupted uncertainty, missing status/result, and no blind resubmission.

An initial harness setup attempt failed because the new isolated SQLite volume was root-owned; the harness now initializes only that local volume for the non-root test user. That was a harness setup issue, not Hermes API evidence.

## Code and operational complexity estimate

- The existing ACP executor module is 378 lines; the new Hermes adapter is 269 lines (about 29% fewer lines at the executor-module boundary). This comparison alone is not proof of lower total code complexity: Hermes transport configuration and Run-ID persistence/recovery were added, while ACP is intentionally retained.
- Current change removes **0** Relay lifecycle components. AsyncDispatcher, TaskStore, backend-neutral admission, dedupe, owner-scoped Inbox and result retention all remain. In the changed production modules the net code is larger, not smaller.
- If a later approved cutover fully retires ACP, the adapter-module-only estimate is roughly 109 fewer lines (378−269), before accounting for shared runtime selection, schema mapping, tests and migration. Treat this as a rough LOC estimate, not an engineering-effort or reliability measure.
- Runtime protocol simplification is narrower: Relay no longer needs to create an ACP session and collect streamed updates for Hermes tasks; it performs one Run submission and bounded status/result polling. The Dispatcher still polls its own ledger, and the Hermes adapter polls Hermes; this POC therefore does not eliminate polling or the Relay control plane.
- Operationally, the likely reduction is one ACP session-management integration path, not the durable queue/ledger, owner auth, Inbox, task retention or deployment burden. Hermes adds its own durable storage, API key, session namespace, status-retention and lifecycle dependencies.

## Remaining risks

### Security and owner isolation

- Relay TaskStore and Inbox remain owner-scoped and the Hermes session header is a stable HMAC-derived opaque namespace; unit tests show different Relay owners receive different session keys.
- This was not a live cross-owner authorization test against Hermes. Hermes API access still uses a server-side bearer key, so Relay must remain the access-control boundary and never return a Run ID/result without the owner-scoped TaskStore lookup.
- Endpoint validation permits loopback or RFC1918/private answers only and pins the resolved address; plaintext remote HTTP requires an explicit local/test configuration flag. Production TLS, secret rotation and least-privilege Hermes API credentials are untested.
- Hermes tools/agent permissions are outside this adapter POC. Do not infer production tool isolation from the fake provider.

### Persistence and uncertain state

- Known Run ID + Relay process restart was recovered locally. If POST may have succeeded but the Run ID write is absent/ambiguous, Relay intentionally makes the task uncertain and will not re-submit; an orphan Hermes Run may continue without automatic Relay reconciliation.
- If a known-ID status read is rejected, missing, times out, or the Run reports interrupted/cancelled/expired, the adapter reports uncertain rather than completed or definitely failed. There is no operator reconciliation UI in this POC.
- Hermes Run/idempotency retention is shorter than Relay's existing result retention (Phase 1 source review found a 24-hour API idempotency/status horizon). The TaskStore mapping is not proof Hermes can return status for the full Relay retention period.
- DynamoDB behavior is covered only by the in-memory ledger/fakes; no AWS tests were run. The existing 100-TASK-row bound remains. SQLite was validated on a local Docker named volume only; SQLite WAL on Fargate/EFS and replacement/failover safety are untested.

### Deployment and lifecycle

- No Fargate/EFS, IAM, security groups, service discovery, scaling, health checks, key distribution, backup/restore, or task replacement was tested.
- Hermes agent restart can interrupt a live Run (Phase 2A G4); this adapter treats interrupted as uncertain. It does not restart work or claim exactly-once side effects.
- Cancellation endpoint, SSE/progress replay, 30-day status/result retention, concurrency/backpressure, API rate limits, and real-model output quality/cost remain untested.

## Recommendation / next gate

**Recommend a separately approved Phase 3 feasibility gate, not a production cutover.** First verify Hermes API auth/owner scope, documented status retention and restart behavior against the pinned server version; then assess Fargate/EFS or other durable storage and recovery/backup mechanics without changing AWS. Only after that should a bounded, explicitly cost-capped real-model test be requested. The current evidence does not authorize any real-model call, AWS change, SPEC/ADR edit, deployment, PR merge, Dispatcher deletion, or DynamoDB refactor.

Phase 2B stops here. Further work requires Arthur's explicit approval.

## H1.1 offline hardening (2026-10-17)

- Adds HermesRunResult schema_version=1 and a bounded mapping into Amusant's existing BoundedTaskResult; normal text uses a safe fallback, while unsupported/empty structured results stay unconfirmed. API Run status—not fields inside generated output—remains the control source.
- Adds offline cases for malformed/oversized/empty/partial/tool-error output, wrong-owner TaskStore lookups, the shared API bearer versus per-client session namespace, public-DNS rejection, and the local fake-provider network/tool policy.
- The local Compose runners now use fresh H1-v1 named volumes; the old P2A/P2B volumes are preserved. The seeder refuses to overwrite an old persistent Hermes config lacking the H1.1 safety settings.
- H1.1 source edits were exercised by the exact Python 3.13 CI commands locally (Fast Voice 74/74, Relay 220/220, TLS-PSK/Opus WSS loopback 10/10) and by local Docker/fake-provider G1–G4 plus all three Phase 2B adapter gates (all PASS). These runs used the PR #45 worktree based at b27b610 with uncommitted H1.1 changes; sanitized Phase 2B evidence records that boundary. The Hermes process ran only inside the internal-only local test network with a deterministic fake provider. No real model/Agent, AWS, hosting, or device E2E was used. Production tool isolation, persistent HERMES_HOME and cross-owner behavior remain acceptance gates.
- Conditional review follow-up was exercised in a dirty worktree based on PR head 4170c85: the full Relay suite passed 228/228 and the Phase 2B fake-provider gates passed 3/3. Suspicious/truncated JSON now fails closed (normal plain-text fallback remains); a Hermes Run status=expired maps to uncertain and the owner Inbox exposes no speak result. On the Hermes path, queue expiry blocks an initial claim only; active work and persisted Run IDs remain resumable after restart beyond the original queue window. OpenAB's legacy expiry behavior is unchanged. The executor poll timeout is a per-invocation bound, not a persisted cumulative deadline across Relay restarts. The proposed 120s execution timeout, 8 queued, and 600s queue wait remain unapproved proposals; no acceptance threshold was inferred.
