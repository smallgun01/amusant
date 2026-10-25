# Amusant — Execution plan after the Owner-managed Hermes decision

Updated 2026-10-25. [SPEC](SPEC.md) and [ADR-0004](docs/adr/0004-owner-managed-hermes.md) govern scope. This plan assigns follow-up work; it is not evidence that the work ran or permission to deploy. Runtime source baseline is #116 at `685db3b`.

## Work sequence and owners

| Order | Owner / lane | Deliverable | Gate / handoff |
| --- | --- | --- | --- |
| D0 | Documentation / Owner review | Align SPEC, architecture, README, status and agent work orders | Documentation-only PR; no runtime changes |
| C1 | Codex, WS2 + WS4 | Finish existing #118: deadline bypass, Cancel race, execution-versus-delivery preservation | Deterministic both-store regressions; corrected PR review; required CI on Ready |
| O1, parallel with C1 | OpenClaw, WS3 + WS4 | Reconcile existing evidence and prepare native Hermes access/change plan | Read-only only within existing explicit access authorization; blocked permissions are reported, not bypassed |
| C2, after C1 review/landing | Codex, WS2 | One small Draft PR for generic Agent delegation and compatible persistence | No classifier, per-tool routing or implicit permissions; migrations/replay/ACP tests |
| O2 | OpenClaw + Owner | Apply an approved native-access/configuration change set using Hermes mechanisms | Separate explicit approval for exact host/config/secrets/restart/network changes |
| V1 | OpenClaw operates, Owner tests device, Codex fixes proven defects | Verify selected v2 bundle and physical Slow -> Inbox -> spoken loop; native Owner access and one tool-agnostic case | Separate live-test authorization and bounded provider budget; keep evidence categories distinct |
| Later, only on demonstrated need | Scoped owner | Facade retrieval/export, partial-success presentation or cross-channel continuation | Separate small design/PR; not prerequisites bundled into C1/C2 |

C2 must not edit the same store/executor branch in parallel with C1. Analysis and test design may start earlier; implementation rebases onto the reviewed dependency. O1 may prepare in parallel without deploying an unmerged branch or changing live configuration.

## Forwardable work orders

- [Codex: C1 reliability, then C2 generic integration](docs/handoffs/20261025-codex.md).
- [OpenClaw: O1 evidence/native-access plan, then gated O2/V1](docs/handoffs/20261025-openclaw.md).
- [Coordination, authority and completion record](docs/handoffs/20261025-owner-managed-hermes.md).

The Owner forwards these documents. Creating them does not automatically dispatch either agent. Do not infer live authority from a forwarded development task.

## Issue and PR disposition

| Tracker | Direction |
| --- | --- |
| #117 | Superseded; do not implement/merge its transcript-derived Artifact requirement. Earlier implementation GO no longer applies. Preserve history; PR closure is a separate GitHub action. |
| #118 / #58 | Continue the existing reliability fix, not a new architecture branch. Resolve both review P1s and preserve trusted execution/result facts. |
| #105 | Hermes-led generic results remain valid. Do not implement the old trusted-intent classifier. Real capabilities are Hermes-managed; Facade readback remains a separate delivery gap. |
| #101 | Bootstrap preserves Owner settings and supports native management. Git owns adapter requirements/examples, not an exhaustive Agent tool allowlist. Do not revive a large Doctor/Admin subsystem. |
| #46 / #62 | Reconcile source/deployed/accepted evidence, then finish the bounded voice loop; no invented or repeated PASS. |
| #27 | Ingress remains independently reviewed; Telegram long polling does not authorize public Hermes endpoints. |
| #43 / WS1 | Capability/device backlog remains distinct from the Agent-management baseline; no broad parity mandate. |

Tracker bodies can contain historical requirements. The 2026-10-25 Owner decision governs conflicting scope; agents should link this plan in their follow-up rather than silently claim those trackers were edited or closed.

## WIP, CI and scope control

Keep one major active runtime change per affected workstream, plus scoped reliability remediation. No new queue, global scheduler, Agent service, Admin API, Facade merge or Hermes core fork.

Use locked offline dependencies and fake providers for code tests. Documentation-only changes use the existing lightweight path checks; do not modify workflows or run heavy ARM64 jobs for this alignment. Draft runtime PRs retain cost-saving skips; corrected Ready PRs need applicable full regression/ARM64 evidence. Skipped, stubbed or environment-blocked tests are not PASS.

Do not block completion of the existing v2 loop on every native capability. Native Owner access is an integration deliverable, not a demand to implement all tools or every management UI. Native scheduling stays in Hermes; no global Inbox/approval synchronization is assumed.

## Historical plan

Earlier P0-P4, AWS and dated execution snapshots are preserved at the [immutable documentation baseline](docs/history/20261025-documentation-baseline.md). They explain past work, not current authorization or a second active backend.
