# Amusant — Execution plan after the Owner-managed Hermes decision

Reconciled 2026-10-26. [SPEC](SPEC.md) and [ADR-0004](docs/adr/0004-owner-managed-hermes.md) govern scope. This plan assigns follow-up work; it is not evidence that the work ran or permission to deploy. Source baseline is `main@a74cfb5` after merged #118/#120/#122/#125; deployed OCI is not inferred.

## Work sequence and owners

| Order | Owner / lane | Deliverable | Gate / handoff |
| --- | --- | --- | --- |
| D0 | Documentation / Owner review | Align SPEC, architecture, README, status and agent work orders | Documentation-only PR; no runtime changes |
| C1 — merged #118 | Codex, WS2 + WS4 | Recovery/deadline/Cancel fixes merged | Offline evidence; live recovery and STOP remain unaccepted |
| O1, parallel with C1 | OpenClaw, WS3 + WS4 | Reconcile existing evidence and prepare native Hermes access/change plan | Read-only only within existing explicit access authorization; blocked permissions are reported, not bypassed |
| C2 — merged #120 | Codex, WS2 | Generic delegation and SQLite v9 source merged | Host schema and real tool behavior not yet accepted |
| Release readiness — #124 open | Codex / Operator | Review SQLite v8→v9 backup, isolated restore and rollback | Ready CI passed; not merged or deployed |
| O2 | OpenClaw + Owner | Apply an approved native-access/configuration change set using Hermes mechanisms | Separate explicit approval for exact host/config/secrets/restart/network changes |
| V1 | OpenClaw operates, Owner tests device, Codex fixes proven defects | Verify selected v2 bundle and physical Slow -> Inbox -> spoken loop; native Owner access and one tool-agnostic case | Separate live-test authorization and bounded provider budget; keep evidence categories distinct |
| Later, only on demonstrated need | Scoped owner | Facade retrieval/export, partial-success presentation or cross-channel continuation | Separate small design/PR; not prerequisites bundled into C1/C2 |

C1/C2 dependency ordering is now historical: #118 and #120 merged. O1/O2 still require active bundle/schema evidence and separate Owner authorization.

## Forwardable work orders

- [Codex: C1 reliability, then C2 generic integration](docs/handoffs/20261025-codex.md).
- [OpenClaw: O1 evidence/native-access plan, then gated O2/V1](docs/handoffs/20261025-openclaw.md).
- [Coordination, authority and completion record](docs/handoffs/20261025-owner-managed-hermes.md).

The Owner forwards these documents. Creating them does not automatically dispatch either agent. Do not infer live authority from a forwarded development task.

## Issue and PR disposition

| Tracker | Direction |
| --- | --- |
| #117 | Closed without merge; its Artifact-intent classifier remains superseded. |
| #118 / #58 | #118 merged; #58 remains open for real recovery/STOP and acceptance, not another offline implementation. |
| #105 / #120 | Generic delegation merged; #105 remains open for physical delivery, Owner tool-policy validation and separate Facade readback. |
| #101 | Bootstrap preserves Owner settings and supports native management. Git owns adapter requirements/examples, not an exhaustive Agent tool allowlist. Do not revive a large Doctor/Admin subsystem. |
| #46 / #62 | Reconcile source/deployed/accepted evidence, then finish the bounded voice loop; no invented or repeated PASS. |
| #27 | Ingress remains independently reviewed; Telegram long polling does not authorize public Hermes endpoints. |
| #43 / WS1 | Capability/device backlog remains distinct from the Agent-management baseline; no broad parity mandate. |
| #121 / #122 / #125 / #123 | Isolated Grok candidate: proposal, G2 offline spike and G3-P1 callback source merged. G3-P2 N100 setup/live remains incomplete; G4 requires Hermes G0 plus G3 evidence. |
| #124 / #102 | #124 open Ready for SQLite release readiness; #102 open Draft/on hold. Neither is a deployed release. |

Tracker bodies can contain historical requirements. The 2026-10-25 Owner decision governs conflicting scope; agents should link this plan in their follow-up rather than silently claim those trackers were edited or closed.

## WIP, CI and scope control

Keep one major active runtime change per affected workstream, plus scoped reliability remediation. No new queue, global scheduler, Agent service, Admin API, Facade merge or Hermes core fork.

Use locked offline dependencies and fake providers for code tests. Documentation-only changes use the existing lightweight path checks; do not modify workflows or run heavy ARM64 jobs for this alignment. Draft runtime PRs retain cost-saving skips; corrected Ready PRs need applicable full regression/ARM64 evidence. Skipped, stubbed or environment-blocked tests are not PASS.

Do not block completion of the existing v2 loop on every native capability. Native Owner access is an integration deliverable, not a demand to implement all tools or every management UI. Native scheduling stays in Hermes; no global Inbox/approval synchronization is assumed.

## Historical plan

Earlier P0-P4, AWS and dated execution snapshots are preserved at the [immutable documentation baseline](docs/history/20261025-documentation-baseline.md). They explain past work, not current authorization or a second active backend.

## 2026-10-27 — Grok as an **alternative replacement** backend: current G4 lane

The historical Hermes schedule above is **not** authorization to continue building a second full Agent platform in parallel. Hermes remains the accepted integration baseline until G5; the Grok pilot's purpose is to assess whether Grok can **replace** its SLOW role. G1/G2 accepted, G3 core Q1/Q2/D1 native Callback and App continuation are Owner-observed PASS; see [#123](https://github.com/smallgun01/amusant/issues/123#issuecomment-6419151666) and [STATUS](STATUS.md). The older 'G3-P2 incomplete' entries above are superseded for product feasibility, not for hardening/native Stop/production ingress.

| Gate | Responsible | Deliverable | Permission / prerequisite |
| --- | --- | --- | --- |
| G4-D0 | Codex Desktop | Read-only source gap, design alternatives, backend identity, callback→Inbox, Single-flight/cancel, schema/rollback and fake-test matrix; comment on #127 | **GO now**; no runtime PR/code |
| G4-O0 | OpenClaw | Read-only G0/active bundle evidence where authorized; stable HTTPS callback, permissions, operations and rollback plan | **GO now**; no sudo/Docker/secret or host mutations |
| G4-D1 | Codex | Small **offline-only** implementation PR with fake Routine, no real calls or infrastructure | After Owner accepts D0 and grants explicit code-scope GO |
| G4-D2 | Operator + Owner | Reviewed stable ingress, staging deployment/security and rollback | G0 physical Slow→Inbox→spoken PASS, exact network/cost approvals and reviewed images |
| G4-D3 | Owner + OpenClaw | Two reserved real StickS3→Grok→Inbox→new-PTT spoken cases | Explicit live GO, bounded usage, no unsolicited speech |
| G5 | Owner | Measure cost, UX, actual recovery and native work access; decide replacement | Separate cutover/rollback decision; never infer Hermes retirement from G3 PASS |

Keep one configured SLOW backend for new tasks, no auto-backend fallback. Callback delivery/Agent self-report/native Run terminal are distinct; Grok cancellation and timeout cannot be represented as a verified remote Stop. Original G3 pilot SQLite is not a second production task ledger. Return transport (v0.2 Remote HTTPS MCP/OAuth primary vs callback-first observed PASS) remains a design/Owner decision, not an implicit permanent switch. Review [#127](https://github.com/smallgun01/amusant/issues/127), [workstreams](docs/project-management/workstreams.md) and [G4 Codex/OpenClaw handoff](docs/handoffs/20261027-grok-g4-agents.md). Documentation-only updates do not deploy or run providers.
