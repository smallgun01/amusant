# Grok G4 — Codex / OpenClaw work orders (2026-10-27)

**Objective:** assess Grok Bot as a **replacement candidate for Hermes** as Amusant's SLOW backend. **Never treat Grok as a tool underneath Hermes.** Selected Hermes target, SPEC v0.6 and ADR-0004 remain active until a distinct Owner cutover decision. Track [G4 #127](https://github.com/smallgun01/amusant/issues/127). Source baseline at handoff: main@e776495; each agent verifies latest main. This is a read-only design authorization, not runtime/live/paid-work approval.

## Shared evidence and constraints

- [#123 G3 closeout](https://github.com/smallgun01/amusant/issues/123#issuecomment-6419151666): G1 native product 4/4 accepted; G2 isolated offline spike accepted; G3 native fixed synthetic + Q1 and Q2 factual dynamic searches + D1 full Markdown document and same-App revision, with matching durable N100 Callback receipts. Four real Webhook POSTs reported. G3-R and G3-A live failure/approval scenarios, native terminal run status, and long-term reliability are NOT TESTED.
- [#46](https://github.com/smallgun01/amusant/issues/46) Hermes real-device G0 Slow→Inbox→spoken is not yet formally PASS. #116/#118/#120 merged in source; neither source merge nor CI proves deployed OCI bundle. [#124](https://github.com/smallgun01/amusant/pull/124) SQLite release readiness is open.
- Invariants: StickS3 PTT and FAST Gemini Live unchanged; generic `delegate_task(task)`; server-selected single backend per new task, one Owner and Amusant single-flight; keep Relay TaskStore/Inbox and bounded v2-compatible speech/card; no unsolicited device speech. Grok full native report/file stays native; `box:` local path is NOT an Amusant authenticated download or Facade artifact.
- Separate webhook acceptance, authenticated durable Callback receipt, Agent reported outcome, independently observed native Run state and device delivery. Default native `unknown` is correct. Never fake Hermes Run ID/Stop or silently auto-retry/fallback to Hermes.
- Tokens remain task-scoped, hashed at rest, owner-bound with expiration and revocation. No secrets, raw tasks, private hostnames, local file paths, cookies or full reports in GitHub or device voice results. No extra Agent framework, business router, production PilotStore duplicate or unwarranted fifth service.

## Codex Desktop — G4-D0 (READ-ONLY GO NOW)

1. Sync/check latest main without disturbing worktrees. Read SPEC, ADR-0003/0004, [v0.2](../project-management/grok-bot-validation-v0.2.md)/[v0.3](../project-management/grok-bot-validation-v0.3.md), #46/#62/#123/#124, `relay/{application,slow_path,task_store,dispatcher,hermes_runs_executor,hermes_result}.py`, Facade/OCI Compose and `spikes/005-grok-bot-validation/`.
2. Map source-anchored flow `delegate_task → TaskStore admission/outbox → executor → bounded result → Inbox → explicit later PTT`. Identify current Hermes-only executor kinds, v2 contract check, Run polling/deferred, Cancel/Stop, migration/rollback and receipt race constraints.
3. Compare **at most two minimal designs** for Grok Webhook sending and callback receipt projection. Keep **Relay TaskStore/Inbox the sole Amusant source of truth**; name exact adapter/ingress component, internal auth, egress, ports and file changes. No public Relay WSS management or private Facade exposure.
4. Compare the original v0.2 Remote HTTPS MCP/OAuth primary against proven Callback-first transport. Propose **one** final return path subject to Owner choice; do not silently declare MCP obsolete or build both.
5. Define precise durable task states for prepared/sending/accepted/acceptance_unknown/rejected, waiting/received/conflict/late and `native_state=unknown`. Propose whether final authenticated Callback may complete *Amusant's delivery channel* while native Run is unverified, and how Single-flight, deadline, explicit Owner abandon/reconcile and fake/unsupported remote Cancel behave.
6. Offline fake regression matrix: Callback before submit response, POST timeout/sender crash no second trigger, bad Owner/token/expired/revoked, duplicate-same vs conflict, late after closure, storage-commit/ACK loss, restart/recovery, result bounds, forged locator, no unsolicited speech, v9 historical records/schema rollback.
7. Post a concise **GO/BLOCKED G4-D1 offline code recommendation** on #127, with exact file diff and necessary CI. **Do not write code/open runtime PR yet**. Draft PR only after separate Owner implementation GO.

## OpenClaw — G4-O0 (existing-access READ-ONLY GO NOW)

1. Inventory G0/#46/#62 historical proof, active OCI release/digests/TaskStore schema **only with existing read permissions**. Permission denial = BLOCKED, not permission to use sudo/Docker, dump secrets, restart or run a paid Provider.
2. Produce exact **managed HTTPS return-ingress plan**: existing N100 pilot versus stable OCI/Cloudflare route, fixed hostname, loopback/UID/modes, proxy Host and forwarded-header policy, rate/time limits, task capability protection, SQLite transactional receipt, log redaction, backup, reboot and revocation/rollback. Specify proposed changed resources; **do not start tunnel/service or alter DNS/network**.
3. Identify live pilot artifacts/unfinished/uncertain tasks as sanitized counts only; no blind resubmission. Report what Owner must check in Grok App (native approval/Run status, costs and document access). Original pilot envelope: eight POST maximum, four reported used, two reserved for G4 physical Q/D.
4. Post **READY FOR OWNER REVIEW/BLOCKED** matrix to #127: existing permission, proposed change, security justification, verification and rollback. No OCI writes, root access, cron, credential rotation, real Grok tasks or host modifications.

## Owner decisions / downstream gates

- **D0/O0 now:** review Codex and OpenClaw reports. Select completion/unknown/Cancel semantics and exactly one return transport.
- **D1 later:** separate code GO for small offline-only adapter PR; appropriate focused CI with mocked Native Routine/Callback, existing SQLite and Inbox tests; no broad Draft ARM64 waste.
- **D2/D3 later:** only after G0 physical Hermes baseline PASS, approved architecture, reviewed ingress/permissions/schema/backup, explicit cloud/provider usage GO and reversible deployment; two bounded real StickS3 query/document runs.
- **G5 later:** compare verified device UX, maintenance, actual fees/quotas, error handling and native App work continuation. Do not remove Hermes, rewrite historical tasks or pretend a local abort is native Stop.

Return format: `GO/BLOCKED | source/runtime evidence | minimal architecture | task lifecycle | risk/auth | exact files/host changes | offline tests | migration/rollback | unresolved approvals`. These written work orders do not automatically invoke either external agent.
