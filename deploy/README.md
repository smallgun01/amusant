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

## Not in this package

- CreateTable
- IAM role creation
- Public ALB
- 24/7 desiredCount
- TLS
