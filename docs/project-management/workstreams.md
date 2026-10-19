# Amusant WS1–WS4 — Current M1 delivery gates

> **Updated 2026-10-19.** Sequencing/ownership only. [SPEC.md](../../SPEC.md) governs product acceptance; [ADR-0003](../adr/0003-oci-a1-hermes-sqlite.md) governs selected architecture; [STATUS.md](../../STATUS.md) records verified evidence. An Issue/PR, CI job or roadmap does not approve paid tests, production deployment, public ingress, firmware flash or data migration.

## Workstream index and near-term WIP

| Lane / owning Issue | Current delivery | Blocking gate / external dependency |
| --- | --- | --- |
| **WS1 — [#47](https://github.com/smallgun01/amusant/issues/47)** StickS3/Fast | Local physical FAST already passed. #69 BLE+CJK + #86 OpenCC merged. #67 physical merged-build glyph/audio regression; #31 H0 measured audible latency/power; #36 Wi-Fi later | Device cloud FAST G2 needs [#27 G1](https://github.com/smallgun01/amusant/issues/27) approved compatible ingress, not W2b phone provisioning |
| **WS2 — [#48](https://github.com/smallgun01/amusant/issues/48)** Hermes contracts | Hermes Native Runs selected; #45/#60/#65 merged, Option D #75/#80 fake-provider PoC. [#56](https://github.com/smallgun01/amusant/issues/56) envelope/artifact validation, [#58](https://github.com/smallgun01/amusant/issues/58) timing policy | #85 integration, real owner-approved provider/tool/cancel evidence; no second parallel OpenAB feature stream |
| **WS3 — [#49](https://github.com/smallgun01/amusant/issues/49)** OCI/Integration | [#70](https://github.com/smallgun01/amusant/pull/70) offline OCI merged; native ARM64 [#83](https://github.com/smallgun01/amusant/pull/83) GO; [#85](https://github.com/smallgun01/amusant/issues/85) B1/B2 is active ([#87](https://github.com/smallgun01/amusant/pull/87) **Draft**). Then approved Canary, [S1 #46](https://github.com/smallgun01/amusant/issues/46), G2/G3a | Never promote pre-Facade Compose; no OCI live, device edge, real Agent or persistent cloud gate yet |
| **WS4 — [#50](https://github.com/smallgun01/amusant/issues/50)** Reliability | Offline CI, durable Run/Single-flight and SQLite maintenance [#78](https://github.com/smallgun01/amusant/pull/78) merged | Add production-scope Option D contracts to ongoing CI; off-host encrypted backup & restore, bounded stop/cost, secrets/monitoring and load gates |

## Required order (no new feature branches on the critical path)

1. **H5-P1B E1:** #85 B1 review then B2 full pinned fake-provider integration; verify owner/task binding, network isolation, prepared short `speak` + `card`, trusted artifact, Inbox, STOP/uncertain and GET-only recovery. Release B1/B2 only on evidence, not green CI alone.
2. **OCI preflight → bounded E3 Canary:** #27 G1 transport design can run alongside B2. New empty SQLite Ledger, reviewed exact image digests, Hermes/Facade state, owner-approved off-host backup/restore, security rollback and load/cost limits before any VM/NSG mutation.
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
