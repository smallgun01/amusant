> **New OCI target (2026-10-18):** [ADR-0003](../docs/adr/0003-oci-a1-hermes-sqlite.md) selects existing `SQLiteTaskStore` for persistent OCI block storage; Hermes Native Runs is the code-default Slow executor. DynamoDB remains a supported AWS legacy path. The sections below about OpenAB/two ECS and historical one-offs describe prior/tested stages; they do not supersede the current OCI target. Production requires an off-host backup/restore drill, safe retention and a reviewed ingress plan.

# P1 Mock Relay

This is a small, hand-written P1 test probe for the Kai simulator's minimal WebSocket/audio path. It is not a fork or full Kai relay. Source-level comparison used Kai commit `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`; the same-LAN kai-sim audio loop and one reconnect smoke have passed; reported manual release-to-first-audio and reconnect times were under one second, but are not statistically robust measurements. It does not call a model, use OpenClaw, run tools, or persist audio. It returns a short tone plus a locally echoed/resampled copy of the PTT audio, with a text/card response.

## Local private server

The server defaults to loopback. For a same-LAN test, supply the server's current private LAN IP; never bind to 0.0.0.0 or a public address. The IP is intentionally not stored in this repository.

~~~bash
# Run from the Amusant repository root
uv sync --project relay
read -rp 'Server private LAN IP: ' RELAY_LAN_IP
read -rsp 'Local mock test token: ' AMUSANT_MOCK_TOKEN; echo
export AMUSANT_MOCK_TOKEN
uv run --project relay python relay/mock_relay.py --host "$RELAY_LAN_IP" --port 8765
unset AMUSANT_MOCK_TOKEN RELAY_LAN_IP
~~~

Use the same locally chosen test-only token on both hosts; do not commit or reuse it for production. This plain ws:// link is for trusted home-LAN testing only. No firewall rule or public port forwarding is configured. The process runs in the foreground; stop with Ctrl-C. No systemd service is installed.

## Home computer (same LAN)

From the Kai checkout pinned in SPEC.md, in its relay directory:

~~~powershell
uv sync --extra sim
uv run kai-sim --list-devices
$relayHost = Read-Host "Enter the mock server's private LAN IP"
$env:KAI_DEVICE_TOKEN = Read-Host "Enter the same local mock test token"
uv run kai-sim --host $relayHost --port 8765
~~~

Press Enter to start PTT and press Enter again to send. Expected: a card/caption, a short tone, and a local audio echo. This checks audio/protocol transport only; it is not a Live-provider latency test.

The test server binds to a private IP or loopback only, requires Authorization bearer token, caps each incoming WebSocket frame and the buffered utterance, and logs byte counts/timing only. It never writes audio to disk.

## P2 Live bridge harness (local development only)

`live_bridge.py` is separate from the no-model P1 mock. It accepts the Kai-sim `/ws` protocol with a bearer token, buffers the current PTT utterance in RAM, and forwards mono PCM16LE 16 kHz audio to the selected `LiveProvider`. It returns output captions, chunked mono PCM16LE 24 kHz audio, and `turn_complete`. A Live session is opened only after at least 300 ms of PTT audio; short taps do not create a Live session. The server binds only to loopback or an explicit private LAN IP. Audio and transcripts are not persisted. `python -m relay.live_bridge` now composes FAST audio with durable `delegate_task` admission and fails closed if server-owned identity, backend, or SQLite path are missing.

Run from the Amusant repository root on the private host. Inject the API key into the process environment from the approved Secrets Manager flow; never paste it into this file or shell command history. `AMUSANT_DEVICE_TOKEN` must match the Kai-sim client's `KAI_DEVICE_TOKEN`. `AMUSANT_CLIENT_ID` is the server-owned identity bound to that token; never take it from a client hello. `AMUSANT_BACKEND_ID` and `AMUSANT_TASK_DB` are also server-owned; the database path must be absolute and its parent directory must already exist. The installed SDK warns if both `GOOGLE_API_KEY` and `GEMINI_API_KEY` are present; its explicit `api_key` argument takes precedence, but unset ambient `GOOGLE_API_KEY` in this dedicated bridge process to keep selection unambiguous.

