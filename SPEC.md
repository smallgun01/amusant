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

**This section supersedes the OpenClaw/N100 Home Bridge design for P3 in older sections of this document.** See the normative [ADR-0001](docs/adr/0001-openab-acp-slow-path.md) for decisions, sequence, security, failure modes and acceptance gates. Older references to `delegate_to_openclaw`, the Home Bridge, direct N100 integration and transient task storage below are **historical superseded proposals**, not P3 implementation instructions. P0/P1/P2 design and verified runtime gates remain intact.

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

Amusant is a low-latency voice interface to a persistent OpenClaw agent system. It is not defined by a particular physical device. The project consists of a realtime voice frontend, a low-latency relay, fast/slow task routing, persistent OpenClaw integration, asynchronous background-task delivery, and replaceable voice frontends.

The product experience should feel like:

> Speak to Amusant with minimal friction and receive a useful spoken response almost immediately.

For complex work, Amusant acknowledges promptly, delegates the work to OpenClaw, and returns the result asynchronously. In this phase, Amusant handles voice communication and spoken/text responses only; it does not add location sensing, environmental sensors, or physical-device control.

# 2. Product Hypotheses

The project tests two separate hypotheses.

## Hypothesis A — Software/backend

A realtime Live voice model combined with OpenClaw can provide fast conversational interaction plus persistent agent capability without forcing every request through a slow agent pipeline. This hypothesis can be tested with kai-sim before hardware work.

## Hypothesis B — Dedicated hardware

A dedicated physical Amusant device may provide lower interaction friction than using a phone directly. This is not assumed to be true. Failure of the hardware hypothesis may pause or replace the hardware track without invalidating the software/backend.

# 3. Naming Convention

- Project: Amusant
- Cloud voice gateway: Amusant Relay
- Realtime conversational layer: Amusant Live
- Home-side connector: Amusant Home Bridge
- Persistent backend, first integration: OpenClaw
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

The software MVP must be testable without owning an M5StickS3. The target software architecture is:

~~~text
PC / Kai kai-sim
    │ microphone + speaker; Kai-compatible WebSocket protocol
    ▼
AWS Amusant Relay
    ├── FAST: user-selected Amusant Live provider → realtime speech/text
    └── SLOW: delegate task → Amusant Home Bridge
                                  ↓
                            OpenClaw @ private host
                       (configured agents/tools
                         such as configured, allowlisted agents)
                                  ↓
                         async result to client
~~~

P1 may use a local mock Relay/provider to isolate the simulator and wire protocol. The complete software MVP must be testable with kai-sim and without the physical device. AWS deployment is a later integration gate and requires separate cost/security approval.

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

The primary input is streaming audio. Do not put a custom audio → STT → classifier → LLM → TTS chain on the realtime critical path when the selected Live provider supports native realtime audio. The fast layer handles direct answers and bounded quick tools; long work is delegated to OpenClaw.

# 9. Fast / Slow Architecture

Amusant uses three logical lanes.

## Lane 0 — Direct

Example: “某車款的引擎有幾缸？” The selected Live provider answers directly from available knowledge; no external tool is needed.

## Lane 1 — Fast Tool

Example: “現在幾點？” A bounded, predictable low-latency tool may run inline and return its result to Live. The MVP must name and authorize any quick tool before implementation; do not silently grant arbitrary tool access.

## Lane 2 — Slow Delegation

Example: “查詢目前的二手筆電行情，並依指定用途比較。” Research, persistent memory/history, browser/computer work, complex comparisons/planning, other agents, external actions, scheduled work, and long-running jobs go to OpenClaw. Live acknowledges promptly while the task runs asynchronously.

# 10. Fast Provider Is the Initial Router

The selected Live provider owns the immediate conversational experience and may choose DIRECT, FAST TOOL, or SLOW DELEGATION through its native function/tool calling. Do not insert another model into the realtime critical path unless observed routing failures justify it. Jev may later be evaluated as a shadow classifier or policy guardrail; it is deferred.

The user manually selects the fast provider. Amusant must not automatically choose or switch providers. Gemini Live is the required initial baseline. A second real Live provider is optional validation of the provider abstraction, not an MVP pass/fail gate.

# 11. OpenClaw Responsibilities

OpenClaw owns tasks requiring depth or persistence:

- Persistent memory and personal history.
- Multi-source research and comparison.
- Browser/computer work and complex planning.
- Agent orchestration, retries, long-running jobs, and persistent state.
- External actions, scheduled tasks, and monitoring, subject to existing approval/safety policy.

