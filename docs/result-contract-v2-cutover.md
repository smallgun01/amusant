# Hermes Result Contract v2-only cutover

## Decision and source audit

The Owner has retired Hermes Option D Result Contract v1. Result Contract v2 is the only contract for new Hermes Runs. There is no runtime selector, dual writer, v1 parser, or v1 recovery mode. This change does not alter Hermes HTTP `/v1/runs`, Facade's private `/internal/v1/...` routes, SQLite schema migration versions, the `details_md` storage slot, or OpenAB ACP behavior.

| Area | Classification | Result |
| --- | --- | --- |
| `relay/application.py`, `relay/slow_path.py`, OCI preflight | Replace with v2 | New Hermes admissions no longer read a selection setting; Relay and OCI preflight reject a stale `AMUSANT_RESULT_CONTRACT_VERSION` other than blank/`2`, and neither value selects behavior. The default path needs no opt-in. |
| `relay/hermes_result.py`, `relay/hermes_runs_executor.py` | Remove v1 runtime behavior; replace with v2 | The only prompt and parser use the bounded v2 result. The executor always submits header `2`, refuses a pre-cutover Hermes row pinned to v1 before network access, and records a bounded delivery failure when a confirmed completed Run has invalid/missing presentation. |
| `relay/task_store.py`, `relay/dynamodb_task_store.py` | Preserve historical storage; enforce v2 for Hermes | New Hermes writes pin `2`, including SQLite and DynamoDB. Historical missing version values still read as v1 for audit and fail closed if an old Hermes row is dispatched. SQLite's additive schema migration retains its historical default `1`; fresh schema default is `2`. ACP continues to use its existing independent contract metadata. |
| `relay/task_store.py` result `details_md` | Preserve | The column and result API remain for unrelated history/ACP. Hermes v2 writes only its bounded `speak` value there. Complete reports never enter Relay or Gemini Live. |
| `deploy/oci-a1/facade.py` | Remove v1 presentation and projection branches; replace with v2 | New request rows pin `2`; submit and GET require the v2 header. An existing v1-pinned row returns a contract error without mutation or upstream reinterpretation. A v2 short answer can project with `artifact_status=none`; an observed writer failure cannot project as preserved success. |
| `deploy/oci-a1/hermes_plugin/__init__.py`, `reprojection.py` | Remove v1 schema and Artifact-mandatory branches; replace with v2 | The active plugin accepts only the v2 presentation schema. Artifact is optional before a writer attempt and remains integrity-checked/bound when committed. Replay, owner/task/session/Run binding, writer errors, bounded output, and fail-closed projection remain. The authenticated private Facade writer boundary remains. |
| `deploy/oci-a1/compose.yaml` | Preserve and verify | It contains no result-version selection setting. It mounts the reviewed Hermes plugin read-only. The complete release must install Relay, Facade, mounted plugin, and Compose together. |
| `/v1/runs`, `/internal/v1/...` paths | Preserve | Route spelling is a separate HTTP API namespace and does not mean Result Contract v1. |
| SQLite schema versions and `details_md` | Preserve | No schema rewrite or column removal is needed for the contract cutover. |
| OpenAB ACP fallback | Preserve | ACP's independent execution and result handling is outside this Hermes contract decision. |
| `spikes/003-hermes-option-d-poc` | Remove v1 executable paths; preserve sanitized historical summary | The old Facade, plugin, fake provider, runner, and Compose files are removed. The README preserves only the bounded historical proof summary; the v2 integrated harness and regression suites provide current acceptance evidence. |
| `spikes/004-relay-facade-hermes-e2e`, CI and ARM64 gate | Replace acceptance with v2 proof | New admissions are asserted as v2 without a selector. Existing identity, artifact-integrity, malformed/oversized, report privacy, replay, recovery, and wrong-owner cases remain in the v2 suites. |

The sanitized known OCI test-task facts supplied for this cutover are recorded in [Result Contract v1 retirement evidence](result-contract-v1-retirement-evidence.md). They are historical evidence only; the runtime does not rewrite them.

## Read-only precheck

The precheck reads both SQLite databases in read-only mode, verifies they can be read, counts saved task/request statuses and contract versions, checks for queued/running/uncertain Relay tasks, and blocks when a Facade request has an unknown or nonterminal Hermes status. It also checks that the artifact and Hermes Home directories exist. If a nonempty SQLite WAL has no existing shared-memory sidecar, it blocks rather than letting SQLite create one. It prints only aggregate counts and sanitized finding codes, never task IDs, owners, report content, Run IDs, or artifact references. It performs no database writes, backup, deletion, migration, service control, or provider request.

