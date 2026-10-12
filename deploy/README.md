# Store-ready Relay packaging

This directory is a **template**. It does not register a task definition or create a DynamoDB table.

- `../Dockerfile` — Fargate image. `uv` from PyPI. Default command is `python -m relay.acp_smoke`.
- `oab-amusant-relay.taskdef.json` — replace `REPLACE_*` placeholders before `register-task-definition`.

## Store switch

- Default: `AMUSANT_TASK_STORE=sqlite` and `AMUSANT_TASK_DB`.
- DynamoDB: `AMUSANT_TASK_STORE=dynamodb`, `AMUSANT_DDB_TABLE`, `AMUSANT_DDB_REGION` or `AWS_REGION`. Do **not** set this until a table and task role exist under a separate envelope.
- A DynamoDB-backed task **requires** a task role with `GetItem/PutItem/UpdateItem/Query/TransactWriteItems` on that table only. SQLite one-off may omit `taskRoleArn` (A1).

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

## Not in this package

- CreateTable
- IAM role creation
- Public ALB
- 24/7 desiredCount
- TLS
