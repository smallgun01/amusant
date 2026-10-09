# Project Amusant

## Voice Companion Architecture & MVP Specification v0.3 — P3 Proposal

- **Status:** Planning / software-first; not yet implementation-ready
- **Project:** Amusant
- **Upstream foundation:** zbruceli/kai
- **Initial slow-task backend:** OpenAB ACP (proposed; P3.0 smoke required)
- **Software development client:** Kai kai-sim
- **First hardware candidate:** M5StickS3 using Kai-derived firmware

> This is the complete requirements source of truth, canonicalized from the supplied v0.2 specification and decisions confirmed on 2026-10-07. P0 naming and provider-gate decisions below supersede conflicting labels or gates in the original attachment.

## Repository document authority

- SPEC.md is the requirements source of truth: scope, constraints, architecture requirements, and acceptance criteria.
- PLAN.md is the execution plan: ordering, estimates, and gates; it must not silently redefine requirements.
- STATUS.md records current verified state and evidence; it does not override SPEC.md.
- SPEC-P0-DELTA.md is a historical decision record, not a competing requirements source.

## Milestone naming

- P0 — Foundation: project identity, repository/document structure, upstream boundary, and canonical requirements. Closed after this reconciliation.
- P1–P4 — Software validation and acceptance, using kai-sim before hardware.
- H0 — Hardware UX Gate: decide whether dedicated hardware earns its place, after software acceptance.
- H1 — Hardware integration/validation, only if H0 passes.

---

## P3 Architecture Update — OpenAB ACP (proposed 2026-10-09)

**This section and the updated requirements below define the proposed P3 design.** See [ADR-0001](docs/adr/0001-openab-acp-slow-path.md) for decisions, sequence, security, failure modes and acceptance gates. P0/P1/P2 design and verified runtime gates remain intact.

**Product:** Grab-to-talk, bring-your-own-agent voice companion. Gemini Live remains the realtime voice layer and FAST/SLOW router; simple conversations never go through the slow backend. `kai-sim` remains the P3 software acceptance client; M5StickS3 follows the H0 hardware UX gate.

**P3 default Slow Backend:** OpenAB native authenticated ACP-over-WebSocket (`GET /acp`) with **one isolated, capability-restricted ACP agent**. No N100 Home Bridge, no connection to the main OpenClaw Gateway and no inheritance of its memories/credentials. An independently isolated OpenClaw backend may be considered later; Gemini-facing tool naming remains backend-neutral.

**P3 sequence and ownership:**

~~~text
Device/kai-sim → Amusant Relay ↔ Gemini Live (FAST)
                                    │ function call: delegate_task (SLOW)
                                    ▼
                        Relay validates / authorizes
                                    │
                        SQLite durable task + dispatch intent
                                    │
                        FunctionResponse (same call_id):
                        accepted + task_id, or honest error
                                    │
                        short Gemini spoken acknowledgement
                                    │ (separate async worker)
                        Worker → OpenAB /acp → restricted agent
                                    │
                        Result Inbox (status, card, speak, details_md)
                                    │
                        reconnect notification / user-requested
                        new tool-disabled Live speech turn
~~~

`delegate_task` accepts a **bounded task string**, optionally a bounded task-kind hint (`research`, `compare`, `summarize`). Do not expose backend URL, agent name, arbitrary tools/commands or secrets as model-controlled parameters. Admission means task + dispatch intent durably committed, **not finished**. Gemini receives the same function call ID only once; completed results are independent inbox events, not a second function response. Use admission-only short blocking tool behavior only if the selected Gemini model supports it; never block Live until OpenAB finishes.

**P3.0 gate before #1 implementation:** prove OpenAB binary/image actually includes its `acp` feature; run authenticated `initialize`, `session/new`, `session/prompt` with an explicitly chosen restricted agent, model credentials and `OPENAB_ACP_ENABLED=true`. OpenAB uses `OPENAB_ACP_AUTH_KEY` transport bearer and allowed synthetic `acp_client` identity. A successful initial smoke is a go/no-go; no speculative adapter implementation if unsupported. Confirm upstream 180-second ACP prompt idle timeout and incomplete underlying cancellation; P3 limits attempts to **120 seconds** and cannot promise stopping side effects on timeout.

