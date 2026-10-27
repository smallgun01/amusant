# Issue #27 G1: private device ingress decision

## Decision

For the offline G1 PoC, select the OCI host's built-in `systemd-socket-proxyd`
behind a socket bound to the owner-supplied private VNIC address on TCP/443.
If a separately authorized owner later installs it, it forwards that one fixed
endpoint to Relay's existing address on `relay_api:8443`. The proxy carries
bytes only: TLS-PSK and WebSocket authentication remain end-to-end at Relay.
The rendered units are **proposed and uninstalled**; this PR neither starts a
listener nor changes host/cloud networking.

This adds two rendered systemd unit files, but no container, image, Docker
network, Relay listener, or cloud rule. The current Compose port mapping is
removed because Docker reported `8443/tcp` without an effective host binding.
The Relay, Facade, Hermes, and Egress Proxy network memberships remain as they
are. Relay and Hermes keep their existing internal-only service networks and
their configured outbound proxy path.

## Options compared

| Option | Components and network effect | G1 result |
| --- | --- | --- |
| A. Publish Relay's Docker port directly | Needs Relay's internal network to publish a host port; changing it to a non-internal network may give Relay a route around the Egress Proxy. | Reject as-is: the current mapping is ineffective. Do not weaken Relay network isolation to make it work. |
| B. Host `systemd-socket-proxyd` | Uses systemd already on the host; binds only the private VNIC and forwards to one fixed Relay IP. No TLS termination or container/network change. | Preferred minimum PoC, subject to the offline packet-flow and isolation gates below. |
| C. Dedicated L4 ingress container | Adds a service image, lifecycle, and a host-published port; requires its own access to the private Relay network and careful network/egress boundaries. | Do not add this component unless B fails a required host support or packet-flow gate. |

