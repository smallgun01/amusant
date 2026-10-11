# ADR 0002 — External Task Store (design, unapproved)

**Status:** Proposed. Documentation only. This ADR does **not** create a DynamoDB table, IAM role, VPC endpoint, or paid AWS resource.

**Date:** 2026-10-12

**Context:** P4-A1 live one-off PASS proved real Dispatcher → OpenAB ACP → bounded Inbox on a one-off Fargate Relay. The Inbox lived in container-local SQLite. When ECS A STOPPED, that ledger died with the task. That is not ECS-replacement durability.

## 1. Decision sought

Approve a **small single-owner Task Ledger + Inbox** in DynamoDB so a later one-off can prove: *write completed result, stop A, still read it*.

Not sought here: 24/7 Relay, device ingress, TLS, production sandbox, SQS, RDS, S3-as-ledger.

## 2. Why DynamoDB

ADR-0001 already names DynamoDB as the preferred small-store candidate. It matches the actual workload:

- One owner, handful of Slow Tasks, not chat history.
- Need atomic admission + dispatch intent, conditional claim, and Get by `task_id` or latest-for-owner.
- Need the bytes to outlive Fargate replacement.

Rejected for this ledger:

| Option | Why not |
|---|---|
| Container SQLite | Dies with the task (A1) |
| RDS / Aurora | Operator and cost mass for a few rows |
| S3 | Not a claim/lease/Inbox store |
| SQS | Queue, not the system of record |
| OpenAB `sessionId` | Conversation context, not a durable job key |

## 3. What is stored

Mirror the existing SQLite schema (`slow_tasks`, `dispatch_outbox`, `task_results`, `task_inbox`). One DynamoDB table, two item types.

**Partition key:** `client_id` (owner scope).
**Sort key:**

- `TASK#<task_id>` — ledger + outbox + bounded result + delivery_state
- `DEDUP#<conversation_id>#<turn_id>#<call_id>` — idempotent admission

**Attributes on `TASK#` (minimum):** `task_id`, `client_id`, `conversation_id`, `turn_id`, `call_id`, `payload_sha256`, `task_kind`, `backend`, `status` (`queued|running|completed|failed|uncertain`), `created_at`, `expires_at`, `outbox_state`, `attempts`, `claimed_until`, `speak`, `card_json`, `details_md`, `delivery_state` (`pending|notified|read|spoken`), `retain_until`.

Task text is stored because admission and Dispatcher claim need it. Bound `task` ≤ 4096 characters. Bound `speak` / `card` / `details_md` as today.

**Do not persist:** PCM, Gemini transcripts, chain-of-thought, agent history, web/shell traces, secrets, ACP bearer, raw prompt dumps beyond the bounded task string, account IDs in GitHub.

`sessionId` is optional diagnostic only, never the lookup key.

**Latest lookup:** Query `client_id` on `TASK#` items, newest `created_at`. Do not scan the table.

**Dedupe:** `TransactWrite` of `TASK#` + `DEDUP#` with `attribute_not_exists`. Same fingerprint → return existing `task_id`. Different fingerprint on the same call identity → conflict, same as SQLite.

**Claim:** conditional `queued`/`claimed` → `running` with `claimed_until`. Expired lease may be reclaimed. Ambiguous commit → `uncertain`, never fake `completed`.

**TTL:** DynamoDB TTL on `retain_until` (proposed 30 days after terminal state). Application expiry of pending tasks remains in Relay code; TTL is garbage collection, not the honesty of `expired`.

## 4. Access and IAM

- Only the Relay process (ECS A task role) reads/writes this table.
- Least privilege: `GetItem`, `PutItem`, `UpdateItem`, `Query`, `TransactWriteItems` on that table ARN only. No `Scan`, no `DeleteTable`, no `*`.
- A1 smoke used **no taskRole**. A store-backed run **requires a new task role**. That is a separate envelope, not this ADR.
- Owner isolation is application-enforced (`client_id` from server config, not from the device). Wrong `client_id` must not return another owner's row.

No ECS Exec. No shared `oab-task-role` dump of extra services.

## 5. Network

Fargate A already reaches AWS APIs (public IP on the existing task network). First proof may use the DynamoDB public endpoint.

A later production envelope may add a VPC interface endpoint. Do not create it in the first table envelope unless Arthur explicitly includes it.

## 6. One-off proof (future envelope, not this PR)

PASS only if all hold:

1. Table empty or owned solely by this experiment.
2. One-off A admits one Slow Task, reaches terminal Inbox state, STOPPED.
3. **After A is STOPPED**, a second reader (admin CLI or a second one-off with no Jellii) `GetItem`s the same owner/`task_id` and sees `status=completed` (or honest `failed`/`uncertain`) plus bounded result.
4. Jellii returns to desiredCount 0 if it was woken.
5. No second `session/prompt` on failure.

That envelope must separately name: table name, billing cap, task role, image pin, Jellii 1→0 if ACP is in path, and cleanup (table keep vs delete).

A fixture write without ACP does **not** prove the live Slow Path. A live ACP write whose only copy is still SQLite does **not** prove the store.

## 7. What this does not change

- Two ECS Services only. Dispatcher stays in Relay. No Worker ECS.
- Continue one-off until 24/7 is separately approved.
- SQLite remains the offline/unit-test backend.
- Packaging (version-controlled Dockerfile/taskdef) is a separate P4 track.

## 8. Cost shape (order of magnitude, not a quote)

On-demand DynamoDB for a handful of items is typically cents, not the model bill. Exact spend is **unverified until Cost Explorer is pulled after a real table exists**. Do not treat this paragraph as a budget.

## 9. Consequences

- Next *code* slice (after this ADR is accepted as design, still before table): a `TaskStore` protocol implemented by SQLite and a DynamoDB adapter, with fake-endpoint tests. No AWS create in that PR unless Arthur's envelope says so.
- Next *AWS* slice: explicit envelope for table + task role + one-off write/read-after-stop.
- Reject expanding the table into chat memory, artifacts, or audio.