Resolve the paths from the actual OCI block volume and active Compose release. For the checked-in Compose layout, the default locations are `$OCI_DATA_DIR/relay/task-ledger.sqlite3`, `$OCI_DATA_DIR/results/facade/facade-ledger.sqlite3`, `$OCI_DATA_DIR/results/facade/artifacts`, and `$OCI_DATA_DIR/hermes`; verify these against the live mounts before running.

```sh
python3 deploy/oci-a1/result_contract_v2_cutover_precheck.py \
  --dry-run \
  --relay-db "$OCI_DATA_DIR/relay/task-ledger.sqlite3" \
  --facade-db "$OCI_DATA_DIR/results/facade/facade-ledger.sqlite3" \
  --artifacts-dir "$OCI_DATA_DIR/results/facade/artifacts" \
  --hermes-home "$OCI_DATA_DIR/hermes"
```

Exit `0` means only that the read-only checks found no recorded active or unresolved task and the directories/databases are present. Exit `2` blocks cutover; stop and investigate the reported condition. A missing table/column, database error, unknown state, unresolved Facade request, or active task is not permission to repair or edit the database.

### Hermes Runs registry inventory

The pinned Hermes `v2026.9.24` API has `POST /v1/runs` and authenticated `GET /v1/runs/{run_id}`; it has no `GET /v1/runs` listing route. `/api/sessions` lists sessions, not durable Runs. The Run idempotency store is `HERMES_HOME/runs_idempotency.db`; the checked-in OCI Compose sets `HERMES_HOME=/opt/data`, ordinarily mounted at `$OCI_DATA_DIR/hermes`. Resolve this path from the active Compose mounts instead of assuming the default.

Run this inventory with ingress paused. It opens the Hermes registry, Relay ledger, and Facade ledger in SQLite read-only mode. It prints aggregate status and join counts only: no owner scopes, task keys, Run IDs, `status_json`, prompts, reports, or secrets. `--dry-run` is required.

```sh
python3 deploy/oci-a1/hermes_runs_inventory.py \
  --dry-run \
  --hermes-db "$OCI_DATA_DIR/hermes/runs_idempotency.db" \
  --relay-db "$OCI_DATA_DIR/relay/task-ledger.sqlite3" \
  --facade-db "$OCI_DATA_DIR/results/facade/facade-ledger.sqlite3"
```

The inventory matches Hermes `idempotency_key` values to Relay/Facade task keys and Hermes `run_id` values to their saved Run IDs. Relay sends the task ID as the `Idempotency-Key`; Facade persists the same task/run binding. The script parses `status_json` only in memory to count allowlisted status names and classifies everything else as `unknown`. A successful read is **not** a cutover approval. Any unresolved/unknown Run, unmatched registry row, binding conflict, absent registry, incomplete schema, or SQLite read failure blocks cutover until safely reconciled. The command does not call the Runs API or any model/provider.

The registry covers only keyed Runs whose records remain in the durable SQLite store. The pinned implementation uses a 24-hour default retention and a 24-hour acknowledged retention; stale rows are pruned lazily during later reserve/lookup operations, and only after a Run is terminal. Active grants may extend retention. The API server can fall back to in-memory idempotency storage if SQLite cannot be opened, so a missing/empty file or an observed startup fallback is not evidence that no Run exists. This registry also cannot prove the absence of arbitrary non-idempotent work. If the runtime's durable backing cannot be established, or any row cannot be mapped to the two ledgers, leave services stopped and ingress closed for an owner-controlled investigation. Do not print raw rows or invent a list endpoint.

