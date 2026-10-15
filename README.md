# Project Amusant

Amusant is an independently maintained, one-to-one Kai-compatible personal voice companion. Muse Charm is a product-category/interaction benchmark only—not a feature-parity, compatibility, or dependency claim. Its first phase is software-first: validate the voice loop with Kai's simulator, then proceed to Kai-derived hardware only after software acceptance.

## Scope

- Accepted target AWS architecture (PR #9): exactly two ECS Services—Amusant Relay (in-process Dispatcher) and OpenAB/Jellii Agent Runtime. **The P4-T4b live proof used a standalone Relay RunTask; the persistent Relay ECS Service A remains a future step.** No standalone Worker ECS or assumed SQS.
- Voice input; spoken and text responses.
- Fast realtime providers are manually selected by the user. Gemini Live is the first baseline; a second provider is optional validation, not an MVP gate.
- P3 proposed slow backend: **OpenAB native ACP WebSocket** with a restricted agent, behind Amusant's backend-neutral durable task boundary. Main N100 OpenClaw integration is deferred.
- No location, sensors, or physical-device control in this phase.

## Source of truth

- [SPEC.md](SPEC.md) — canonical requirements (P3 design proposed).
- [ADR-0001](docs/adr/0001-openab-acp-slow-path.md) — OpenAB ACP Slow Path architecture decision and acceptance gates.
- [PLAN.md](PLAN.md) — execution sequence and gates.
- [Cross-track integration roadmap](docs/hardware/cross-track-integration-roadmap.md) — G0 local hardware, parallel P4-A2 / H1 Wi-Fi, G1 edge and G2/G3 joining gates (planning only).
- [STATUS.md](STATUS.md) — current verified state.
- [P4-T4b Live acceptance](docs/p4-t4b-live-acceptance.md) — deployed WSS/ACP evidence, after-stop DynamoDB result and recorded exceptions.
- [`pyproject.toml`](pyproject.toml) + [`uv.lock`](uv.lock) — Python runtime dependencies and reproducible lock.
- [SPEC-P0-DELTA.md](SPEC-P0-DELTA.md) — historical P0 decision record.
- [P1 mock relay and P2 Live bridge instructions](relay/README.md).
- [P2 Fast Voice contract, adapter, and runtime verification](fast_voice/README.md).

## Upstream

Amusant respects [zbruceli/kai](https://github.com/zbruceli/kai) as the source for the initial architecture, simulator protocol, and later hardware firmware. Kai firmware is not copied into this repository.

## Current stage

- **P0–P2 complete.** Historical Live/Kai-sim counts and billing caveats live in `STATUS.md`.
- **P3.1–P3.4 squash-merged.** Offline Fast Voice 52/52. Spoken Gemini tool-disabled delivery remains unproven.
- **P4-A1 live one-off PASS (2026-10-12).** Real Dispatcher/Inbox against Jellii, one ACP `session/prompt`, then Jellii returned to 0/0/0. Same-SG plaintext `ws://` `/acp` is a one-off risk exception, not TLS. Evidence: `STATUS.md`.
- **P4-T1 offline WSS PoC merged (PR #23).** Relay 105/105 and Fast Voice 52/52 offline PASS; fake ACP only, not live Jellii/AWS WSS.
- **P4-T2/T3 NGINX sidecar path** was exercised in a separately authorized bounded Live window. The actual ENI/SG controller checks passed; the standalone SG script was not invoked.
- **ADR-0002 accepted.** Store-ready Relay squash-merged PR #19. Durability live one-off PASS (2026-10-12): DynamoDB ledger survived A STOPPED; `store_get` still completed. Table kept. Same-SG `ws://` is a one-off exception, not TLS. CLI default remains SQLite. Evidence: `STATUS.md`.

- **P4-T4b private WSS/ACP live smoke: functional PASS (2026-10-13; Issue #26).** Real Jellii:10 + NGINX and Relay Fargate 1.4.0 RunTask; current-IP Cloud Map and isolated SG, eight preflight checks, one completed ACP task and same-task after-STOPPED strongly consistent DynamoDB read. Jellii reverted to Revision 9/0/0/0. Operational exceptions and unverified cost are documented in the [acceptance record](docs/p4-t4b-live-acceptance.md). **Not** persistent Relay Service A, device/voice end-to-end, or production acceptance.
- **Next:** persistent Relay ECS Service A and real device/voice → Slow Task → Inbox/voice E2E; H0/H1 and device ingress remain separately scoped in Issues #11, #14 and #27.
