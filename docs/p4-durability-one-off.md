# P4 durability one-off envelope

**Status:** templates + offline reader. Merging this document is **not** CreateTable, IAM, ECR, Fargate, or Jellii scale.

This is ADR-0002 §6: write a completed bounded Inbox row through the real Dispatcher/ACP path, stop A, then strongly consistent `GetItem` still returns it.

## Functional PASS

All must hold:

1. Table empty or owned solely by this experiment.
2. One-off A admits one Slow Task through Dispatcher → OpenAB ACP.
3. A reaches `status=completed` with persisted bounded `speak` / `card` / `details_md` and Inbox state, then STOPPED.
4. Operator records A STOPPED from live `describe-tasks` (UTC started/stopped, container `exitCode`). `python -m relay.store_get` cannot certify that the writer stopped.
5. After that STOPPED evidence, `python -m relay.store_get` does a strongly consistent AWS DynamoDB read for the same owner/`task_id` and reports `exit_code=0`, `status=completed`, `has_result=true`. SQLite, moto, and `AMUSANT_DDB_ENDPOINT` are not this gate.
6. The reader does **not** start Jellii and does **not** send `session/prompt`.
7. If Jellii was woken for the writer, it returns to desiredCount 0.

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
| On-demand max RRU / WRU | `REPLACE_MAX_READ_RRU` / `REPLACE_MAX_WRITE_WRU`; throughput cap is **not** a dollar cap |
| Spend / elapsed / cleanup guard | fill at execute; keep vs delete table; AWS Budget alert is not a hard cutoff |
| Writer permission policy | `deploy/amusant-relay-task-role.iam.json` (`PutItem` covers transactional Put; no `dynamodb:TransactWriteItems` IAM action) |
| Writer trust | `deploy/amusant-relay-task-role.trust.json` (`ecs-tasks.amazonaws.com`, `aws:SourceAccount`, `aws:SourceArn`) |
| Reader permission policy | `deploy/amusant-store-reader.iam.json` (GetItem/Query only) |
| Reader trust / principal | `deploy/amusant-store-reader.trust.json` (`REPLACE_READER_PRINCIPAL_ARN`) |
| Task **execution** role | existing `oab-task-execution` for ECR / CloudWatch Logs / Secrets Manager; not the ledger task role; not Jellii's broad role |
| Writer taskdef | `deploy/oab-amusant-relay-dynamodb.taskdef.json` |
| ACP transport go/no-go | prove live Jellii `wss://` + trusted cert, **or** request a new time-bounded private `ws://` exception; do not silently reuse A1 plaintext |
| VPC path | subnet, SG, and DynamoDB access (public egress vs gateway endpoint) |
| Jellii | 1 then restore 0 if ACP is in path; awake cap fill at execute |
| Model budget | one `session/prompt`, no automatic retry |
| ECS A spend ceiling | fill at execute (image + one-off Fargate, not the model bill unless named) |

## Allowed actions (when execute is granted)

- Create the named table from `deploy/amusant-task-ledger.table.json` (PAY_PER_REQUEST, `pk`/`sk` only, on-demand max RRU/WRU filled). No GSI. No Scan. Application code still never calls CreateTable.
- Create **roles**: attach the permission policy to a new role whose **trust** is the matching `.trust.json`. Not `oab-task-role`. Not Jellii's task role.
- Pin a private ECR image from the in-repo Dockerfile at the named SHA.
- One-off Fargate A with the DynamoDB taskdef. One ACP prompt. Stop A.
- After independent STOPPED evidence, run `python -m relay.store_get` with the reader role. No second prompt.

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

Requires explicit `AMUSANT_TASK_STORE=dynamodb`. Rejects `AMUSANT_DDB_ENDPOINT`. Prints `exit_code`, `task_id`, `status`, `delivery_state`, `error`, `has_result`. Does not print speak/card/details. Does not admit or dispatch.

## Not authorized by this PR

CreateTable, IAM, ECR push, Fargate run, Jellii scale, Cost Explorer, 24/7, device ingress, TLS production cutover.
