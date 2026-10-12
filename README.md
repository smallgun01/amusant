# Project Amusant

Amusant is an independently maintained, one-to-one Kai-compatible personal voice companion. Muse Charm is a product-category/interaction benchmark only—not a feature-parity, compatibility, or dependency claim. Its first phase is software-first: validate the voice loop with Kai's simulator, then proceed to Kai-derived hardware only after software acceptance.

## Scope

- Accepted AWS architecture baseline (PR #9; not deployed): exactly two ECS Services—Amusant Relay, with the Async Dispatcher inside Relay, and OpenAB Agent Runtime. No standalone Worker ECS or assumed SQS.
- Voice input; spoken and text responses.
- Fast realtime providers are manually selected by the user. Gemini Live is the first baseline; a second provider is optional validation, not an MVP gate.
- P3 proposed slow backend: **OpenAB native ACP WebSocket** with a restricted agent, behind Amusant's backend-neutral durable task boundary. Main N100 OpenClaw integration is deferred.
- No location, sensors, or physical-device control in this phase.

## Source of truth

- [SPEC.md](SPEC.md) — canonical requirements (P3 design proposed).
- [ADR-0001](docs/adr/0001-openab-acp-slow-path.md) — OpenAB ACP Slow Path architecture decision and acceptance gates.
- [PLAN.md](PLAN.md) — execution sequence and gates.
- [STATUS.md](STATUS.md) — current verified state.
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
- **ADR-0002 accepted.** Store-ready Relay: `TaskStore`, fake ledger + boto3 ledger, `AMUSANT_TASK_STORE=sqlite|dynamodb`, Dockerfile + taskdef template. Offline Relay 73/73. No table. CLI default remains SQLite.
