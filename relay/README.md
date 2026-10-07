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