~~~bash
read -rp 'Private LAN IP (or 127.0.0.1): ' RELAY_LAN_IP
read -rsp 'One-time device test token: ' AMUSANT_DEVICE_TOKEN; echo
read -rp 'Server-owned client ID: ' AMUSANT_CLIENT_ID
read -rp 'Server-selected backend ID: ' AMUSANT_BACKEND_ID
read -rp 'Absolute SQLite task DB path: ' AMUSANT_TASK_DB
unset GOOGLE_API_KEY
export AMUSANT_LIVE_PROVIDER=gemini
export AMUSANT_DEVICE_TOKEN AMUSANT_CLIENT_ID AMUSANT_BACKEND_ID AMUSANT_TASK_DB
# GEMINI_API_KEY must be injected securely by the local secret-loading process
uv run --locked python -m relay.live_bridge --host "$RELAY_LAN_IP" --port 8765
unset AMUSANT_DEVICE_TOKEN AMUSANT_LIVE_PROVIDER AMUSANT_CLIENT_ID AMUSANT_BACKEND_ID AMUSANT_TASK_DB RELAY_LAN_IP
~~~

On the Kai-sim laptop (same trusted LAN), use that same one-time token:

~~~powershell
$relayHost = Read-Host "Enter the bridge host's private LAN IP"
$env:KAI_DEVICE_TOKEN = Read-Host "Enter the same one-time device test token"
uv run kai-sim --host $relayHost --port 8765
~~~

This is a local PTT-to-Live test harness, not production relay deployment; do not expose it publicly or add firewall/port-forward rules. Fake-provider tests do not call Gemini; a real Kai-sim turn does incur provider usage and must stay within the separately approved budget.

## Dispatcher admission wakeup and fallback polling

The supported Relay runtime keeps one in-process Dispatcher. After durable admission is confirmed—including an idempotent duplicate receipt—the router notifies the Dispatcher only after the to_thread store call has returned to the asyncio owner loop. Cross-thread callers use call_soon_threadsafe; rejected or unavailable admissions do not notify. The loop clears its wake event before draining, so notifications arriving around an empty claim are not lost, and it drains the durable outbox before waiting again.

A bounded fallback poll recovers commits whose admission response timed out or was ambiguous. Startup recovery is owned once by dispatcher.start() when ACP is configured; without a Dispatcher, runtime composition performs the one recovery itself. AMUSANT_DISPATCHER_POLL_INTERVAL_SECONDS optionally overrides the idle fallback and must be finite, positive, and at most 60 seconds. Defaults are **1 second for SQLite** and **15 seconds for DynamoDB**, so worst-case discovery is bounded by the interval plus scheduling/query latency. DynamoDB idle polling uses a strongly consistent Query over the owner TASK# partition (not a Scan); offline query counters are not a dollar-cost estimate. This preserves one Dispatcher and the existing two-ECS architecture; it adds no SQS, GSI, or worker service.

## P4-T4b WSS preflight image entrypoint (bounded live PASS 2026-10-13)

The root Relay Dockerfile's one-off command is `python -m relay.wss_preflight_smoke`: it requires `AMUSANT_TASK_STORE=dynamodb`, an owner-approved fresh `AMUSANT_CLIENT_ID` in the `p4-t4b-<unique-run-id>` namespace supplied as a RunTask override (the task-definition template deliberately contains `REPLACE_RUN_CLIENT_ID`), the current Jellii private IPv4 as a runtime-only `AMUSANT_ACP_EXPECTED_PRIVATE_IP` override, `wss://...:8443/acp`, the ACP bearer secret, and the public private-CA certificate in `AMUSANT_ACP_CA_PEM`. The wrapper verifies the expected client partition is empty using a strong TASK# query and rejects custom DynamoDB endpoints and plaintext mode.

Before the existing `relay.acp_smoke` flow admits anything, this wrapper strongly checks that the fresh client partition has no TASK rows, verifies the Cloud Map A answer matches the expected current Jellii task address, then runs bounded no-prompt TLS/SAN, missing/invalid Bearer, non-`/acp` 404, and TCP/8080-unreachable probes. It passes `AMUSANT_ACP_EXPECTED_PRIVATE_IP` to the production executor, which verifies the dispatch-time DNS result still matches that IP before dialing, then pins that IP while keeping the original hostname for TLS/SNI. Any failed or indeterminate check exits without admission or dispatch. Only a complete probe pass calls `relay.acp_smoke` once, which can send at most one `session/prompt`. The wrapper emits sanitized probe names/results and never logs the expected IP, credentials, raw prompt, or result body.

This bounded entrypoint ran **once in a separately authorized AWS Fargate Live window**: 8/8 preflight PASS, one ACP smoke completed, Relay STOPPED, then same-task independently read-only strongly consistent DynamoDB `store_get` PASS. Jellii was rolled back to Revision 9/0/0/0. [Acceptance evidence](../docs/p4-t4b-live-acceptance.md). This does **not** mean it is the correct persistent Relay Service A loop or that Device/Voice E2E passed. The code PR itself authorizes no future AWS mutation or model call; offline tests still use fake fixtures.

## Slow Path adapter test command (offline only)

From the Amusant repository root, run:

