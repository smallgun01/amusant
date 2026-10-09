# ADR-0001 — OpenAB ACP Slow Path for Amusant

- **Status:** Proposed for review (architecture-only PR; no runtime implementation yet)
- **Date:** 2026-10-09
- **Scope:** P3 local Slow Path, P4 cloud-deployment design
- **Related:** [SPEC.md](../../SPEC.md), [PLAN.md](../../PLAN.md), [Issue #1](https://github.com/smallgun01/amusant/issues/1)
- **Research baseline:** OpenAB [commit 6431a1b6](https://github.com/openabdev/openab/tree/6431a1b6991673429f344acb78a58d94658d30fb), Kai pinned by SPEC
- **Decision owner:** Amusant project

## Context and product boundary

Amusant is a grab-to-talk, bring-your-own-agent voice frontend. The existing Gemini Live fast voice loop remains unchanged. P3 should add asynchronous tool delegation without connecting the public Relay to the owner's high-privilege, personal OpenClaw Gateway on the N100.

OpenAB is an **ACP agent broker**, not by itself a full personal assistant with durable memory. It is chosen as the **first isolated slow-task runtime**, not as the owner of Amusant device sessions, user identity, durable task status, or inbox. Future backends (including an independently secured OpenClaw adapter) must not require rewiring Gemini's tool interface.

## Decision summary

1. Gemini Live owns conversational FAST/SLOW selection; no separate classifier in the critical path.
2. One backend-neutral tool, `delegate_task`, admits a bounded **text** task. The Relay authenticates the client, validates schema/size/policy, selects the backend **server-side**, and persists a new task.
3. Gemini receives the **admission result** with the **same function-call ID** after the task is durably recorded: `accepted + task_id` or an explicit rejection/unavailable response. It **never waits for the ACP agent to finish**.
4. A separate Amusant Worker invokes OpenAB's existing authenticated **ACP-over-WebSocket GET /acp** endpoint using `initialize` → `session/new` → `session/prompt`. Do not invent a second agent wire protocol or forward Gemini audio to the slow runtime.
5. Amusant (not OpenAB) owns durable Task Registry, retries/idempotency, Result Inbox, and notification/delivery state. Task completion is **not a second response** to the original Gemini function-call ID.
6. P3 uses local-only services, SQLite, a worker and a verified OpenAB ACP build. P4 (separately authorized) introduces AWS ECS services, DynamoDB and SQS. No N100 bridge in the P3 default path.
7. P3 enables only a **single configured, restricted ACP agent** for low-risk text/research/summarization tasks. Memory, more agents, side-effecting tools, schedules, and unlimited-duration work are deferred.

## Logical sequence

~~~mermaid
sequenceDiagram
    participant D as Kai-sim / M5
    participant R as Amusant Relay
    participant G as Gemini Live
    participant T as Amusant Task Store
    participant W as Amusant Worker
    participant A as OpenAB /acp

    D->>R: PTT audio (Kai protocol)
    R->>G: Real-time audio
    G->>R: function call delegate_task(call_id, task)
    R->>R: Authenticate / validate / authorize / deduplicate
    R->>T: Durable write (queued + dispatch intent)
    T-->>R: Commit OK + task_id
    R->>G: FunctionResponse(call_id, accepted, task_id)
    G-->>D: Brief spoken acknowledgment via Relay
    W->>T: Claim queued task
    W->>A: ACP initialize / session-new / session-prompt
    A-->>W: session/update messages
    A-->>W: session/prompt terminal response
    W->>T: Save outcome + result / failure
    T-->>R: Inbox status/result (on reconnect or polling)
    R-->>D: Card / inbox notification when available
    D->>R: Ask to hear completed task
    R->>G: Inject bounded speak summary as a NEW turn
    G-->>D: Audio response via Relay
~~~

The first acknowledgment confirms **accepted work**, not completed work. Device disconnects and Gemini session expiration must not erase task results. An offline result remains in Inbox until delivered or its documented retention window expires.

## Tool contract and trust boundary

- Suggested Gemini declaration: `delegate_task(task: string, task_kind?: enum)`. `task_kind` is optional, bounded and treated only as a hint; the server decides allowed capabilities.
- **Do not** accept client/model-specified backend URLs, filesystem paths, executable commands, provider credentials, unrestricted agent IDs or tool lists.
- Server-generated identifiers: `task_id` plus a stable dedupe/admission key derived from the authenticated client, conversation turn and provider call ID. Enforce unique acceptance with database constraints, not an in-process boolean.
- Admission payload: `{"status":"accepted","task_id":"..."}`. Rejections: `rejected`, `unavailable` or `error` with sanitized reason. Never claim success when persistence/dispatch scheduling has not succeeded.
- Never send raw audio to OpenAB. Send only the validated task text plus a small, explicitly authorized context.
- Tool declaration and `send_tool_result` handling must respect the selected Gemini Live model's actually supported function-calling behavior. Prefer a **short blocking admission** (only until the durable write), if verified supported; do not block while the agent executes. Test this with mocks before real Live usage.
- Cap task text/context size, number of outstanding tasks per user/device, and tool-call rate. Suppress delegation from a rejected, stale or canceled PTT turn; preserve turn ownership / barge-in protections.

## OpenAB ACP adapter

OpenAB as-built ACP Server accepts **GET /acp** WebSocket, JSON-RPC 2.0:

1. `initialize` (ACP protocol version `1`).
2. `session/new` using a **server-controlled** `cwd` and `mcpServers: []` for P3. Capture `sessionId`.
3. `session/prompt` with a text content block; collect text from `session/update` messages correlated to the active request.
4. Observe the terminal `session/prompt` response: `stopReason: end_turn` for success, `cancelled` when reported; handle JSON-RPC error/timeouts as failures.
5. Normalize an agent result to `speak` (at most two short sentences), `card` and `details_md`. If the agent returns plain text rather than structured JSON, use a safe bounded fallback; do not trust the agent's self-reported status.

OpenAB authentication is separate from Amusant device auth: `Authorization: Bearer <OPENAB_ACP_AUTH_KEY>` on WebSocket upgrade **and** the gateway's allowed synthetic ACP sender (`GATEWAY_ALLOWED_USERS=acp_client`). Route only through trusted loopback/private connectivity.

**Implementation prerequisite:** verify the selected OpenAB binary/image is compiled with the `acp` feature, has `OPENAB_ACP_ENABLED=true` at runtime, and returns a successful authenticated session smoke. Do not assume every OpenAB image includes `/acp`. Pin a known-good image revision/digest. Explicitly verify the selected downstream ACP agent and its model credential mechanism; `openab-agent` credentials are not interchangeable with generic LLM API keys by assumption.

As of the researched upstream revision, OpenAB's base ACP server delivers a terminal agent message instead of progressive streaming. There is a **180-second prompt idle timeout**; agent-side cancellation is not yet fully propagated. P3 therefore chooses a **maximum 120-second bounded task attempt**, reports `expired` when timed out, and never claims a `cancel` message stopped underlying side effects. Actual cancellation and longer jobs need a separate design.

Source:
- [ACP as-built ADR](https://github.com/openabdev/openab/blob/6431a1b6991673429f344acb78a58d94658d30fb/docs/adr/acp-server-websocket-base.md)
- [ACP server code](https://github.com/openabdev/openab/blob/6431a1b6991673429f344acb78a58d94658d30fb/crates/openab-gateway/src/adapters/acp_server.rs)
- [ACP smoke client](https://github.com/openabdev/openab/blob/6431a1b6991673429f344acb78a58d94658d30fb/scripts/acp-ws-smoke.py)

## State, reliability and result delivery

Task states: `queued → running → completed | failed | expired`; retries may move tasks through `retrying`. Track **delivery state separately** (pending/notified/delivered/read/spoken) because worker completion is not device delivery.

- **P3 local:** SQLite is the task system of record. Persist queue intent in the same transaction, let the worker lease/claim jobs, and recover undispatched/expired leases on restart. No claim of strict exactly-once execution.
- **P4 AWS:** DynamoDB conditional writes/leases are the task system of record; SQS drives dispatch. Avoid the dual-write gap by storing dispatch intent durably and using an outbox sweeper/dispatcher to send or re-send SQS messages. SQS consumers are idempotent and safe under at-least-once delivery.
- All retries are bounded with backoff, task TTL and concurrency caps. Retried side-effecting jobs are **prohibited** during P3; action-level approvals/idempotency are later work.
- Device delivery: an inbox/card notification can arrive immediately if connected, but retained results are discoverable after reconnect. Speech is initially **user-requested** from a new/bounded Live turn; automatic unsolicited speech is deferred. The short result should be injected as **untrusted content** into a tool-disabled speech-only turn to prevent malicious agent output from triggering new actions.
- Keep final full details in Inbox/approved text surface. Loss of original Gemini session is not a failure of result delivery.

## Phased deployment

### P3 — local PoC; no unapproved cloud spend

~~~text
kai-sim → local Relay + Gemini Live
                 |
           SQLite task store
                 |
            local worker
                 |
         OpenAB /acp on loopback
                 |
         one restricted ACP agent
~~~

Start with deterministic mocks for tool admission, worker failure and inbox. Then prove real OpenAB ACP smoke and a single permission-restricted end-to-end text job. Keep the P1/P2 Kai wire loop working.

### P4 — AWS staging only after separate budget/security approval

~~~text
Internet device → ALB/WSS → Relay ECS Service (IAM: admit/read-result)
                                   |
                      DynamoDB task store + outbox → SQS
                                                   |
                                             Worker ECS Service
                                                   |
                                         authenticated internal WSS
                                                   |
                                             OpenAB ECS Service
                                               (ACP Agent)
~~~

**Separate ECS tasks**, not privileged sidecars sharing one task role. Relay, Worker and OpenAB have different task roles and model credentials; OpenAB gets **no** task DB/SQS privileges or N100 access. OpenAB listens only on internal/private networking behind authenticated entry; no public ALB path to `/acp`. Confirm TLS on any network hop, strict task/model secret segregation, network egress restrictions and no cross-user ACP session reuse. Fargate task-role isolation is task-granular; two containers in one ECS task do not constitute separate IAM boundaries.

Cloud delivery and reliability must be tested under Relay restarts, Worker restarts, disconnected clients, retries and delayed agent completions. P4 does not automatically authorize or provision AWS resources by merging this ADR.

## Security requirements

- Device authenticates to Relay; Relay verifies user/turn, policy and task admission.
- Slow worker receives **no** main OpenClaw Gateway token, memory or host mounts. No N100 Home Bridge or inbound home routing.
- OpenAB runtime has an isolated filesystem, non-root user, pinned dependencies, minimal necessary outbound access and no host or Docker socket mounts. Restrict shell/file/browser/control-plane abilities at enforceable tool/runtime boundaries, not only in prompts.
- Give the slow runtime its own provider credential only; inject via Secrets Manager in P4, do not hardcode credentials or rely on personal subscription OAuth without explicitly verifying its supported non-interactive lifecycle.
- No raw audio logging/storage; sanitize task content/errors, redact secrets and define short bounded retention of task texts and results. Log task IDs, timings and statuses by default.
- Do not make an Agent's answer authoritative for task status/authorization; Task Store and trusted worker own those transitions.
- Disallow outgoing side-effecting tools (messages, purchases, destructive modifications, arbitrary shell, scheduled autonomous commands) until separately authorized and tested.
- Protect ACP session isolation, rate limits, maximum tokens/spend and prompt-injection-contaminated summaries.

## Acceptance gates

1. **P3.0 upstream viability:** authenticated `/acp` smoke using pinned build, one restricted agent, verified model credential, timeout/cancellation behavior documented. If this fails, stop and revisit choice instead of building a replacement broker.
2. **P3.1 Gemini admission (#1):** mocked function-call routes to validated/deduplicated durable task; same call ID receives accepted/unavailable/rejected; FAST never delegates.
3. **P3.2 task durability:** worker crash/restart, queued recovery, duplicate prompts and two client reconnects do not lose accepted work or incorrectly claim exactly-once execution.
4. **P3.3 ACP execution:** successful text task, timeout/failure/rate limit, unauthorized tool refusal, safe `speak/card/details_md` normalization.
5. **P3.4 delivery:** disconnect Live before job completion; result remains in Inbox and can be spoken in a new session on request; no automatic extra tool call during speech.
6. Existing P1/P2 and PTT rejection/barge-in regression tests stay green. **No P3 implementation, paid integration calls or deployment is included in this ADR PR.**

## Alternatives considered

- **N100 main OpenClaw Gateway:** rejected for P3 due to high-trust personal workspace, broad credentials and blast radius.
- **Isolated OpenClaw ECS:** technically feasible and still an option if OpenAB cannot provide the desired general personal-agent task capabilities; not selected as first PoC.
- **OpenAB via IM bots / MCP facade:** unnecessary additional transport layers; direct ACP already exists. MCP tool support alone does not make an Agent externally callable.
- **Building a new ACP broker or coupling agent execution into Relay:** rejected; duplicates OpenAB and endangers voice latency.
- **Embedding Relay/Worker/agent in one privileged ECS task:** rejected as an IAM/isolation boundary.

## Deferred and follow-up work

- Durable long-term memory, cross-session user profile, skills beyond one proven safe skill, proactive monitoring/schedules and cross-backend agent switching.
- True agent cancellation semantics and arbitrarily long asynchronous jobs.
- Additional MCP tools after explicit capability/permission review.
- Future backend adapters must implement the same `submit/status/result` contract; do not require the Gemini tool schema to expose a backend selector.
- Revisit H0 hardware value only after software acceptance; neither this ADR nor OpenAB changes the no-mobile-app constraint.

**Scope of this PR:** architecture and documentation only. A follow-up change should revise Issue #1 into a backend-neutral admission implementation; ACP execution, persistence, AWS infrastructure and live tests are distinct implementation steps.
