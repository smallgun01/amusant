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

Hermes timing uses independent clocks; the existing TaskStore and Inbox remain authoritative. Hermes mode requires its server-owned timing values to be explicitly configured. No candidate product values are supplied as defaults:

- `AMUSANT_MAX_TASK_TTL_SECONDS` remains the admission-side queue TTL cap. For Hermes it prevents an unclaimed task from starting after expiry; it does not expire a task after a Run ID is durably known. OpenAB ACP retains its legacy expiry behavior.
- `AMUSANT_HERMES_HTTP_TIMEOUT_SECONDS` bounds one HTTP request.
- `AMUSANT_HERMES_POLL_INTERVAL_SECONDS` sets the delay before the next GET and is persisted as `next_poll_at`.
- `AMUSANT_HERMES_POLL_CYCLE_SECONDS` bounds one local execute/poll cycle. Reaching it yields the task for a later GET; it is not the Run deadline.
- `AMUSANT_HERMES_RUN_DEADLINE_SECONDS` sets the overall Run lifetime. Its absolute `run_deadline_at` is stored with the Run ID and is not reset by a Relay restart. The current anchor is the durable first-dispatch timestamp immediately before the first POST.

Missing or invalid timing settings fail startup. Do not copy candidate values from Issue #58 into a product environment without Owner approval. The previous `AMUSANT_HERMES_TIMEOUT_SECONDS` setting is rejected because it conflates timing semantics.

The Relay writes the Run ID and deadline before polling. `poll_attempts` counts GET attempts initiated and durably recorded before the request; STOP POST retries use their separate `stop_attempts` field. A completed GET updates `next_poll_at` from request completion, so provider latency does not shorten the configured interval. `run_deadline_at` and `next_poll_at` are timezone-aware UTC timestamps; the event loop's monotonic clock bounds only one local polling cycle. An ambiguous POST before a Run ID is durable remains `uncertain` and is never blindly resent.

A poll-cycle yield releases the local Dispatcher claim while retaining the owner's Single-flight slot, write-once Run ID, absolute deadline, and next-poll time. Before STOP is requested, `claim_due` can wake for either `next_poll_at` or the Run deadline. After STOP is requested, only `next_poll_at` is due; the expired deadline no longer causes immediate repeated GETs. If uncertainty precedes the deadline, recovery resumes by GET without another Run POST; deadline expiry still raises the first bounded STOP. An Owner Cancel recorded during a recovery GET is likewise preserved and makes its first STOP due. After STOP acceptance or exhausted retry/grace bounds, the task stays busy and scheduled GETs continue until terminal evidence. Only a successful Result Contract v2 status response with the exact persisted `run_id` may establish remote state. A trusted terminal GET closes the task; unknown states, plain 502, failed GET, and mismatched or missing Run bindings keep Single-flight. A confirmed native `completed` Run with missing, malformed, partial/interrupted, or Facade-reported failed presentation remains `completed` with a fixed bounded delivery-failure result; partial presentation is never spoken. Validated Facade artifact state/reference survives presentation failure, including partial/interrupted presentation only when the full v2 envelope validates; malformed output is never used to recover a reference. STOP Accepted remains distinct from Terminal Confirmed. Recovered known-Run GETs do not consume the first-submission `max_attempts` budget. HTTP uses a worker thread; the poll interval holds no blocking thread.

The original OCI replay profile (120s Run deadline, 2s poll interval, 20s STOP grace, 30ms fake Facade GET) exceeded 600 GETs before this scheduling correction. The deterministic replay now exercises the same interval through grace without increasing the Run deadline; each GET increments `poll_attempts` once, while STOP calls do not.

The timing and uncertainty-recovery fields live on the existing SQLite task row / DynamoDB task item; no second TaskStore, queue, worker or lock service is introduced. Current Facade v2 GET responses expose the authenticated owner/run mapping, exact Run ID and terminal status needed for safe recovery. The threat model treats a guessed Run ID, stale owner mapping, forged/missing Run ID in a response, unknown status, or error body as untrusted; only a 200 response for the HMAC-derived owner session and exact persisted Run ID can drive terminal state. The Relay parses only the bounded v2 projection and does not log/store raw provider output. Older Facade error wrappers that return only HTTP 502 without trusted terminal status cannot be used to close a task; supporting those requires an explicit owner-bound `remote_terminal_state` plus exact `run_id`, emitted only after Facade authentication and owner/task/Run lookup. Offline/fake-provider tests are not production or deployment acceptance.

## Generic Agent task delegation (C2)

New `delegate_task` calls provide only `task`, plain-language intent for the Owner-managed Agent. Hermes and Grok task-only calls always store the neutral internal kind `agent`, even when an older environment still sets `AMUSANT_DEFAULT_TASK_KIND=research|compare|summarize`. That setting remains a task-only default for the legacy ACP executor. A stale Live function schema may explicitly send one of the old `task_kind` values on either a first admission or a retry; Relay stores that bounded value as descriptive compatibility metadata, never as tool routing or permission. The selected backend applies its own Owner policy.