**Reliable tasks:** P3 uses local SQLite + dispatch intent/outbox + leases + bounded worker retries and `queued → running → completed | failed | expired` statuses. Task execution is **at least once**; enforce idempotency and enable no side-effecting tasks in P3. Result Inbox keeps separate delivery state, surviving Live/device disconnects and process restarts. Long-term memory, multi-agent routing, monitoring, broad shell/browser tools and permanent OpenAB subscription OAuth are not assumed.

**P4 (separate security/budget approval):** deploy Relay, Worker, OpenAB as **separate ECS tasks/services with distinct IAM roles**, OpenAB reachable privately over authenticated/TLS ACP only, DynamoDB as task source of truth plus SQS/outbox reconciliation. No privileged Relay/Agent sidecar sharing one IAM role, no public ACP, no N100 or personal OpenClaw secrets. Do not claim P4 deployed as a result of this proposal.

**Execution:** P3.0 ACP smoke → P3.1 `delegate_task` durable admission (#1) → P3.2 registry/worker recovery → P3.3 restricted ACP execution → P3.4 Inbox + speech. Keep P1/P2 tests green; no paid Live call or AWS deployment without separate approval.

---

# 1. Project Definition

Amusant is a low-latency, grab-to-talk and backend-neutral AI voice companion. The project consists of a realtime voice frontend, Relay, native fast/slow routing, durable slow-task delegation, asynchronous result delivery, and replaceable voice frontends and agent backends.

The product experience should feel like:

> Speak to Amusant with minimal friction and receive a useful spoken response almost immediately.

For complex work, Amusant acknowledges only after durable task admission, delegates to a restricted ACP agent and delivers the outcome asynchronously. In this phase, Amusant handles voice communication and spoken/text responses only; it does not add location sensing, environmental sensors, or physical-device control.

# 2. Product Hypotheses

The project tests two separate hypotheses, with a replaceable backend as the software hypothesis.

## Hypothesis A — Software/backend

A realtime Live voice model combined with a restricted, replaceable slow agent can provide immediate conversation and reliable background work without forcing every request through a slow agent pipeline. This hypothesis can be tested with kai-sim before hardware work.

## Hypothesis B — Dedicated hardware

A dedicated physical Amusant device may provide lower interaction friction than using a phone directly. This is not assumed to be true. Failure of the hardware hypothesis may pause or replace the hardware track without invalidating the software/backend.

# 3. Naming Convention

- Project: Amusant
- Cloud voice gateway: Amusant Relay
- Realtime conversational layer: Amusant Live
- Slow-task coordinator: Amusant Task Manager and Result Inbox
- First backend: OpenAB ACP agent
- Development frontend: Kai kai-sim
- First hardware candidate: Amusant Device based on M5StickS3
- Upstream architecture and hardware firmware: Kai, repository zbruceli/kai

Keep Amusant-owned service names separate from Kai internals. Do not rename upstream Kai internals merely for branding when that would make upstream synchronization harder.

# 4. Primary UX Goal

The critical product metric is interaction friction, not raw model intelligence. The intended fast interaction is:

~~~text
press → speak → release → hear response
~~~

For a dedicated device to justify its existence, normal use should target one physical PTT action, zero required screen attention, and zero phone interaction per request. H0 targets audio capture within 0.5 seconds of PTT down and first audible response at p50 ≤1.2 seconds / p95 ≤2.0 seconds after PTT release. These are goals, not claims about current performance.

# 5. Engineering Priorities

Optimize in this order:

1. Perceived voice latency
2. Interaction friction
3. Reliability
4. Security
5. Maintainability
6. Model/provider sophistication

Never make the realtime path slower merely to make the backend architecture more elegant.

# 6. MVP Scope

~~~text
kai-sim / future M5 → Relay ↔ Gemini Live (FAST)
                         │ delegate_task (SLOW)
                         ▼
                   Durable Registry
                         ▼
                   Worker → OpenAB ACP
                         ▼
                   Inbox → notification / requested speech
~~~

P3 is local-only with SQLite and restricted ACP. P4 evaluates separate ECS Relay/Worker/OpenAB tasks, DynamoDB and SQS after approval. No N100 main OpenClaw connection. Existing P0–P2 acceptance remains intact.

# 7. Explicit Non-Goals

Do not implement in the initial MVP:

- Android or iOS companion applications.
- BLE audio bridge, LE Audio, cellular modem, or eSIM integration.
- Major Kai firmware rewrite or direct M5StickS3-to-Live-provider integration.
- Persistent cloud conversation storage, audio recording/archive, custom STT, or custom TTS.
- Public exposure of the OpenClaw Gateway or an unprotected relay port.
- Automatic fast-provider selection/failover.
- Multi-provider production support as a prerequisite to software MVP acceptance.
- Consumer hardware certification.
- Location, sensors, or physical-world actuation.

# 8. Core Architecture Principle — Voice First

The primary input is streaming audio. Do not put a custom audio → STT → classifier → LLM → TTS chain on the realtime critical path when the selected Live provider supports native realtime audio. The fast layer handles direct answers and bounded quick tools; long work uses the backend-neutral durable task boundary.

# 9. Fast / Slow Architecture

Amusant uses three logical lanes.

## Lane 0 — Direct

Example: “某車款的引擎有幾缸？” The selected Live provider answers directly from available knowledge; no external tool is needed.

## Lane 1 — Fast Tool

Example: “現在幾點？” A bounded, predictable low-latency tool may run inline and return its result to Live. The MVP must name and authorize any quick tool before implementation; do not silently grant arbitrary tool access.

## Lane 2 — Slow Delegation

Example: “查詢目前的二手筆電行情，並依指定用途比較。” Supported bounded current research and comparison tasks go to the restricted P3 ACP backend. Persistent memory/history, arbitrary browser/computer operations, multi-agent actions, scheduled monitoring and unbounded work remain deferred. Live acknowledges only after durable admission.

# 10. Fast Provider Is the Initial Router

The selected Live provider owns the immediate conversational experience and may choose DIRECT, FAST TOOL, or SLOW DELEGATION through its native function/tool calling. Do not insert another model into the realtime critical path unless observed routing failures justify it. Jev may later be evaluated as a shadow classifier or policy guardrail; it is deferred.

The user manually selects the fast provider. Amusant must not automatically choose or switch providers. Gemini Live is the required initial baseline. A second real Live provider is optional validation of the provider abstraction, not an MVP pass/fail gate.

# 11. Slow Backend Responsibilities

Amusant owns the durable admission/Task Registry/Result Inbox and user/device authorization. A single restricted OpenAB ACP agent performs bounded slow text work. It is not a durable personal memory service. N100 OpenClaw integration, cross-agent memory, monitoring and unbounded actions are outside P3. See [ADR-0001](docs/adr/0001-openab-acp-slow-path.md).

# 12. Frontend Independence

Amusant must not depend on one frontend. Possible frontends include kai-sim, M5StickS3, desktop, browser, future phone client, or future hardware. The software backend remains valid if the M5StickS3 hypothesis fails. Kai remains the upstream source for the original simulator/protocol and later hardware firmware.

# 13. H0 — Hardware UX Gate

H0 asks whether dedicated hardware provides enough UX advantage over an optimized phone voice workflow. H0 runs only after software acceptance and before significant M5-specific development. Hardware success is not assumed.

## Baseline

Measure the same voice request using an optimized phone flow: wake/unlock if required, invoke voice, speak, and receive a response.

## Hardware target

~~~text
Touch device → hold PTT → speak → release
~~~

Targets:

- User actions before speaking: 1 physical action.
- Eyes required: 0.
- Phone interaction per request: 0.
- PTT down to audio capture: <0.5 seconds.
- PTT release to first audible response: p50 ≤1.2 seconds, p95 ≤2.0 seconds.

H0 must show meaningfully lower interaction friction than the same command on a phone. If it does not, hardware may be paused/replaced while the backend continues.

# 14. Mobile Network Rule

The MVP does not require a custom phone application. The hardware candidate may use:

~~~text
Amusant Device → phone hotspot → 4G/5G → AWS Amusant Relay
~~~

Hotspot usability is part of H0/H1: persistent-hotspot behavior, idle timeout, reconnect time, battery impact, known-network reconnect, and Android/iOS differences. Normal interaction should not require manually operating the phone before every request. A hotspot does not itself provide a private route to the home Gateway. Never expose the OpenClaw Gateway or an unprotected relay port publicly.

# 15. Target Software Architecture

~~~text
Kai-sim or M5 → Amusant Relay ↔ Gemini Live (FAST)
                       │       delegate_task (SLOW)
                       ▼
                Durable Task Registry
                       ▼
                Worker → OpenAB /acp
                       ▼
                Result Inbox → notification / requested speech
~~~

P3 uses local SQLite and worker. P4 may deploy Relay, Worker and OpenAB as separate ECS services after authorization; there is no default N100 Home Bridge. The existing Kai /ws Fast Voice code remains the baseline.

# 16. Amusant Relay

Deploy the target Relay as a Docker container on ECS Fargate after local protocol validation. Responsibilities:

## Client transport

- Accept Kai-compatible WSS clients and authenticate clients.
- Receive streaming audio, PTT/activity boundaries, and maintain turn/session state.
- Stream returned audio and deliver text/card/notification data.

## Live provider

- Establish/manage realtime provider sessions.
- Stream input audio and PTT/activity boundaries.
- Receive model audio, text/transcript, and tool calls; return tool results.

## Slow tasks

- Receive validated backend-neutral `delegate_task` calls and durably commit task + dispatch intent before replying with the same Gemini tool call ID.
- Dispatch independently via Worker and authenticated OpenAB ACP; never wait for agent execution in the Live callback.
- Track durable state and deliver results through Inbox across process/client reconnects.

The Relay should remain largely stateless. If slow-task acceptance/results must survive a Fargate restart, define durable storage, idempotency, expiry, and delivery semantics; never rely on container-local memory while promising guaranteed delivery.

# 17. Amusant Live Provider Interface

Provider-specific logic must be abstracted. Gemini Live is the initial required provider because Kai already uses it. OpenAI GPT Live and xAI Grok Voice are optional candidates for later/provider-abstraction validation, subject to current capability checks. A second provider is not mandatory for MVP acceptance.

~~~python
class LiveProvider:
    capabilities: ProviderCapabilities  # per-provider; never infer identical support
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
    def on_interrupted(self, callback): ...
    def on_turn_complete(self, callback): ...
    def on_error(self, callback): ...
~~~

The initial Kai-compatible audio envelope is mono PCM16LE with explicit sample rate: 16 kHz client input and 24 kHz client output. Provider adapters normalize their own audio encoding/rate to this boundary; an `AudioFrame` carries bytes and sample-rate metadata. If a format/rate is not implemented, the adapter must fail closed rather than mislabel the audio. This baseline is grounded in the pinned Kai v0.12.0 relay source.

Callbacks are dispatched serially in provider receive order. Transcript events identify input/output and partial/final state. Tool-call events carry the provider call ID, function name, and JSON arguments. `on_interrupted` reports provider-confirmed interruption separately from `on_turn_complete`. Background receive errors are emitted as sanitized provider errors; credentials, raw audio, and full prompts must not enter logs/events.

PTT `start_turn`/`end_turn` represent activity start/release; starting a new turn may barge in when the provider supports it. Barge-in and standalone `cancel_response` are distinct capabilities. Do not report a cancellation or completed turn before the provider confirms it.

The UI/config selects the provider manually, for example `AMUSANT_LIVE_PROVIDER=gemini`; missing or unknown selection fails closed, with no automatic provider choice or fallback. Providers must be compared for streaming audio, speech output, tool calling, barge-in/cancellation, session lifecycle, language support, errors, cost, and latency. An adapter does not imply identical capabilities.

# 18. Slow Delegation Tool

Gemini Live registers one backend-neutral `delegate_task` function, with a bounded string `task` and optional enum `task_kind` (`research`, `compare`, `summarize`). No model-selected backend URL, arbitrary agent, tools, command or file path is allowed. Relay validates request/user/turn, persists task + dispatch intent, and returns the **same call ID** with `{"status":"accepted","task_id":"..."}` only after durable admission, or honest rejection/unavailability. It must **not** await OpenAB execution; blocking can only cover brief verified admission.

# 19. Routing Policy

Direct normal conversation and small bounded tools through Gemini Live. Delegate only allowed background text/research tasks. Don't claim background work was started without a successful function call and durable acceptance. This first P3 backend does not imply cross-session personal memory, autonomous schedules, browser/computer control or side-effecting actions; prompts are not a substitute for runtime authorization.

# 20. Slow Task UX

~~~text
PTT → Gemini Live delegate_task → Relay durable accepted + task_id
→ short spoken acknowledgement → Worker calls OpenAB ACP
→ status/result persisted → Inbox notification/card on reconnect
→ user-requested spoken summary in NEW tool-disabled Live turn
~~~

`speak` (brief), `card` and `details_md` (bounded) results are normalized from untrusted ACP output. Do not return completion via the already-answered admission function-call ID.

# 21. OpenAB ACP Worker

Worker uses native authenticated OpenAB `GET /acp` WebSocket with JSON-RPC: `initialize` → `session/new` (fixed `cwd`, no model-selected MCP servers) → `session/prompt`; text is received in `session/update` and success requires the correlated terminal prompt response. A pinned image compiled with `acp` support, `OPENAB_ACP_ENABLED=true`, bearer `OPENAB_ACP_AUTH_KEY` and narrowly permitted `acp_client` sender must pass P3.0 before tool implementation. Session resume is not durable memory replay. Research baseline timeout 180s; P3 attempt max 120s. `session/cancel` does not guarantee backend execution stops.

# 22. Durable Task Registry and Inbox

P3 local SQLite stores owner/turn/task ID, dedupe key, task status (`queued`, `running`, `completed`, `failed`, `expired`), leases/attempts and transactional dispatch intent. Independent delivery status survives disconnect and is used for retry-safe notifications. Execution is at-least-once; exactly-once side effects are not promised. P4 proposes DynamoDB conditional claims + SQS with outbox reconciliation. Agent result is `{"task_id":"...","status":"completed","result":{"speak":"...","card":"...","details_md":"..."}}`.

# 23. Isolation and Security

Do not connect to the main N100 OpenClaw or expose ACP publicly. OpenAB must run restricted tools/filesystem/network, separate credentials, and no access to the Amusant task database/SQS or home assets. P4 Relay/Worker/OpenAB require different ECS tasks and IAM roles, not privileged sidecars. Agent output is untrusted and speech replay disables Live tools. No raw audio persistence; keep bounded task-data retention. See [ADR-0001](docs/adr/0001-openab-acp-slow-path.md).

# 24. Credentials and Data

- AWS uses ECS task roles/execution roles; do not embed static AWS credentials in source, .env examples, or images.
- Live-provider credentials belong in AWS Secrets Manager if cloud deployment is approved.
- ACP transport and agent provider credentials must be distinct from device/Relay credentials, revocable and injected securely.
- No main N100 OpenClaw Gateway credentials are used in P3.
- Simulator may start with one test credential. Future devices use unique device_id and revocable device secret/nonce/HMAC; never put Live provider credentials in firmware.
- Do not log raw audio. Transcript/task/result retention and deletion policy must be defined; provider data handling must be checked.

# 25. AWS Infrastructure

P3 is local-only and must not incur unapproved AWS costs. P4 proposes ALB/ACM WSS to Relay ECS, independently permissioned Worker ECS and private authenticated/TLS OpenAB ECS, DynamoDB/SQS with outbox, IAM, Secrets Manager, CloudWatch, and configurable AWS region; deployment requires separate budget/security approval.

# 26. Repository Structure

Keep Kai protocol/P1/P2 `relay/` and `fast_voice/` intact. Proposed P3 additions under `slow/` for durable admission, worker, OpenAB ACP adapter and result Inbox; these modules are **not yet implemented**. See [ADR-0001](docs/adr/0001-openab-acp-slow-path.md).

# 27. Observability

Every conversation turn receives turn_id; every delegated task receives task_id. Required timestamps:

~~~text
ptt_down
first_audio_received_by_relay
live_session_ready
input_turn_end
live_tool_call
first_output_audio
delegate_created
home_bridge_received
slow_worker_started
slow_worker_finished
result_received_by_relay
result_delivered
~~~

Metrics: PTT release → first audio; PTT release → delegation acknowledgement; Relay↔Live RTT; Relay↔Worker dispatch delay; OpenAB ACP execution duration; slow-task end-to-end delivery. Do not log raw audio or full transcripts by default; debug content logging requires explicit configuration.

# 28. Software Latency Targets

Fast path, PTT release → first audible response: target p50 ≤1.2 sec, p95 ≤2.0 sec; stretch p50 <1.0 sec. Slow path, PTT release → spoken acknowledgement: same target; actual bounded ACP work may take seconds or minutes and does not count as realtime latency. Targets remain unmeasured. Thirty samples are smoke-level, not robust p95 evidence.

# 29. Routing Evaluation

Evaluate at least 100 utterances, including conversation, factual questions, simple reasoning, current information, research, comparison, personal memory, named-agent requests, browser work, scheduled tasks, long-running work, ambiguous requests, Chinese, English, mixed language, and technical names. Examples:

~~~text
“某車款的引擎有幾缸？” → FAST
“曲軸箱是幹嘛的？” → FAST
“比較兩款筆電的效能差異。” → FAST or offer
“查詢目前的二手筆電行情，並依指定用途比較。” → SLOW
“請其他已授權的 agent 協助規劃今日訓練。” → SLOW
“回顧先前保存的模型實驗結果。” → SLOW
“嗨，你在嗎？” → FAST
~~~

Record expected_route, actual_route, tool_called, latency. Original v0.2 target is ≥90% routing accuracy; report confusion matrix and especially false-fast decisions. This is an evaluation target, not proof of quality. Provider count does not gate MVP acceptance.

# 30. Failure Handling

Reject unauthorized/malformed/stale/oversized slow tool calls; do not falsely acknowledge a failed admission. Failed task storage returns unavailable. Worker crash/leases and queued dispatch are recoverable; OpenAB unavailable/overload/timeouts produce bounded retry or honest failed/expired status. Provider cancellation must never be reported as confirmed stopped work. Live/device disconnect does not erase results; deliver them from Inbox on reconnect.

# 31. Testing

Keep the existing P1/P2 regression suite green. Add mock tests for FAST without slow calls, same Gemini function call ID, malformed/unauthorized admission, durable acceptance and idempotent duplicate calls, SQLite crash/restart/worker lease recovery, ACP success/error/timeout and permission restrictions, offline Inbox, and tool-disabled spoken delivery. Real API calls and AWS staging require separate approval.

# 32. Implementation Phases

- P0/P1/P2: complete, unchanged.
- **P3.0:** pinned OpenAB ACP feature/auth/model-credential smoke, restricted agent.
- **P3.1 (#1):** `delegate_task` durable admission + same-call-ID Gemini response, fake backend.
- **P3.2:** SQLite Task Registry + dispatch intent + Worker restart recovery.
- **P3.3:** authenticated ACP execution, 120s attempt bound, failure handling and normalized outputs.
- **P3.4:** Result Inbox, notifications, user-requested tool-disabled Live speech.
- **P4:** approved ECS/DB/SQS deployment, security/latency/reliability/routing acceptance.
- H0/H1: existing hardware UX gate and candidate integration remain unchanged.

No P3 features have been implemented by this docs-only PR.

# 33. M5StickS3 Hardware Candidate

Kai-derived hardware provides microphone, speaker, PTT buttons, LCD, Wi-Fi, Opus/WebSocket, and sleep/wake. The device remains an I/O terminal; AI/provider logic and credentials stay server-side. Use the original integrated board first; no custom PCB/soldering in the initial hardware track.

# 34. H0 Hardware UX Gate and Continuation Criteria

H0 compares the same request on an optimized phone voice flow and on the device. Measure required actions, eyes-on-screen time, phone interaction, PTT-to-capture time, PTT-release-to-first-audio, reconnect time, hotspot dependency, battery, audio quality, and subjective friction. Targets are one physical action, zero eyes/phone interaction, capture <0.5 seconds after PTT down, and first audible response p50 ≤1.2 seconds / p95 ≤2.0 seconds. Continue only if the device has a meaningful friction advantage, reliable reconnect, and acceptable battery/audio. If H0 fails, pause/replace hardware while keeping the software project.

# 35. Deferred Roadmap

- v0.3: optional OpenAI GPT Live provider evaluation.
- v0.4: optional xAI Grok Voice provider evaluation.
- v0.5: compare provider latency and voice quality.
- v0.6: Jev shadow-routing experiment; v0.7: routing guardrail only if real data justifies it.
- v0.8: native OpenClaw Gateway RPC evaluation; v0.9: multi-device identity/registry.
- v1.x: hardware production tuning. BLE companion and cellular hardware remain future options only if H0/H1 evidence justifies them.
- Do not implement deferred features opportunistically.

# 36. Primary Product Rule

Project Amusant succeeds only if it improves the user's actual interaction with their AI system. Do not optimize for a gadget for its own sake. Amusant Live owns immediacy; Amusant owns durable delegation/delivery; an interchangeable restricted backend owns bounded execution; fast providers are manually selected; the frontend is replaceable; Kai is respected upstream; the dedicated device must earn its place through UX.
