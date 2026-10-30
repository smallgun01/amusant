# ADR-0003 — OCI A1 / Hermes / SQLite and the Amusant result boundary

> **Current applicability (2026-10-30): HISTORICAL Hermes-profile decision, retained for explicit rollback and design provenance, not the installed Grok-first topology.** Owner selected Grok via [#127](https://github.com/smallgun01/amusant/issues/127) and staged two services in [#132](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695). For the latest runtime and independent Physical/Cloud acceptance, use [STATUS](../../STATUS.md). The original four-service contract and dated evidence below are preserved, not silently rewritten.

**Status:** Hosting and isolation decision accepted by Owner on 2026-10-18; documentation reconciled on 2026-10-25. [ADR-0004](0004-owner-managed-hermes.md) clarifies Agent ownership and channel scope without replacing this deployment. Architecture acceptance is not production acceptance or permission to apply changes.

[Original decision and dated updates](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/docs/adr/0003-oci-a1-hermes-sqlite.md) remain immutable history. [ADR-0001](0001-openab-acp-slow-path.md) and [ADR-0002](0002-external-task-store.md) preserve AWS/OpenAB/DynamoDB provenance, not a second active target.

## Decision and trust boundaries

Use one OCI Ampere A1 host with four isolated services: Relay, Facade, Hermes and Egress Proxy. The original 1 OCPU / 6 GiB candidate was a hypothesis, not a mandated or measured production size. Later Owner-reported host observations exist; verify actual shape/resource caps and contention instead of inferring capacity from `nproc` or this ADR.

```text
StickS3 <--> Relay / Gemini FAST
              |-- persistent SQLite Ledger / Inbox
              +-- private Facade <--> private Hermes Runs / Gateway
                     |                    |-- persistent Hermes Home
                     +<-- trusted writer--+-- Amusant channel plugin
                     +-- private artifacts
Owner -- Hermes-native Telegram / CLI / UI --^
Relay / Hermes -> controlled Egress Proxy -> public HTTPS
```

The native path is required by ADR-0004 and has separate acceptance evidence. It does not grant Relay direct raw Runs access or publish Hermes/Facade API ports.

- Relay owns device authentication, PTT, FAST transport, admission, Dispatcher, bounded Inbox and recovery for **Amusant-originated Runs**. One outstanding such Run per Owner; no global Hermes lock or queue.
- Facade mediates authenticated Amusant submissions and bounded results. It derives owner/task/session/Run identity, commits optional text reports through its separate writer and alone mints trusted artifact refs. It does not manage Hermes tools or judge every tool's business success.
- Hermes is independently Owner-managed. Its native tools, Skills, MCP, credentials, approvals, memory and Cron are not configured through Relay. Native replies do not acquire the Amusant output schema.
- Keep Relay<->Facade and Facade<->Hermes network separation; only Facade mounts artifact state. Relay has no direct raw Hermes route, no artifact write privileges and no external Agent credentials.
- Preserve non-root containers, distinct file secrets and controlled egress. The proxy permits approved public HTTPS connectivity under its network policy, not per-tool business authorization. No Docker socket, privileged container, metadata/private-target bypass or new public API.

Container isolation is not protection from a hostile host administrator. Both channels share host/provider/network failure domains; independent native usability is not a high-availability promise.

## Backend, persistence and lifecycle

`hermes_runs` remains the selected default; `openab_acp` remains an explicit legacy alternative, never automatic failover. Main after #116 uses v2-only new Hermes results. API `/v1/runs` naming does not mean Result Contract v1.

Use existing SQLite TaskStore/Inbox with WAL and `synchronous=FULL` on persistent storage. Preserve related Hermes Home and Facade data in coherent backup/restore procedures. Do not add a database or Worker. A block volume is not an off-host backup.

Known Runs recover through authenticated GET without resubmission; ambiguous POST stays uncertain. GET recovery must retain first deadline/Owner STOP intent. STOP acknowledgement is not terminal confirmation, billing settlement or reversal of side effects. Relay cannot cancel unrelated native TG/Cron work.

Store minimal request/receipt/status/deadline metadata, bounded `speak/card` and verified opaque references in Relay. Never transfer full reports, raw logs, transcripts or secrets through Relay/Gemini. Artifact and presentation failures remain distinct from native execution outcome. Preserve verified recoverable references when presentation fails; invalid refs remain rejected.

Terminal Inbox retention target is 30 days under reviewed predicates, not a blind TTL. Protect active/uncertain/reconciliation state. Preserve historical v1/ACP records without reinterpretation. No automatic AWS cleanup or DynamoDB-to-SQLite migration.

## Deployment and acceptance

[#87/#90](https://github.com/smallgun01/amusant/pull/90) establish integrated offline E1 evidence. Dated Owner GO 2 backend and GO 3 tunneled FAST evidence is retained in [the live record](../oci-go2-go3-live-evidence-20261022.md); current deployed digests/config and complete physical Slow spoken acceptance are not inferred from it. See [STATUS](../../STATUS.md).

Keep separate gates for permanent G1 ingress, real model/device traffic, native Owner access, shared-load FAST responsiveness, encrypted off-host restore, actual STOP behavior/cost and sustained E4 operation. Test ceilings are not measured capacity, and documentation does not authorize tests that call real providers.

Rollback uses the [coordinated v2-compatible bundle/state procedure](../result-contract-v2-cutover.md). Reconcile unresolved Runs before changing readers; preserve Ledger/Facade/Hermes state and Owner configuration. Never silently restore a reader that misinterprets new state, discard unresolved work, or claim old one-off AWS evidence is an instant production failover.
