# Project Amusant

## Voice Companion Architecture & MVP Specification v0.4 — Two-ECS Baseline

- **Status:** Accepted requirements specification (PR #9); persistent two-Service product configuration not yet deployed. Bounded P4-T4b live WSS/ACP proof PASS (2026-10-13).
- **Updated:** 2026-10-16 (evidence alignment only; normative requirements unchanged)
- **Product model:** One-to-one personal, grab-to-talk voice companion
- **Deployment baseline:** Exactly **two ECS Services**: Amusant Relay + OpenAB Agent Runtime
- **Real-time baseline:** Gemini Live, manually selected provider
- **Slow-task transport:** OpenAB native authenticated ACP WebSocket
- **Software acceptance frontend:** Kai `kai-sim`; **M5StickS3 local FAST functional E2E PASS on 2026-10-15 (#31)**, formal H0 audible-onset/battery UX and Cloud E2E still pending
- **Upstream reference:** [Kai](https://github.com/zbruceli/kai), pinned study baseline `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`
- **Architecture decision:** [ADR-0001 — Two-Service Relay + OpenAB ACP](docs/adr/0001-openab-acp-slow-path.md)

> This document is the accepted **full replacement** of the previous `SPEC.md` (v0.3), retaining validated P0/P1/P2 foundations and P3.0/P3.1 evidence while correcting the deployment boundary. This is documentation-only: it does not authorize AWS changes or paid tests. The requirements intentionally omit personal agent aliases, account identifiers, local machine names, private endpoints and credentials.

## 0. Document authority and implementation truth

- **`SPEC.md`** defines product requirements, architecture constraints, non-goals, and acceptance criteria. It is the canonical requirements source.
- **`docs/adr/0001-openab-acp-slow-path.md`** explains architecture rationale and the OpenAB ACP protocol; it may not silently expand deployment beyond this spec.
- **`PLAN.md`** contains execution sequence, estimates, and approval gates; it must be aligned to this spec when amended.
- **`STATUS.md`** documents evidence of completed work, not hypothetical implementation.
- **`SPEC-P0-DELTA.md`** remains a historical decision record.

Terminology must distinguish **required**, **recommended**, **implemented**, and **verified**. An architecture diagram or passing mock test is not a deployed feature. A previous test authorization does not authorize new cloud spending, real model calls, or security changes.

## 1. Product definition and fixed boundary

Amusant is a low-friction **one-to-one personal voice companion**, designed to offer a grab-to-talk experience comparable to a dedicated voice companion device rather than a general AI chat app. The product/UX reference is Muse Charm as a category and interaction benchmark only; this does not claim feature parity, compatibility, or a product dependency. Its first interaction should require:

```text
press PTT → speak → release → hear response
```

Normal operation is **one owner, one dedicated Relay, one configured OpenAB agent runtime**. The simulator and candidate hardware may be used interchangeably during development, but **multi-user tenancy, multi-Relay scaling, shared queues and fleet administration are outside the MVP**.

Amusant does not aim to implement a second agent platform. It is a thin voice gateway that adds reliable handoff for bounded slow work. The actual agent reasoning, tool execution and agent-side context belong to the backend.

The project tests two separable hypotheses:

1. **Software:** Native live speech and a replaceable restricted agent backend provide immediate conversation plus asynchronous completion of selected complex tasks.
2. **Hardware:** A dedicated PTT object offers meaningfully less friction than an optimized phone voice workflow. Hardware must pass H0 before substantial device-specific investment.

## 2. Normative architecture — two ECS Services

```text
                  Kai-sim / M5StickS3 (PTT)
                            |
                authenticated device link
                            |
    +---------------------------------------------------+
    | ECS A: AMUSANT RELAY                              |
    |                                                   |
    |  Device Gateway ↔ Gemini Live Adapter ---------+--+----> Gemini Live
    |                    FAST (voice)                 |  |      (Google service)
    |                    SLOW: delegate_task()        |  |
    |                              |                  |  |
    |                         Task Ledger             |  |
    |                              |                  |  |
    |                    Internal Async Dispatcher    |  |
    |                              |                  |  |
    |                        ACP Client --------------+--+--------+
    |                              |                  |           |
    |                         Result Inbox            |           |
    +------------------------------|-------------------+           |
                                   |                               |
                          durable read/write               private ACP WSS
                                   |                               |
                         +------------------+            +--------v-----------------+
                         | Small Task Store |            | ECS B: OPENAB RUNTIME   |
                         | Task + Inbox     |            | AI agent + allowed tools|
                         +------------------+            | Agent session/context   |
                                                         +--------------------------+
```

**Requirements:**

- **ECS A — Amusant Relay:** device authentication, PTT/audio stream, Live provider integration, Gemini function-call handling, task admission, lightweight task records, **in-process Async Dispatcher/ACP client**, and Result Inbox.
- **ECS B — OpenAB Agent Runtime:** authenticated native ACP endpoint and the configured restricted agent with independent tools and credentials.
- **Exactly two ECS Service types.** No independent Worker ECS, no additional worker API tier, no multi-Relay orchestration.
- One active Relay task in normal operation; independent horizontal scaling is not an acceptance gate. Avoid overlapping active Relay instances during deployment if required, accepting a brief reconnect/downtime window.
- A managed task database, if approved, is **not** another ECS Service. Gemini Live is an external model provider, not another ECS.
- A logical Worker/Dispatcher is not equivalent to a deployed Worker Service. Never add an ECS merely to execute asynchronous ACP network calls.

**Kai precedent:** Kai's Relay implements voice, `AgentHub`, task watching and SQLite Inbox in a single Python service; Hermes is the separate agent runtime. Amusant preserves the **two-component responsibility split**, substituting OpenAB ACP for Hermes HTTP. The two components are placed in separate ECS Services in AWS, whereas Kai originally runs its Relay and agent on the same host.

## 3. Product architecture principles

1. **Voice first:** Never put custom STT → classifier → LLM → TTS in the real-time path when native streaming Live is available.
2. **Keep Relay thin:** Relay holds only the minimum task receipts and deliverable results. No model reasoning, vector memory, agent scratchpads, multi-agent routing, or broad tool execution in ECS A.
3. **Fast stays fast:** Normal answers should not await task-store scans, ACP agent responses, or cloud queue round-trips.
4. **Agent backend remains replaceable:** `delegate_task` does not expose agent/backend selection; the server chooses it.
5. **Honest task acknowledgement:** Say *accepted* only after durable admission. Acknowledgement does not mean the work is finished.
6. **One-to-one MVP:** Do not expand scope to multi-tenancy or scaled worker fleets in anticipation of hypothetical demand.
7. **No silent permission growth:** No uncontrolled agent commands, side-effecting actions, or access to unrelated personal services without a new authorization gate.

Engineering priority order: interaction friction, perceived voice latency, reliable completion/delivery, security, maintainability, then optional provider sophistication. Reliability must be proportionate to the one-to-one product.

## 4. Fast / Slow routing

Gemini Live is the initial native audio model and the FAST/SLOW router via model function calling.

| Lane | Example | Behavior |
|---|---|---|
| **Direct (FAST)** | “What is a crankcase?” | Gemini Live answers directly; no slow-task persistence |
| **Fast tool** | “What is the current time?” | A specifically authorized bounded local/API tool may run inline |
| **Slow delegation** | “Research and compare current prices for three laptops.” | Gemini calls `delegate_task`, Relay durably admits, OpenAB runs asynchronously, result enters Inbox |

The selected Live provider is **manual**, with Gemini Live as the required initial baseline. Automatic provider switching and multi-provider production support are deferred. A second Live provider may later validate the abstraction but is not an MVP pass/fail gate.

The selected Live model must not claim that a Slow Task has been delegated unless the Relay actually returns a successful admission result. Unavailable, denied or uncertain admission must be reported honestly.

## 5. Device and realtime voice UX

### Baseline input/output

- Software client: pinned Kai-compatible `kai-sim`; future PTT hardware: M5StickS3 using Kai-derived firmware after H0.
- Transport: Kai-compatible audio/PTT/control envelopes; local prototype uses PCM16LE input at **16 kHz**, output at **24 kHz** with explicit sample-rate metadata. Opus support may be preserved/added as required by the hardware.
- Device only handles buttons, microphone, speaker, display/card, and network; provider credentials and agent logic stay server-side.
- Input activity follows PTT start/release boundaries. Short/invalid taps and stale turns must not generate unauthorized model/tool calls.
- Barge-in and explicit cancellation are distinct events. Do not assert an agent or voice cancellation was successful until the relevant provider reports what actually stopped.
- No app-unlock or phone interaction per normal PTT turn is the hardware UX goal.

### Live provider interface

The provider adapter must expose explicit capabilities and event callbacks, including audio, transcripts, tool calls, interruption, completion and sanitized errors. Illustrative contract:

```python
class LiveProvider:
    capabilities: ProviderCapabilities
    async def connect(self): ...
    async def close(self): ...
    async def send_audio(self, frame: AudioFrame): ...
    async def start_turn(self): ...
    async def end_turn(self): ...
    async def send_tool_result(self, call_id: str, result: dict): ...
    async def cancel_response(self): ...
    async def inject_text(self, text: str): ...
    def on_audio(self, callback): ...
    def on_transcript(self, callback): ...
    def on_tool_call(self, callback): ...
    def on_tool_calls_complete(self, callback): ...
    def on_interrupted(self, callback): ...
    def on_turn_complete(self, callback): ...
    def on_error(self, callback): ...
```

The adapter must normalize actual audio formats and fail closed if an unsupported rate/encoding is requested. Provider callbacks remain ordered and stale provider/turn events are ignored. For a provider message containing multiple function calls, the adapter processes the complete list serially and invokes on_tool_calls_complete once after valid distinct call IDs have received correlated responses; malformed or replayed IDs fail closed. The Relay may use that batch boundary to retire the tool-enabled session only after processing the whole message. No automatic fallback to another provider when selection/configuration fails.

### Voice targets (goals, not measured production claims)

- PTT down → audio capture: **< 0.5 s**.
- PTT release → first audible FAST response: **p50 ≤ 1.2 s; p95 ≤ 2.0 s** (stretch p50 < 1.0 s).
- PTT release → short SLOW acceptance speech: same response-latency target, conditional on live model/tool behavior.
- Slow agent execution time is **not** counted inside realtime first-audio latency.

Sample sizes must be reported. Earlier successful one-turn audio smoke tests do not establish production p50/p95 latency.

## 6. `delegate_task` tool contract

### Gemini-facing schema

```text
delegate_task(
  task: string,                        # bounded, non-empty; <= 4096 chars
  task_kind?: research|compare|summarize
)
```

No model-controlled `backend_url`, arbitrary `agent_id`, filename, shell command, credential, tool list, IAM role, or execution endpoint is allowed. The backend and allowed capabilities are server-side policy.

### Admission

1. Verify an authenticated device/owner context and valid current PTT/turn state.
2. Validate the model tool name, size, schema, task kind and pending-capacity policy.
3. Generate server-side `task_id` and identity/deduplication fields.
4. **Durably commit task + dispatch intent together.**
5. Respond once to the exact original provider call ID:

```json
{"status": "accepted", "task_id": "t_illustrative"}
```

For invalid/conflicting/unavailable/ambiguous admission return sanitized `rejected`, `unavailable`, or `unknown`. `unknown` is required when a persistence operation may have committed but acknowledgement cannot be confirmed. Retries of the same trusted call key must deduplicate rather than create a second task.

Deduplication key: trusted `(client_id, conversation_id, turn_id, call_id)`; conversation/turn scopes must not be accidentally reused after reconnects.

**Tool behavior:** Verify the selected Gemini model/SDK ordering and blocking semantics with offline tests and a separately approved real tool-call test. Use short admission-only blocking behavior if needed and supported; **never wait for the ACP agent** while answering the original function call. Completion is an Inbox event, not a second FunctionResponse.

## 7. Slow-task coordination lives inside Relay

**Single application, two logical responsibilities:**

- **Voice loop:** handle audio/device events, maintain Live session, deliver timely speech.
- **Internal Async Dispatcher:** read admitted queued tasks, send bounded text through ACP, await terminal result in the background, save completion/failure, and notify Inbox listeners.

The dispatcher is application-scoped, **not** tied to a device WebSocket or Live session. Disconnects must not erase previously accepted jobs. It must use asynchronous I/O or offload blocking storage operations so that an agent timeout cannot block the voice path.

Initial default: allow **one concurrent ACP slow task** per personal Relay, with configurable pending-task count and rate limits. Do not add a third ECS, cross-Relay job claiming, work-stealing or a mandatory queue service to implement this simple behavior.

### Operational stages

```text
queued (durably admitted)
  → running (ACP may have been dispatched)
      → completed / failed / expired / uncertain
```

`retrying` may be added for bounded eligible retries. The dispatcher must distinguish **definitely not sent** from **may have been sent**. A terminated connection does not prove backend execution stopped.

### Recovery requirements

- If a task was admitted but not dispatched, restarting ECS A must recover and dispatch the queued record.
- If the Relay exits after an ACP prompt may have been sent, the outcome is **uncertain** unless confirmed. Do not claim success or claim cancellation.
- Only approved **read-only/idempotent research tasks** may be retried automatically, with bounded attempts and backoff; repeated execution may incur cost and is not guaranteed exactly-once.
- Explicitly fail or expire tasks outside policy. No side-effecting autonomous actions in P3.
- A new Relay process must find still-pending task records and Inbox items independently of the original Live session.
- A single active Relay is in scope. Cross-replica conditional claims and distributed consensus are **not** MVP acceptance requirements; any future deployment permitting simultaneous active Relays requires a separate design review.

### Realistic limitation

OpenAB ACP **does not provide Hermes-style durable `run_id` querying**. Its `session/resume` preserves conversation context under retention constraints and does not replay lost output or guarantee retrieval of an interrupted in-flight prompt. Amusant must not promise seamless continuation of arbitrary long agent runs after a Relay crash. That capability would require a separately designed backend-owned durable job API, not another Worker ECS by default.

## 8. Thin Task Ledger + Result Inbox

**Definition:** A minimal work-order register owned by Relay, stored outside volatile process memory for the deployed AWS configuration. It is **not** a conversation archive or agent memory system.

### Minimal task record

| Data | Needed? | Purpose |
|---|---|---|
| `task_id` | Required | Stable identifier for asking about previous work |
| Trusted client / conversation / turn / call identifiers | Required | Authorization, deduplication, stale-turn protection |
| `task_text`, `task_kind` | Required | What was requested; possible bounded safe replay |
| `backend_id` (server-selected) | Required | Know which approved agent runtime was used |
| `status` and dispatch stage | Required | `queued`, `running`, `completed`, `failed`, `expired`, `uncertain` |
| `created_at`, `updated_at`, `expires_at` | Required | Timeline, retry bounds and retention |
| `attempts`, optional `next_attempt_at` | Required when retry is supported | Bounded retries and recovery |
| `speak`, small `card`, bounded `details_md` | Required on completion | What a future Live session needs to tell the user |
| `delivery_state` | Required on completion | Pending/notified/read/spoken, separate from execution |
| ACP `sessionId` | Optional | Context diagnostic/continuation **only**, never the durable task-result key |

Do **not** save raw audio PCM, full Gemini transcripts by default, model reasoning traces, chain-of-thought, all search pages, agent tool logs, broad browser/shell state, arbitrary attachments, or credentials in the Relay ledger.

### Example: work admitted on Monday, user asks on Tuesday

```json
{
  "task_id": "t_laptop_comparison",
  "task_kind": "compare",
  "task_text": "Compare three current laptops for local AI workloads",
  "status": "completed",
  "created_at": "2026-10-12T09:00:00Z",
  "completed_at": "2026-10-12T09:01:18Z",
  "result": {
    "speak": "Option B offers the best balance of memory and price.",
    "card": {"title": "Comparison ready", "lines": ["Option B recommended"]},
    "details_md": "Short, bounded final comparison ..."
  },
  "delivery_state": "pending"
}
```

The new Gemini Live session has no obligation to remember Monday's conversation. The Relay exposes an authorized local lookup (`check_task_status`); it retrieves the completed task and returns the bounded result. A new **tool-disabled** spoken Gemini turn remains a later Live-session behavior, not proven by the SQLite lookup tests.

This is **Task History**, not general chat memory. Agent memory and scratchpad remain backend responsibilities. OpenAB need not be asked to reconstruct an already-delivered final result.

### Volume, retention and result limits

- Only Slow Tasks generate Task Ledger records. Ordinary FAST dialogue does not.
- Bound task size (`task` <= 4096 characters), result size, number of pending records, and concurrent agent calls.
- Normalize `speak` to a short spoken response (at most two brief sentences); keep `card` small and truncate or reject oversized `details_md` according to an explicit limit.
- **Proposed policy:** retain completed/failed task receipts and bounded results for **30 days by default**, configurable after product/privacy review. Pending task expiry is handled by the application, not by relying on eventual database TTL deletion.
- For larger documents, use a separately approved document/artifact storage design later rather than turning the ledger into a file repository.

### Storage implementation

- **Local/offline P3:** Existing SQLite atomic admission/outbox implementation is preserved. P3.2–P3.4 will add state transitions, recovery and result records. SQLite can remain the automated-test backend.
- **Target AWS:** Use **external durable storage** to survive ECS task replacement. **DynamoDB** is the ledger. ADR-0002 is accepted. The 2026-10-12 durability one-off PASS proved write → A STOPPED → strongly consistent `GetItem`/`store_get`. 24/7 Relay, TLS cutover, and device ingress remain separately gated.
- **SQS is not required** for the initial one-Relay, low-volume Dispatcher. Wake the in-process Dispatcher on admission and recover eligible queued tasks on startup / bounded periodic scan.
- Storing data in an external service does not make the Relay heavyweight: it performs a handful of small records per task, not model inference or agent execution.

## 9. Session persistence: explicit ownership

| State or 'memory' | Owner | Required behavior |
|---|---|---|
| **Gemini Live voice session** | Live + Relay | Ephemeral, may end/reconnect; preserve active-turn integrity |
| **Amusant Task History and Result Inbox** | Relay's durable store | Survives reconnection and ECS A replacement; queryable by a new voice session |
| **OpenAB ACP / downstream agent session** | ECS B | Context continuity if backend supports it; **not** durable job tracking |
| **User profile / general conversational long-term memory** | Out of scope | Do not silently build it in Relay or assume OpenAB already fulfills it |

Acceptance scenario: user delegates a task, leaves, and asks its status in a new session the next day. Amusant must use Task Ledger lookup and report `queued`, `running`, `completed`, `failed`, `expired`, or `uncertain` based on actual persisted state. It must not invent a completed answer.

## 10. OpenAB ACP adapter and limits

Pinned upstream ACP baseline: [OpenAB `6431a1b6`](https://github.com/openabdev/openab/tree/6431a1b6991673429f344acb78a58d94658d30fb).

- Endpoint: native authenticated **`GET /acp` WebSocket**, JSON-RPC 2.0.
- Sequence: `initialize` (protocol v1) → `session/new` (server-controlled `cwd`, `mcpServers: []`) → `session/prompt` (bounded text content).
- Receive correlated `session/update` output; success requires a terminal response to that prompt with `stopReason: end_turn`.
- Handle errors, cancellation, overload, disconnection and timeout explicitly; a transient tool stream is not itself proof of completion.
- Runtime must be built with the ACP feature, explicitly enable it, authenticate transport, and authorize the synthetic ACP client identity.
- OpenAB's researched base timeout is **180 s of prompt inactivity**; Amusant's initial maximum task attempt is **120 s**. Real multi-hour autonomous jobs are **not supported** by this initial bounded execution contract.
- ACP `session/cancel` does **not** guarantee underlying agent execution stops. Retried read-only jobs may run twice; no exactly-once or side-effect-free guarantee follows merely from a Task Ledger.
- Normalize and sanitize untrusted outputs to `speak`, `card`, `details_md`; never let backend text determine user authorization, tools, or internal task status.

The first backend is one configured, restricted ACP agent. Do not expose arbitrary backend selection through Gemini function arguments.

## 11. Network, AWS, credentials, and isolation

### Target deployment

- ECS A Relay: one active application task, device-facing authenticated endpoint, outbound Gemini Live, outbound authenticated ACP, access to the approved task store.
- ECS B OpenAB: separate ECS task and IAM/security boundary with a private authenticated ACP endpoint. No direct internet-facing ACP listener.
- Secure secrets via a supported secret manager at deployment; never commit or log actual tokens.
- Agent runtime should have only explicitly approved tools, filesystem access, egress, and model credentials. Privileged personal gateway access, unrelated local systems and broad host mounts are out of scope.
- No third Worker ECS, independent Worker IAM role, mandatory SQS or multi-tenant infrastructure.

### Device ingress constraint

Upstream Kai uses TLS-PSK for the device connection. A standard AWS ALB TLS listener must **not** be assumed to transparently support Kai's TLS-PSK handshake. Choose and verify an actually compatible TLS/WSS termination/routing design (or a separately reviewed device transport change) **before** exposing the Relay to a public network. Local `ws://` test harnesses must never become public production endpoints by accident.

### ACP trust boundary

The OpenAB ACP transport uses its own bearer credential and authorized synthetic sender; these are independent of device and Gemini credentials. Restrict ingress by network policy and enforce **TLS/WSS between ECS tasks** or a documented equivalent protected transport. Separate task roles and secrets for ECS A and ECS B. No reliance on prompt instructions alone to limit tools.

### Privacy and permission policy

- Do not persist audio or full transcripts by default.
- Do not log task content/result text in normal telemetry; log task IDs, status, timings, bounded error codes.
- Protect the small Task Ledger/Inbox as personal task data; define deletion/retention before production use.
- Disable side-effecting Agent actions and broad privileged tools until separately reviewed and approved. The historical P3.0 feasibility test does **not** prove production tool restrictions.
- No public ACP, exposed home gateway, unattended messaging, purchasing, file modification, device control, schedules or browser automation as an implicit MVP feature.
- Real model use, AWS deployment changes and infrastructure spending require explicit approval.

## 12. Failure handling and observability

### Required failure behavior

| Scenario | Required response |
|---|---|
| Invalid, oversized, unauthorized or stale function call | Reject with sanitized reason; no persistence and no false acknowledgement |
| Storage unavailable | Return `unavailable` or `unknown` when commit result is ambiguous |
| Duplicate same tool call | Return original accepted task ID, never create a second task |
| Accepted but not dispatched; Relay restarts | Recover queued record on application startup |
| ACP dispatched; Relay loses connection mid-task | Mark `uncertain` unless terminal result is confirmed; do not assume cancellation |
| OpenAB error/timeout | Respect bounded retry policy; record failed/expired/uncertain honestly |
| Device or Live disconnect after acceptance | Task result remains in durable Inbox for later query |
| Agent text tries to invoke more tools | Treat text as untrusted; tool-disabled result speech, no command execution |

### Event names and measurements

Suggested instrumentation: `ptt_down`, `first_audio_received`, `input_turn_end`, `live_tool_call`, `task_accepted`, `dispatch_started`, `acp_prompt_sent`, `acp_terminal`, `task_finalized`, `inbox_created`, `result_read_or_spoken`, `first_output_audio`, and sanitized errors. Correlate by `turn_id` and `task_id` without logging raw content.

Measure end-to-end first-audio latency and delegation acknowledgment separately from slow execution duration. Track accepted→dispatched delay, ACP duration, completion/failure/uncertain counts, queued backlog, Inbox latency and restart recovery behavior. Avoid presenting smoke observations as statistically validated service-level performance.

## 13. Validation and acceptance criteria

### FAST/P2 regression

- Existing Kai-sim PTT/audio bridge and provider-adapter tests stay green.
- Short invalid PTT, stale turns, interruption and reconnect do not trigger incorrect tool routing or corrupt another turn.
- Direct FAST questions do not create Task Ledger records or open ACP connections.

### Slow admission/P3.1

- Valid tool call → exactly one durable `queued` task plus dispatch intent → same-call-ID `accepted` result.
- Duplicate call with same content → same task ID; conflicting payload → rejection.
- Invalid/unauthorized/oversized/canceled-turn tool calls rejected.
- Ambiguous commit is reported `unknown`, never invented as `accepted`.
- Real Relay startup must wire the optional tool declaration and admission handler; offline component tests alone are insufficient.
- Verify model-specific admission/acknowledgement ordering before claiming real Gemini behavior.

### Internal Dispatcher/P3.2

- One Relay with an in-process Async Dispatcher; no separately deployed Worker required.
- Crash *before* ACP send: recovery dispatches queued work.
- Crash *during* an ACP prompt: state does not falsely become `completed`; deterministic uncertain/limited-retry behavior.
- Background ACP work does not delay normal voice turns.
- Bounded pending tasks, concurrency and retries.

### ACP execution/P3.3

- Authenticated ACP handshake/session/prompt works with the configured restricted runtime.
- Correlated success and terminal result; validated `speak/card/details_md` normalization.
- Explicit errors for overload, disconnection, timeout, unauthorized capabilities, agent failure and ambiguous completion.
- No raw audio forwarded to OpenAB and no side-effecting agent capabilities in the initial scope.

### Inbox and session/P3.4

- Completed result persists when Live/device disconnects.
- A **new Live session** can answer “What happened to my previous task?” by querying the Relay ledger.
- Task status and Inbox delivery state are independent; user-requested speech happens in a **new tool-disabled** turn.
- Final results are not delivered as duplicate Gemini FunctionResponses.
- Result retention, data size and deletion policies are enforced.

### P4 AWS acceptance

- Exactly two ECS Services, each with one intended active task, and a compatible device ingress design.
- External Task Ledger/Inbox persistence survives ECS A replacement.
- Agent ECS is isolated by network/security credentials and tool/runtime policy.
- Tested reconnect, restart, failure, latency, usage cost, secret handling, and log redaction.
- No new resources or paid calls performed without separately documented approval.

### Routing evaluation

Evaluate **at least 100** representative utterances across direct chat, quick facts, low-latency tools, research, comparisons, stale tasks, error states, Chinese/English and mixed language. Include requests that are *out of scope* (unbounded autonomous work, personal memory, unauthorized tools) and verify honest refusal or deferral. Record expected/actual lane, tool calls and latency. Aim at ≥90% routing accuracy and report false-fast/false-slow errors; this is a target, not current evidence.

## 14. Current evidence and implementation boundary (as of 2026-10-16; evidence only)

This section describes **repository-recorded status**, not tests rerun for this documentation revision:

| Work | Status / evidence | Not yet proven |
|---|---|---|
| **P0** project foundation | Recorded complete | — |
| **P1** kai-sim mock/audio transport | Recorded pass | Full production transport/security |
| **P2** Gemini Live baseline | One real kai-sim PTT → Live → audible audio turn recorded as pass; offline tests | Cloud ingress and production latency distribution |
| **P3.0** OpenAB viability | One-off authenticated ACP/model response and separate client-to-Agent smoke recorded as pass, under limited test risk acceptance | Safe production agent sandbox, durable Relay and Inbox |
| **P3.1** durable admission | Existing `relay/slow_path.py` and `relay/task_store.py` implement opt-in tool routing and SQLite atomic task + outbox admission; local CLI `relay.live_bridge.run()` fail-closes into that composition. PR #10 squash-merged. Repository reports offline Fast Voice 52/52 | ECS image/taskdef/ingress, replacement-safe store, physical-device startup |
| **P3.2** in-process Dispatcher | PR #12 merged. SQLite claim/recovery; CLI recovers crashed in-flight work as uncertain | ECS replacement durability |
| **P3.3** ACP execution | PR #13 merged. Authenticated JSON-RPC ACP client against a local fake `/acp` server | — |
| **P3.4 / PR #40/#41** Inbox & Dispatcher | `check_task_status` lookup, independent delivery state, bounded results; #40 tool-disabled spoken-delivery guard and #41 wake/idle polling merged, offline CI PASS | **Actual Gemini new-session spoken retrieval** remains unproven (#46 S1); `spoken` must not be marked before full server-observed emission |
| **P4-A1** ACP one-off | Live one-off PASS 2026-10-12: real Dispatcher/Inbox against Jellii, one ACP `session/prompt`, cleanup to scale 0. Same-SG plaintext `/acp` is a one-off risk exception, not TLS | External durable store, 24/7 Relay, device ingress, production sandbox, billed model-inference proof |
| **P4 durability** | Live DynamoDB one-off PASS 2026-10-12: task result persisted after Relay stopped and read-only `store_get` succeeded | Persistent service and restart recovery |
| **P4-T4b** Private WSS/ACP | Live bounded functional PASS 2026-10-13: Jellii:10/NGINX, Relay RunTask, 8/8 preflight, one successful ACP task, same-task after-stop durable reader, rollback; see [evidence](docs/p4-t4b-live-acceptance.md) | Persistent Relay Service A, device/voice E2E, full operational runbook conformance, independently measured spend |
| **P4** AWS integration | Target two-ECS Services remain accepted; bounded T4b functional proof validated | Persistent Relay Service A, G1 external device ingress, #46 S1, G2, G3a/G3b and production operations |
| **H0/G0** real device FAST | #31 2026-10-15 physical StickS3 → **local Amusant Relay** → Gemini FAST 10/10 functional PASS, wake/barge-in/reconnect observed; #33 six Jellii states and real PTT regression PASS | H0 actual speaker-audible p50/p95 and battery/UX Go/No-Go; **no cloud device G2 proof** |
| **WS2 / B0** Agent choice | #44 / Draft PR #45 Hermes native Runs **local fake-provider POC PASS** (2A/2B) | Real Agent quality, hosting/run durability/security, architecture decision; current OpenAB/two ECS still accepted |
| **WS4** service reliability | PR #34 CI, PR #40 Inbox, PR #41 Dispatcher wake merged | #30 PR-C >100 history, PR-D bounded shutdown/non-smoke persistent entrypoint, #35 A2 operating acceptance |

P3.2 migrates the SQLite schema to `queued/running/completed/failed/uncertain` with outbox claim/recovery. A `sent` or `completed` row from the fake executor is **not** proof that OpenAB ACP ran. Do not interpret an outbox record as a real ACP result. Inbox lookup is P3.4 SQLite evidence, not ECS durability.

## 15. Delivery plan and approval boundaries

- **P0:** Foundation — completed.
- **P1:** Kai-sim protocol/audio mock smoke — completed under its recorded gate.
- **P2:** Native Gemini Live FAST voice — completed under its recorded software/runtime gate.
- **P3.0:** OpenAB ACP/model feasibility — one-off smoke completed, **not** production security acceptance.
- **P3.1:** Durable `delegate_task` admission — SQLite foundation merged; local CLI now wires tool declaration + admission. ECS/service startup and a replacement-safe store remain.
- **P3.2:** Relay-internal Async Dispatcher, queued recovery, execution state, and SQLite lifecycle migration — implemented offline with a fake executor; real ACP remains P3.3.
- **P3.3:** Authenticated ACP client with bounded text prompts and honest timeout/uncertainty handling — implemented offline against a fake `/acp` server. Live ACP was exercised once in P4-A1, but authenticated TLS/WSS, restricted production agent sandbox, restart/persistence, and full device-to-agent runtime remain separately gated.
- **P3.4:** Durable Result Inbox, authorized previous-task lookup, reconnect status and user-requested spoken result — implemented offline on SQLite; a new Gemini tool-disabled spoken session remains unproven.
- **P4:** Two-ECS staging. P4-A1, DynamoDB durability, and bounded P4-T4b WSS/ACP functional Live have PASS evidence; this does not approve 24/7 Relay Service A or device/voice end-to-end.
- **H0/G0:** M5StickS3 on the local Amusant Relay passed functional PTT/FAST 10/10 (#31, 2026-10-15) and #33 six Jellii states; **formal H0 audible-onset, battery and phone-friction UX Gate remains open**.
- **H1:** Owned Wi-Fi provisioning/features #36 and cloud G2/G3 remain gated separately; local functional success is not automatic flash or Cloud authorization.
- **WS1–WS4 delivery:** [Active execution index](docs/project-management/workstreams.md), [PLAN](PLAN.md), [STATUS](STATUS.md). B0 Hermes/OpenAB is **open**. Agent and hosting changes require separate approved SPEC/ADR revision, not this evidence refresh.

P3 development may use local mocks, temporary databases and approved isolated connectivity smokes. These do not imply authorization for ongoing AWS ECS deployment, database provisioning, networking modifications or paid model tests. `PLAN.md` must reflect this revised topology **when a documentation PR is approved**.

## 16. Hardware and network continuation rules

### H0 hardware UX comparison

Compare the identical tasks on an optimized smartphone voice path and the candidate dedicated device. Record: actions before speaking, eyes-on-screen, phone interaction, PTT-to-capture time, first-audio time, reconnect behavior, battery, audio quality and subjective friction. Candidate targets are one physical action, zero eyes and zero phone interaction per normal request, audio capture within 0.5 s, and first audible audio p50 ≤1.2 s / p95 ≤2.0 s after release.

If the dedicated device does not meaningfully improve friction, defer or replace the hardware track without invalidating software/agent work.

### Mobile data access

The device may use a phone hotspot / mobile network. No dedicated companion app is required. Validate persistent hotspot availability, idle timeouts, reconnection and phone battery across supported platforms. A hotspot does not create an implicit private connection to personal or home services.

### Hardware scope

The initial candidate uses the integrated M5StickS3 board and Kai-derived firmware for I/O, display, Wi-Fi, PTT, Opus and power state. No custom PCB/soldering or production certification is part of the initial software MVP.

## 17. Explicit non-goals and deferred roadmap

**Not in MVP:**

- Multi-user SaaS, multi-Relay scaling, horizontal worker fleets or background service topology beyond two ECS.
- Separate Worker ECS, mandatory SQS, general-purpose orchestration framework or distributed task broker.
- General long-term personal/agent memory in Relay, full chat transcript archive, vector search, or history-based personalization.
- Multi-agent switching and unrestricted access to external personal gateways.
- Unbounded long-duration autonomous tasks, proactive monitoring, scheduled work and exactly-once action execution.
- Arbitrary shell/browser/file operations, purchases, outbound messaging and device control without explicit new permission review.
- Automatic fast-provider switching, required second provider, custom STT/TTS chain.
- Native mobile app, BLE audio bridge, cellular modem/eSIM, significant firmware rewrite, custom hardware manufacturing.
- Location sensors, environmental sensing, actuation, or third-party services not explicitly designed and authorized.

Potential future work may include additional Live providers, a policy-based routing guardrail *only after measured need*, improved ACP task continuation via a backend durable-job API, explicit user-approved tools and reminders, and hardware refinement if H0/H1 validate the UX benefit. These remain separate proposals and must not be implemented opportunistically.

## 18. Repository implications of this spec revision

PR #9 accepted this spec as a documentation-only architecture change. **Subsequent** P3/P4 code and separately authorized bounded AWS live tests have since been delivered, including the 2026-10-13 T4b smoke. This evidence update does not deploy a persistent service, change the architecture, or authorize additional AWS/model operations.

**Architecture acceptance rule:** No new ECS Service may be added merely because a logical Worker, session manager or queue is depicted. The default Amusant backend consists of **two** ECS Services, with a **thin Relay + lightweight Task Ledger/Inbox**, and an independently isolated OpenAB Agent Runtime.
