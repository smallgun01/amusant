# Hermes Native Runs — local POC

This isolated spike tests the pinned Hermes Native Runs API against a deterministic fake model provider. It does not modify SPEC/ADR or deploy production Relay. The Docker network is internal-only and no ports are published. No AWS, Gemini Live, real model credentials, paid model API, Docker socket, or home-directory mounts are used. The API keys are generated in memory by the runners and are not written to files.

The fake provider returns a fixed local marker and records only bounded request metadata; it does not store prompts. The pinned image reference is the Kai v0.12.0 integration image: Hermes v2026.9.24 at sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7.

## Phase 2A — Hermes API-only G1–G4

All four local API gates passed. The sanitized summary is evidence/phase2a-gates-sanitized.json.

- G1: create a Run, poll it to completion, retrieve the deterministic result.
- G2: same idempotency key and payload returns the same Run ID; changed payload conflicts; no additional fake completion.
- G3: a new disposable client recovers the completed result using the saved Run ID.
- G4: a completed Run survives Hermes restart on the same named volume; an active Run becomes interrupted and is not replayed after Hermes restart.

Run with Python 3 and Docker Compose v2: python3 run_poc.py

## Phase 2B — Relay adapter POC

Phase 2B imports the actual Relay adapter and TaskStore into a disposable container. It keeps delegate_task, check_task_status, AsyncDispatcher, the existing task ledger and Inbox. The default remains OpenAB ACP; Hermes is selected only by the server-owned AMUSANT_SLOW_EXECUTOR=hermes_runs setting plus the required Hermes URL and API key. No client/model field selects a URL, key, executor, or session key.

The adapter sends one POST /v1/runs with Idempotency-Key=task_id, persists the returned Run ID write-once on the existing task, and polls GET /v1/runs/{id}. It derives an opaque stable Hermes session key from the authenticated Relay owner identity. A resumed task with a known Run ID issues GET only. Ambiguous submission without a durably confirmed Run ID becomes uncertain and is not replayed.

Final local Docker outcomes are in evidence/phase2b-gates-sanitized.json:

- relay_submit_mapping: PASS — one Relay POST; Run ID persisted while fake execution was active.
- relay_process_restart_recovery: PASS — a new Relay process recovered the result from the same SQLite volume; all recorded Relay HTTP calls after restart were GET, with no second POST.
- ambiguous_submit_fail_closed: PASS — Hermes accepted the POST but the test dropped the response; Relay stored no Run ID, left the task uncertain across recovery, and made zero dispatches after restart.

The fake provider recorded multiple provider completions during each Hermes Run (2 in the recovery case, 2 in the ambiguous case). Those are Hermes-to-provider requests, not Relay POST submissions; the Relay HTTP-method trace independently shows one initial POST and GET-only recovery. Do not interpret the provider count as an additional Relay Run submission.

Run Phase 2B locally with: python3 run_phase2b.py

A harness-only initial attempt failed because the new SQLite volume was root-owned; the runner now initializes that isolated volume for UID 10000. That setup failure was not an API/Relay behavior result. The current summary contains only sanitized metrics; attempt files are ignored by Git.

## Limits and safety

- Local Docker/fake-provider evidence is not production acceptance or AWS/Fargate evidence.
- No real model, billing, end-user tool effects, AWS, Fargate, or EFS behavior was tested.
- Hermes Run status/idempotency retention is not a substitute for Relay's existing Inbox retention; the 30-day Relay result policy remains Relay-owned.
- No SSE replay, cancellation API, 30-day retention, multi-owner live access-control test, EFS/WAL safety, or Hermes agent restart reattachment for in-flight work is established here.
- An ambiguous POST with no stored Run ID can leave an orphan Run on Hermes. Relay deliberately reports uncertain rather than risk duplicate side effects.
- The DynamoDB 100-TASK-row scan bound and Dispatcher remain unchanged.

Named Hermes and Relay state volumes are retained. The runners stop containers but never remove volumes. Do not run docker compose down -v for this POC.
