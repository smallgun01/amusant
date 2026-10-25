# Grok Bot validation spike (G2 offline-only)

This isolated spike validates one bounded result path without changing Relay, Facade, Hermes, production data, CI, or PR #120. The applicable stage gate and Owner-reported evidence are in [`grok-bot-validation-v0.3.md`](../../docs/project-management/grok-bot-validation-v0.3.md); unmodified contract and security requirements remain in [v0.2](../../docs/project-management/grok-bot-validation-v0.2.md).

G1 is Owner-reported as four PASS cases and accepted. This code is only G2 offline evidence. It does not establish Grok account access, a real Routine run, a deployed Remote MCP/OAuth endpoint, native Run completion, or a Relay integration. G3 remains unstarted and requires its own environment, permissions, limits, and Owner review.

## Scope and flow

The spike contains a bounded report validator, an independent SQLite store, a dry-run-by-default CLI, a single MCP SDK `submit_result` tool, a one-shot HTTPS Routine Webhook client gated by `--live`, a redacted evidence summary, and focused offline tests.

The Golden Path in `tests/test_offline.py` runs this sequence on ephemeral loopback ports and a temporary SQLite file:

1. Prepare a task and a random per-task result capability; persist only its SHA-256 hash.
2. POST once to a local Mock Webhook. Its fixed side-effect-free business fixture runs once and returns a report.
3. Use the official MCP Python SDK client to initialize, list tools, and call `submit_result` against the SDK Streamable HTTP server with a fake Owner bearer token.
4. Commit the immutable report and receipt in one SQLite transaction before the tool returns its acknowledgement.
5. Replay the same MCP result to simulate a lost acknowledgement. It receives the same receipt; the Mock Webhook and business fixture counts stay at one.
6. Reopen SQLite and use the actual CLI `readback` command. It returns the same receipt, while native state remains `unknown`.
7. Restart the MCP service with a newly opened store and replay once more. The same receipt returns and the business fixture still has one execution.

No test contacts xAI, Grok, Cursor, a Routine endpoint, or an external MCP server. The MCP verifier used by this local test is a fixed fake Owner token, not OAuth and not suitable for deployment. Remote HTTPS MCP/OAuth remains the G3 primary. The per-task HTTPS callback fallback is not implemented as a live route.

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
& $pilot close --db $db --task-id $taskId --reason 'Owner review complete'
& $pilot revoke --db $db --task-id $taskId
Remove-Item Env:GROK_PILOT_RESULT_TOKEN
Remove-Variable prepared
```

The future one-shot Routine POST is implemented behind `--live`; it requires an HTTPS URL and both private environment values. The Routine ingress credential is separate from the per-task result token. After a separately authorized G3 GO, the syntax would be:

```text
grok-pilot submit --db <private G3 database> --task-id <new G3 task id> --routine-url https://owner-provided.example/routine --live
```

**Do not run that `--live` command as part of G2.** It is documented for a later, separately authorized G3 only. It performs at most one POST, disables redirects, has no automatic retry, and preserves `acceptance_unknown` after timeout, crash, or redirect. A second send attempt is refused. This code has not been validated against a real Grok Routine contract or endpoint.

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
"$pilot" close --db "$db" --task-id "$task_id" --reason 'Owner review complete'
"$pilot" revoke --db "$db" --task-id "$task_id"
unset GROK_PILOT_RESULT_TOKEN
unset prepared
```

Only after a separate G3 GO, an operator could provide `GROK_PILOT_ROUTINE_BEARER_TOKEN` and `GROK_PILOT_RESULT_TOKEN` through a private process environment/secret manager, then use the syntax above with a new G3 task. Do not put either credential in command arguments or the database.

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

G3 would need an independently reviewed HTTPS host reachable by the platform connector, Remote MCP/OAuth with the smallest Owner scope, explicit limits and cases, credential revocation, and rollback. The static Owner verifier in this spike is test-only. Do not expose it through a tunnel or public interface. Do not deploy this spike or call the Routine endpoint without a separate G3 authorization. G4 still requires Hermes physical G0 evidence plus relevant G3 evidence under v0.3.
