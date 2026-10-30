# ADR-0004 — Owner-managed Hermes, tool-agnostic Amusant

> **Current applicability (2026-10-30): the ownership boundary survives, but Hermes as the selected SLOW backend is SUPERSEDED by the later Owner-approved Grok-first decision** ([#127](https://github.com/smallgun01/amusant/issues/127), [#132](https://github.com/smallgun01/amusant/issues/132)). Hermes/Facade remain historical and explicitly selectable compatibility/rollback implementations, not the running Grok pair or automatic fallback. This ADR's earlier 10/27 *candidate* wording below is dated history; see [SPEC](../../SPEC.md) and [STATUS](../../STATUS.md) for current truth.

- **Status:** Accepted Owner decision, 2026-10-25; source implementation progress reconciled 2026-10-26. This ADR records the decision, not deployment acceptance.
- **Scope:** Product responsibility and integration boundary. Generic delegation was subsequently implemented in merged #120; native Owner access and live operations remain separate unaccepted gates.
- **Related:** [SPEC v0.6](../../SPEC.md), [ADR-0003](0003-oci-a1-hermes-sqlite.md), issues #48/#55/#56/#101/#105; PRs #116/#117/#118.

## Context

Amusant is a personal portable AI companion combining device, real-time voice and an Agent backend. A small PTT device is not an adequate sole interface for Agent configuration, credentials, OAuth, detailed results or sensitive interactions. Building those functions again in Relay/Facade would couple frontend development to every new Hermes capability.

The original report-mandatory delivery failure was addressed by optional-Artifact v2 in #107 and v2-only runtime retirement in #116. PR #117 proposed a different guarantee: infer document intent from transcription and enforce an immutable requirement. The Owner rejects that additional semantic admission layer as outside the product's responsibility.

## Decision

Hermes is an independently usable **Owner-managed personal Agent**. Amusant is the integrated voice companion product, not merely hardware, but it does not own Agent administration. Hermes decides how to execute authorized work and interprets its actual tool results; Amusant reliably submits work and delivers a bounded, authenticated result.

| Responsibility | Owner |
| --- | --- |
| Device/PTT/audio/display/provisioning | Amusant device and voice stack |
| Immediate dialogue and task handoff | Gemini Live and Relay |
| Request identity, bounded transport, Run tracking/cancellation, Inbox | Relay/Facade, only for their channel |
| Reasoning, tool choice, Skills, MCP, long-term memory, Cron | Hermes |
| Models, external credentials, tool/approval policies | User through Hermes-native management |
| Existing Facade report storage/integrity | Facade until a separately approved change |
| Hosting, dependency availability, backup, sandbox and resources | Owner/Operator using reviewed deployment procedures |

Use Hermes-native Telegram as the initial direct conversational access path alongside appropriate CLI/UI/OAuth/secret setup. Do not require all administration through Telegram and do not paste secrets into ordinary bot conversations. The two paths target the same intended Owner backend/profile, not two independently configured Agents.

## Invariants

1. Adding an ordinary Hermes tool, Skill or MCP must not require Relay/Facade source changes. New device/media/interactive protocols are explicit adapter scope, not a per-tool catalog.
2. Tool availability and authorization remain in Hermes. Platform policies may differ; Relay does not mirror or grant them. Generic task text is not consent to new permissions.
3. Amusant updates preserve Owner configuration, credentials, Skills and memory. Repo templates document adapter requirements and safe examples, not a permanent exhaustive tool policy.
4. Retain existing four-service isolation, bounded v2 output, authentication, deduplication and safe recovery. Tool-agnostic does not mean unvalidated transport or unbounded content.
5. Single-flight and cancellation apply to Amusant-admitted Runs. They neither block all native Hermes work nor undo actions already performed. Device no-unsolicited-speech does not forbid native notifications.
6. Native sessions remain native: no global `speak/card` rewrite. Same profile does not prove shared conversation context, automatic Inbox sync or cross-channel approval/resumption.
7. Execution, channel delivery and stored-artifact state are separate. Hermes reports business outcomes from actual tool results; Facade does not become an arbitrary side-effect proof engine.
8. Missing native approval/credentials must be reported honestly. Do not enable all tools, default approvals to allow, or blindly replay an action to manufacture a smooth handoff.
9. Private Facade storage is not native download. Existing artifacts require authenticated retrieval/export; native Hermes content uses native delivery without traversing Relay/Live.

## Rejected alternatives

- Relay transcript/keyword classifier, two-second intent gate, forced PTT restatement, or new `artifact_requirement` ledger metadata from PR #117.
- Amusant Admin UI, mirrored credentials/allowlists, per-tool task enums and domain routers.
- A global TG/Cron/voice queue, new Agent service, automatic cross-channel sessions or an unbounded generic approval engine.
- Immediate Facade consolidation or artifact migration bundled with this scope alignment.

## Consequences and implementation boundary

The v2 runtime remains the source target; generic delegation and SQLite v9 compatibility were implemented in merged #120. Release/rollback compatibility still requires the active OCI schema and deployed bundle to be verified; #124 is an open release-readiness PR. Existing research kinds are not a permission firewall. Do not rewrite old fingerprints or reinterpret historical v1/ACP rows.

Provide a usable native-management/configuration path without disabling container security. Initially a read-only runtime config with an Owner/Operator-controlled backing-file update can remain valid; any writable native config workflow needs its own reviewed minimal change.

Capabilities remain dependent on Hermes configuration, credentials, dependencies and network reachability. Reusing the upstream Agent does not guarantee every tool works in the current sandbox. Measure shared-host load; do not build a second scheduler to hide it.

## Superseded work and authority

PR #117's implementation direction and earlier review GO are superseded by this Owner decision. Do not merge or implement it. Preserve its discussion as history. PR #117 was closed without merge on 2026-10-25.

#105 retains universal bounded results and separate authenticated content retrieval, but not its old trusted-document-intent admission requirement. #101 retains reproducible bootstrap and preserve-on-update, but must not establish Amusant as Owner of an exhaustive Agent tool profile. Follow [PLAN](../../PLAN.md) and the [handoff index](../handoffs/20261025-owner-managed-hermes.md).

The Owner has settled this responsibility decision. Agents need not reopen it before every change; they must identify genuine implementation gaps and preserve explicit live-operation authorization.

## 2026-10-27 candidate replacement evaluation — not an ADR reversal

The Owner is separately evaluating Grok Bot as a **managed replacement** for Hermes's SLOW Agent role, not as a Hermes tool. G3 product/callback evidence is recorded at [#123](https://github.com/smallgun01/amusant/issues/123#issuecomment-6419151666); [G4 #127](https://github.com/smallgun01/amusant/issues/127) presently authorizes design and existing-access read-only planning. The Hermes-selected design in this ADR and SPEC v0.6 remains controlling for active/deployed work until a **separate accepted backend-selection/cutover ADR or equivalent explicit Owner decision**. No automatic two-Agent chain, permanent double stack, OAuth/Ingress rollout, new schema or runtime switch is approved by this note.

Retain the general responsibility boundary (Amusant owns device, FAST, authenticated admission/delivery; selected native Agent product owns tools, credentials, approvals and full artifacts). Future Grok design must separately resolve Callback receipt versus native Run termination, absent verified native Stop, Single-flight/Owner abandon, historical TaskStore data and rollback. G0 physical Hermes baseline still gates G4 live/device acceptance. See [G4 agent handoff](../handoffs/20261027-grok-g4-agents.md).