The route and store behavior above were checked against the pinned source: [`api_server_runs.py`](https://github.com/NousResearch/hermes-agent/blob/f97608f178d1ffeca59860195ab7da295f7c8e5f/gateway/platforms/api_server_runs.py) and [`api_server_run_idempotency.py`](https://github.com/NousResearch/hermes-agent/blob/f97608f178d1ffeca59860195ab7da295f7c8e5f/gateway/platforms/api_server_run_idempotency.py).

## One-time operator-controlled retirement

The smallest safe strategy is to leave the two terminal v1 rows and their existing artifacts in place, pinned to their historical contract, and make them ineligible for v2 execution. Do not migrate a v1 payload into a fabricated v2 result, initialize over the current databases, delete rows, or remove WAL files. No schema rewrite is required. Physical row/artifact cleanup, if ever wanted, needs a separate retention and evidence decision after this backup.

1. Identify the currently active release directory, Compose project, `.env` file, Hermes config, image IDs/digests, and mounted data paths. In particular inspect the existing local Relay image override (for example `RELAY_IMAGE=amusant-relay:oci-arm64`) and all versioned release directories; do not assume the checked-in `compose.yaml` or a shell's current directory is the live deployment. Record which plugin directory is mounted read-only. Do not overwrite a shared local image tag while services still use it.
2. Close or pause the operator-controlled ingress for new Slow admissions. Run the dry-run Relay/Facade precheck and the read-only Hermes Runs registry inventory against the active mounted databases. Wait for/reconcile every active or uncertain task using authenticated evidence; never clear a slot based only on time or a timeout. Any queued, running, uncertain, submitting, unknown, unmatched, conflicting, or nonterminal remote state blocks cutover.
3. After both inventories are clear, stop Relay first to prevent new admissions, then stop Facade and Hermes. Keep the Egress proxy and persistent volumes intact until the backup completes. Run both read-only inventories again against the stopped services' mounted data. If any count changes, any finding appears, or a database becomes unavailable, do not continue.
4. Create one operator-controlled, encrypted, access-restricted archive from the stopped services' complete data roots: `$OCI_DATA_DIR/relay`, `$OCI_DATA_DIR/results/facade` (including the Facade ledger, artifacts, and any SQLite `-wal`/`-shm` files), and `$OCI_DATA_DIR/hermes` (including Hermes's own Run/session state). Include the active release's Compose file, mounted plugin source, non-secret runtime configuration, image references, and a manifest of secret file paths/versions; keep secret values in the existing secret manager. Do not copy individual SQLite main files while their services are writing. Keep the original data and release directories untouched.
5. Verify the archive can be listed, record its SHA-256 in the operator evidence, and run `PRAGMA quick_check` on database copies from the archive in an isolated read-only verification location. Verify the artifact directory and Hermes Home are present. Keep a separate encrypted off-host copy and confirm it is readable before cutover. This PR does not execute these steps against OCI.
6. Prepare a new versioned release directory from one source revision. Build/publish the matching Relay and Facade images; place the matching mounted `deploy/oci-a1/hermes_plugin` and `compose.yaml` there; keep the reviewed Hermes config and pinned Hermes image; preserve the existing isolated networks, secrets, volumes, and writer boundary. Do not copy the old v1 POC database into the runtime volume. Validate the rendered Compose file and image IDs before starting services.
7. Start the complete bundle in dependency order: Hermes and egress proxy, Facade, then Relay. Confirm the new Relay image, Facade image, mounted plugin directory, Compose release path, and persistent volume paths are the intended set. Confirm new Hermes admissions have saved contract version `2`, then reopen admission only after operator health checks pass.

### Local Relay image override and release directories

The repository build example uses the local tag `amusant-relay:oci-arm64`; an OCI `.env` may override `RELAY_IMAGE` with that tag instead of the immutable reference expected in a fresh deployment. Before any image switch, resolve the effective Compose image list using the active release's exact `--env-file` and `-f` arguments, then inspect the referenced local image ID/digest. Build the candidate with a release-specific tag or immutable digest, update only the new release's env file, and retain the previous image/tag and release directory until the rollback window closes. Do not retag a shared local Relay image in place. Apply the same release-directory discipline to the Facade, plugin mount, Compose file, and Hermes config; a Relay-only upgrade is incompatible with removal of the other components' v1 branches.

## Rollback

Rollback is a coordinated release-and-data operation. Prefer the last **v2-only** release and restore its matching Relay image, Facade image, mounted plugin, Compose file, Hermes configuration, and compatible data snapshot together. Keep ingress closed until all checks below pass.

The reviewed source baseline `a416ff3` can run v2, but it is **not v2-by-default**: its Relay defaults to v1, and its Compose file does not forward `AMUSANT_RESULT_CONTRACT_VERSION` from `.env`. Setting that value in `.env` alone has no effect on the old Relay process. If this baseline is the only retained compatible release, use a release-local Compose override to inject the selector into Relay's actual process environment:

