# Project Amusant

Amusant is an independently maintained, Kai-compatible voice interface. Its first phase is software-first: validate the voice loop with Kai's simulator, then proceed to Kai-derived hardware only after software acceptance.

## Scope

- Voice input; spoken and text responses.
- Fast realtime providers are manually selected by the user. Gemini Live is the first baseline; a second provider is optional validation, not an MVP gate.
- OpenClaw agent is the initial slow-task backend.
- No location, sensors, or physical-device control in this phase.

## Source of truth

- [SPEC.md](SPEC.md) — requirements and acceptance criteria.
- [PLAN.md](PLAN.md) — execution sequence and gates.
- [STATUS.md](STATUS.md) — current verified state.
- [`pyproject.toml`](pyproject.toml) + [`uv.lock`](uv.lock) — Python runtime dependencies and reproducible lock.
- [SPEC-P0-DELTA.md](SPEC-P0-DELTA.md) — historical P0 decision record.
- [P1 mock relay and P2 Live bridge instructions](relay/README.md).
- [P2 Fast Voice contract, adapter, and runtime verification](fast_voice/README.md).

## Upstream

Amusant respects [zbruceli/kai](https://github.com/zbruceli/kai) as the source for the initial architecture, simulator protocol, and later hardware firmware. Kai firmware is not copied into this repository.

## Current stage

P0 Foundation and the P1 Kai-sim mock audio/reconnect smoke are complete. P2 Fast Voice is complete against its current software gate: Gemini adapter and a private-LAN Kai `/ws`/PTT-to-Live bridge harness are in the worktree. The root suite has 25 passing fake/offline tests; the isolated P1 relay suite has 4. A bounded real Gemini Live text-to-speech smoke passed: transcript `Ready.`, non-cancelled turn completion, and 48,000 bytes of 24 kHz PCM16LE (~1 second). Bridge tests use a fake provider. Real Kai-sim PTT→Live runtime acceptance passed once on 2026-10-09: the cap-128 turn logged 152,576 input bytes, `turn_complete`, and 92,642 output bytes (~1.93 s at 24 kHz mono PCM16LE); the user confirmed full speech playback. The previous cap-64 turn produced only 23,520 output bytes (~0.49 s) and was truncated. The higher cap correlates with full playback but is not isolated as the sole root cause. The AWS key was loaded from Secrets Manager into process memory only; no key or raw audio was persisted. The cap-128 voice-loop acceptance passed once. The bridge was stopped at the user's request after the P2 runtime pass; private port 8765 was verified closed. Previous top-up and test runs' billing was not independently checked. Formal latency evaluation remains in P4; OpenClaw slow lane, AWS deployment, and hardware remain out of this gate.
