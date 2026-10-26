# Amusant WS1-WS4 — Owner-managed Agent integration

Reconciled 2026-10-26 to [SPEC](../../SPEC.md), [ADR-0004](../adr/0004-owner-managed-hermes.md) and retained [OCI architecture](../adr/0003-oci-a1-hermes-sqlite.md). [STATUS](../../STATUS.md) owns evidence. This index assigns work; it does not grant deployment authority.

## Workstreams

| Lane / tracker | Responsibility | Next scoped work |
| --- | --- | --- |
| WS1 / #47 | StickS3, provisioning, FAST speech/display and physical UX | Preserve working FAST/BLE/QR; separate W2a, CJK, latency/battery and ingress acceptance |
| WS2 / #48 | Generic Hermes task/result integration, not Agent administration | #118 and #120 merged; real Owner-policy, v2 delivery and native tool acceptance remain |
| WS3 / #49 | Deployment compatibility and voice/native access integration | Active OCI bundle/schema, #124 readiness, #46/#62 physical loop; separate Grok #123 G3 pilot |
| WS4 / #50 | Persistence, secrets, resources, backup/restore, CI discipline | #118 offline recovery merged; #124 v8→v9 readiness open; live recovery, restore/load and security pending |

## Coordination

Codex owns repository runtime edits/tests. OpenClaw owns permitted host inspection, change planning and separately authorized operations. Owner supplies credentials through secure native mechanisms, approves merges/live changes and performs device interaction. Review checks scope and evidence; it does not substitute for those approvals.

Use [the handoff index](../handoffs/20261025-owner-managed-hermes.md). C1 (#118) and C2 (#120) have merged; the earlier sequence is historical. O1/O2 still require active runtime evidence and separate Owner authority. OpenClaw reports a repo defect to Codex rather than hot-patching the live container.

## Required sequence and limits

1. Preserve merged #118/#120 as source milestones; #117 closed without merge.
2. Review #124 v8→v9 release readiness and verify the active OCI bundle/schema before any authorized upgrade.
3. Reconcile existing OCI/device evidence and prepare native Hermes access under O1 read-only limits.
4. With separate Owner authority, verify deployed v2 Slow→Inbox→new-PTT spoken and independent native access.
5. Track Grok independently: G1 Owner-reported and G2 offline accepted, #125 G3-P1 merged; #123 G3-P2 setup/live incomplete. G4 still requires Hermes G0.

Do not wait for a catalog of Calendar/Cron/booking adapters. Those capabilities belong to Hermes. Native scheduling/notifications are not new Relay features. Native management is required integration work, not a mandate to implement every upstream capability before the current voice loop can be tested.

Keep one major build per affected lane plus scoped reliability remediation. Documentation-only PRs use lightweight checks. Draft heavy-job skips save minutes but are not runtime evidence; corrected Ready runtime PRs require applicable full jobs.

## Channel boundaries

- One outstanding Amusant-admitted Run per Owner, not one job across all Hermes channels.
- No unsolicited Amusant speech, not a ban on Owner-configured native Telegram delivery.
- Shared Agent/profile is not shared session/approval/Inbox; do not promise automatic continuation.
- No raw reports/credentials to Relay or Live, no direct Relay-to-Hermes route, no public Hermes/Facade API, no new Worker/queue/Admin service.

## History

[#87/#90](https://github.com/smallgun01/amusant/pull/90) remain offline integration evidence; [GO 2/3](../oci-go2-go3-live-evidence-20261022.md) remains dated live functional evidence. Legacy WS and AWS snapshots are available through the [immutable history index](../history/20261025-documentation-baseline.md), not competing current requirements.

## 2026-10-27 G4 alternative backend coordination

[Issue #127](https://github.com/smallgun01/amusant/issues/127) is the active G4 **Grok-as-Hermes-replacement** design tracker. WS2/Codex owns a read-only source/lifecycle design and (after independent code GO) a narrow offline adapter. WS3/OpenClaw owns authorized read-only G0 runtime evidence, ingress and operational plan; WS1/Owner owns the eventual physical PTT acceptance; WS4 reviews secret/auth/duplicate/late/backup/rollback semantics. This does **not** turn Grok into a Hermes Skill or require per-tool Relay adapters.

G3 [#123](https://github.com/smallgun01/amusant/issues/123#issuecomment-6419151666) Q1/Q2/D1 and fixed synthetic callback all have Owner-supplied native/SQLite matching evidence; G3-R/A and native Run terminal remain NOT TESTED. The 'G3-P2 incomplete' text in the dated workstream sequence above is superseded for pilot feasibility only. G4 **design GO** is not production/device GO. Hermes G0 physical v2 Slow→Inbox→spoken remains a gating item. Keep source, deployment and physical evidence distinct; do not run expensive CI on a docs-only change. [Agent handoff](../handoffs/20261027-grok-g4-agents.md).