```yaml
services:
  relay:
    environment:
      AMUSANT_RESULT_CONTRACT_VERSION: "2"
```

Use that override with the rollback release's exact `.env` and Compose file for every Compose command. Before starting services, require the rendered configuration to contain the Relay value:

```sh
docker compose --env-file "$ROLLBACK_DIR/.env" \
  -f "$ROLLBACK_DIR/compose.yaml" \
  -f "$ROLLBACK_DIR/compose.v2-rollback.yaml" \
  config --format json \
  | jq -e '.services.relay.environment.AMUSANT_RESULT_CONTRACT_VERSION == "2"' >/dev/null
```

Start the coordinated bundle only while operator ingress remains closed. Use the same `--env-file` and `-f` arguments to verify the running Relay's configured environment and loaded process config:

```sh
relay_id=$(docker compose --env-file "$ROLLBACK_DIR/.env" \
  -f "$ROLLBACK_DIR/compose.yaml" \
  -f "$ROLLBACK_DIR/compose.v2-rollback.yaml" ps -q relay)
test -n "$relay_id"
docker inspect "$relay_id" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -Fx 'AMUSANT_RESULT_CONTRACT_VERSION=2' >/dev/null
docker exec -i "$relay_id" /app/.venv/bin/python - <<'PY'
import os
from pathlib import Path
from relay.application import load_relay_process_config

token = Path(os.environ["AMUSANT_DEVICE_TOKEN_FILE"]).read_text(encoding="utf-8").rstrip()
config = load_relay_process_config(os.environ, expected_device_token=token)
assert config.result_contract_version == 2
print("Relay process configuration selects Result Contract v2")
PY
```

Before reopening ingress, run the native ARM64 Hermes→Facade→Relay/Inbox integration proof in an isolated environment against the selected release's exact image digests with fake providers. Confirm a newly admitted task persists contract version `2` and its first Facade submit carries header `2`. Do not use a live provider for this gate. If the candidate release cannot run that proof, or any gate fails, keep services stopped and ingress closed; do not silently fall back to a v1-default binary.

Do not treat v1 as a normal product mode, and do not choose a v1-only image or plugin. The result-version selector is only a compatibility input for that explicitly reviewed old release override; it is not a supported runtime toggle.

Never roll back while any remote Hermes Run is unresolved, any Relay task is queued/running/uncertain, any Facade request has unknown/nonterminal Hermes status, or new v2 state would be lost, overwritten, or misread by the selected release. Stop new admissions, re-run both status inventories, and wait for terminal confirmation. Preserve the post-cutover databases and artifacts before any restore. Restore a coordinated snapshot only when the chosen v2-capable release understands every v2 row and artifact reference in that snapshot. If that cannot be proven, leave the system stopped and escalate for an explicit data-recovery plan; do not point a v1-only release at v2 state.

## Regression proof, risks, and limits

Targeted tests cover default v2 admission and prompt, no-Artifact short answers, committed reports, truthful writer failure, v1 schema/header rejection, malformed/oversized/forged results, owner/session/Run/reference binding, report privacy, idempotent POST and GET-only restart, SQLite/DynamoDB durable Run state and single-flight, plus FAST and spoken delivery regressions. Current CI replaces the old Issue #79 v1 acceptance job with the focused v2 test suites; the native integrated E2E and ARM64 gate now use the v2 proof. Draft CI skips heavy jobs, so before merge require full CI and the separate native ARM64 Hermes→Facade→Relay/Inbox proof to pass on the same head SHA. The historical v1 POC executable sources are removed; its sanitized summary remains separate from current acceptance evidence.

Material limitations:

- The Relay/Facade precheck does not inspect Hermes's idempotency registry. The separate inventory matches keyed records across all three ledgers, but cannot identify arbitrary non-idempotent or in-memory Runs.
- This PR does not access OCI, validate the current deployment inventory, modify production databases, restart containers, or verify an off-host restore. Repeat the precheck at deployment time.
- Result Contract v2 prompts Hermes to use the Artifact writer for a requested report. If no writer attempt reaches Facade, an ordinary bounded answer may complete without an Artifact; the runtime will not claim a report was saved. Any observed writer failure is surfaced as failure.
- Hermes execution completion and Artifact commit are separate events. The authenticated writer ledger and Artifact integrity/bindings remain the source of preservation evidence.
- Full-document retrieval remains outside Relay/Inbox; the result envelope provides only a bounded short presentation and optional artifact reference.
