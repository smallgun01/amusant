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
- [SPEC-P0-DELTA.md](SPEC-P0-DELTA.md) — historical P0 decision record.
- [P1 mock relay instructions](relay/README.md).

## Upstream

Amusant respects [zbruceli/kai](https://github.com/zbruceli/kai) as the source for the initial architecture, simulator protocol, and later hardware firmware. Kai firmware is not copied into this repository.

## Current stage

P0 Foundation is complete. The P1 mock smoke passed: cross-LAN Kai-sim captured and played audio, and one reconnect succeeded. The user-reported manual release-to-first-audio and reconnect times were under 1 s; sample count and timing precision were not recorded. This mock does not test speech understanding or Live. Formal latency evaluation remains in P4; hardware remains gated on software acceptance.
