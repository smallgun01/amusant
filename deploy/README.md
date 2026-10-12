# Store-ready Relay packaging

This directory is a **template**. It does not register a task definition or create a DynamoDB table.

- `../Dockerfile` — Fargate image. `uv` from PyPI. Default command is `python -m relay.acp_smoke`.
- `oab-amusant-relay.taskdef.json` — replace `REPLACE_*` placeholders before `register-task-definition`.

## Store switch

- Default: `AMUSANT_TASK_STORE=sqlite` and `AMUSANT_TASK_DB`.
- DynamoDB: `AMUSANT_TASK_STORE=dynamodb`, `AMUSANT_DDB_TABLE`, `AMUSANT_DDB_REGION` or `AWS_REGION`. Do **not** set this until a table and task role exist under a separate envelope.
- A DynamoDB-backed task **requires** a task role with `GetItem/PutItem/UpdateItem/Query/TransactWriteItems` on that table only. SQLite one-off may omit `taskRoleArn` (A1).

## Not in this package

- CreateTable
- IAM role creation
- Public ALB
- 24/7 desiredCount
- TLS
