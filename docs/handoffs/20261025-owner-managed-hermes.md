# Owner handoff index — architecture alignment, 2026-10-25

These are **forwardable work orders**, not evidence of agent execution. Owner has finalized the architecture in [SPEC](../../SPEC.md) and [ADR-0004](../adr/0004-owner-managed-hermes.md). No repeated architecture approval is needed; live host/provider authority remains separate.

| Agent | First assignment | Next assignment | Must not do |
| --- | --- | --- | --- |
| [Codex](20261025-codex.md) | C1: fix/review existing #118 with deterministic offline regressions | C2: one generic-delegation Draft after dependency review/landing | OCI operations, tool/credential administration, #117 classifier, self-merge |
| [OpenClaw](20261025-openclaw.md) | O1: reconcile evidence and prepare native Owner-access change plan using only permitted reads | O2/V1: exact approved changes and bounded live verification after separate Owner GO | Live hot patches, automatic sudo/Docker escalation, config/secrets writes/restarts/provider calls during O1 |

## Forwarding and branch rules

Forward the relevant document/link to each agent. Before implementation, verify whether this documentation PR is merged; otherwise use its reviewed branch explicitly and keep the dependency visible. Do not assume a file exists on main before merge. Re-read current PR heads/comments rather than treating the snapshot hashes as permanent.

Codex C1 and OpenClaw O1 can proceed in parallel. Codex C2 can be analyzed while C1 is active, but overlapping store/executor changes must wait for C1 review/landing or an explicitly reviewed stack. OpenClaw may not deploy C1/C2 merely because their branches exist.

No automatic merge, issue closure, workflow dispatch, live provider use or production activation is implied by forwarding. Normal offline repo changes/test PRs are the Codex work; existing separately granted read-only access bounds the OpenClaw work. Missing access is a specific blocker, not permission to find another privileged route.

## Common invariants

Hermes owns model/tools/Skills/MCP/memory/Cron/external credentials and approval policy. Amusant owns device/FAST/transport, its own Run lifecycle and bounded results. Native access does not traverse Relay and does not prove shared sessions or approvals. Single-flight is channel-scoped. Preserve isolation, no blind resubmission, truthful result states and Owner configuration.

#117 is superseded: do not implement or merge it. #118 remains the existing reliability lane. #101 bootstrap must preserve Owner profile and use native management; #105 retains generic results and a separate real-content-delivery gap. No new Admin framework or service is requested.

## Completion record required from both agents

Return a short report containing: exact source/branch or deployed bundle; work completed; actual tests with PASS/FAIL/BLOCKED/NOT TESTED; sanitized evidence links; remaining risks; next action and who owns it. Clearly separate source, offline, deployed, physical and native-access evidence.

Codex additionally supplies PR/files, migration/replay/rollback details and test commands. OpenClaw supplies a source/deployed matrix, actual access boundaries, an exact proposed change set and rollback; no secret/config dumps or raw private Run output.

## Owner checkpoints

1. Review/merge the documentation alignment; later review corrected C1 and C2 separately.
2. Receive O1's plan. Approve only the specific host/config/secrets/network/restart actions desired; credentials enter via native secure setup, not these documents.
3. Approve a bounded V1 test envelope: release bundle, test cases, external side effects, provider/run budget, stop conditions and evidence handling. Reuse valid existing evidence instead of repeating paid tests.

The acceptance target is simple: add an authorized test capability in Hermes, use it through the generic Amusant path without Relay/Facade code changes, receive a truthful bounded result, and keep native Owner interaction usable and isolated. This does not require enabling every Hermes tool.
