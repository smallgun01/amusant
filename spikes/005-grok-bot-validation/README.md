# Grok Bot validation spike (G2 baseline, G3-P1 offline-only)

This isolated spike validates one bounded result path without changing Relay, Facade, Hermes, production data, or PR #120. The existing offline CI job runs this spike with its own locked dependencies. The applicable stage gate and Owner-reported evidence are in [`grok-bot-validation-v0.3.md`](../../docs/project-management/grok-bot-validation-v0.3.md); unmodified contract and security requirements remain in [v0.2](../../docs/project-management/grok-bot-validation-v0.2.md).

G1 is Owner-reported as four PASS cases and accepted. The merged G2 spike remains the result-contract and SQLite receipt baseline. This branch adds only the G3-P1 offline callback adapter, TTL handling, and local tests. It does not establish Grok account access, a real Routine run, a deployed callback or Remote MCP/OAuth endpoint, native Run completion, or a Relay integration. G3-P2 live work remains unstarted and requires its own environment, permissions, limits, and Owner review.

## Scope and flow

The spike contains a bounded report validator, one independent SQLite store, a dry-run-by-default CLI, a single MCP SDK `submit_result` tool, a one-shot HTTPS Routine Webhook client gated by `--live`, a single-route ASGI callback adapter, an explicit foreground callback runner that binds only to loopback, a redacted evidence summary, and focused offline tests. `serve-callback` requires explicit Owner, Host allowlist, port, and concurrency settings; tests replace the runner and never start it. `submit --live --callback-mode` requires a valid task capability with a finite TTL before changing the durable state to `sending`; ordinary G2 `submit --live` behavior remains compatible when that explicit flag is absent.

The Golden Path in `tests/test_offline.py` runs this sequence on ephemeral loopback ports and a temporary SQLite file:

1. Prepare a task and a random per-task result capability; persist only its SHA-256 hash.
2. POST once to a local Mock Webhook. Its fixed side-effect-free business fixture runs once and returns a report.
3. Use the official MCP Python SDK client to initialize, list tools, and call `submit_result` against the SDK Streamable HTTP server with a fake Owner bearer token.
4. Commit the immutable report and receipt in one SQLite transaction before the tool returns its acknowledgement.
5. Replay the same MCP result to simulate a lost acknowledgement. It receives the same receipt; the Mock Webhook and business fixture counts stay at one.
6. Reopen SQLite and use the actual CLI `readback` command. It returns the same receipt, while native state remains `unknown`.
7. Restart the MCP service with a newly opened store and replay once more. The same receipt returns and the business fixture still has one execution.

No test contacts xAI, Grok, Cursor, a real Routine endpoint, or an external MCP server. The MCP verifier used by the G2 test is a fixed fake Owner token, not OAuth and not suitable for deployment.

## G3-P1 callback adapter (offline only)

The offline ASGI adapter accepts exactly `POST /callback/results`. It maps every accepted task to the locally configured canonical Owner, accepts a token-free JSON report plus the task-scoped capability in `Authorization: Bearer`, and uses the existing `PilotStore.submit_report` transaction and immutable receipt. It creates no second task ledger. A receipt ACK is returned only after SQLite commits. Before validation or persistence, it rejects any report whose string values or object keys contain the exact task capability; this is best-effort data hygiene, not proof that the capability is secret from the Bot.

`grok-pilot prepare` has an optional `--result-token-ttl-seconds` value with no default. Callback tasks require an explicit finite TTL from 1 through 86,400 seconds; legacy G2 rows without an expiry fail closed for callback submission. Revoked or expired capabilities are checked inside the same `BEGIN IMMEDIATE` transaction before both first insert and duplicate handling. Previously committed receipts remain available to CLI readback after expiry or revocation.

