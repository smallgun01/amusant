# OCI A1 offline deployment package

This is an **offline-only Phase 1 candidate** for review. It is not a deploy instruction or evidence that OCI, Hermes ARM64, device ingress, or 1 OCPU capacity works. The Compose file refuses missing image digests, network addresses, secret-file paths, timing choices, and resource ceilings. Do not run `docker compose up` without a separately approved canary envelope.

## Topology

```text
StickS3 --TLS-PSK--> OCI private address:8443 --> Relay (172.30.62.2)
                                                    |  relay_api (internal)
                                                    +----> Hermes Runs (172.30.62.3:8642)
Relay + Hermes --controlled_egress (internal)--> HTTPS CONNECT proxy (172.30.63.4:3128)
                                                      |
                                                      +--> public TCP/443 only
```

`relay_api` is internal and has only Relay and Hermes as service members. Hermes binds Runs API to its static `relay_api` address instead of `0.0.0.0`; the proxy is not attached to that network. Port 8642 is never published. Relay and Hermes have no internet-routed Docker network, so provider/model HTTPS goes through the small CONNECT-only proxy. It permits public destinations on port 443 and rejects non-global IPv4/IPv6 addresses, including link-local/OCI metadata, loopback, private and reserved ranges. It does not proxy arbitrary HTTP methods or non-443 ports. DNS is resolved by the proxy and the connection is pinned to a validated IP to prevent rebinding between check and connect.

Relay-to-Hermes uses `http://<static Hermes API IP>:8642` on the dedicated private Docker network. This explicit plaintext exception is limited to same-host internal bridge traffic, has no host port mapping, and still requires the mounted Hermes bearer key. It trusts the OCI Docker host as an administrator. If that boundary is not acceptable, replace it with TLS before canary; do not remove the bearer key.

The device listener binds to a deterministic, actual private container address from Compose IPAM. Docker publishes TCP/8443 only on the explicitly supplied RFC1918 OCI host address. This does not select or approve the final #27 ingress path. Before canary, verify the OCI VCN/NSG source allowlist, host address assignment, device reachability, PSK authentication and reconnect after container recreation. No public host address is accepted by the offline preflight.

## Build and local checks

Build from the repository root. The Python 3.13 multi-platform base index and `uv` are pinned; the Relay build installs the locked `hardware-opus` extra into `/app/.venv` and starts with `/app/.venv/bin/python`. Relay runs as UID 10001, Hermes as UID 10000, and the egress proxy as UID 10002. All drop capabilities, use a read-only root filesystem and have no Docker socket, privileged mode, host network, or broad host mounts.

```sh
docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.relay -t amusant-relay:oci-arm64 .
docker run --rm --platform linux/arm64 --entrypoint /app/.venv/bin/python amusant-relay:oci-arm64 -m unittest discover -s relay/tests -p test_device_opus.py -v
docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.egress-proxy -t amusant-egress-proxy:oci-arm64 .
```

Publish reviewed images to the chosen private registry, then put their immutable `@sha256:` references in `.env`. `HERMES_IMAGE` must be the exact digest that passes the ARM64 gates below; the previous x86 POC digest is not presumed to pass. Run static validation with `python deploy/oci-a1/preflight.py --env-file deploy/oci-a1/.env`, then `docker compose --env-file deploy/oci-a1/.env -f deploy/oci-a1/compose.yaml config --quiet`. Neither command starts a container.

To run the offline Hermes compatibility harness against a selected ARM64 candidate digest, set `POC_HERMES_IMAGE` to that exact `@sha256:` reference and `POC_HERMES_PLATFORM=linux/arm64`, then run `python spikes/002-hermes-native-runs-poc/run_poc.py` and `python spikes/002-hermes-native-runs-poc/run_phase2b.py`. Both runners verify the image architecture, native imports, version and doctor before their fake-provider recovery gates. Candidate runs use a digest-specific Compose project and named Hermes/Relay volumes, leaving the existing x86 POC volumes intact. The fake provider makes no paid model calls; this remains local compatibility evidence, not OCI or real-provider acceptance.

## Secrets and storage

`.env` contains non-secret values and secret **file paths only**. Keep secret files outside Git. The Relay entrypoint reads `GEMINI_API_KEY`, `AMUSANT_DEVICE_TOKEN`, `AMUSANT_DEVICE_PSKS`, and `AMUSANT_HERMES_API_KEY` from mounted files and `exec`s the locked venv interpreter. The Hermes wrapper reads the same API-key file into `API_SERVER_KEY`; neither wrapper prints values. Files for Relay-only secrets must be owned by UID 10001 with mode 0400. The shared Hermes API key file must be owned by root, group 20000, mode 0440; Compose adds group 20000 to both processes. Check the source-file ownership survives Compose's file-secret bind mount on the target Docker Engine before canary. Never put values in `.env`, command arguments, image layers, or logs.

