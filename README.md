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

P0 Foundation and the P1 Kai-sim mock audio/reconnect smoke are complete. P2 Fast Voice is complete against its current software gate: Gemini adapter and a private-LAN Kai `/ws`/PTT-to-Live bridge harness are in the worktree. The merged PR #8 baseline had 39 Fast Voice tests; PR #10, #12, and #13 are squash-merged. This P3.4 branch has Fast Voice 52/52 and Relay 39/39 offline tests, including Inbox lookup. The PR declares `delegate_task` as `BLOCKING`, so Live waits only for durable admission; OpenAB execution remains asynchronous. A separately authorized local paced-PTT Live smoke with `behavior=BLOCKING` confirmed one same-call-ID admission, durable task/outbox, post-tool audio/caption, and the expected acceptance phrase in memory. This validates the local handler path, not physical Kai-sim/ECS runtime. The later refreshable 3-second inactivity timeout / 15-second absolute bound is covered offline only; no Live call was made after that timeout refinement. The CLI `relay.live_bridge.run()` now fail-closes into `build_supported_relay_handler` and requires server-owned `AMUSANT_CLIENT_ID`, `AMUSANT_BACKEND_ID`, and `AMUSANT_TASK_DB`; this is local process wiring, not ECS deployment or AWS runtime evidence. A bounded real Gemini Live text-to-speech smoke passed: transcript `Ready.`, non-cancelled turn completion, and 48,000 bytes of 24 kHz PCM16LE (~1 second). Bridge tests use a fake provider. Real Kai-sim PTT→Live runtime acceptance passed once on 2026-10-09: the cap-128 turn logged 152,576 input bytes, `turn_complete`, and 92,642 output bytes (~1.93 s at 24 kHz mono PCM16LE); the user confirmed full speech playback. The previous cap-64 turn produced only 23,520 output bytes (~0.49 s) and was truncated. The higher cap correlates with full playback but is not isolated as the sole root cause. The AWS key was loaded from Secrets Manager into process memory only; no key or raw audio was persisted. The cap-128 voice-loop acceptance passed once. The bridge was stopped at the user's request after the P2 runtime pass; private port 8765 was verified closed. Previous top-up and test runs' billing was not independently checked. On 2026-10-10, authenticated Jellii ACP session/prompt/end_turn and a one-off Fargate Worker→Jellii smoke passed with a fake Relay/Inbox. Arthur accepted the dedicated task (including its built-in tools, task-local OAuth state, and unrestricted egress) as the P3.0 trust boundary for this one-off viability smoke; this is not production Relay/Result Inbox acceptance or P4 deployment approval. P3.1 `delegate_task` → SQLite task+outbox admission is merged in PR #8 (`e354b926`). PR #10 is squash-merged (`39a4b5e`); `delegate_task` BLOCKING was live-tested once with a local spoken acknowledgement. The local CLI is wired to the composition root and fail-closes without identity/backend/SQLite config; it also recovers crashed in-flight dispatch as uncertain. P3.2 Dispatcher and P3.3 ACP client are merged. P3.4 Inbox lookup is implemented against local SQLite and is not a live Jellii/OpenAB or spoken Gemini session. ECS image/task definition/ingress remain P4. The two-ECS topology is accepted as a docs amendment but not deployed; no AWS change is claimed. Formal latency evaluation remains in P4; AWS deployment and hardware remain out of this gate.
