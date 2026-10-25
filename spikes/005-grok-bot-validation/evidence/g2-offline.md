# G2 local offline evidence

Date: 2026-10-25 (Asia/Taipei)

Source baseline: `origin/main` at `33b65627175efd70b3cb8063f9d4fe091f81ff20`.

## Commands and results

| Command | Result |
| --- | --- |
| `python -m uv sync --locked --project . --cache-dir .uv-cache` | PASS — installed locked `mcp==2.3.0` and the isolated spike package; no provider was contacted. |
| `.venv\Scripts\python.exe -m unittest discover -s tests -v` | PASS — 12 focused tests; see the committed Golden Path assertion and offline failure cases. |

The Golden Path used ephemeral `127.0.0.1` HTTP servers and a temporary SQLite file under ignored `.test-tmp/`. It completed **Prepare → Mock Webhook → MCP initialize/tools/list/tools/call → SQLite receipt → CLI readback**. It simulated an acknowledgement-loss retry by submitting the exact same report again, restarted the MCP service with a reopened store, then replayed the report after restart. Every acknowledgement contained the same receipt ID, the Mock Webhook received one POST, its fixed fixture ran once, and CLI readback left `native_state=unknown`.

The focused suite also exercised token-free key-order-independent report digests, unknown fields, control characters, UTF-8 argument overflow, Owner/task-token checks, token hash-at-rest, write-once conflict, late reports after manual close, token revocation, storage failure before acknowledgement, early result arrival, crash recovery from `sending` to `acceptance_unknown`, dry-run-by-default CLI behavior, redirect refusal, no resend, MCP bearer rejection, the SDK body-size 413, and durable readback.

## Not tested

- Real Grok account, model, Routine Webhook, external MCP host, or OAuth provider.
- Remote HTTPS reachability, real MCP/OAuth credential scope, or native Grok Run state.
- G3 live deployment or G4 Relay/StickS3 integration.
- CI: Draft CI skips the heavy offline job; this local command is the G2 test evidence.

Only fake Owner credentials and fixed mock task data were used. The test database, bearer token, and result capabilities are not committed.
