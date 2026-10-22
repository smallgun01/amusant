# Issue #27 G1: private device ingress decision

## Decision

Use the OCI host's built-in `systemd-socket-proxyd` behind a socket bound to the
owner-supplied private VNIC address on TCP/443. Forward that one fixed endpoint
to the existing Relay address on `relay_api:8443`. The proxy carries bytes only:
TLS-PSK and WebSocket authentication remain end-to-end at Relay.

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

## Required configuration and operation

`RELAY_HOST_PRIVATE_IP` must be the host's private VNIC address and
`RELAY_PUBLISH_PORT` must be `443`. `RELAY_BIND_IP` remains the fixed address of
the Relay on `relay_api`. `render_device_ingress.py` validates the same offline
Compose inputs and writes the socket/service units; rendered files contain the
selected IPs, so keep them owner-readable and out of logs or artifacts.

Reproduce the static checks from the repository root with:

```sh
python3 -m unittest discover -s deploy/oci-a1/tests -v
python3 deploy/oci-a1/preflight.py --env-file /secure/path/compose.env
python3 deploy/oci-a1/render_device_ingress.py \
  --env-file /secure/path/compose.env --output-dir /tmp/amusant-g1-units
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

