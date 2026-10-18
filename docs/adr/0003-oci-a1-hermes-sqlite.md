# ADR-0003 — OCI A1 co-hosting, Hermes Native Runs and SQLite Task Ledger

- **Status:** **Accepted target architecture for implementation** (Owner direction, 2026-10-18). **Not a live deployment, production cutover, cost approval or evidence of OCI acceptance.**
- **Decision scope:** single-owner, single-active-Relay Amusant M1; Oracle Cloud Ampere A1 candidate (existing 1 OCPU / 6 GiB VM). Actual free-tier eligibility/capacity and performance remain live gates.
- **Implementation tracking:** [#62 OCI assessment](https://github.com/smallgun01/amusant/issues/62), [PR #70 OCI offline package](https://github.com/smallgun01/amusant/pull/70); WS2 [#56](https://github.com/smallgun01/amusant/issues/56) / [#73](https://github.com/smallgun01/amusant/issues/73); device ingress [#27](https://github.com/smallgun01/amusant/issues/27).
- **Supersession:** For the **new OCI target only**, this ADR supersedes the AWS ECS placement and OpenAB-as-default assumptions of [ADR-0001](0001-openab-acp-slow-path.md) and the ECS-motivated DynamoDB selection of [ADR-0002](0002-external-task-store.md). Those ADRs and the AWS one-off evidence remain valid historical records; no existing AWS resources or data are deleted.

## Context and reasons

Amusant is a one-to-one, PTT-first voice companion. Gemini Live serves short FAST conversations; a separate Hermes Agent handles asynchronously delegated SLOW work. Relay must remain a thin control/data plane. AWS Fargate needed an external datastore because its container filesystem is not a durable Ledger. OCI A1 can host the two logical components on one VM with durable block storage. Sending each Ledger read/write from OCI to DynamoDB would reintroduce cross-cloud latency, AWS credentials and an additional failure dependency for this low-volume application.

This decision **does not** attribute a 100-task capacity limit to DynamoDB: `QUERY_ITEM_BOUND = 100` was an application query-design problem ([#30](https://github.com/smallgun01/amusant/issues/30)), independently fixable; [PR #65](https://github.com/smallgun01/amusant/pull/65) includes >100-history admission regression evidence. Database choice is based on deployment simplicity and the operational trade-off, not that limit.

## Decision: two logical services on one VM

```text
StickS3 -- [#27 approved secure device transport, TBD] --> OCI A1
                                                          |
                                             Relay (FAST + Dispatcher)
                                                |         |
                                    Gemini Live |         +-- SQLite Task Ledger + Inbox
                                                |
                                   private authenticated Hermes Runs API
                                                |
                                            Hermes Agent
                                       persistent Hermes Home
                                       (memory, sessions, artifacts)
```

- Two separately configured runtime containers: **Relay** and **Hermes**. An egress proxy or network sidecar may be included for isolation; it is not a third work-queue/Agent Worker or a separate orchestration service.
- Relay owns device/owner authentication, Gemini Live, `delegate_task`/`check_task_status`, admission, atomic single-flight slot, Dispatcher, recovery and bounded Inbox. **Target boundary:** Hermes owns reasoning, tooling, full-report summarization, personal agent state and full artifacts; Gemini Live should receive only a small authorized result, not a full report. **Current gap:** the existing `check_task_status` Tool still returns the legacy bounded `details_md` alongside `speak`/`card` into Live tool responses. [#73](https://github.com/smallgun01/amusant/issues/73) must implement and test the enforced small-result/artifact boundary before live activation.
- **Hermes Native Runs** is the code-selected Slow executor (PRs #45/#60/#65 merged); `openab_acp` remains an **explicit legacy fallback**, not an automatic fallback or an authorized second production agent.
- The Relay↔Hermes Runs route is private, authenticated and unavailable from the public network. No public Hermes Gateway/admin/Runs port, host Docker socket, privileged containers, or unreviewed tool-to-host access. Container isolation is not equivalent to two separate VMs. Restrict Agent egress, especially instance metadata and internal addresses, and separate credentials/volumes.
- **Device↔Relay ingress is not yet selected or proved**: StickS3's current TLS-PSK cannot be assumed to traverse normal Cloudflare HTTPS/WSS. Native compatible TCP ingress and an explicitly revised cert-WSS client remain alternatives under #27. Nothing here authorizes an open inbound port or firmware change.
- OCI size (1 OCPU / 6 GiB) is a candidate **not a capacity acceptance**. FAST first-audio p50/p95 under concurrent Hermes work, memory and CPU throttling, restart/cancel/health and cost must be measured.

## Decision: SQLite persistence and data boundary

- Set the OCI Relay `AMUSANT_TASK_STORE=sqlite`, with `AMUSANT_TASK_DB` pointing to a database on a **persistent OCI block-volume filesystem**, not a writable container layer or ephemeral tmpfs. The existing `SQLiteTaskStore` supports WAL, `synchronous=FULL`, atomic admission/outbox and restart recovery; reuse the current TaskStore interface and domain model. **No new task DB or standalone Worker.**
- Retain **exactly one outstanding SLOW task per owner** for v1, with immediate busy rejection, idempotent deduplication, explicit cancel (`cancel_task` is declared/handled in Relay; remote stop and physical/live UX remain gated for verification) and conservative ambiguous-submit handling. Do not re-POST a Run with an existing persisted Run ID: restart uses GET-only recovery. A durable task record does not guarantee exactly-once model execution.
- Relay currently stores minimal task receipts, durable backend Run IDs, deadlines, delivery states, bounded `speak`/`card` **and the legacy `details_md` field (up to 4,096 characters), still returned by `check_task_status` to the Live tool response until #73 changes or strips it**. The intended design may add an authenticated opaque artifact reference after separate approval. Do not expand Relay storage into raw audio, full reports, transcripts, secrets, agent scratchpad or tool logs. **Hermes Home and full artifacts remain a separate persistence/backup domain**; Hermes Run history is not an Inbox-retention guarantee.
- Target retention for terminal Task Ledger / Inbox data is **30 days**, subject to explicit reviewed cleanup rules: only confirmed `completed`/`failed` records older than the cutoff; protect `queued`, `running`, `uncertain`, stop/reconciliation state and any recent Inbox activity. **SQLite's existing `expires_at` controls admission/queue expiry, not 30-day terminal-history retention**; a separate WS4 cleanup predicate or schema field is required. Never silently purge ambiguous Runs. Archival/deletion policy and any automated enforcement must pass a separate test/review.
- Require **consistent SQLite online backups** (not an uncoordinated copy of a live WAL file), off-host encrypted transfer, restricted credentials, integrity/foreign-key verification and a **restore drill** of Ledger + Hermes Home before unattended 24/7 operation. A block volume is persistence, **not an independent backup**. Host or volume loss may still affect both FAST and SLOW.
- **No automatic DynamoDB→SQLite migration**. First OCI canary starts with a new empty Ledger only after proving no active/uncertain outstanding AWS-owned task must be retained there. Preserve AWS Task IDs, dedup semantics and task results if an audited migration is separately approved. Do not delete existing DynamoDB/IAM/ECS assets without explicit Owner signoff.

## Deployment and rollback gates

1. **Offline packaging / E1:** review [PR #70](https://github.com/smallgun01/amusant/pull/70), pin/test actual ARM64 Hermes image digest and native dependencies; validate Python 3.13 + Opus, non-root and image entrypoints, private networks/Secrets, SQLite backup/restore/retention and fake-provider restart/stop/busy/inbox tests. The current PR #70 is **not** proof of OCI runtime.
2. **OCI canary / E3:** obtain a **separate Owner-approved** bounded change/spend window before touching VM/network/tunnels; verify host quota/disk and ingress, credentials/egress, backup destination, actual data persistence and rollback; no production default flip. Test an authenticated fake-provider end-to-end path.
3. **Real integrations / E3:** separately approve Gemini and Hermes LLM/model/tool budget; test device PTT, Fast/Slow parallel load, audio p50/p95, wake/reconnect, Inbox spoken retrieval, restart and uncertain/cancel recovery.
4. **Unattended / E4:** operational monitoring, log scrubbing, backup frequency/retention, off-host recovery and host-loss behavior all pass. OCI Always Free has availability/reclaim limitations; zero cost is not guaranteed without account verification.
5. **Rollback:** suspend new admissions, reconcile/finish known Runs, preserve backups and owner/run/deadline/Inbox identity, then switch to an **independently verified** deployment. Existing AWS proof was a bounded one-off RunTask, **not** an instantly available production failover. DynamoDB support stays in code for explicit legacy/AWS recovery.

## What remains unchanged

PTT voice UX and Fast/Slow separation; one-to-one identity; backend-neutral Gemini tool contract; thin Relay; single-flight/GET-only recovery; no surprising unsolicited spoken completion; WS1–WS4 acceptance gates. The historical AWS Fargate + DynamoDB / OpenAB T4b functional evidence must never be relabeled as OCI/Hermes acceptance.

**Approval boundary:** This ADR authorizes offline implementation and documentation alignment only. It does **not** authorize merging this PR, paid provider traffic, OCI/AWS/Cloudflare modification, public ingress, firmware flashing or deleting/migrating live state.
