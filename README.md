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
- [P1 mock relay instructions](relay/README.md).
- [P2 Fast Voice contract and adapter scaffold](fast_voice/README.md).

## Upstream

Amusant respects [zbruceli/kai](https://github.com/zbruceli/kai) as the source for the initial architecture, simulator protocol, and later hardware firmware. Kai firmware is not copied into this repository.

## Current stage

P0 Foundation and the P1 Kai-sim mock audio/reconnect smoke are complete. P2 Fast Voice is in progress: the provider-neutral contract and injected Gemini adapter scaffold are covered by 14 local tests (13 fake-session tests plus one no-network check using google-genai SDK types). `google-genai==2.29.0` is locked and installed only in the repo-local N100 `.venv`; no Gemini Live session/API call has been made. The P1 mock's user-reported <1 s observations are manual estimates, not formal latency statistics or Live-provider evidence. Formal latency evaluation remains in P4; hardware remains gated on software acceptance.
