# Project Amusant — Voice Companion Specification v0.6

**Decision:** Owner-approved architecture and scope baseline, 2026-10-25. Documentation alignment is not runtime implementation or deployment approval.

**Authority:** this SPEC defines requirements; [ADR-0004](docs/adr/0004-owner-managed-hermes.md) records Agent ownership; [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md) retains hosting/security decisions; [PLAN](PLAN.md) sequences work; [STATUS](STATUS.md) records evidence. Superseded specifications and embedded historical appendices remain available through the [immutable history index](docs/history/20261025-documentation-baseline.md).

## A. M1 product contract and non-goals

Amusant is a one-owner, one-active-Relay personal AI companion: **device + voice channel + Agent integration**. Its core experience is press PTT, speak, release and hear FAST conversation; or delegate a task, receive a truthful admission acknowledgement, and later explicitly request its short result.

Hermes is an independently usable, **Owner-managed personal Agent backend**. Amusant integrates it but does not recreate its administration or domain workflows. Hermes owns SLOW reasoning, tool selection/execution, Skills, MCP, long-term memory, model configuration, external credentials, approvals, Cron and content creation. Owner policy is enforced on the Hermes side; natural-language task text cannot grant new permissions.

A second, usable Hermes-native access path is part of the integration requirement. Telegram is the initial direct conversational channel, with native CLI/UI/OAuth/secret setup used where appropriate. Not every administration action must be available in bot chat. Routine PTT use must not require opening that interface.

Non-goals: a second Agent framework; Amusant-managed MCP/Skills/credential console; per-tool Relay adapters; a document-intent classifier; multi-owner tenancy; a global Hermes queue/lock/scheduler; automatic cross-channel session merging; broad Kai parity; unsolicited device speech; a second active SLOW backend. OpenAB ACP is an explicit legacy alternative, never an automatic retry.

## B. Selected implementation target — not proof of a deployed service

```text
Owner -- StickS3 <--> Relay <--> Gemini Live (FAST)
                       |-- SQLite Task Ledger / Inbox
                       +-- private authenticated Facade <--> Hermes Runs / Gateway
                                  |                         |-- Hermes Home
                                  +<-- trusted writer ------+-- Amusant plugin
                                  +-- private artifacts
Owner -- Hermes-native Telegram / CLI / UI -----------------^
Relay / Hermes -- controlled Egress Proxy --> public HTTPS
```

Keep four services on the selected OCI A1 host: Relay, Facade, Hermes and Egress Proxy. Do not infer current CPU/RAM, deployed digests or production capacity from an old candidate size. Check actual host evidence separately.

Relay cannot reach raw Hermes Runs directly or mount Facade artifacts. Facade authenticates channel requests, binds owner/task/session/Run, and mints artifact references only after durable commit. Hermes-native traffic does not pass through the Amusant result projection. Maintain non-root execution, separate credentials/networks, persistent state and controlled egress; no public Hermes/Facade API, Docker socket, host-admin escalation or unrestricted private-network access.

## C. FAST / device contract

- Device responsibilities: buttons/PTT, microphone, speaker, display, connectivity and device provisioning. Agent administration and external-service secrets do not belong on the device.
- Gemini Live owns immediate conversation and FAST/SLOW delegation. Expose bounded generic delegation, owner-scoped status and cancellation; no caller-selected backend URL, credentials or admin arguments.
- Preserve authenticated transport, per-turn/epoch ownership, barge-in safety and stale/replay rejection. The audible first-sound target remains p95 <= 2.0 seconds where measured; Relay first-wire measurements are not an audible-onset acceptance.
- BLE/Wi-Fi, CJK display, battery and G1 ingress retain their independent acceptance gates. No implicit firmware or public-ingress change follows from this scope decision.

## D. SLOW admission, persistence and recovery

The target public interface is generic `delegate_task(task=...)`. Existing `research/compare/summarize` hints are legacy metadata, not permission grants or a semantic firewall. Removing their product restriction requires a separately tested code/migration change; this document does not claim it is implemented. Do not replace them with an expanding cron/calendar/booking enum.

At most one outstanding **Amusant-admitted Run per authenticated Owner**. A second admission receives `slow_busy`, not a user-facing FIFO. Native Telegram/Cron work is outside this Relay slot and remains governed by Hermes. Shared-host resource controls do not make Relay a global Agent scheduler.

Durably record admission and deduplication before acknowledging acceptance. Keep one in-process Dispatcher and the existing TaskStore/Inbox. Store bounded task requests, trusted identifiers, Run ID, deadlines, status, short presentation and verified opaque references. Do not copy raw audio, full transcripts/reports, credentials, scratchpads or tool logs into Relay.

A known Run resumes by GET without a new Run POST. Ambiguous submission remains uncertain; never blindly replay a side-effecting task. GET-only recovery must not suppress the first authorized deadline/Owner STOP. STOP acknowledgement alone does not release the slot; trusted terminal evidence is required. Cancellation targets the bound Run, not the Gateway, unrelated native work, an existing Cron schedule, or reversal of external side effects.

