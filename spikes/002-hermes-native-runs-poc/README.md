# Hermes Native Runs — local POC

This isolated spike tests the pinned Hermes Native Runs API against a deterministic fake model provider. It does not modify SPEC/ADR or deploy production Relay. The Docker network is internal-only and no ports are published. No AWS, Gemini Live, real model credentials, paid model API, Docker socket, or home-directory mounts are used. The API keys are generated in memory by the runners and are not written to files.

The fake provider returns a fixed local marker and records only bounded request metadata; it does not store prompts. The pinned image reference is the Kai v0.12.0 integration image: Hermes v2026.9.24 at sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7.

## Phase 2A — Hermes API-only G1–G4

All four local API gates passed. The sanitized summary is evidence/phase2a-gates-sanitized.json.

- G1: create a Run, poll it to completion, retrieve the deterministic result.
- G2: same idempotency key and payload returns the same Run ID; changed payload conflicts; no additional fake completion.
- G3: a new disposable client recovers the completed result using the saved Run ID.
- G4: a completed Run survives Hermes restart on the same named volume; an active Run becomes interrupted and is not replayed after Hermes restart.

The runners supply explicit test-only values for the eight Run, polling, HTTP, and stop-timing variables because Compose evaluates the shared Phase 2B service interpolation even for Phase 2A. These values are harness inputs, not approved Hermes product defaults.

Run with Python 3 and Docker Compose v2: python3 run_poc.py

## Phase 2B — Relay adapter POC

Phase 2B imports the actual Relay adapter and TaskStore into a disposable container. It keeps delegate_task, check_task_status, AsyncDispatcher, the existing task ledger and Inbox. **Hermes Native Runs is the Relay product default** when `AMUSANT_SLOW_EXECUTOR` is unset. Startup requires both server-owned `AMUSANT_HERMES_RUNS_URL` and `AMUSANT_HERMES_API_KEY`; if missing, startup fails closed rather than silently falling back to OpenAB. The old OpenAB ACP backend is available only by explicitly setting `AMUSANT_SLOW_EXECUTOR=openab_acp` (with its existing ACP configuration). The backend selector is server-owned, never client/model-controlled. **Changing the code default does not authorize changing any deployed environment, launching Hermes, or cutting over production.** No client/model field selects a URL, key, executor, or session key.

The adapter sends one POST /v1/runs with Idempotency-Key=task_id, persists the returned Run ID write-once on the existing task, and polls GET /v1/runs/{id}. It derives an opaque stable Hermes session key from the authenticated Relay owner identity. A resumed task with a known Run ID issues GET only. Ambiguous submission without a durably confirmed Run ID becomes uncertain and is not replayed.

### H1.1 structured result contract (offline code only)

The Relay now sends the bounded task plus a HermesRunResult schema_version=1 instruction, then maps only speak/card/details_md into the existing BoundedTaskResult limits (240-char speak, title 80, 4 lines of 120, details 4,096). Plain text and known legacy text fields use a bounded fallback; empty, partial, or unsupported output without a safe text field never becomes completed. Unknown result keys such as status, owner, task_id or tool instructions never alter Relay state. The check_task_status tool warns that saved Agent text is untrusted. This does not prove a model will obey the warning or produce correct schema output.

The local fake-provider harness disables high-risk toolsets, denies unattended/cron approvals and uses an internal-only network with no published ports. Static config checks are not production tool-sandbox acceptance.

H1.1 local verification (2026-10-17) re-ran both Phase 2A and Phase 2B Docker/fake-provider gates. Sanitized summaries are in evidence/phase2a-gates-sanitized.json and evidence/phase2b-gates-sanitized.json. The runs used the PR #45 worktree based at b27b610 with uncommitted H1.1 changes; Phase 2B records working_tree_dirty_at_test=true. These are local fake-provider results, not GitHub CI or production acceptance.

The repository CI commands passed locally at the first H1.1 head in the isolated Python 3.13 environment: Fast Voice 74/74, Relay 220/220, and TLS-PSK/Opus WSS loopback 10/10.

### Conditional PASS follow-up (2026-10-17; worktree based on 4170c85)

- Truncated/suspected JSON now fails closed instead of being sent through the plain-text fallback; ordinary plain-text fallback remains. A remote Hermes Run status of expired maps to Relay uncertainty, and the Inbox does not expose it as completed/speakable.
- On the Hermes path, queue expiry blocks an initial claim only. Running work and a persisted Hermes Run ID are not marked expired or blocked by that queue window; Relay resumes GET polling after restart. OpenAB's legacy expiry behavior remains unchanged. The Hermes executor timeout is a separate per-invocation polling deadline, not a persisted cumulative deadline across Relay restarts.
- The full Relay suite passed 228/228; the Phase 2B internal fake-provider gates passed 3/3. The latest sanitized evidence records the tested worktree as dirty and based on 4170c85.
- The proposed 120-second execution timeout, eight queued tasks, and 600-second queue wait remain proposals, not approved product requirements.