The Hermes model/provider selection and its separate provider credential are still an Owner decision. This candidate only mounts the Runs API key; prepare a reviewed `HERMES_HOME` configuration and a file-backed provider secret path before any task can execute. Do not copy the POC fake-provider configuration or put a provider key in `config.yaml`.

`relay_state` and `hermes_home` bind to `/mnt/amusant-data/{relay,hermes}` under the mounted OCI block volume. Prepare those directories before starting Compose: Relay UID/GID 10001 owns `relay` with mode 0700; Hermes UID/GID 10000 owns `hermes` with mode 0700. Existing DynamoDB rows and AWS resources are outside this package; use only a new empty SQLite ledger for a canary. Do not copy a live SQLite database file while WAL is active. Use SQLite's online backup API, integrity-check the result, copy it off-host, and perform a restore drill into a separate empty directory before any cutover. Hermes `HERMES_HOME` and its Run database also need an independent backup and restore drill. This package does not automate either backup or transfer.

On SIGTERM or SIGINT, Relay stops waiting for new WebSocket work, closes active WebSockets with a two-second protocol timeout, then lets the durable dispatcher drain for at most `AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS` (default 10 seconds outside Compose; Compose requires an explicit value, maximum 120). If that grace expires, shutdown cancels the local dispatcher task; startup recovery resumes a persisted Hermes Run by GET only and leaves a submission without a durable Run ID uncertain rather than replaying it. Set Docker's `RELAY_STOP_GRACE_PERIOD` above the Relay drain grace plus WebSocket close timeout.

The CONNECT proxy allows at most 32 tunnels, split into 16 slots per configured client address, so Hermes cannot consume Relay's reserved capacity. DNS uses at most four outstanding lookups per client and eight overall; each request returns within two seconds, while a timed-out resolver continues to hold its slot until the OS lookup actually finishes. DNS answers are still rejected if any result is private, loopback, link-local, or otherwise not globally routable. The existing destination allowlist remains public TCP/443 only.

The Compose resource values are ceilings, not reservations. The sample 0.35/0.55/0.05 OCPU split and 1536m/3584m/128m memory limits leave little headroom on a 1 OCPU / 6 GB host and are only test inputs. Do not call them sufficient until one representative Hermes tool task runs while FAST audio is measured. Record FAST p50/p95, RSS/OOM, CPU throttling, and restart/recovery. Keep health checks scoped: Relay's TCP probe and Hermes `/health` check prove listeners only, not provider, admission, or persisted Run health.

## Required offline and live gates

1. CI builds the Relay and proxy images for `linux/arm64`; Relay smoke checks import `av`/NumPy, exercise Opus encode/decode, and verify Python 3.13 TLS-PSK support. Run the existing Phase 2B fake-provider harness on the exact Hermes image candidate after it has passed the ARM64 image checks.
2. For the exact Hermes `@sha256:` reference, verify the pulled platform is `linux/arm64`; run native-module imports, `hermes --version`, doctor, and `gateway run`; exercise authenticated `/v1/runs`, stop, restart, and persisted Run-state readback using the fake provider. `/health` alone is not acceptance. The existing POC runners accept `POC_HERMES_IMAGE` and `POC_HERMES_PLATFORM` to select this exact immutable candidate without changing the normal pinned default.
3. Before any separately approved canary, verify API reachability only from Relay service, API-key rejection, no published 8642, proxy denial of metadata/private/host addresses and Relay capacity under Hermes saturation, no direct egress from Relay/Hermes, persistent-volume ownership/WAL/Run durability, off-host backup and restore, host port/NSG/device authentication/reconnect, and Hermes tool restrictions on the persisted `HERMES_HOME` configuration.
4. Measure FAST p50/p95 with one representative Hermes tool task; collect per-container RSS/OOM and CPU throttling; stop and restart both services and prove a known Relay Run resumes by GET only and the same Hermes Run state persists. A single-host outage still loses FAST+SLOW availability.

Hermes API timing variables remain mandatory because Issue #58 has no approved product values. Main now contains the owner-approved OCI A1 + Hermes + SQLite target architecture overlay in ADR-0003, merged by PR #77. This candidate does not amend that approved SPEC/ADR baseline; live OCI capacity, provider configuration, networking, restore, and FAST performance checks remain separate acceptance gates.