OpenClaw is not required for ordinary realtime conversation.

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
                         USER
                           │ audio
                           ▼
                 Voice frontend
                 MVP: Kai kai-sim
                           │ WSS / audio
                           ▼
                 AWS Application LB
                    TLS / WebSocket
                           │
                           ▼
                   Amusant Relay
                     │         │
                 FAST│         │SLOW
                     ▼         ▼
            Amusant Live    Home Bridge on private host
            selected user      outbound WSS
            provider               │ local/private
                                   ▼
                             OpenClaw Gateway
~~~

The Relay is the cloud-facing audio/provider endpoint. The Home Bridge initiates outbound WSS from private host; no home inbound port is required. Local/mock P1 testing may omit AWS. Public OpenClaw Gateway exposure is prohibited.

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

- Receive delegate_to_openclaw, create a task_id, and return accepted/unavailable status.
- Forward the task to an authenticated Home Bridge.
- Track task state and deliver final results through the client inbox mechanism.

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

The fast provider receives a bounded function definition:

~~~json
{
  "name": "delegate_to_openclaw",
  "description": "Delegate work that should not block the realtime conversation to the user's persistent OpenClaw backend.",
  "parameters": {
    "type": "object",
    "properties": {
      "task": {"type": "string"},
      "preferred_agent": {"type": "string"},
      "reason": {"type": "string"}
    },
    "required": ["task"]
  }
}
~~~

The default agent is selected by server-side configuration. An explicit request for another configured agent may select it only through a server-side allowlist. General orchestration stays inside OpenClaw where practical.

# 19. Routing Policy

The Live system prompt should state:

~~~text
Answer directly for ordinary conversation, simple explanation/reasoning, and questions you can answer confidently from current context.
Use a bounded quick tool only when it settles the request inline.
Delegate to OpenClaw for current external research, multi-source comparison, persistent memory/history, other agents, browser/computer work, long-running actions, scheduled/background/monitoring work, or complex planning.
When delegating: call delegate_to_openclaw; do not claim completion; acknowledge briefly only after actual acceptance; do not block the realtime conversation while the task runs.
Never say you are researching/checking/working unless the delegate call was made.
~~~

Prompt text is not the security boundary. The Relay/Home Bridge must enforce allowed agents/tools and approval rules.

# 20. Slow Task UX

Example: the user asks for current used-laptop prices and a comparison.

~~~text
PTT release → Live selects SLOW → delegate_to_openclaw → Relay/Bridge confirms accepted → Live says a short acknowledgement.
Meanwhile OpenClaw researches → result returns through Home Bridge and Relay → client speaks/displays the result.
~~~

Example: for a request to compare current used-laptop prices, Live calls the delegation function, waits for actual acceptance, then gives a short acknowledgement such as “好，我幫你仔細比一下。” OpenClaw researches in the background and the result is delivered later. The fast conversation must not wait for the full job. Acceptance must mean the task was actually recorded/accepted; eventual delivery and failure semantics must be explicit.

# 21. Amusant Home Bridge

Run amusant-home-bridge on the private host. It establishes an outbound persistent WSS connection:

~~~text
private host Home Bridge → WSS → Amusant Relay
~~~

Do not expose private host port 18789 or the OpenClaw Gateway to the public Internet. OpenClaw remains localhost/private. Home Bridge owns local authentication, agent/tool allowlists, task execution and result return.

# 22. Home Bridge Protocol

Proposed endpoint: wss://amusant.example.com/home-bridge. Message classes include authentication, task_start, task_ack, task_result, failure, ping, and pong. Examples:

~~~json
[
  {"type":"auth","home_id":"home-001","token":"<redacted>"},
  {"type":"task_start","task_id":"uuid","client_id":"client-001","agent":"default","task":"Compare ...","created_at":"ISO8601"},
  {"type":"task_ack","task_id":"uuid"},
  {"type":"task_result","task_id":"uuid","status":"completed","result":"..."},
  {"type":"task_result","task_id":"uuid","status":"failed","error":"..."}
]
~~~

Reconnect backoff target: 1, 2, 4, 8, 15, 30 seconds maximum. Define replay protection, rotation/revocation, idempotency, task TTL, accepted-vs-durable semantics, and result retention before implementation.

# 23. OpenClaw Adapter

Initial proposal: local OpenAI-compatible endpoint http://127.0.0.1:18789/v1/chat/completions. Agent target example: openclaw/<configured-default-agent>; other agents are selected from a server-side allowlist. Stable session identity candidate: amusant:<client_id>, refined per conversation/device before implementation; use the supported stable user/session routing field and avoid sharing history unintentionally across devices/conversations.

Security gate: the Chat Completions endpoint is disabled by default and a shared bearer token is broad Gateway/operator access, not a narrow per-agent credential. If used, keep the credential on private host only and do not send it to AWS/device. Before P3, prove a least-privilege boundary, an isolated/restricted agent or supported identity-aware proxy scopes, server-side allowlists, and confirmation for external actions. Verify session behavior and long-task cancellation/recovery. Native Gateway RPC may be evaluated later.