The adapter bounds request bodies at 16 KiB, requires JSON and one Bearer header, rejects query strings and duplicate JSON keys, checks an exact configured Host allowlist and configured service port, ignores forwarded headers, and caps concurrent requests per ASGI app instance at a required 1–32 setting. Responses are bounded: `200` means a committed first receipt or identical replay; `400` malformed or disallowed request; `401` invalid task capability/Owner/expiry/revocation (kept indistinguishable); `404` unknown route; `405` method mismatch; `409` a different report already has a receipt; `413` body too large; `415` unsupported media type/encoding; `429` the ASGI in-flight cap; and `503` receipt storage or runner capacity is unavailable. The `max_inflight` setting limits concurrency, not request rate. The ASGI factory does not start a listener; only an explicit `serve-callback` invocation runs the foreground Uvicorn process, hard-bound to `127.0.0.1`, with access logging and forwarded-header handling disabled. Tests replace the runner and use in-process ASGI transports and temporary SQLite only. No real listener, public ingress, proxy, tunnel, TLS endpoint, Routine, OAuth flow, or provider is configured or started.

Callback-first is the current **validation test order only**, not a final product route selection. Remote HTTPS MCP/OAuth remains an independent, gated candidate (conditional Plan B in this validation sequence); it is neither implemented here nor declared obsolete. This offline PR does not prove that Grok can reach a callback or that a Routine can deliver the per-task capability privately. A task-scoped capability may be visible in the Bot's execution context; an Authorization header does not make it secret from the Bot. Exact echo rejection only prevents an identical capability value from being persisted in a report; it cannot prevent the Bot or its environment from seeing the capability. Long-lived Owner or ingress credentials must not enter prompts or task payloads. Routine ability, approvals, egress, delivery and exposure are NOT TESTED and require separate G3-P2 observation and approval.

The callback Golden Path in `tests/test_callback.py` is **Prepare → Mock Webhook → `POST /callback/results` → SQLite durable receipt → CLI readback**. It replays only the same callback result to model a lost acknowledgement; it asserts exactly one Mock Webhook POST and one fixture execution, with `native_state=unknown`.

The explicit local runner syntax is `grok-pilot serve-callback --db <private local database> --owner-id <canonical Owner> --allowed-host 127.0.0.1 --port 8765 --max-inflight 4`. This command is foreground-only and hard-binds to `127.0.0.1`; it is not part of startup or tests. The CLI configuration test replaces `uvicorn.run`, so this G3-P1 evidence does not start a listener. `--callback-mode` is likewise an explicit guard on a later `submit --live`, not authorization to run a Routine in this stage.

If a future public URL differs from `/callback/results`, a separately reviewed HTTPS reverse proxy must explicitly map that public route to this exact path, set the upstream Host to a configured allowlist entry, enforce the Host policy, apply request-rate limits and connect/read/write timeouts, strip untrusted `Forwarded` and `X-Forwarded-*` headers, and preserve the Authorization header end-to-end without logging credentials or request bodies. The callback concurrency cap is not a substitute for proxy rate limiting. No proxy mapping or public route is selected or enabled by this PR; all of these remain G3-P2 review items.

## Windows setup and CLI

From PowerShell, in the spike directory:

```powershell
py -3.13 -m uv sync --locked
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Use a private local data directory outside the repository. `prepare` prints the result capability once; protect the terminal and keep that value out of shell history, logs, evidence, and Git.

```powershell
$db = Join-Path $env:LOCALAPPDATA 'Amusant\GrokPilot\pilot.sqlite3'
$pilot = '.\.venv\Scripts\grok-pilot.exe'
$prepared = & $pilot prepare --db $db --label G2-D1 --instruction 'Run one approved task.' | ConvertFrom-Json
$taskId = $prepared.task_id
$env:GROK_PILOT_RESULT_TOKEN = $prepared.result_token

# Default: local dry-run only; no HTTP request is made.
& $pilot submit --db $db --task-id $taskId --routine-url 'https://owner-provided.example/routine'

