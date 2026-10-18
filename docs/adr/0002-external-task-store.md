> **Supersession note (2026-10-18):** [ADR-0003](0003-oci-a1-hermes-sqlite.md) selects persistent **SQLite** for the *new OCI A1 target*. DynamoDB remains the historical AWS/Fargate durability solution, supported adapter and optional explicit rollback. Existing AWS table/data are untouched; migration is not automatic. SQLite off-host backup, retention and recovery gates must pass before unattended operation.

# ADR 0002 — External Task Store (design, unapproved)

**Status:** Accepted as design (PR #18). Offline adapter code may follow without creating a DynamoDB table, IAM role, VPC endpoint, or paid AWS resource.

**Date:** 2026-10-12

**Context:** P4-A1 live one-off PASS proved real Dispatcher → OpenAB ACP → bounded Inbox on a one-off Fargate Relay. The Inbox lived in container-local SQLite. When ECS A STOPPED, that ledger died with the task. That is not ECS-replacement durability.

## 1. Decision sought

Approve a **small single-owner Task Ledger + Inbox** in DynamoDB so a later one-off can prove: *write a completed bounded result, stop A, still read it*.

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

Primary-key order is **not** time order. `TASK#<uuid>` Query with `Limit=1` returns a lexical UUID, not the newest task. Do not use SK ordering for latest-for-owner or oldest-queued.

**Attributes on `TASK#` (minimum):** `task_id`, `client_id`, `conversation_id`, `turn_id`, `call_id`, `payload_sha256`, `task_kind`, `backend`, `status` (`queued|running|completed|failed|uncertain`), `created_at`, `expires_at`, `outbox_state`, `attempts`, `claimed_until`, `dispatch_started_at`, `speak`, `card_json`, `details_md`, `delivery_state` (`pending|notified|read|spoken`), `retain_until`.

Task text is stored because admission and Dispatcher claim need it. Bound `task` ≤ 4096 characters. Bound `speak` / `card` / `details_md` as today.

**Do not persist:** PCM, Gemini transcripts, chain-of-thought, agent history, web/shell traces, secrets, ACP bearer, raw prompt dumps beyond the bounded task string, account IDs in GitHub.

`sessionId` is optional diagnostic only, never the lookup key.

### Access patterns (first slice)

Single owner, low volume, **single-writer Relay**. No table `Scan`. No GSI in the first slice.

1. **Get by id (`check_task_status` with `task_id`):** strongly consistent `GetItem` on `PK=client_id`, `SK=TASK#<task_id>`. This is the A1-style proof read.
2. **Latest for owner (no `task_id`):** paginated `Query` `PK=client_id` and `begins_with(SK,'TASK#')` until the page bound, then pick max `created_at` in process. Do **not** use `Limit=1` on the base table. First-slice bound: at most 100 `TASK#` items or 1 MB of pages, whichever comes first. If the bound is hit, fail closed (`AdmissionCapacity` / lookup error), do not guess. A sparse GSI on `client_id` + `created_at#task_id` is a later option if this bound is too small; it is eventual-consistent and needs its own IAM index ARN. Not part of the first table envelope.
3. **`claim_due` (oldest eligible queued):** same bounded owner Query, filter `status=queued`, not expired, `outbox_state` pending/unclaimed; pick **oldest** `created_at`, then conditional `UpdateItem`. UUID sort order is irrelevant.
4. **Pending capacity:** before admit, count `status=queued` on that same bounded Query. If count ≥ `max_pending_tasks`, `AdmissionCapacity`. First slice relies on the single-writer assumption; competing multi-writer admission needs a capacity counter item, not SQS.

### Dedupe

`TransactWrite` of `TASK#` + `DEDUP#` with `attribute_not_exists` on the DEDUP SK.

`DEDUP#` attributes: `task_id`, `payload_sha256`, `retain_until` (same epoch as the TASK item). Do not leave a permanent DEDUP after TASK TTL.

- Same fingerprint on an existing DEDUP → return that `task_id` (duplicate admit).
- Different fingerprint on the same call identity → `AdmissionConflict`.
- Transaction network/commit **ambiguous**: strongly consistent `GetItem` on the DEDUP item. If it matches, return that receipt. If missing or mismatched, `AdmissionUnknown`. Never automatically re-admit.
- `ClientRequestToken` is a ~10 minute idempotency window. It does **not** replace durable DEDUP.

### Claim and crash recovery (match P3)

Copy `SQLiteTaskStore.recover_after_restart()`:

- Persist `dispatch_started_at` with a conditional write **before** the ACP network send.
- On restart / replacement: `dispatch_started_at` absent → requeue (`queued` / outbox pending). `dispatch_started_at` set → `uncertain`, `acp_result_unconfirmed`, **no automatic resend**.
- `claimed_until` is a short in-process claim lock (today 30s). It may expire while a 120s ACP call is still running. **Expired lease must not reclaim post-dispatch work** and must not start a second paid `session/prompt`.
- Bounded attempts as in SQLite. Ambiguous store commit after dispatch → `uncertain`, never fake `completed`.

### TTL

DynamoDB TTL attribute `retain_until` is a Unix-epoch **Number** (proposed 30 days after terminal state). Deletion is asynchronous. Application expiry of pending tasks remains in Relay code; TTL is garbage collection, not the honesty of `expired`.

## 4. Access and IAM

- Only the Relay process (ECS A task role) reads/writes this table. The after-stop proof reader uses a separately named admin/one-off role with `GetItem` (and Query if needed) on the same table, not ECS Exec.
- Least privilege: `GetItem`, `PutItem`, `UpdateItem`, `Query`, `TransactWriteItems` on that table ARN only. No `Scan`, no `DeleteTable`, no `*`. No GSI ARN until a GSI exists.
- A1 smoke used **no taskRole**. A store-backed run **requires a new task role**. That is a separate envelope, not this ADR.
- Owner isolation is application-enforced (`client_id` from server config, not from the device). Wrong `client_id` must not return another owner's row.

No ECS Exec. No shared `oab-task-role` dump of extra services.

## 5. Network

Do not assume future Fargate tasks keep assign-public-IP.

| Path | When |
|---|---|
| DynamoDB public endpoint | Acceptable only for an explicit first one-off envelope that already has public IP |
| **Gateway VPC endpoint** | Preferred first *private* same-VPC option; no extra hourly endpoint charge |
| Interface VPC endpoint | Later, if cross-VPC / on-prem is actually required; metered |

Public vs gateway vs interface is an **explicit later deployment choice**. This ADR does not create an endpoint.

## 6. One-off proof (future envelope, not this PR)

**Functional ACP/store PASS** only if all hold:

1. Table empty or owned solely by this experiment.
2. One-off A admits one Slow Task through the real Dispatcher/ACP path.
3. A reaches `status=completed` with persisted bounded `speak` / `card` / `details_md` and Inbox state, then STOPPED.
4. **After A is STOPPED**, a second reader (admin CLI or a second one-off) does a **strongly consistent** base-table `GetItem` for the same trusted owner/`task_id` and reads that completed bounded result.
5. The second reader does **not** start Jellii and does **not** send a second `session/prompt`.
6. If Jellii was woken for the writer, it returns to desiredCount 0.

If the durable row is honest `failed` or `uncertain` after A STOPPED, that is **failure-state persistence evidence**, not functional ACP/result PASS. Same distinction as `relay.acp_smoke` EXIT_PASS vs EXIT_FAILED / EXIT_UNCERTAIN.

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

- *Code slice:* Store-ready Relay is on main (PR #19). Default remains SQLite. Application code never calls CreateTable.
- *Durability one-off:* executed 2026-10-12. Functional PASS is recorded in `STATUS.md`. Table kept. 24/7 Relay is not authorized by that proof.
- Reject expanding the table into chat memory, artifacts, or audio.