Keep explicit reviewed timing bounds. Retention target remains 30 days for eligible terminal Ledger/Inbox records; protect active/uncertain/reconciliation records. `expires_at` is admission expiry, not history deletion. Back up and restore related Relay, Hermes and Facade state coherently; preserve historical v1/ACP rows and fingerprints without reinterpretation.

## E. Result / artifact / spoken contract

New Hermes Runs use **Result Contract v2 only**. `/v1/runs` and `/internal/v1/...` are API namespaces, not result-schema versions. See the [v2 cutover contract](docs/result-contract-v2-cutover.md).

Hermes prepares bounded `speak` and `card` suitable for the actual task: findings for research, factual completion/refusal/failure for actions. Hermes interprets tool receipts. Facade does not validate the business semantics of every external service; valid JSON and native Run completion are not independent proof of a booking, payment or calendar change.

Keep `speak <= 240 characters`, `card.title <= 80`, one to four lines of <= 120 characters, and the authenticated v2 envelope <= 3,000 UTF-8 bytes. Never clip or forward raw/long output as a fallback. No LLM summarizer is added to Relay or Facade.

Artifacts are optional. Hermes decides whether to call `save_report`; no transcript classifier, two-second intent gate, `artifact_requirement` admission field or additional PTT restatement policy is introduced. For existing Facade storage, verify committed bytes, length, digest, reference and owner/task/session/Run binding. The 64 KiB text-report cap is this adapter's limit, not a limit on all native Hermes output. A model claim or lost tool acknowledgement cannot replace the trusted storage record.

Preserve distinct facts: native execution outcome, presentation/delivery outcome, and verified artifact state. A confirmed completed Run with invalid presentation yields bounded delivery failure, not a fabricated native execution failure. Retain recoverable verified artifact metadata where available; never propagate an unverified reference. The current v2 writer-failure-to-delivery-failure behavior remains until a separately reviewed partial-success change; do not silently relax it here.

`check_task_status` exposes bounded results, not complete reports, storage locations or logs. Spoken retrieval uses a separate tool-disabled Live session. Mark `spoken` only after complete server-observed transmission, not as proof the user heard it. No unsolicited Amusant speech; this does not prohibit Owner-authorized Hermes-native notifications.

Private Facade storage is not Owner readback/download. Native Hermes attachments and Facade-owned reports are different paths. Full Facade retrieval requires a separately tested owner-bound export; no public file serving, shared result mount in Relay, or regeneration masquerading as retrieval.

## F. Acceptance, safety, change control

| Gate | Required evidence |
| --- | --- |
| E1 offline integration | Bounded generic task/result flow, identity/replay negatives, cancellation/recovery, migration compatibility and no raw-content leakage |
| E2 device | Real PTT/FAST, provisioning, display and audible/physical evidence rather than firmware CI alone |
| E3 voice loop | Approved deployed bundle; genuine delegation -> Hermes -> Inbox -> new-PTT spoken retrieval, including failure/restart cases |
| Native Owner access | Owner-only direct Hermes access; correct profile/platform policies; secure configuration updates; native replies not forced into Amusant JSON |
| Tool-agnostic integration | Owner adds a test Skill/MCP in Hermes, with no Relay/Facade source change; unauthorized actions still fail honestly |
| E4 operations | Persistent state, encrypted off-host restore, bounded resources, shared-load behavior, secret hygiene, actual STOP/cost evidence |

Reconcile dated existing evidence before requesting repeat live tests. Source merged, deployed, offline-tested and physically accepted are different states. Documentation, green CI, forwarded implementation instructions and a PR merge do not by themselves authorize OCI mutation, secret rotation, paid calls, firmware flashing, public ingress or new external side effects.

## G. Owner management and channel independence

Hermes configuration is the source of truth for models, Skills, MCP, memory, credentials and per-platform tool/approval policies. API and Telegram policies may differ; neither is copied into Relay. Amusant updates must not overwrite Owner settings or learning state. The repo may document adapter requirements and safe initial examples, not impose a permanent product-specific tool catalog.

Relay retains its own device/FAST/transport credentials; Facade retains its integration credentials. External-service secrets remain on the Hermes side. Native access does not imply that the model can self-authorize or acquire host privileges.

Same backend/profile does not imply identical conversations, automatic Task Inbox visibility, or cross-channel approval. A native Telegram 'yes' is not authorization for an unrelated API Run. Until an owner-bound continuation is implemented and tested, report the need for native interaction truthfully and do not promise automatic resumption or blindly resubmit the action.

Native Hermes should remain independently usable when the Amusant voice channel is unavailable, provided shared host/provider/network dependencies are healthy. Do not claim independent availability from a shared-host deployment. New media/hardware/interactive protocols require explicit integration work, not a growing per-tool frontend framework.
