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

P0 Foundation and the P1 Kai-sim mock audio/reconnect smoke are complete. P2 Fast Voice is in progress: Gemini adapter and a private-LAN Kai `/ws`/PTT-to-Live bridge harness are in the worktree. The root suite has 25 passing fake/offline tests; the isolated P1 relay suite has 4. A bounded real Gemini Live text-to-speech smoke passed: transcript `Ready.`, non-cancelled turn completion, and 48,000 bytes of 24 kHz PCM16LE (~1 second). The bridge tests use a fake provider, so actual Kai-sim microphone/PTT → Gemini → speaker remains unverified. The AWS key was fetched from Secrets Manager into process memory only; no credential/audio was persisted. Previous top-up and test runs' billing was not independently checked. Formal latency evaluation remains in P4; OpenClaw slow lane, AWS deployment, and hardware remain out of this gate.