- relay_submit_mapping: PASS — one Relay POST; Run ID persisted while fake execution was active.
- relay_process_restart_recovery: PASS — a new Relay process recovered the result from the same SQLite volume; all recorded Relay HTTP calls after restart were GET, with no second POST.
- ambiguous_submit_fail_closed: PASS — Hermes accepted the POST but the test dropped the response; Relay stored no Run ID, left the task uncertain across recovery, and made zero dispatches after restart.

The fake provider recorded multiple provider completions during each Hermes Run (2 in the recovery case, 2 in the ambiguous case). Those are Hermes-to-provider requests, not Relay POST submissions; the Relay HTTP-method trace independently shows one initial POST and GET-only recovery. Do not interpret the provider count as an additional Relay Run submission.

Run Phase 2B locally with: python3 run_phase2b.py

A harness-only initial attempt failed because the new SQLite volume was root-owned; the runner now initializes that isolated volume for UID 10000. That setup failure was not an API/Relay behavior result. The current summary contains only sanitized metrics; attempt files are ignored by Git.

## Hermes product default and activation gate

- **Product direction:** Hermes Native Runs is the selected target Slow Brain. OpenAB ACP is an explicit legacy/rollback executor only. An unset `AMUSANT_SLOW_EXECUTOR` selects `hermes_runs`; `AMUSANT_SLOW_EXECUTOR=openab_acp` selects the legacy path. Missing Hermes URL/API key fails startup; no silent fallback.
- **PR #45 scope:** adapter foundation only; it merged to `main` on 2026-10-17. It does not establish live Agent, hosting, memory, notification, security, or production readiness. Merge did not authorize production changes or cutover.
- **Pre-58A limitation (merged #45 baseline):** `AMUSANT_HERMES_TIMEOUT_SECONDS` conflated a per-invocation polling budget with the overall Run lifetime and could make a known Run uncertain at the cycle bound. Issue #58 split follow-up work into #58A durable polling/deadline and #58B capacity/concurrency. This POC README records only offline evidence; long-running activation remains gated on reviewed code and separate operational acceptance.
- The existing 8 pending / 600s queue / 120s OpenAB-era execution figures are **not approved Hermes product requirements**. Candidate replacement values in #58 are subject to Owner approval.
- Legacy rollback is **explicit configuration**, not automatic fallback. A production cutover and rollback runbook require separate review; #45 merge is not that approval.

## Limits and safety

- Local Docker/fake-provider evidence is not production acceptance or AWS/Fargate evidence.
- No real model, billing, end-user tool effects, AWS, Fargate, or EFS behavior was tested.
- The local Hermes config denies terminal/file/code/browser/computer-use and other high-risk toolsets, requires manual approvals, and denies cron/unattended/single-query approval modes. The Compose network is internal-only with no published ports. Static configuration coverage is not proof of a production tool sandbox or owner-isolation boundary.
- H1.1 uses fresh named volumes amusant-hermes-runs-h1-v1-home/state and leaves the prior p2a-v4/p2b volumes untouched. The config seeder fails closed if an existing persistent Hermes volume lacks this exact safety policy; it never overwrites that volume. Do not run docker compose down -v or delete retained volumes to bypass the check.
- Hermes Run status/idempotency retention is not a substitute for Relay's existing Inbox retention; the 30-day Relay result policy remains Relay-owned.
- No SSE replay, cancellation API, 30-day retention, multi-owner live access-control test, EFS/WAL safety, or Hermes agent restart reattachment for in-flight work is established here.
- Hermes source opens its Run idempotency database under HERMES_HOME but falls back to in-memory storage if SQLite cannot open. The local G4 restart/readback is evidence for the named POC volume only; a successful POST/202 is not proof the deployed store is durable. Production acceptance must prove Run status/readback across restart on the selected persistent storage.
- An ambiguous POST with no stored Run ID can leave an orphan Run on Hermes. Relay deliberately reports uncertain rather than risk duplicate side effects.
- The DynamoDB 100-TASK-row scan bound and Dispatcher remain unchanged.

Named Hermes and Relay state volumes are retained. The runners stop containers but never remove volumes. Do not run docker compose down -v for this POC.


## 58A implementation contract (under independent review)

- Queue TTL, per-request HTTP timeout, per-cycle poll budget, poll interval, and overall Run deadline are distinct controls. Hermes requires explicit timing configuration; no Issue #58 candidate value is installed as a product default.
- The Run ID, first-dispatch anchor, absolute deadline, next poll time, and attempt count are persisted on the existing Relay task. A deferred cycle releases the local claim; claim_due filters by next_poll_at and the background loop schedules the next wake from persisted poll/deadline state. Known IDs resume GET-only after a yield or repeated Relay restart, and do not consume the first-submission max_attempts budget.
- Ambiguous POST without a durably stored Run ID remains fail-closed and is never replayed. At the Run deadline, Inbox exposes the Relay policy status `timed_out` while disclosing possible remote activity; no cancellation is claimed unless confirmed.
- SQLite and DynamoDB use their existing task stores. No #58B capacity/concurrency behavior is included.
- Local unit/CI/fake-provider evidence is not real-Agent, paid-model, AWS, N100, or production acceptance.
