# Store-ready Relay packaging

This directory is a **template**. It does not register a task definition or create a DynamoDB table.

- `../Dockerfile` — Fargate image. `uv` from PyPI. Current default command is **`python -m relay.wss_preflight_smoke`** (P4-T4b bounded preflight + ACP one-off), **not** the long-running Relay Service entrypoint.
- `oab-amusant-relay.taskdef.json` — replace `REPLACE_*` placeholders before `register-task-definition`.

## Live evidence / template warning (2026-10-13)

- P4-T4b actually deployed Jellii:10 (OpenAB+NGINX) and a standalone Relay Fargate task, passed private WSS/ACP + eight preflight checks + after-stop DynamoDB reader proof, then restored Jellii:9/0/0/0. See [T4b acceptance](../docs/p4-t4b-live-acceptance.md).
- The actual registered task definitions used **rendered** task-role, secret, log group, immutable image, CA and per-run IP values. The checked-in JSON files remain incomplete templates (including old shared role/log references): **never register them verbatim.** Do not commit private ARNs, bearer keys or leaf key.
- Retained T4b ECR/secret/log/role/task-definition assets do not establish that a short-lived TLS leaf certificate is still valid. No long-running Relay Service is deployed; further AWS writes need fresh Owner authorization.

## Store switch

- Default: `AMUSANT_TASK_STORE=sqlite` and `AMUSANT_TASK_DB`.
- DynamoDB: `AMUSANT_TASK_STORE=dynamodb`, `AMUSANT_DDB_TABLE`, `AMUSANT_DDB_REGION` or `AWS_REGION`. Do **not** set this until a table and task role exist under a separate envelope.
- A DynamoDB-backed task requires the existing table-scoped `dynamodb:GetItem`, `PutItem`, `UpdateItem`, `Query` permissions. Do not add a fictional `dynamodb:TransactWriteItems` IAM action. SQLite A1 historically omitted `taskRoleArn`.

## ACP URL

The taskdef defaults to fail-closed `wss://` via `AMUSANT_ACP_URL=REPLACE_ACP_WSS_URL`. Inject the private endpoint at register/run time. Do **not** commit real hosts or IPs.

`AMUSANT_ACP_ALLOW_PLAINTEXT=1` is **not** in the template. It is only a separately approved one-off runtime override, as in the 2026-10-12 A1 envelope.

## Durability one-off templates

See `../docs/p4-durability-one-off.md`. These files do not create AWS resources:

- `amusant-task-ledger.table.json` (PAY_PER_REQUEST + `REPLACE_MAX_*` on-demand caps)
- `amusant-relay-task-role.iam.json` and `.trust.json`
- `amusant-store-reader.iam.json` and `.trust.json`
- `oab-amusant-relay-dynamodb.taskdef.json`
- After-stop reader: `python -m relay.store_get` (DynamoDB only; no custom endpoint)

## P4-T4b controller pre-run gate

Before any authorized one-off Relay RunTask, run the read-only controller-side actual-task SG check in [docs/p4-t4b-security-group-gate.md](../docs/p4-t4b-security-group-gate.md). The Relay TCP/8080 reachability probe is not proof of Jellii ingress isolation. A missing/failed/unknown SG check is a stop-before-prompt condition. In the 2026-10-13 Live window the controller performed equivalent real ENI/SG checks and reported PASS, but **this standalone checker was not run against AWS**.

## Not in this package

- CreateTable
- IAM role creation
- Public ALB
- 24/7 desiredCount
- The TLS sidecar **has source in this repo and a bounded AWS live smoke PASS**, but the templates are not a complete reusable TLS provisioning package.
