# OCI A1 offline deployment package — #85 B1/B2 merged (E1 GO only)

[PR #87](https://github.com/smallgun01/amusant/pull/87) wires **Relay → authenticated Option D Facade → Hermes** with private Docker network isolation. [PR #90](https://github.com/smallgun01/amusant/pull/90) additionally proved the **integrated** Relay/Dispatcher/SQLite→Facade→pinned Hermes→Artifact/Inbox path with synthetic providers on a native ARM64 runner. Both are merged; [#85](https://github.com/smallgun01/amusant/issues/85) is closed as **E1 GO**. This does **not** establish OCI deployment, actual model/provider operation, production credentials, external device ingress, real cancellation or 1 OCPU performance.

## Host readiness is not a canary

As of 2026-10-21, the owner reports an existing OCI Ubuntu ARM64 A1 VM and an N100 OpenClaw SSH **read-only** connection using a dedicated unprivileged account (BatchMode exit 0). This is **only a host-access milestone**. Docker/Compose, block volumes, secrets and actual OCI runtime execution have **not** been proven. Follow [HOST-READINESS.md](HOST-READINESS.md) for the non-mutating H2 inventory and request explicit approval for installs, sudo, volume/network or ingress changes, credentials, image pulls, containers and any provider call. Nothing in this package auto-deploys services or opens ports.

## Self-hosted Doctor and Safe Setup (PR-A)

The generic standard-library-first CLI lives in `deploy/self-hosted/`; the OCI A1 input profile is `deploy/oci-a1/self-hosted-profile.json`.

From the repository root:

```sh
python3 deploy/self-hosted/toolkit.py --profile deploy/oci-a1/self-hosted-profile.json doctor
python3 deploy/self-hosted/toolkit.py --profile deploy/oci-a1/self-hosted-profile.json setup
python3 deploy/self-hosted/toolkit.py --profile deploy/oci-a1/self-hosted-profile.json setup --apply
```

Pass `--env-file` to select a private env file and repeat `--external-network-cidr` with operator-supplied network evidence. For the OCI profile, set `OCI_EXPECTED_DATA_UUID` from owner-approved fstab/volume evidence; a `/dev/sdX` path is not treated as stable identity. `OCI_VCN_CIDRS` is optional and never looked up remotely. Doctor reuses the existing OCI preflight, checks file metadata without opening secret/config contents or printing file paths, and runs only local `docker compose config --quiet` when inputs are complete. It never invokes sudo: if an unprivileged operator cannot stat root-only paths or the Docker socket, those checks are `UNKNOWN`; the owner may explicitly run Doctor in an already-authorized privileged context, which still cannot relax permissions or reveal contents. It does not connect to cloud/registry/provider APIs, pull images, or start services. Digest syntax is distinct from verified image provenance/platform, and every report remains explicitly short of deployment GO.

`setup` is a dry-run by default. `setup --apply` requires an interactive `APPLY SAFE` confirmation and only applies the listed safe local operations. It never overwrites `.env`, secrets, Hermes config or existing data; never changes existing ownership/modes; never invokes sudo, creates credentials, or starts containers. If volume identity or permissions are unknown, data-directory changes stay blocked. Unit tests use temporary fixtures and fake command results only.

## Runtime topology

- StickS3 connects to the private Relay listener on TCP/8443 using the existing fail-closed TLS-PSK path.
- Relay joins only relay_api and relay_egress. It has no route to the Hermes API network, Hermes port 8642, the result volume, or Hermes artifacts.
- Facade joins relay_api and hermes_api, listens only on its Relay-side IP, and exposes no host port. It is the only service that holds the separate Hermes API credential. Relay receives a facade-only key.
- Hermes joins only hermes_api and hermes_egress. Its Runs API binds to its static Hermes-side IP and is never published.
- The egress proxy has one listener on each egress network and accepts only the configured Relay and Hermes IPs. It permits CONNECT to public destinations on port 443 and rejects private, loopback, link-local, reserved, and mixed public/private DNS answers. Facade has no egress network.
- Compose publishes only Relay 8443, bound to the selected RFC1918 host address. The four internal subnets must be private, non-overlapping, and separate from the VCN.

All service images require immutable @sha256 references. Preflight validates image digests, four isolated subnets, unique static addresses, and private host binding. It makes no cloud or provider calls.

## Hermes plugin and result boundary

The runtime plugin is mounted at /opt/data/plugins/amusant-option-d. Manually merge hermes-config.fragment.yaml into an operator-owned configuration, enable the plugin and its API toolset, and pass the full reviewed config path as HERMES_CONFIG_FILE. Compose mounts that file read-only. There is no automatic config migration, and the POC fake-provider configuration is not used.

Hermes submits report text through the Facade's narrow authenticated writer API. The Facade derives owner, task, session, and Run identity from its own request ledger, mints the opaque artifact reference, and writes the report. The plugin receives only a bounded commit acknowledgement; the output hook asks the Facade to validate and project the short presentation against the committed artifact.

For WS2/#56 compatibility, Hermes may emit the legacy details_md presentation field only up to 512 characters. The Hermes output hook validates and consumes it, then strips it from the runtime Facade-to-Relay envelope. Relay also accepts the pinned #75 legacy envelope shape for compatibility only when details_md is at most 512 characters, and discards that field. Relay rejects raw text, unknown fields, missing state, malformed references, and oversized legacy details. The persisted legacy task-store details_md slot contains speech only, and Live task lookup returns only the short speech/card result. Neither the full report nor a clipped/summarized version of it crosses into Relay's Gemini result path.

The Facade creates a fresh `facade-ledger.sqlite3` and artifact directory on the results mount. Prepare an empty `results/facade` directory owned by UID 10003 with mode 0700. The Facade checks that ownership at startup and enforces mode 0700. Only Facade mounts this volume. Its SQLite database and WAL files are mode 0600; the artifact subdirectory is mode 0700 and report files are mode 0600. Hermes cannot read or write the Facade ledger or artifacts. The writer listener is bound only to the hermes_api network and accepts a report body without any caller-supplied identity or artifact reference. Do not copy or migrate a POC database into this directory.

## Offline build and checks

Build from the repository root:

    docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.relay -t amusant-relay:oci-arm64 .
    docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.facade -t amusant-facade:oci-arm64 .
    docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.egress-proxy -t amusant-egress-proxy:oci-arm64 .

Set all image references and owner-approved timing/resource values in a private .env. Secret files stay outside Git and .env: separate facade, Hermes API, and Facade-writer keys are required, plus Gemini/device credentials for Relay. Relay mounts only the facade key; Facade mounts the facade, Hermes, and writer keys; Hermes mounts only the Hermes API and writer keys. On Linux, keys shared with Facade or Hermes must be root:20000 mode 0440. Prepare `results/facade` on the mounted block volume as UID 10003, mode 0700. Relay-only secret files follow the existing Relay entrypoint ownership/mode checks.

Prepare the operator-owned Hermes config and data directories before starting Compose. Run:

    python deploy/oci-a1/preflight.py --env-file deploy/oci-a1/.env
    docker compose --env-file deploy/oci-a1/.env -f deploy/oci-a1/compose.yaml config --quiet

These two commands validate **static** inputs only; they neither start the service nor constitute #90 E1 evidence. PR #87 CI verifies ARM64 Relay/Facade/proxy images, static Compose isolation and two-UID Facade write/readback. The separately merged #90 B2 proof uses the **real Relay runtime** with a fake Gemini provider and pinned Hermes with a fake model: Run submit, trusted report commit, 10k/30k marker isolation, bounded Inbox, wrong-owner/key denial, raw-Hermes GET/SSE denial, duplicate/ambiguous submit, in-process Relay runtime restart/GET-only recovery and Stop status handling. Native ARM64 [native ARM64 E2E #39240814687](https://github.com/smallgun01/amusant/actions/runs/39240814687) PASS; 23/23 local contract tests and [regular CI #39240814701](https://github.com/smallgun01/amusant/actions/runs/39240814701) PASS. [Harness instructions](../../spikes/004-relay-facade-hermes-e2e/README.md) and [selective CI workflow](../../.github/workflows/issue-85-pr-b2-e2e.yml) document the exact E1 limits.

**Important STOP caveat:** Pinned Hermes v2026.9.24 reported terminal `failed / interrupt` for an active fake-provider Run. Relay and Inbox correctly showed `hermes_failed` and the proof recorded `stop_cancel_confirmed=false`; E1 STOP *handling* passed, but true active-run cancellation, provider/tool settlement and billing cessation remain **unverified**, as do OCI host restart and off-host restoration.

Relay retains its durable task ledger, persisted Run ID, GET-only restart recovery, per-owner single-flight, deadlines and stop semantics. Hermes remains the same pinned image; this package does not fork Hermes. Keep the existing Relay and Hermes volumes. This package does not deploy to OCI, alter ingress, call real providers, manipulate production credentials, or perform data migrations.
