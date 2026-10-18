# OCI A1 offline deployment package — Issue #85 PR-B1

PR-B1 wires the runtime as **Relay → authenticated Option D Facade → Hermes** and isolates their Docker networks. This is an offline packaging and CI gate. It does not claim a live OCI deployment, provider call, ARM64 Hermes runtime acceptance, or 1 OCPU capacity; PR-B2 owns the integrated fake-provider E2E evidence.

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

Hermes writes the complete report into the private results volume. The writer derives owner, task, session, and Run identity from the trusted Facade-created request mapping; model arguments contain only report text. The opaque artifact reference is withheld from model-visible tool output. The Facade validates the committed artifact and the bounded presentation before returning an authenticated envelope.

For WS2/#56 compatibility, Hermes may emit the legacy details_md presentation field only up to 512 characters. The Hermes output hook validates and consumes it, then strips it from the Facade-to-Relay envelope. Relay accepts only the exact bounded envelope with a committed artifact reference; it rejects raw text, unknown fields, missing state, malformed references, and legacy details_md. The persisted legacy task-store details_md slot contains speech only, and Live task lookup returns only the short speech/card result. Neither the full report nor a clipped/summarized version of it crosses into Relay's Gemini result path.

The Facade creates a fresh facade-ledger.sqlite3 and artifact directory on the shared results mount. This runtime-owned state is separate from POC state. Prepare a new empty results directory with owner root, group 20000, mode 2770 (setgid). Relay does not mount this volume. Hermes and Facade run as non-root members of group 20000. Report files are group-readable for Facade reprojection. Do not copy or migrate a POC database into this directory.

## Offline build and checks

Build from the repository root:

    docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.relay -t amusant-relay:oci-arm64 .
    docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.facade -t amusant-facade:oci-arm64 .
    docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.egress-proxy -t amusant-egress-proxy:oci-arm64 .

Set all image references and owner-approved timing/resource values in a private .env. Secret files stay outside Git and .env: separate facade and Hermes API keys are required, plus Gemini/device credentials for Relay. Relay mounts only the facade key; Facade mounts the facade and Hermes keys; Hermes mounts only the Hermes API key. On Linux, shared API key files must be root:20000 mode 0440. Relay-only secret files follow the existing Relay entrypoint ownership/mode checks.

Prepare the operator-owned Hermes config and data directories before starting Compose. Run:

    python deploy/oci-a1/preflight.py --env-file deploy/oci-a1/.env
    docker compose --env-file deploy/oci-a1/.env -f deploy/oci-a1/compose.yaml config --quiet

These commands validate static inputs only; they do not start services. CI builds Relay, Facade, and the proxy for ARM64 and runs offline regression/static Compose guards. No container-network integration is claimed here; Issue #85 PR-B2 must prove fake-provider submit, recovery, stop, report-boundary, Inbox, and routing-negative behavior before the offline E1 gate can be accepted.

Relay retains its durable task ledger, persisted Run ID, GET-only restart recovery, per-owner single-flight, deadlines and stop semantics. Hermes remains the same pinned image; this package does not fork Hermes. Keep the existing Relay and Hermes volumes. This package does not deploy to OCI, alter ingress, call real providers, manipulate production credentials, or perform data migrations.