# 24. Credentials and Data

- AWS uses ECS task roles/execution roles; do not embed static AWS credentials in source, .env examples, or images.
- Live-provider credentials belong in AWS Secrets Manager if cloud deployment is approved, e.g. amusant/prod/gemini.
- Home Bridge credentials belong in protected private-host configuration under a non-identifying home id, and must be revocable/rotatable.
- OpenClaw Gateway credentials remain only on private host.
- Simulator may start with one test credential. Future devices use unique device_id and revocable device secret/nonce/HMAC; never put Live provider credentials in firmware.
- Do not log raw audio. Transcript/task/result retention and deletion policy must be defined; provider data handling must be checked.

# 25. AWS Infrastructure

Proposed: ECR, ECS Fargate, Application Load Balancer, ACM, CloudWatch Logs, Secrets Manager, IAM; Route53 is optional. Initial deployment region is configurable and selected during the approved cost/security review. Support WSS keepalive and suitable ALB idle timeout. No cloud resource creation or spend is authorized by this spec alone.

# 26. Repository Structure

One independent Amusant monorepo for the software MVP; defer splitting services until there is a concrete release/ownership need. Proposed layout:

~~~text
amusant/
├── README.md
├── SPEC.md
├── PLAN.md
├── STATUS.md
├── relay/
│   └── amusant_relay/
├── home_bridge/
│   └── amusant_home_bridge/
├── infra/aws/
├── tests/
└── docs/
~~~

Kai firmware remains upstream and is not copied into this repository initially. If source is reused later, preserve MIT notices and pin the upstream commit. Prefer upstream PRs or small tracked patches for hardware changes.

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
openclaw_started
openclaw_finished
result_received_by_relay
result_delivered
~~~

Metrics: PTT release → first audio; PTT release → delegation acknowledgement; Relay↔Live RTT; Relay↔Home Bridge RTT; OpenClaw duration; slow-task end-to-end delivery. Do not log raw audio or full transcripts by default; debug content logging requires explicit configuration.

# 28. Software Latency Targets

Fast path, PTT release → first audible response: target p50 ≤1.2 sec, p95 ≤2.0 sec; stretch p50 <1.0 sec. Slow path, PTT release → spoken acknowledgement: same target; actual OpenClaw work may take seconds or minutes and does not count as realtime latency. Targets remain unmeasured. Thirty samples are smoke-level, not robust p95 evidence.

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

- Live unavailable: return a short error; do not silently send ordinary conversation through OpenClaw.
- Home Bridge unavailable: return unavailable; explain the persistent backend is unavailable.
- OpenClaw failure: return failure through the normal background-task result path.
- Fargate restart: clients and Home Bridge reconnect; recovery follows the chosen task persistence semantics; do not depend on container-local state.
- Auth, quota, malformed frames, oversized messages, provider timeout, cancellation, and partial output must fail safely and be observable without exposing credentials/audio.

# 31. Testing

Unit tests: LiveProvider abstraction; provider selection; slow delegation; task registry/idempotency; Home Bridge authentication/reconnect; OpenClaw adapter; failure propagation.

Integration:

~~~text
FAST → Live/mock provider → no OpenClaw task
SLOW → delegate → accepted acknowledgement → OpenClaw task → async result
~~~

End-to-end: kai-sim → Amusant Relay → Gemini Live → audio response; and kai-sim → Relay → Home Bridge → OpenClaw → async text/voice result. P1 begins with a mock provider; it does not prove live-provider or production readiness.

# 32. Implementation Phases

P0 Foundation is the project/repository/requirements baseline and is complete only when the canonical SPEC and document roles are aligned.

- P1 — Kai simulator software loop: kai-sim on a LAN client with microphone/speakers (OS to be confirmed), mock Amusant Relay on a private host, audio capture/playback and Kai protocol; no hardware required.
- P2 — Fast voice: Gemini Live baseline, explicit manual provider selection, provider abstraction. A second real Live provider is optional validation, not an MVP gate.
- P3 — OpenClaw slow lane: Home Bridge, least-privilege security, task lifecycle and async result path.
- P4 — Software acceptance: Relay/Bridge integration, latency, routing, reliability, privacy and failure tests; AWS staging only after cost/security review and approval.
- H0 — Hardware UX Gate: compare device vs optimized phone flow after software acceptance.
- H1 — M5StickS3/Kai firmware integration and portability testing only if H0 passes.

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

Project Amusant succeeds only if it improves the user's actual interaction with their AI system. Do not optimize for a gadget for its own sake. Amusant Live owns immediacy; OpenClaw owns depth and persistence; fast providers are manually selected; the frontend is replaceable; Kai is respected upstream; the dedicated device must earn its place through UX.
