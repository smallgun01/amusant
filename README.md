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

P0 Foundation is complete. The local P1 mock probe is running; the remaining smoke test is Kai-sim from a LAN computer with audio devices (OS to be confirmed). The probe is not a full Kai or production Amusant Relay. Hardware remains gated on software acceptance.