& $pilot list --db $db
& $pilot readback --db $db --task-id $taskId
& $pilot evidence --db $db
# Optional after a sender crash and after verifying its PID has exited:
# & $pilot recover --db $db --task-id $taskId
& $pilot close --db $db --task-id $taskId --reason 'Owner review complete'
& $pilot revoke --db $db --task-id $taskId
Remove-Item Env:GROK_PILOT_RESULT_TOKEN
Remove-Variable prepared
```

The future one-shot Routine POST is implemented behind `--live`; it requires an HTTPS URL and both private environment values. The Routine ingress credential is separate from the per-task result token. Only after a separately authorized G3-P2 live GO, the syntax would be:

```text
grok-pilot submit --db <private G3 database> --task-id <new G3 task id> --routine-url https://owner-provided.example/routine --live --callback-mode
```

**Do not run that `--live` command as part of G2 or this G3-P1 offline task.** It is documented for a later, separately authorized G3-P2 live stage. It performs at most one POST, disables redirects, has no automatic retry, and preserves `acceptance_unknown` after timeout or redirect. If the sender process crashes while the durable state is `sending`, opening the store leaves that state unchanged. After confirming the recorded sender process has exited, an operator may explicitly run `grok-pilot recover --db <private database> --task-id <task id>`; this only changes the state to `acceptance_unknown` and never sends again. Active or unverifiable sender PIDs fail closed. A second send attempt is refused. This code has not been validated against a real Grok Routine contract or endpoint.

On POSIX systems, the database directory must be a real directory with mode `0700`; the database, WAL, SHM, and journal files are protected with mode `0600`. Existing databases or parent directories with broader access are refused instead of silently accepted. On Windows, use the current user's private `%LOCALAPPDATA%` directory; the database inherits its Windows ACL, which this G2 code does not inspect. Windows ACL verification remains a separate review item.

## Ubuntu setup and CLI

From the spike directory, with Python 3.11+ and `uv` available:

```bash
python3 -m uv sync --locked
.venv/bin/python -m unittest discover -s tests -v
```

Keep the database and one-task token in a private local directory and process environment:

```bash
db="${XDG_STATE_HOME:-$HOME/.local/state}/amusant-grok-pilot/pilot.sqlite3"
pilot=".venv/bin/grok-pilot"
prepared="$($pilot prepare --db "$db" --label G2-D1 --instruction 'Run one approved task.')"
task_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])' <<< "$prepared")"
export GROK_PILOT_RESULT_TOKEN="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["result_token"])' <<< "$prepared")"

# Default: dry-run; it makes no HTTP request.
"$pilot" submit --db "$db" --task-id "$task_id" --routine-url 'https://owner-provided.example/routine'
"$pilot" list --db "$db"
"$pilot" readback --db "$db" --task-id "$task_id"
"$pilot" evidence --db "$db"
# Optional after a sender crash and after verifying its PID has exited:
# "$pilot" recover --db "$db" --task-id "$task_id"
"$pilot" close --db "$db" --task-id "$task_id" --reason 'Owner review complete'
"$pilot" revoke --db "$db" --task-id "$task_id"
unset GROK_PILOT_RESULT_TOKEN
unset prepared
```

Only after a separate G3-P2 live GO, an operator could provide `GROK_PILOT_ROUTINE_BEARER_TOKEN` and `GROK_PILOT_RESULT_TOKEN` through a private process environment/secret manager, then use the syntax above with a new G3 task. Do not put either credential in command arguments or the database.

## Local verification

Run the focused suite with:

```text
Windows: .venv\Scripts\python.exe -m unittest discover -s tests -v
Ubuntu:  .venv/bin/python -m unittest discover -s tests -v
```

The checked-in test evidence records the actual local results and limits. Draft CI jobs that skip these tests are not evidence of a pass. A Ready PR must follow the repository's existing offline CI gate before merge.

## Rollback and cleanup

The spike does not create a listener by itself and has no background process. The `submit_result` service is only instantiated by tests in this G2 package. For an authorized future local test, stop the foreground process, revoke the per-task token with the CLI, unset both credential environment variables, and remove only the selected experiment database plus its `-wal`/`-shm` companions. Never point `--db` at a production database. No production rollback is needed because G2 changes no production path.

## Future handoff boundary

G3-P2 would need a separately selected non-production host and independently reviewed HTTPS route, capability delivery, limits, retention, revocation, and rollback. Callback reachability and Routine-context behavior are **NOT TESTED** by this offline work. Remote MCP/OAuth remains independently gated and requires its own exact Owner scope and test evidence if selected. The static Owner verifier in the MCP G2 test is test-only. Do not expose it through a tunnel or public interface. Do not deploy this spike or call the Routine endpoint without a separate G3-P2 authorization. G4 still requires Hermes physical G0 evidence plus relevant G3 evidence under v0.3.
