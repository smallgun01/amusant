# OCI A1 offline deployment package — #85 B1/B2 merged (E1 GO only)

[PR #87](https://github.com/smallgun01/amusant/pull/87) wires **Relay → authenticated Option D Facade → Hermes** with private Docker network isolation. [PR #90](https://github.com/smallgun01/amusant/pull/90) additionally proved the **integrated** Relay/Dispatcher/SQLite→Facade→pinned Hermes→Artifact/Inbox path with synthetic providers on a native ARM64 runner. Both are merged; [#85](https://github.com/smallgun01/amusant/issues/85) is closed as **E1 GO**. This does **not** establish OCI deployment, actual model/provider operation, production credentials, external device ingress, real cancellation or 1 OCPU performance.

## Historical host-readiness snapshot (2026-10-21)

The following records the initial host-access milestone only; its runtime status is superseded by the owner's [2026-10-22 #62 live evidence](https://github.com/smallgun01/amusant/issues/62#issuecomment-6344705541) and the follow-up in [Draft PR #106](https://github.com/smallgun01/amusant/pull/106). The owner reported a functional four-service backend and StickS3 FAST over a temporary private SSH forward; the physical Slow path remained incomplete and was later reconciled as failed with no result. This does not establish formal E3/E4 acceptance or public device ingress. The G1 TCP/443 path below is **proposed and uninstalled**; this package creates no listener and changes no OCI rule.

At the 2026-10-21 snapshot, the owner reported an existing OCI Ubuntu ARM64 A1 VM and an N100 OpenClaw SSH **read-only** connection using a dedicated unprivileged account (BatchMode exit 0). At that time, Docker/Compose, block volumes, secrets and actual OCI runtime execution had **not** been proven. The snapshot's H2 inventory guidance is retained in [HOST-READINESS.md](HOST-READINESS.md); nothing in this package auto-deploys services or opens ports.

## Runtime topology

- The proposed, uninstalled G1 path would use the host's selected private VNIC address on TCP/443 and forward opaque TCP to Relay's existing private listener on `relay_api:8443`; TLS-PSK and Bearer checks would still terminate only in Relay. No OCI ingress rule or listener is created here. A private-VNIC bind alone does not prevent a connection arriving through an OCI public-IP/NAT mapping if a separate cloud rule allows it; public access requires a separate owner review and authorization.
- Relay joins only relay_api and relay_egress. It has no route to the Hermes API network, Hermes port 8642, the result volume, or Hermes artifacts.
- Facade joins relay_api and hermes_api, listens only on its Relay-side IP, and exposes no host port. It is the only service that holds the separate Hermes API credential. Relay receives a facade-only key.
- Hermes joins only hermes_api and hermes_egress. Its Runs API binds to its static Hermes-side IP and is never published.
- The egress proxy has one listener on each egress network and accepts only the configured Relay and Hermes IPs. It permits CONNECT to public destinations on port 443 and rejects private, loopback, link-local, reserved, and mixed public/private DNS answers. Facade has no egress network.
- Compose does not publish a host port. If separately installed after review, the G1 systemd socket would bind TCP/443 on the selected RFC1918 private VNIC address and use one fixed Relay target. The four internal subnets must be private, non-overlapping, and separate from the VCN.

All service images require immutable @sha256 references. Preflight validates image digests, four isolated subnets, unique static addresses, and the private host binding. The systemd renderer fixes the proposed ingress at TCP/443 and Relay's internal target at TCP/8443. `RELAY_PUBLISH_PORT` is an optional legacy Compose setting, retained for existing `.env` compatibility; it is not used by Compose or the ingress renderer. Neither command changes a host or makes cloud/provider calls. See [G1 ingress decision, tests, and rollback](G1-INGRESS-DECISION.md).

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
