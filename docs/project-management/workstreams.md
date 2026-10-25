# Amusant WS1-WS4 — Owner-managed Agent integration

Aligned 2026-10-25 to [SPEC](../../SPEC.md), [ADR-0004](../adr/0004-owner-managed-hermes.md) and retained [OCI architecture](../adr/0003-oci-a1-hermes-sqlite.md). [STATUS](../../STATUS.md) owns evidence. This index assigns work; it does not grant deployment authority.

## Workstreams

| Lane / tracker | Responsibility | Next scoped work |
| --- | --- | --- |
| WS1 / #47 | StickS3, provisioning, FAST speech/display and physical UX | Preserve working FAST/BLE/QR; separate W2a, CJK, latency/battery and ingress acceptance |
| WS2 / #48 | Generic Hermes task/result integration, not Agent administration | Codex C1 #118, then C2 generic delegation; channel-scoped plugin and truthful outcomes |
| WS3 / #49 | Deployment compatibility and voice/native access integration | OpenClaw O1 release/evidence inventory, native Owner path proposal; approved O2/V1 only |
| WS4 / #50 | Persistence, secrets, resources, backup/restore, CI discipline | Both-store recovery regressions; preserve Owner profile; gated host evidence and restore/load work |

## Coordination

Codex owns repository runtime edits/tests. OpenClaw owns permitted host inspection, change planning and separately authorized operations. Owner supplies credentials through secure native mechanisms, approves merges/live changes and performs device interaction. Review checks scope and evidence; it does not substitute for those approvals.

Use [the handoff index](../handoffs/20261025-owner-managed-hermes.md). C1 and O1 can proceed in parallel. C2 store/executor edits wait for C1 review/landing; do not maintain competing branches that each alter lifecycle or migrations. OpenClaw reports a repo defect to Codex rather than hot-patching the live container.

## Required sequence and limits

1. Land the documentation baseline; do not implement superseded #117.
2. Resolve #118 review P1s and completed-versus-delivery semantics; keep required CI honest.
3. Reconcile existing OCI/device evidence and prepare native Hermes access without mutation under O1.
4. Implement generic delegation offline and prepare a reviewed, compatible release bundle.
5. With separate Owner authority, verify deployed v2 voice delivery and independent native access; reuse existing evidence where valid.

Do not wait for a catalog of Calendar/Cron/booking adapters. Those capabilities belong to Hermes. Native scheduling/notifications are not new Relay features. Native management is required integration work, not a mandate to implement every upstream capability before the current voice loop can be tested.

Keep one major build per affected lane plus scoped reliability remediation. Documentation-only PRs use lightweight checks. Draft heavy-job skips save minutes but are not runtime evidence; corrected Ready runtime PRs require applicable full jobs.

## Channel boundaries

- One outstanding Amusant-admitted Run per Owner, not one job across all Hermes channels.
- No unsolicited Amusant speech, not a ban on Owner-configured native Telegram delivery.
- Shared Agent/profile is not shared session/approval/Inbox; do not promise automatic continuation.
- No raw reports/credentials to Relay or Live, no direct Relay-to-Hermes route, no public Hermes/Facade API, no new Worker/queue/Admin service.

## History

[#87/#90](https://github.com/smallgun01/amusant/pull/90) remain offline integration evidence; [GO 2/3](../oci-go2-go3-live-evidence-20261022.md) remains dated live functional evidence. Legacy WS and AWS snapshots are available through the [immutable history index](../history/20261025-documentation-baseline.md), not competing current requirements.
