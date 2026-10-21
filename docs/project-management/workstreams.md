# Amusant WS1–WS4 — Current M1 delivery gates

> **Reviewed 2026-10-21** against `main@6de2f08`. [SPEC](../../SPEC.md) governs product acceptance, [ADR-0003](../adr/0003-oci-a1-hermes-sqlite.md) the architecture and [STATUS](../../STATUS.md) evidence. Owner-reported OCI SSH access is recorded in the [host handoff](../../deploy/oci-a1/HOST-READINESS.md), not a live deploy gate. This index authorizes no cloud mutation, paid providers, public ingress, flash or state migration.

## Workstream index and near-term WIP

| Lane / owning Issue | Current delivery | Blocking gate / external dependency |
| --- | --- | --- |
| **WS1 — [#47](https://github.com/smallgun01/amusant/issues/47)** StickS3/Fast | Local physical FAST passed; #69 BLE+CJK, #86 OpenCC and [#92](https://github.com/smallgun01/amusant/pull/92) W2b-1 offline firmware **merged**. [#95](https://github.com/smallgun01/amusant/pull/95) BLE teardown fix and [#97](https://github.com/smallgun01/amusant/pull/97) QR are **Drafts** | [#91](https://github.com/smallgun01/amusant/issues/91) Android teardown and #67 CJK/OpenCC physical acceptance remain open; cloud G2 depends on [#27 G1](https://github.com/smallgun01/amusant/issues/27), **not** QR/W2b completion |
| **WS2 — [#48](https://github.com/smallgun01/amusant/issues/48)** Hermes contract | #45/#60/#65 Native Runs, #75/#80 Option D, [#87](https://github.com/smallgun01/amusant/pull/87)/[#90](https://github.com/smallgun01/amusant/pull/90) **offline E1 GO**. [#94](https://github.com/smallgun01/amusant/pull/94) native ARM64 **fake-provider H1.2 toolset Case A/B PASS** | #56 full typed-input/v2/section-read, #58 timing/STOP/cost decisions, real-provider/tool scope and #46 S1; #94 is only a bounded offline toolset preflight |
| **WS3 — [#49](https://github.com/smallgun01/amusant/issues/49)** OCI/Integration | #70/#83 native ARM64 package/image prep, [#85](https://github.com/smallgun01/amusant/issues/85) #87/#90 integrated **offline E1 GO**; **Owner-reported 2026-10-21 OCI A1 ARM64 VM is SSH-accessible from N100 and OpenClaw read-only BatchMode succeeded** | [#62](https://github.com/smallgun01/amusant/issues/62) **host inventory first**, then separately approved installed runtime/backup/restore/canary; [#27](https://github.com/smallgun01/amusant/issues/27) G1 and #46 S1 → physical G2/G3a remain open. SSH is not application E3 |
| **WS4 — [#50](https://github.com/smallgun01/amusant/issues/50)** Reliability | #78 SQLite maintenance, #89 general CI proof, #90 path-filtered ARM64 integrated E1 and #94 path-filtered H1.2 toolset E1 | Off-host encrypted backup/restore, STOP/billing, timing/monitoring, actual host contention and E4; never equate offline resource ceilings to observed capacity |

## Required order (no new feature branches on the critical path)

1. **H5-P1B E1 COMPLETE:** #87/#90 merged and pinned native ARM64 offline Relay→Facade→Hermes→Artifact→Inbox proof passed. **Not** a live OCI test, actual provider-cancellation confirmation, paid-model test or full host reboot.
2. **OCI H2 inventory → reviewed setup → separately approved E3 canary (active):** [#62](https://github.com/smallgun01/amusant/issues/62) existing SSH-reachable VM is **not** yet Docker/Compose/volume/backup-ready by evidence. Start with read-only [host readiness](../../deploy/oci-a1/HOST-READINESS.md), then request narrow Owner approval for each host mutation and live canary. [#27](https://github.com/smallgun01/amusant/issues/27) G1 transport design runs in parallel; do not open public 8443 merely because SSH works.
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