~~~bash
PYTHONPATH=.:relay uv run --locked python -m unittest discover -s relay/tests -v
~~~

This suite includes the P1 mock relay, P3 Slow Path, P4-A1 ACP-only smoke, packaging pins, and ADR-0002 store tests (in-memory fake ledger plus moto). Set `AMUSANT_TASK_STORE=dynamodb` only with `AMUSANT_DDB_TABLE`. ACP tests use a local fake `/acp` WebSocket. Default Inbox/smoke paths use local SQLite. Nothing here creates a DynamoDB table in AWS or calls Gemini, Jellii, or OpenAB.

`python -m relay.acp_smoke` is the ACP-only one-shot: it reuses `SQLiteTaskStore`, `AsyncDispatcher`, `OpenAbAcpExecutor`, and Inbox. Exit 0 only when one admitted task is `completed` with a bounded Inbox result. `failed`/`uncertain` are non-zero. `AMUSANT_ACP_CWD` is sent to the remote runtime and is not required to exist on the Relay. `wss://` verifies the default system roots and hostname. Optional `AMUSANT_ACP_CA_PEM` appends a PEM trust anchor to the default context without replacing system roots; it is accepted only with `wss://` and is public trust material, never a private key. Private DNS is resolved and pinned per ACP connection, so Relay startup doesn't require a sleeping backend to have a DNS record; a dispatch with no private answer fails closed before prompt. Non-loopback `ws://` requires `AMUSANT_ACP_ALLOW_PLAINTEXT=1`. ACP hosts are loopback or RFC1918 only (not link-local/metadata). The executor uses one finite wall-clock deadline, capped at 120s. Do not set `AMUSANT_LIVE_PROVIDER=gemini` for this entrypoint. Never forward audio to ACP. A 2026-10-12 one-off Fargate run against Jellii recorded exit 0 with a completed Inbox result, then scaled Jellii back to 0. That live path used same-SG plaintext `ws://` `/acp` as a one-off risk exception, not TLS, and does not survive task replacement. Sanitized evidence is in `STATUS.md`.

## Hermes Native Runs durable polling and Run deadline (Issue #58A)

Hermes timing uses independent clocks; the existing TaskStore and Inbox remain authoritative. Hermes mode requires all four server-owned values below to be explicitly configured. No candidate product values are supplied as defaults:

- `AMUSANT_MAX_TASK_TTL_SECONDS` remains the admission-side queue TTL cap. For Hermes it prevents an unclaimed task from starting after expiry; it does not expire a task after a Run ID is durably known. OpenAB ACP retains its legacy expiry behavior.
- `AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS` bounds one HTTP request.
- `AMUSANT_HERMES_POLL_INTERVAL_SECONDS` sets the delay before the next GET and is persisted as `next_poll_at`.
- `AMUSANT_HERMES_POLL_CYCLE_SECONDS` bounds one local execute/poll cycle. Reaching it yields the task for a later GET; it is not the Run deadline.
- `AMUSANT_HERMES_RUN_DEADLINE_SECONDS` sets the overall Run lifetime. Its absolute `run_deadline_at` is stored with the Run ID and is not reset by a Relay restart. The current anchor is the durable first-dispatch timestamp immediately before the first POST.

Missing or invalid timing settings fail startup. Do not copy candidate values from Issue #58 into a product environment without Owner approval. The previous `AMUSANT_HERMES_TIMEOUT_SECONDS` setting is rejected because it conflates timing semantics.

The Relay writes the Run ID and deadline before polling. A GET timeout, network failure, HTTP 408/429, or 5xx increments attempts and schedules another poll; a known Run remains resumable and recovery performs GET only. An ambiguous POST before a Run ID is durable remains `uncertain` and is never blindly resent. At the overall deadline without confirmed cancellation, the persisted terminal uncertainty code is surfaced by Inbox as policy status `timed_out` with a possible-remote-activity disclosure; it is not reported as cancelled or completed.

A poll-cycle yield atomically releases the Dispatcher claim while retaining the task as `running`, its write-once Run ID, absolute deadline, and next-poll time. `claim_due` reclaims a known Run only when persisted `next_poll_at` or the Run deadline is due. The background loop schedules the next wake from the earliest stored poll/deadline, with the bounded idle fallback for missed notifications. `_dispatch_one` does not wait through the Run lifetime, so other Slow Tasks and FAST operations can proceed. Recovered known-Run polls do not consume the first-submission `max_attempts` budget. HTTP uses a worker thread; the poll interval holds no blocking thread.

The timing fields live on the existing SQLite task row / DynamoDB task item; no second TaskStore or Inbox is introduced. These changes do not implement Issue #58B concurrency or queue-capacity policy, and offline/fake-provider tests are not production or deployment acceptance.
