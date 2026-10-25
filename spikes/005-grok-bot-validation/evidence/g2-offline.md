# G2 local offline evidence

Date: 2026-10-25 (Asia/Taipei)

Source baseline: `origin/main` at `33b65627175efd70b3cb8063f9d4fe091f81ff20`.

## Commands and results

| Command | Result |
| --- | --- |
| `py -3.13 -m uv --cache-dir spikes/005-grok-bot-validation/.uv-cache sync --locked --python 3.13 --project spikes/005-grok-bot-validation` | PASS — lock resolved and all 30 packages were checked; no provider was contacted. |
| `.venv\Scripts\python.exe -m unittest discover -s tests -v` | PASS — 15 tests; 13 passed and the 2 POSIX-only permission tests were skipped on this Windows host. |
| `git diff --check` | PASS. |

The Golden Path used ephemeral `127.0.0.1` HTTP servers and a temporary SQLite file under ignored `.test-tmp/`. It completed **Prepare → Mock Webhook → MCP `submit_result` → SQLite receipt → CLI readback**. It simulated an acknowledgement-loss retry by submitting the exact same report again, restarted the MCP service with a reopened store, then replayed the report after restart. Every acknowledgement contained the same receipt ID, the Mock Webhook received one POST, its fixed business fixture ran once, and CLI readback left `native_state=unknown`.

The sender success-path test used `routine.submit_once` with `MockTransport`: exactly one POST moved `sending` to `accepted`; an observer opened the store and read CLI list/readback while the send was active, and an early durable result remained readable. The crash test ran a child process that exited abruptly after persisting `sending`; reopening left the state unchanged, explicit CLI recovery changed it to `acceptance_unknown`, and another submit attempt made zero POSTs. The suite also exercised token-free key-order-independent report digests, unknown fields, control characters, UTF-8 argument overflow, Owner/task-token checks, token hash-at-rest, write-once conflict, late reports after manual close, token revocation, storage failure before acknowledgement, dry-run-by-default CLI behavior, redirect refusal, no resend, exact MCP bearer HTTP 401, the SDK body-size 413, and durable readback.

Linux-only permission tests verify a private mode `0700` directory, mode `0600` database/WAL/SHM files, and refusal of broad existing paths. They are configured in the new focused Ubuntu CI step and were skipped locally because this host is Windows. The CI step has not executed while this PR is Draft, so it is not claimed as evidence.

## Not tested

- Real Grok account, model, Routine Webhook, external MCP host, or OAuth provider.
- Remote HTTPS reachability, real MCP/OAuth credential scope, or native Grok Run state.
- G3 live deployment or G4 Relay/StickS3 integration.
- CI: the focused locked-dependency step was added to the existing offline-regression job; it has not run while this PR is Draft.

Only fake Owner credentials and fixed mock task data were used. The test database, bearer token, and result capabilities are not committed.
