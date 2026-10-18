> **Architecture update (2026-10-18; pending reviewed merge):** The selected **implementation target** is a single OCI A1 host running Hermes Native Runs and Relay with a persistent **SQLite** Task Ledger; see [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md), [OCI offline package PR #70](https://github.com/smallgun01/amusant/pull/70) and [hosting issue #62](https://github.com/smallgun01/amusant/issues/62). Hermes is the code-default executor (PRs #45/#60/#65 merged); OpenAB ACP and the ECS/DynamoDB one-off are historical/explicit rollback, not the new deployment target. **No OCI live deployment, production cutover, public device ingress or paid provider run has passed.** Earlier dated progress descriptions below remain historical snapshots; do not interpret them as the current target.

# Project Amusant

Amusant is an independently maintained, one-to-one Kai-compatible personal voice companion. Muse Charm is a product-category/interaction benchmark only—not a feature-parity, compatibility, or dependency claim. Its Kai-sim software smoke and **real StickS3 → local Amusant Relay → Gemini FAST functional test** have passed. Cloud device ingress, persistent cloud Relay and complete Slow Inbox voice integration are separate pending gates.


## Active project delivery (2026-10-16)

Use the [WS1–WS4 execution plan](docs/project-management/workstreams.md) as the **current project management and gate index**, alongside [PLAN.md](PLAN.md) (sequence), [STATUS.md](STATUS.md) (evidence), and [SPEC.md](SPEC.md) (normative requirements). The original P0–P4/H0/H1 milestones are retained for history; workstreams do not themselves authorize architecture changes.

- **WS1 [#47](https://github.com/smallgun01/amusant/issues/47)** — physical StickS3 and Relay Fast UX/selected Kai capabilities, firmware Wi-Fi provisioning. Local device FAST functional PASS; latency product gate open.
- **WS2 [#48](https://github.com/smallgun01/amusant/issues/48)** — B0 Slow Task product contract and backend/hosting decision. Existing OpenAB cloud proof; Hermes [Draft PR #45](https://github.com/smallgun01/amusant/pull/45) local fake-provider POC only; no backend switch yet.
- **WS3 [#49](https://github.com/smallgun01/amusant/issues/49)** — device edge G1, persistent Relay A2, [S1 #46](https://github.com/smallgun01/amusant/issues/46) software cloud Fast→Slow→Inbox spoken, G2 physical FAST, G3a physical Slow, G3b Wi-Fi mobility.
- **WS4 [#50](https://github.com/smallgun01/amusant/issues/50)** — reliability, bounded recovery, DynamoDB history, security, monitoring and cost gates. CI and PR #40/#41 merged; #30 PR-C/D pending.

Important: **WS1** denotes the workstream; **WiFi-W1** denotes the firmware NVS sub-stage. Neither the accepted OpenAB/two-ECS SPEC nor the running cloud footprint changes merely because the Hermes selection is being evaluated. No new cloud/spend/flash authorization follows from these documents.

## Scope

- Accepted target AWS architecture (PR #9): exactly two ECS Services—Amusant Relay (in-process Dispatcher) and OpenAB/Jellii Agent Runtime. **The P4-T4b live proof used a standalone Relay RunTask; the persistent Relay ECS Service A remains a future step.** No standalone Worker ECS or assumed SQS.
- Voice input; spoken and text responses.
- Fast realtime providers are manually selected by the user. Gemini Live is the first baseline; a second provider is optional validation, not an MVP gate.
- Accepted Slow backend baseline: **OpenAB native ACP WebSocket** and a restricted agent. Hermes Native Runs (#44 / Draft PR #45) is a local POC under B0 evaluation, **not an accepted backend cutover**. Main N100 OpenClaw bridge remains deferred.
- No location, sensors, or physical-device control in this phase.

## Source of truth

- [SPEC.md](SPEC.md) — **accepted v0.4 requirements and OpenAB/two-ECS architecture baseline**; B0 decision has not changed it.
- [ADR-0001](docs/adr/0001-openab-acp-slow-path.md) — OpenAB ACP Slow Path architecture decision and acceptance gates.
- [PLAN.md](PLAN.md) — execution sequence and gates.
- [Cross-track technical roadmap](docs/hardware/cross-track-integration-roadmap.md) — G0 evidence, G1/A2, S1, G2/G3a/G3b and WiFi test isolation (subordinate to the WS1–WS4 execution index).
- [H0.3 Stage D: Jellii Persona + Zephyr Voice](docs/hardware/sticks3-jellii-voice-stage-d.md) — opt-in Gemini Live voice/persona, Google Search Grounding, offline tests, and rollback.
- [STATUS.md](STATUS.md) — current verified state.
- [P4-T4b Live acceptance](docs/p4-t4b-live-acceptance.md) — deployed WSS/ACP evidence, after-stop DynamoDB result and recorded exceptions.
- [`pyproject.toml`](pyproject.toml) + [`uv.lock`](uv.lock) — Python runtime dependencies and reproducible lock.
- [SPEC-P0-DELTA.md](SPEC-P0-DELTA.md) — historical P0 decision record.
- [P1 mock relay and P2 Live bridge instructions](relay/README.md).
- [P2 Fast Voice contract, adapter, and runtime verification](fast_voice/README.md).

## Upstream

Amusant respects [zbruceli/kai](https://github.com/zbruceli/kai) as the source for the initial architecture, simulator protocol, and later hardware firmware. Kai firmware is not copied into this repository.

## Current verified stage — 2026-10-16

- **Software baseline:** P0–P2 PASS (Kai-sim P2 complete audible 2026-10-09), P3.1–P3.4 merged. PR #40 safe Inbox delivery/tool-disabled spoken *implementation* and PR #41 Dispatcher notification/polling merged and offline-tested; **real new Gemini Live spoken retrieval is not proven** (#46 S1).
- **Physical/local WS1:** #31 Stage C **StickS3 → local Amusant Relay → Gemini FAST 10/10 functional PASS** (2026-10-15), wake/barge-in/reconnect observed; #33 six-state Jellii/avatar and physical PTT regression PASS. Reported first-wire-audio latency is **Relay-side**, not speaker audible-onset p95; H0 UX/battery decision pending.
- **Fast feature work:** Draft PR #42 Jellii persona, Zephyr and Google Search is offline-CI green, **live device acceptance pending**; WiFi-W1 NVS, W1.5 BLE PoC, W2a profiles and W2b BLE-vs-SoftAP provisioning pending (#36).
- **Slow WS2:** OpenAB authenticated ACP live one-off baseline accepted. Hermes #44 / Draft PR #45 only local fake-provider Native Runs Phase 2A/2B POC, **not selected or live/cloud accepted**; B0 remains open.
- **AWS WS3:** #26 2026-10-13 T4b **bounded functional PASS**: Relay RunTask → private TLS WSS OpenAB/NGINX + DynamoDB Inbox same-task after-stop strong readback, with documented procedural exceptions and rollback. **No persistent ECS Relay A service, public device ingress, S1, G2, G3a, G3b or 24/7 production acceptance.**
- **Reliability WS4:** CI #34, Inbox PR #40 and Dispatcher PR #41 merged. #30 PR-C (>100 history) / PR-D (bounded stop / real service entrypoint) and #35 A2 operations remain pending.
- **Next:** parallel WS2 B0, WS4 PR-C/D, WS3 G1 offline + A2.0/A2.1 and WS1 #42/H0 + independently gated firmware work; after B0/C/D approve A2 cloud canary, S1 software E2E, G2 physical cloud FAST and G3a physical Slow, then G3b mobility. [Workstreams and gates](docs/project-management/workstreams.md).

Historical P1–P4 details and earlier phase-level test counts remain in [STATUS.md](STATUS.md). No deployment, paid model execution, firmware flashing or accepted-backend change is authorized by these documents.
