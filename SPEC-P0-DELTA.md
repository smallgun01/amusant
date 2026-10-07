# P0 Decision Record — Amusant

> Historical record of P0 decisions. This file is not the requirements source of truth; SPEC.md is canonical, PLAN.md defines execution, and STATUS.md records verified state.

## Decisions incorporated into SPEC.md

- Amusant is an independent, private monorepo; Kai remains the upstream architecture, simulator-protocol, and hardware-firmware source.
- Scope is voice input and spoken/text responses only.
- Software verification with kai-sim precedes hardware work.
- P0 means Foundation. H0 means Hardware UX Gate.
- Fast provider selection is manual. Gemini Live is the initial baseline; a second real provider is optional validation, not a mandatory MVP gate.
- OpenClaw agent is the first slow-task backend.

## Upstream baseline

- Kai v0.12.0, commit 1681fb3c11acd28525d8987bba4b9bf4bc2a288f.
- LICENSE: MIT; preserve copyright and license notices when reusing code.
- kai-sim source-inspected; end-to-end simulator runtime remains unverified.
