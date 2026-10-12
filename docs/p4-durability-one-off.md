# P4 durability one-off envelope

**Status:** templates + offline reader. Merging this document is **not** CreateTable, IAM, ECR, Fargate, or Jellii scale.

This is ADR-0002 §6: write a completed bounded Inbox row through the real Dispatcher/ACP path, stop A, then strongly consistent `GetItem` still returns it.

## Functional PASS

All must hold:

1. Table empty or owned solely by this experiment.
2. One-off A admits one Slow Task through Dispatcher → OpenAB ACP.
3. A reaches `status=completed` with persisted bounded `speak` / `card` / `details_md` and Inbox state, then STOPPED.
4. After A is STOPPED, `python -m relay.store_get` (admin/one-off reader) does a strongly consistent read for the same owner/`task_id` and reports `exit_code=0`, `status=completed`, `has_result=true`.
5. The reader does **not** start Jellii and does **not** send `session/prompt`.
6. If Jellii was woken for the writer, it returns to desiredCount 0.

Honest durable `failed` / `uncertain` after A STOPPED is failure-state persistence, not functional PASS.

A fixture write without ACP is not Slow Path proof. An ACP write that only exists in SQLite is not store proof.

## Fill before any AWS mutate

A pasted template with blanks is not approval. Mutate only after Arthur names every field and says to execute.

| Field | Value |
|---|---|
| Source SHA / image pin | fill at execute time |
| Table name | `REPLACE_TABLE_NAME` |
| Region | `us-east-1` |
| TTL | `retain_until` epoch Number; enable after CreateTable |
| Relay task role | `REPLACE_RELAY_TASK_ROLE` using `deploy/amusant-relay-task-role.iam.json` |
| Reader identity | separate admin/one-off role using `deploy/amusant-store-reader.iam.json` |
| Writer taskdef | `deploy/oab-amusant-relay-dynamodb.taskdef.json` |
| ACP URL | inject `REPLACE_ACP_WSS_URL` at register/run; prefer `wss://` |
| Jellii | 1 then restore 0 if ACP is in path; awake cap fill at execute |
| Model budget | one `session/prompt`, no automatic retry |
| ECS A spend ceiling | fill at execute (image + one-off Fargate, not the model bill unless named) |
| Table billing cap | fill at execute |
| Cleanup | keep vs delete table; restore Jellii 0; stop A |

## Allowed actions (when execute is granted)

- Create the named table from `deploy/amusant-task-ledger.table.json` (PAY_PER_REQUEST, `pk`/`sk` only). No GSI. No Scan. Application code still never calls CreateTable.
- Create the named least-privilege task role and reader role from the IAM templates. Not `oab-task-role`.
- Pin a private ECR image from the in-repo Dockerfile at the named SHA.
- One-off Fargate A with the DynamoDB taskdef. One ACP prompt. Stop A.
- After STOPPED, run `python -m relay.store_get` with reader credentials. No second prompt.

## Prohibitions

- No 24/7 Relay Service, no idle 50ms DynamoDB Query as a permanent worker.
- No SQS, no Worker ECS, no ALB, no Gemini Live, no ECS Exec.
- No public ACP. No disabling TLS verification.
- No second `session/prompt`. No auto-retry after `dispatch_started_at`.
- Do not commit account IDs, IPs, secret ARNs, bearer, prompt text, result bodies, ECS task IDs, or log stream names.

## Reader

```text
AMUSANT_TASK_STORE=dynamodb
AMUSANT_DDB_TABLE=REPLACE_TABLE_NAME
AMUSANT_DDB_REGION=us-east-1
AMUSANT_CLIENT_ID=amusant-a1
AMUSANT_TASK_ID=<uuid>
python -m relay.store_get
```

Prints `exit_code`, `task_id`, `status`, `delivery_state`, `error`, `has_result`. Does not print speak/card/details. Does not admit or dispatch.

## Not authorized by this PR

CreateTable, IAM, ECR push, Fargate run, Jellii scale, Cost Explorer, 24/7, device ingress, TLS production cutover.
