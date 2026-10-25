# OCI A1 deployment package — Amusant integration, Owner-managed Hermes

[ADR-0003](../../docs/adr/0003-oci-a1-hermes-sqlite.md) retains the four-service topology. [ADR-0004](../../docs/adr/0004-owner-managed-hermes.md) defines Owner management and native access. Runtime baseline after #116 is v2-only; this README does not establish which release is currently deployed.

[#87](https://github.com/smallgun01/amusant/pull/87) wired Relay -> private Facade -> Hermes. [#90](https://github.com/smallgun01/amusant/pull/90) proved the integrated fake-provider path on native ARM64; #85 is complete for E1. Preserve the [2026-10-22/23 GO 2/3 live evidence](../../docs/oci-go2-go3-live-evidence-20261022.md). Neither offline CI nor earlier functional evidence establishes full current E3/E4 acceptance. [HOST-READINESS.md](HOST-READINESS.md) includes historical host-access observations, not blanket SSH/sudo/Docker authority.

## Runtime topology

- G1 remains separately reviewed. The proposed host private-VNIC TCP/443 -> Relay TCP/8443 path forwards opaque TLS-PSK; it does not terminate PSK in a normal HTTPS reverse proxy. A private bind alone does not prevent public-IP/NAT reachability if cloud rules allow it.
- Relay joins `relay_api` and `relay_egress`. It cannot reach raw Hermes Runs or mount Facade artifacts.
- Facade joins `relay_api` and `hermes_api`, publishes no host port, and alone mounts result state. Relay uses a Facade-only key; it does not obtain the Hermes API key.
- Hermes joins `hermes_api` and `hermes_egress`, keeps its Runs API private and its Home persistent. The existing entrypoint launches Hermes Gateway.
- Egress Proxy accepts configured Relay/Hermes peers and public HTTPS CONNECT/443, rejecting private/loopback/link-local/reserved or mixed DNS targets. Facade has no egress network. This is network policy, not a per-tool allowlist.

Keep immutable `@sha256` image references, non-overlapping private subnets, distinct secrets, non-root UIDs and resource limits. Compose publishes no host port. `RELAY_PUBLISH_PORT` is a legacy compatibility setting, not an active listener. See [G1 decision](G1-INGRESS-DECISION.md); this documentation creates no listener or cloud rule.

## Owner-managed Hermes configuration

`hermes-config.fragment.yaml` documents the Amusant plugin requirement and a minimal example. **It is not the complete production profile or a permanent tool ceiling.** Merge it into the Owner's existing configuration without replacing model settings, platform tool policies, MCP servers, Skills, memory, credentials or native channels. Do not copy the intentionally restricted fake-provider fixtures into production as the Owner's full capabilities.

Owner controls effective Telegram/API-server toolsets and approval policy in Hermes. They may differ by platform; Relay/Facade must not mirror those lists. A Skill installed in a profile is not proof it is enabled and usable through every platform. Verify effective availability, dependencies and authorization without defaulting to `all` or unattended approval.

The current Compose mounts `/opt/data/config.yaml` read-only. For the initial operational path, retain this protection and update the backing configuration through an authorized Owner/Operator Hermes-side workflow, then apply/restart only with explicit approval. If native setup requires direct writes, propose the smallest specific mount/configuration change; do not make the whole host writable or add sudo/Docker socket access. Preserve Hermes Home and state on update. A writable Home does not make the separately read-only config writable.

External API keys, OAuth and native-channel credentials belong to Hermes-native secure setup/storage. Do not pass them through Relay, prompts, PRs, ordinary Telegram bot messages or the public repo. Amusant's own device/FAST/transport credentials remain separate; its Compose `.env` remains a non-secret configuration file under the existing package contract.

## Native Owner access: planned integration, not deployed evidence

Use the existing Hermes-native Telegram adapter and the intended Owner profile, not an Amusant Telegram bot or a second Agent. Prefer Owner-only private DM, explicit user-ID authorization and outbound long polling; no new public Hermes listener is needed for that design. Verify pinned-image adapter dependencies and use of the existing egress proxy before proposing any install/network change.

Telegram is a convenient direct conversation/management entry, not a universal secret-entry UI. Keep native CLI/UI/OAuth procedures where needed. Native replies must not be forced into the Amusant `speak/card` envelope. Check the existing session-scoped hook with a non-Amusant-session regression.

Same profile does not imply shared conversation sessions, cross-channel approval, Amusant Inbox visibility or access to Facade files. Do not promise that an API Run resumes after a Telegram 'yes'. Native Cron notifications may target Telegram under Owner policy; Amusant does not implement their scheduler or globally reserve those Runs.

Follow [OpenClaw O1/O2 instructions](../../docs/handoffs/20261025-openclaw.md). The first deliverable is a sanitized inventory/change plan. No token creation, private-config write, restart, Telegram message or real-provider test is authorized by this README.

## Amusant plugin and result boundary

Mount the reviewed plugin at `/opt/data/plugins/amusant-option-d` and enable it in the Owner's effective profile. Its writer uses Facade-derived owner/task/session/Run identity; model text cannot choose trusted refs or paths. New Hermes Runs use Result Contract v2 only: bounded short answer/card, optional Artifact and trusted status/reference. HTTP `/v1/runs` and writer `/internal/v1/...` remain API namespaces, not result versions.

`save_report` stores bounded text through the private writer; its 64 KiB cap is not a cap on all native Hermes files. Only Facade mounts `result_state`; Hermes-native attachments do not automatically retrieve those reports. Existing report readback/export is a separate authenticated delivery task. Do not share the result mount with Relay or create public file URLs.

For a fresh install only, prepare empty `results/facade` as UID 10003, mode 0700. Facade SQLite/WAL/report files use mode 0600. Preserve all existing production state on update; never empty an existing directory to rerun a bootstrap or copy a POC database into it.

## Offline build and static checks

Build from the repository root in an authorized development environment:

```sh
docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.relay -t amusant-relay:oci-arm64 .
docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.facade -t amusant-facade:oci-arm64 .
docker buildx build --platform linux/arm64 --load -f deploy/oci-a1/Dockerfile.egress-proxy -t amusant-egress-proxy:oci-arm64 .
```

Use private approved image/timing/resource values. Integration keys remain separate mounted files: Relay gets its Facade key; Facade gets Facade/Hermes/writer keys; Hermes gets Hermes API/writer keys. Linux shared keys use root:20000, mode 0440; Relay-only files follow entrypoint checks. Never print secrets or rendered configuration containing them.

```sh
python deploy/oci-a1/preflight.py --env-file deploy/oci-a1/.env
docker compose --env-file deploy/oci-a1/.env -f deploy/oci-a1/compose.yaml config --quiet
```

These are static checks, not service starts or live acceptance. Runtime/ARM64 builds are not required for a Markdown-only alignment PR. [B2 harness](../../spikes/004-relay-facade-hermes-e2e/README.md) and [selective CI](../../.github/workflows/issue-85-pr-b2-e2e.yml) retain offline evidence limits. #90 observed `failed / interrupt`, `stop_cancel_confirmed=false`; do not label that confirmed real-provider cancellation or billing settlement.

Preserve per-channel single-flight, GET-without-resubmission recovery and cancellation evidence. Use [v2 cutover/rollback](../../docs/result-contract-v2-cutover.md) for a separately approved coordinated release, including plugin/config and preserved state. Do not deploy an unreviewed branch or hot-patch live code to bypass a failed gate.
