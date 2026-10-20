# Amusant WS1–WS4 — Current M1 delivery gates

> **Updated 2026-10-20** against `main@5c788be`. Sequencing/ownership only. [SPEC.md](../../SPEC.md) governs product acceptance; [ADR-0003](../adr/0003-oci-a1-hermes-sqlite.md) governs selected architecture; [STATUS.md](../../STATUS.md) records verified evidence. This index does not approve paid tests, production deployment, public ingress, firmware flash or data migration.

## Workstream index and near-term WIP

| Lane / owning Issue | Current delivery | Blocking gate / external dependency |
| --- | --- | --- |
| **WS1 — [#47](https://github.com/smallgun01/amusant/issues/47)** StickS3/Fast | Local physical FAST passed; #69 BLE+CJK and #86 OpenCC merged. [#92](https://github.com/smallgun01/amusant/pull/92) W2b re-provisioning is **Draft and offline-tested only**; #67 physical captions/LCD; #31 measured audio latency/power | Physical G2 needs [#27 G1](https://github.com/smallgun01/amusant/issues/27) approved transport; BLE/W2b is parallel WS1 UX work, not an M1 cloud gate |
| **WS2 — [#48](https://github.com/smallgun01/amusant/issues/48)** Hermes contract | Native Runs #45/#60/#65, Option D #75/#80, [PR #87](https://github.com/smallgun01/amusant/pull/87)/[PR #90](https://github.com/smallgun01/amusant/pull/90) merged **offline E1 GO**; #56 feature-level artifact/v2 contract and #58 timing remain | Real provider/model/tool/cancel-cost approval and S1; no parallel OpenAB product path |
| **WS3 — [#49](https://github.com/smallgun01/amusant/issues/49)** OCI/Integration | #70/#83 native ARM64 prep and [#85](https://github.com/smallgun01/amusant/issues/85) B1/B2 ([PR #87](https://github.com/smallgun01/amusant/pull/87)/[PR #90](https://github.com/smallgun01/amusant/pull/90)) closed **E1 GO**; [native ARM64 E2E #39240814687](https://github.com/smallgun01/amusant/actions/runs/39240814687) is a **synthetic integrated** proof | [#62](https://github.com/smallgun01/amusant/issues/62) separately approved E3 OCI canary with backup/restore and load metrics, [#46](https://github.com/smallgun01/amusant/issues/46) real software S1, #27 G1 → G2/G3a; do not infer cloud acceptance |
| **WS4 — [#50](https://github.com/smallgun01/amusant/issues/50)** Reliability | #78 SQLite maintenance, #89 pinned Option D proof on general CI, and #90 path-filtered native ARM64 B2 E2E for relevant changes | Off-host encrypted backup/restore, provider STOP/billing observation (`stop_cancel_confirmed=false`), bounded production timings, secret/monitoring, concurrent 1 OCPU load and E4 |

## Required order (no new feature branches on the critical path)

1. **H5-P1B E1 COMPLETE:** #87/#90 merged and pinned native ARM64 offline Relay→Facade→Hermes→Artifact→Inbox proof passed. **Not** a live OCI test, actual provider-cancellation confirmation, paid-model test or full host reboot.
2. **OCI preflight → bounded E3 Canary (active):** [#62](https://github.com/smallgun01/amusant/issues/62) private VM/Compose/volumes, image digests, off-host encrypted backup plus restore, operational/cost limits and explicit owner approval. [#27](https://github.com/smallgun01/amusant/issues/27) compatible G1 edge transport can be designed in parallel with preparation; no public ingress without review.
3. **S1 / #46:** Genuine software Gemini FAST → durable Hermes SLOW → bounded Inbox → new tool-disabled Live spoken session, including restart/negative paths. Real traffic needs separate Owner approval.
4. **G2 FAST then G3a SLOW:** secure device connection on a known Wi-Fi; S1 must be satisfied for G3a. G3b mobility, scheduling, proactive cues and Kai feature parity are post-M1 backlogs.

## Gate vocabulary and coordination

- **E1:** reviewed/merged code + pinned offline tests. **E2:** physical/local real-device evidence. **E3:** approved bounded cloud/provider evidence. **E4:** sustained operational/security/cost verification. No automatic promotion.
- **Single-flight:** exactly one outstanding SLOW task, immediate `slow_busy`; legacy 8-item FIFO remains historical only. No surprise voice. Hermes produces full report and short result; Relay/Gemini do not summarize artifacts.
- **B0 resolved:** Hermes implementation target and OCI A1 + SQLite chosen, *not* production deployed. AWS/OpenAB/DynamoDB one-off receipts are historical, not new deployment blockers. [#35](https://github.com/smallgun01/amusant/issues/35) ECS Service A is superseded; [#30](https://github.com/smallgun01/amusant/issues/30) tracks bounded residual reliability work, not an entire AWS PR-C/D requirement for OCI.
- **WIP limit:** one active build per WS1, WS2, WS3; one scoped WS4 remediation. [#43](https://github.com/smallgun01/amusant/issues/43) is capability backlog, not feature-parity mandate. A Draft PR, issue comment or old AWS acceptance does not authorize merge, flashing, paid calls or new cloud resources.
- **Preserve trust boundaries:** StickS3→Relay G1, Relay→Facade private auth, Facade→Hermes private Runs, Hermes→Facade trusted writer, provider traffic via controlled egress. No extra Worker/SQS/Agent, direct Relay→Hermes, public Hermes/Facade API or unreviewed tool credentials.

## Archived evidence

Historical P0–P4/H0 and AWS ACP/DynamoDB decisions remain in [PLAN.md](../../PLAN.md), [STATUS.md](../../STATUS.md), [ADR-0001](../adr/0001-openab-acp-slow-path.md), [ADR-0002](../adr/0002-external-task-store.md) and [P4-T4b](../p4-t4b-live-acceptance.md). Those documents explain what was proven *then*; they do not override current gates.
