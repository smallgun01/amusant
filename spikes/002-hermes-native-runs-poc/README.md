# Phase 2A — Hermes Native Runs API local POC

This is an isolated test harness on branch spike/hermes-runs-phase-2a. It does not import or call Amusant Relay code. It uses the exact Hermes image reference pinned by Kai v0.12.0, a deterministic local OpenAI-compatible stub, synthetic prompts, disposable client containers, and an internal Docker network with no external route. No ports are published to the host.

No AWS credentials, home-directory mounts, real model credentials, Gemini Live, external model APIs, or Docker socket are used. The only network fetch is the pinned public Hermes container image. API keys for the POC are generated in memory at runtime and are not written to files. The fake provider returns one fixed marker and never records prompt text.

## G1–G4 final result

All four local gates PASS. The sanitized, machine-readable summary is in evidence/phase2a-gates-sanitized.json. The runner also creates local raw evidence files with ephemeral Hermes Run IDs; those files are ignored by Git and are not part of the PR.

- G1: create a Run, query to terminal status, recover the deterministic result.
- G2: repeat the same Idempotency-Key and payload, then reuse that key with a changed payload. Check Run identity, conflict, and fake-completion count.
- G3: submit a delayed synthetic Run, let the client container exit, then start a new client container and recover the result by run_id.
- G4: recover a completed Run after restarting Hermes with the same persistent home volume; restart Hermes during a delayed fake request and verify interrupted status with no second fake completion.

Run from this directory with Python 3 and Docker Compose v2: python3 run_poc.py

The named Hermes volume is intentionally retained. The runner stops containers but does not remove the volume. Do not run docker compose down -v for this POC.