SQLite schema v10 adds nullable Grok Callback lifecycle, task-token hash/TTL, wait-deadline, receipt, and late-callback metadata to the existing `slow_tasks` row. It keeps v9 history, Hermes Run/stop state, the shared outbox, results, and Inbox in place. New Grok Callback tasks require the existing SQLite TaskStore; the DynamoDB adapter does not dispatch this executor. Their server-owned identity is `backend='grok_bot'`, `executor_kind='grok_callback'`, and result contract v2. Setting `AMUSANT_DEFAULT_TASK_KIND=agent` is recommended for clarity; legacy values remain accepted for ACP compatibility and explicit stale-schema metadata, but cannot change the kind of a task-only slow-backend call.

Do not roll back only the code after `agent` rows have been written. An older SQLite reader's CHECK constraint can reject updates to those rows, and older application validation may reject them in DynamoDB. Stop new admissions and drain/reconcile active generic tasks. SQLite has no automatic row expiry, so keep an `agent`-capable reader unless a separately reviewed, state-preserving migration/archive handles every generic row. DynamoDB TTL, when configured, is asynchronous and is not an immediate rollback strategy. Preserve the ledger, known Run IDs, stop intent, Artifact metadata, and Inbox results; do not relabel or rehash rows. Immediate rollback needs a separately reviewed data-compatible plan or a matching database/code restore. This change does not migrate production data.

Grok G4-D1 stores its lifecycle in the existing SQLite v10 ledger. Existing runtime data is disposable test data, and a separately authorized staging setup may start with a clean v10 database; legacy preservation and restore drills are not prerequisites. An uncertain Routine Webhook must never be automatically resubmitted. Releasing an in-flight Owner slot requires a valid Callback or explicit Owner Abandon/Reconcile.

### Grok G4-D1 offline adapter

The offline adapter is selected with `AMUSANT_SLOW_EXECUTOR=grok_callback`. It requires `AMUSANT_GROK_ROUTINE_URL`, `AMUSANT_GROK_ROUTINE_TOKEN`, `AMUSANT_GROK_CALLBACK_URL` (HTTPS with the exact `/callback/results` path), and the existing explicit `HTTPS_PROXY` egress setting. `AMUSANT_GROK_CALLBACK_TOKEN_TTL_SECONDS` defaults to 3600 and `AMUSANT_GROK_CALLBACK_WAIT_SECONDS` defaults to 900; both are bounded and the wait deadline cannot outlive the capability. A Routine Webhook is sent once; redirects, timeouts, non-200 replies, and ambiguous outcomes do not trigger retry or Hermes fallback.

`build_grok_callback_receiver_app()` creates a restricted ASGI application for a server-owned Owner mapping. Mounting it requires `AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS`, `AMUSANT_GROK_CALLBACK_SERVICE_PORT`, and optionally `AMUSANT_GROK_CALLBACK_MAX_INFLIGHT` (default 8). The factory does not bind or start a listener. No public ingress, reverse proxy, or TLS termination is configured by this change; the exact HTTPS proxy mapping and deployment remain subject to G4-D2 review.

## Hermes Result Contract v2 (Issue #105)

Every new `hermes_runs` task is pinned to Result Contract v2 without an operator opt-in. Relay emits the v2 prompt and parses only the bounded v2 result envelope; Facade and the mounted Hermes plugin accept and project only the active v2 presentation schema. A stale `AMUSANT_RESULT_CONTRACT_VERSION` value is not a selector: the Relay rejects values other than `2` (or blank), and deployments should remove the obsolete variable.

Historical schema migrations and stored `result_contract_version` values remain for audit. A pre-cutover Hermes row pinned to v1 is never submitted, polled, or parsed by the v2 executor; it is returned as unsupported/uncertain for operator reconciliation. Facade rejects a request or presentation pinned to v1 without changing the stored row. OpenAB ACP retains its independent existing behavior, and SQLite's schema version is unrelated to the Hermes result contract version.

V2 lets completed generic tasks return a short research finding or truthful action outcome with `artifact_status=none` when no writer attempt was recorded. Action completion, refusal, or failure must reflect actual Hermes tool results. `failed` means the Facade received and recorded a writer failure and no Artifact was committed; it produces a fixed delivery-recovery message. A network failure before a writer request reaches the Facade is unconfirmed and must not be described as a Facade-observed `failed` state. A committed Artifact retains its Facade-owned reference and integrity checks after later writer failures, while the failure remains visible in the short presentation. Hermes is instructed to call `save_report` for genuine document requests and wait for `committed=true` before claiming it saved one. StickS3 supplies audio and the task arguments still come from Gemini, so voice-only document intent has no independent trusted server signal today; optional Artifact behavior is not a deterministic guarantee that every spoken document request receives a report.

Artifact is optional for an ordinary short answer. When a report writer was attempted and failed, the fixed bounded recovery result reports that failure; an observed writer failure is never presented as successful preservation. Committed artifacts remain integrity-checked and bound to owner/task/session/Run. Relay stores only the bounded speech/card projection; the full report does not enter Relay or Gemini Live. The existing `details_md` storage slot is retained for unrelated historical/ACP paths and carries only the bounded speech in the Hermes result path.

See [Result Contract v2 cutover and rollback](../docs/result-contract-v2-cutover.md) for the one-time read-only precheck, operator-controlled backup, compatible deployment set, historical v1 evidence and rollback restrictions. Inbox and Live continue to receive only bounded short text. Authenticated full-document retrieval remains a separate follow-up, and simulated action receipts do not prove general Cron/TG side effects.
