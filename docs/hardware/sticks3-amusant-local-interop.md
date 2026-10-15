# StickS3 → local Amusant Relay interop

**Scope:** H0.3 local device-edge path only. This document does not authorize a real Gemini request, device flash, AWS/public ingress, a PR merge, or a production service. Stage D Jellii/Zephyr persona work stays separate from transport acceptance.

## Modes

- Default PCM/Kai-sim development path remains the existing **--device-mode pcm** listener (default **127.0.0.1:8765**). It does not load PyAV or TLS-PSK keys.
- StickS3 mode must be selected explicitly with **--device-mode sticks3**. It requires an explicit private LAN IP, listens on TLS-PSK port 8443 by default, requires an authenticated hello, and selects Opus or legacy PCM per connection (pinned Kai v0.12.0 advertises Opus). It has no plaintext downgrade and uses TLS 1.2 ECDHE-PSK plus the existing HTTP/WebSocket Bearer authentication.
- Server identity/client/backend remain server-owned. The firmware's hello.device value is telemetry only and is replaced with a generic local log label in StickS3 mode.

## Dependencies and preflight

The normal project dependency set stays unchanged. Hardware Opus uses the locked optional extra. Current lock resolution is PyAV av 16.1.0 plus NumPy 2.4.6 for Python <3.12 / 2.5.4 for Python >=3.12. Linux x86_64 Python 3.13 wheels were installed and exercised in offline codec/WSS tests; this is not device evidence.

<pre><code>uv sync --locked --extra hardware-opus --python 3.13
uv run --locked --extra hardware-opus python -m unittest discover -s relay/tests -v
uv run --locked --extra hardware-opus python -m unittest discover -s fast_voice/tests -v
uv run --locked --python 3.13 --extra hardware-opus python relay/tests/psk_wss_integration.py</code></pre>

The last command is a mandatory real local Python 3.13/OpenSSL TLS-PSK WSS integration test. It is intentionally a separate command and fails fast below Python 3.13; it is not a skipped placeholder. It uses only synthetic in-memory PSK values and a fake LiveProvider; it does not call Google or require a provider API key. Startup checks SSL PSK/cipher capability and the optional Opus encoder/decoder before opening the hardware listener.

## Local-only configuration shape

Keep actual secrets in the owner's private runtime environment; never put them in this file, a committed .env, shell history, command arguments, GitHub comments, or logs.

The local runtime must supply this complete configuration set before run() can compose the supported Relay:

- AMUSANT_DEVICE_TOKEN — private Bearer token, kept out of this file and shell history.
- AMUSANT_DEVICE_PSKS — shape identity:<64-hex-character-key>; multiple unique identities/keys may be comma-separated. Use one unique 32-byte PSK per device. The identity/key must match the already-working Kai firmware; Bearer and PSK are independent secrets.
- AMUSANT_CLIENT_ID — bounded server-owned identity; never derive it from hello.device.
- AMUSANT_BACKEND_ID — fixed server-side backend selection.
- AMUSANT_TASK_STORE=sqlite and AMUSANT_TASK_DB=<absolute path to a local SQLite ledger file>.
- AMUSANT_LIVE_PROVIDER=gemini and GEMINI_API_KEY, injected privately at runtime.

Optional task limits have defaults in relay/application.py. The AMUSANT_ACP_URL / AMUSANT_ACP_AUTH_KEY / AMUSANT_ACP_CWD trio is optional but all-or-none: if all are absent, no AsyncDispatcher starts and admitted tasks can remain pending; a partial trio fails closed. This transport runbook does not authorize real ACP credentials or task execution.

Bind with an explicit private LAN IP; loopback, wildcard, public, link-local, and DNS-name binds are rejected in StickS3 mode. No public ingress or port-forwarding is part of H0.3.

The actual owner-run command, after separate approval for a paid Live turn and physical test, is:

<pre><code>uv run --locked --python 3.13 --extra hardware-opus python -m relay.live_bridge --device-mode sticks3 --host &lt;PRIVATE_LAN_IP&gt;</code></pre>

The device and Relay must use the same local host/port and matching owner-held credentials. Never run the upstream Kai Relay and Amusant on the same host:port at the same time.

## Owner-supervised test and rollback

Only after the focused code PR has been reviewed and the owner separately authorizes device/provider use:

1. Verify the private bind, TLS-PSK handshake, Bearer validation, and service dependencies locally; ensure no AWS, public DNS, or port-forwarding is involved.
2. Stop the temporary upstream Kai Relay before binding Amusant to the same port. Use the already-working, pinned, unmodified StickS3 firmware; no reflashing is required for this interop proof.
3. The owner performs PTT, wake-and-talk, interruption, reconnect, and audible-playback checks. Record sanitized PTT-start, decoded-300ms threshold/provider-connect, release, and first-wire-audio timings; the adapter retains only constant-size current-turn timing state, not per-turn history. Do not log audio, transcript text, MAC, PSK, Bearer, or API key.
4. If Amusant fails, stop its local process and restart the known-good upstream Kai Relay on the same host/port. Confirm listener teardown before handing the device back.

Offline fake-provider PASS is only protocol/software evidence. It is not real-device functional PASS, paid Gemini runtime proof, or H0 latency acceptance.