`systemd-socket-proxyd` accepts an inherited listening socket and connects each
client to the configured fixed target. Its connection and idle limits are
bounded in the unit. See the [systemd-socket-proxyd documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd-socket-proxyd.html)
and [systemd socket unit documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html).
The renderer subtracts one from the requested connection ceiling because
systemd 255 (used by the Ubuntu 24.04 offline runner) checks whether the active
set is greater than the option, while current systemd checks greater-than-or-
equal. This conservative adjustment keeps the actual limit at or below the
requested ceiling across both implementations; the offline test accepts the
expected 2–3 connections for its ceiling of 3, then verifies the next one is
refused. See the [systemd 255 implementation](https://github.com/systemd/systemd/blob/v255/src/socket-proxy/socket-proxyd.c#L428-L466)
and [current implementation](https://github.com/systemd/systemd/blob/main/src/socket-proxy/socket-proxyd.c#L1979-L2046).
The idle timeout option requires systemd 246 or newer; the actual OCI host
version is still unverified and must be checked before any installation.

## Current status, timeouts, and public-ingress safeguards

`--exit-idle-time=120s` exits the proxy only when it has **zero active
connections**; it is not a per-client TCP or WebSocket idle timeout. A client
that completes TLS and answers the Relay keepalive can hold one of the bounded
connection slots. `--connections-max` limits concurrent flows, not connection
rate. The default renderer requests a 32-flow ceiling (rendered as
`--connections-max=31` for systemd-version compatibility) and the socket backlog
is 64; neither is a source-specific rate limiter.

The pinned Relay runtime uses Python 3.13.15 (`Dockerfile.relay`) and
`websockets` 16.1.1 (`uv.lock`). The existing Relay server does not override
`ssl_handshake_timeout` or WebSocket `open_timeout`: asyncio's TLS handshake
default is 60 seconds, and websockets' opening-handshake default is 10 seconds.
Relay explicitly sets WebSocket `ping_interval=20s`, `ping_timeout=20s`,
`close_timeout=2s`, and a 64 KiB maximum message size. These settings support
protocol liveness and frame bounds; they are not a public-flood defense. See
the [Python asyncio server defaults](https://docs.python.org/3.13/library/asyncio-eventloop.html#asyncio.loop.create_server)
and [websockets 16.1.1 server defaults](https://websockets.readthedocs.io/en/16.1.1/reference/asyncio/server.html).

`systemd-socket-proxyd` creates a new upstream connection, so Relay sees the
proxy-side peer address rather than the original device source address. Do not
inject PROXY-protocol bytes into the current TLS-PSK stream; source-address
propagation would require a separately reviewed compatible protocol.

Before any unrestricted OCI public TCP/443 rule or G2 canary, an owner must
separately review and authorize source/connection-rate controls at OCI or the
host, systemd service resource caps (for example memory, CPU, task, and file
descriptor limits), and bounded connection behavior under real load. The
values must be measured against Relay/device reconnect behavior; this offline
PoC does not select production limits. A private-VNIC socket bind does not make
OCI public-IP/NAT access private. No public rule, installation, or deployment
is approved by this G1 patch.

### G2 read-only compatibility gate

Before any installation, an owner-supervised read-only check on the actual OCI
Ubuntu 22.04 host must confirm the installed `systemd-socket-proxyd` binary
and supported flags, the selected private VNIC address, the host route to the
internal Relay listener, persistence of the Relay target address across service
restarts, and the host firewall plus OCI ingress/NAT path. The Ubuntu 24.04
offline runner does not establish these host facts. After those checks, unit
installation, any OCI rule, DNS, and physical S3 testing each still require a
separate explicit owner GO. For public-domain testing, verify TCP/443 over a
mobile-data path with the N100 tunnel stopped, including wrong PSK/token and
reconnect behavior. This document and CI authorize none of those operations.

## Required configuration and operation

`RELAY_HOST_PRIVATE_IP` must be the host's private VNIC address and
`RELAY_BIND_IP` remains the fixed address of Relay on `relay_api`. The renderer
fixes the listener at TCP/443 and the upstream at TCP/8443. The old
`RELAY_PUBLISH_PORT` value is optional and ignored by Compose and the renderer;
existing operator `.env` files may retain the prior `8443` value. The renderer
validates the same offline Compose inputs and writes the socket/service units;
rendered files contain the selected IPs, so keep them owner-readable and out of
logs or artifacts.

The Grok-first Compose profile uses the same fixed TCP/443 ingress and Relay
`RELAY_BIND_IP:8443` target. Select it explicitly with
`render_device_ingress.py --profile grok`; this invokes the existing
`validate_grok()` contract and does not require legacy Facade/Hermes inputs.
The default remains `--profile legacy`. The Grok fixture test checks the
selected private addresses and rejects wildcard or cross-network targets;
rendering does not install or start the units.

Reproduce the static checks from the repository root with:

```sh
python3 -m unittest discover -s deploy/oci-a1/tests -v
python3 deploy/oci-a1/preflight.py --env-file /secure/path/compose.env
python3 deploy/oci-a1/render_device_ingress.py \
  --env-file /secure/path/compose.env --output-dir /tmp/amusant-g1-units
python3 deploy/oci-a1/preflight.py --profile grok --env-file /secure/path/grok.env
python3 deploy/oci-a1/render_device_ingress.py --profile grok \
  --env-file /secure/path/grok.env --output-dir /tmp/amusant-grok-g1-units
systemd-analyze verify /tmp/amusant-g1-units/amusant-device-ingress.socket \
  /tmp/amusant-g1-units/amusant-device-ingress.service
```

The end-to-end packet-flow check is `python3
deploy/oci-a1/tests/run_device_ingress_poc.py`. It requires a disposable Linux
runner with Docker, Buildx, systemd, and root access. The dedicated GitHub
Actions job provides that environment and uploads sanitized JSON evidence.

The socket is the externally reachable listener. After separate owner review,
the apply sequence is to render the units from the owner-reviewed `.env`,
confirm that `ListenStream` is the private VNIC `:443` and `ExecStart` targets
only Relay `:8443`, install the files under `/etc/systemd/system/`, run
`systemctl daemon-reload`, then `systemctl enable --now
amusant-device-ingress.socket`. No Compose or Relay restart is needed. This
patch performs none of those host operations. A VCN rule is a separate owner
change; no OCI API, public ingress, TLS termination, key, Bearer token, or
provider credential is used here.

For a separately authorized host install, use root-owned mode `0644` unit files
and run `systemctl daemon-reload` followed by
`systemctl enable --now amusant-device-ingress.socket`. Rollback is
`systemctl disable --now amusant-device-ingress.socket`, stop
`amusant-device-ingress.service`, remove both installed unit files, then run
`systemctl daemon-reload`. Remove a separately-added private VCN ingress rule
only if that owner change was made.

## Offline acceptance gates

The isolated Linux job uses a synthetic Relay with synthetic TLS-PSK and
Bearer values, a fake provider counter, and local-only network sentinels. It
must prove:

- a client on a separate test network reaches only host TCP/443 and completes
  TLS 1.2 ECDHE-PSK, Bearer authentication, and a synthetic PTT frame cycle;
- wrong PSK, wrong/missing Bearer, plaintext WebSocket, oversized frame, and
  excess connections fail without activating the fake provider;
- that client cannot reach Facade or Hermes, Relay and Hermes cannot reach the
  outbound sentinel directly, and the Egress Proxy can reach its sentinel;
- the existing private Relay listener path still works, and idle exit, socket
  restart, Relay outage, and recovery behave as expected.

Evidence is a sanitized JSON artifact. The tests make no OCI, live provider, or
device calls. Passing them proves the model in the disposable runner only; it
does not authorize or establish host support, OCI routing, cloud firewall, or
real-device behavior.

## Rollback

1. Stop and disable `amusant-device-ingress.socket`.
2. Remove the installed `amusant-device-ingress.socket` and
   `amusant-device-ingress.service` files, then run `systemctl daemon-reload`.
3. Remove the private VCN ingress rule only if it was separately added for this
   listener.
4. Keep the existing Docker networks and Relay's private `8443` listener; the
   private SSH/administrative path is unchanged.

No formal host install, VCN change, or service restart is performed by this
patch. A future installation requires a separate explicit owner authorization
after review and device/network acceptance.
