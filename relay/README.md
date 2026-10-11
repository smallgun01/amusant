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

## Slow Path adapter test command (offline only)

From the Amusant repository root, run:

~~~bash
PYTHONPATH=.:relay uv run --locked python -m unittest discover -s relay/tests -v
~~~

This suite includes the P1 mock relay, P3.1 admission, P3.2 dispatcher/recovery, P3.3 ACP client, P3.4 Inbox lookup, P4-A1 ACP-only smoke, and ADR-0002 DynamoDB adapter tests against an in-memory fake ledger. ACP tests use a local fake `/acp` WebSocket. Inbox and smoke tests use local SQLite. Nothing here creates a DynamoDB table or calls Gemini, Jellii, OpenAB, or AWS.

`python -m relay.acp_smoke` is the ACP-only one-shot: it reuses `SQLiteTaskStore`, `AsyncDispatcher`, `OpenAbAcpExecutor`, and Inbox. Exit 0 only when one admitted task is `completed` with a bounded Inbox result. `failed`/`uncertain` are non-zero. `AMUSANT_ACP_CWD` is sent to the remote runtime and is not required to exist on the Relay. `wss://` verifies TLS; non-loopback `ws://` requires `AMUSANT_ACP_ALLOW_PLAINTEXT=1`. ACP hosts are loopback or RFC1918 only (not link-local/metadata). The executor uses one finite wall-clock deadline, capped at 120s. Do not set `AMUSANT_LIVE_PROVIDER=gemini` for this entrypoint. Never forward audio to ACP. A 2026-10-12 one-off Fargate run against Jellii recorded exit 0 with a completed Inbox result, then scaled Jellii back to 0. That live path used same-SG plaintext `ws://` `/acp` as a one-off risk exception, not TLS, and does not survive task replacement. Sanitized evidence is in `STATUS.md`.
