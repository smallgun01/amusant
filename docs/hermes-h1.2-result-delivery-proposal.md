# Hermes H1.2 Result Delivery Contract — Proposal

**Status (2026-10-20):** [PR #71](https://github.com/smallgun01/amusant/pull/71) merged this **design proposal**; [#75](https://github.com/smallgun01/amusant/pull/75)/[#80](https://github.com/smallgun01/amusant/pull/80) proved the isolated Option D PoC. [#87](https://github.com/smallgun01/amusant/pull/87) runtime B1 and [#90](https://github.com/smallgun01/amusant/pull/90) pinned native ARM64 B2 are now **merged E1 GO** ([evidence](https://github.com/smallgun01/amusant/actions/runs/39240814687)), but not a live OCI deployment, real-provider test, accepted full section-read/v2 envelope, retention policy or artifact-hosting approval. [SPEC v0.5](../SPEC.md) and [ADR-0003](adr/0003-oci-a1-hermes-sqlite.md) govern the target. This proposal alone authorizes no external actions.

## Decision boundary

Hermes owns long-form reasoning, tools, report creation, summarization, and content transformation. Gemini Live handles conversational FAST turns and reads only a short, already prepared answer. Relay stays deterministic: authenticate, admit, route, validate bounds and ownership, persist task state and a pointer, and report delivery truth.

No complete report, source bundle, code block, or long plain-text fallback may enter Relay's polling/result transport or a Gemini Live tool payload. Relay and Gemini Live do not summarize artifacts. Invalid or missing speech text does not invalidate an already committed artifact; the result must report separate execution, artifact, and presentation outcomes. Show artifact availability only when trusted server metadata confirms a reference.

## Proposed result and artifact flow

1. Relay admits a bounded explicit request and binds it to authenticated owner and task IDs. It does not append the Live transcript or guess what “that PR” means.
2. Hermes performs the approved task. The full report is written through a Hermes-side server operation before the run becomes terminal. The Runs API terminal result and Relay-facing polling projection contain only the bounded result envelope, never the report, tool-call body, transcript, or raw fallback. If GET /v1/runs/{run_id} exposes those bodies, the adapter must provide a sanitized result projection and Relay must consume only that projection; otherwise this boundary is not met. Hermes Run history alone is not the artifact-retention contract.
3. The model may ask the Hermes-side write operation to save report content, but it cannot choose or assert an artifact reference, storage key, URL, path, owner, or task binding. The trusted adapter obtains owner/task identity from authenticated server context, validates and durably writes the report, then alone mints an opaque artifact_ref bound to that owner and task. Only verified adapter metadata can be joined to the model's bounded presentation fields.
4. Hermes acknowledges a write only after durable commit. A write retry uses the same server-generated task/artifact-generation idempotency key and resolves to the same committed reference instead of duplicating the artifact. If writing fails after its bounded retry budget, Hermes reports artifact unavailable and does not claim a durable report. If the write succeeds but terminal-envelope delivery fails, retain the artifact and retry/reconcile the small envelope with the same task and reference; do not regenerate the report or create another artifact merely to redeliver the envelope.
5. Hermes returns a small versioned result assembled from model-authored presentation fields plus trusted adapter metadata. Relay validates the envelope and persists only the bounded envelope, task status, and opaque pointer in the existing Task Ledger/Inbox.
6. check_task_status returns a field allowlist: bounded status/outcomes, speak, card, safe artifact availability, has_more, and bounded section metadata. It does not return details_md, report content, tool output, or storage locator to Gemini by default.
7. For a completed task, Relay may open a tool-disabled Live speech turn and supply only a validated speak value with a direct-read instruction. Gemini does not receive the report and is not asked to summarize or rewrite it.

Candidate Relay-facing envelope (field caps and exact schema remain Owner decisions):

~~~json
{
  "schema_version": 2,
  "execution_status": "completed",
  "artifact_status": "committed",
  "presentation_status": "ready",
  "speak": "A short answer ready to read aloud.",
  "card": {
    "title": "Result ready",
    "lines": ["Brief point one", "Brief point two"]
  },
  "has_more": true,
  "sections": [
    {"section_id": "opaque-section-id", "title": "Findings", "has_more": true}
  ],
  "artifact_ref": "server-issued-owner-task-reference"
}
~~~

The candidate status axes keep independent facts separate: task execution may be completed, an artifact may be committed, and speech/card presentation may still need recovery. artifact_ref appears here only after the trusted adapter joins verified metadata; a model-supplied field with that name is discarded. The reference is not a bearer credential. Every retrieval re-authenticates the owner and verifies the owner/task binding. No storage URL or path is exposed to Gemini or accepted as authority.

## Input and section-read contracts

Replace the free-form-only admission shape with a bounded, explicit context envelope. Candidate fields:

- schema_version
- objective: non-empty bounded task description
- target: optional typed target such as a GitHub issue/PR with owner, repository, number, and optional immutable revision
- source_refs: bounded typed references, validated against server policy
- expected_output: requested format and language
- requested_scope: an intent hint such as read-only research, not a permission grant

Relay validates structure, size, identity, target resolution, and allowed source types before admission. Missing or ambiguous references require clarification or a safe rejection. The envelope must not contain arbitrary tools, commands, credentials, storage paths, or backend URLs. The server selects the actual read-only tool allowlist; text in the request cannot widen it. Do not forward the full Live conversation as implicit context.

A section follow-up is a new explicit Slow Task bound server-side to the authenticated owner, source task, committed artifact reference, and one validated section ID; it uses the existing single-flight rule and returns slow_busy while another Slow Task is outstanding. Gemini may map “第二點” only to an unambiguous section/ordinal in the currently presented bounded card and section metadata; otherwise it asks which result or section the user means. If the artifact has expired or is unavailable, return that explicit status and ask the user to rerun the original task; never fall back to Run history. Hermes reads and summarizes only the requested section and returns a small envelope, not the section body.

## Recovery and delivery semantics

- **Artifact write fails:** Hermes reports execution and artifact outcomes separately. It does not send the generated report to Relay as a fallback; it reports no artifact reference and an explicit recoverable save failure. A user retry is an explicit task using the same bounded inputs and idempotency policy.
- **Artifact committed, speech/card invalid:** Preserve the committed artifact and mark presentation as needing recovery. Hermes may make one bounded repair/reformat attempt from the committed artifact, with no Relay/Gemini summarization. If repair fails, the task remains execution-complete and artifact-available with recoverable presentation failure; Relay tells the user the report is saved but the short answer is unavailable. Capacity is released only after the remote run is terminal, consistent with #65.
- **Envelope delivery fails after artifact commit:** Keep the artifact and its task binding. Retry/reconcile only the terminal envelope using the same task and artifact-generation key; delivery retry is idempotent and does not rewrite the report. Exhausted retry leaves a recoverable delivery status, not a false “no report” or duplicated artifact.
- **Section source is missing/expired:** Return an explicit unavailable result. Do not infer content from a remote Run, prior transcript, or Gemini memory.

Retry counts, deadlines, and exact status-field names remain implementation details to approve alongside the schema caps. Retries must be bounded and observable; no silent truncation or infinite retry is allowed.

## Implementation reconciliation — original proposal vs merged E1 (2026-10-20)

| Area | Implemented offline baseline after #87/#90 | Still proposed / not accepted |
|---|---|---|
| [relay/hermes_result.py](../relay/hermes_result.py) | Strict bounded Facade projection with `schema_version`, `execution_status`, `artifact_status`, `presentation_status`, `artifact_ref`, `speak` and `card`; the legacy `details_md` input is capped at 512 characters and the Relay storage slot holds only `speak`. | No general section/has_more readback or accepted complete v2 field policy; source-model provider behavior remains unverified. |
| [relay/slow_path.py](../relay/slow_path.py) | `delegate_task` still accepts task + optional kind; `check_task_status` returns bounded status, `speak`, `card` and no `details_md` to Gemini on the completed path. | No explicit typed context/source/section-follow-up or separately reviewed full artifact readback UX. |
| [relay/live_bridge.py](../relay/live_bridge.py) | Tool responses are bounded and Inbox speech runs through a tool-disabled Live session. #90 proves the fake-provider chain, not live Gemini audio. | Real `check_task_status` → spoken S1, owner-audible completion and interruption/acknowledgement need E3 acceptance. |
| [relay/task_store.py](../relay/task_store.py) and [relay/dynamodb_task_store.py](../relay/dynamodb_task_store.py) | Owner-scoped Ledger/Inbox remain bounded; the legacy details slot stores short speech only on the Hermes path, while Facade owns full reports and references. | No generalized ArtifactRef persistence/section-read schema migration or accepted cross-volume expiration/backup policy. |
| [SPEC.md](../SPEC.md), [ADR-0002](adr/0002-external-task-store.md) | SPEC v0.5 defines the current small-result/Facade target; ADR-0002 remains historical AWS/DynamoDB context. | Finalize #56 extended artifact/section handling and hosting/retention approval without rewriting the Relay Ledger as an artifact repository. |

The [#58/#61 implementation](https://github.com/smallgun01/amusant/issues/58) preserves durable polling, one outstanding task, busy rejection, stop request/GET-terminal **classification** and restart behavior. #90's pinned active fake-provider STOP ended `failed / interrupt` and correctly reported `hermes_failed` (`stop_cancel_confirmed=false`): do **not** call this confirmed cancellation. Real Run deadline and HTTP/poll timing remain Owner-policy and live-cost gates; no extra queue/Worker/ECS/SQS service is proposed.

## Owner decisions for remaining v2 and live operations

1. Select the durable artifact mechanism and its hosting boundary. Define encryption, allowed content types/maximum size, owner/task authentication, retrieval route, deletion, retention, and behavior when an artifact expires or is unavailable. Keep the existing TaskStore a ledger and pointer store.
2. Approve the input and result schema versions, required fields, outcome names, strict caps, section metadata shape, and migration/backward-compatibility behavior. The existing v1 limits are implementation behavior, not approved v2 limits.
3. Set a language-aware speak cap that targets roughly 15–25 seconds without silent truncation. Confirm whether cards and section titles use character or byte limits.
4. Confirm the bounded retry budget/deadlines and whether a user retry after presentation failure is a new task linked to the existing artifact.
5. Approve the pinned real-Hermes smoke model/provider, run count, spend cap, runtime, and tool/egress scope before any live call.

## Remaining acceptance plan (beyond #90 offline E1)

- Input: resolved earlier-PR context succeeds; ambiguous “that PR”, wrong owner, unsupported source, oversized envelope, and unauthorized scope fail safely without using implicit transcript context.
- Hermes-to-Relay boundary: verify the full report is durably committed before terminal success; GET /v1/runs/{run_id} and the Relay polling projection expose only the small envelope, with no report/tool body in result payloads, logs, or traces consumed by Relay. Relay never receives 10k/30k raw output and truncates it.
- Artifact authority and retry: model-supplied reference/path/URL/owner/task values are ignored; only authenticated adapter context can mint a reference. Repeated write and envelope-delivery retries with one idempotency key do not duplicate artifacts or change owner/task binding. Write failure yields no false reference.
- Result parsing: valid versioned envelope passes; malformed JSON, unsupported version, missing/invalid speak, oversized fields, fences, and 10k/30k plain-text outputs fail closed. No long content or silent truncation reaches Relay/Gemini.
- Recovery: an invalid speak after artifact commit preserves the artifact, permits only bounded Hermes-side repair, and reports recoverable presentation failure if repair fails. Relay never creates a replacement summary.
- Tool boundary: default status exposes only the field allowlist; verify serialized Live payloads contain no details_md, report, code, URL/path, or raw storage locator.
- Artifact lifecycle: durable write/read, owner/task isolation, invalid/expired/unavailable reference, Relay and Hermes restart, retention, deletion, and remote Run expiry do not rely on Run history.
- Follow-up: exact section reference starts a new Slow Task; concurrent work returns slow_busy; ambiguous “第二點” asks for clarification; expired artifact returns unavailable; only the requested section is read within Hermes and only a small result leaves Hermes.
- Voice: speak is directly readable in the requested language and targets 15–25 seconds; card/status-only cases, interruption, and audio-confirmed spoken state are covered. A section request causes a new Hermes-side action, never Gemini summarization.
- Keep real-provider testing separate. Run a limited pinned Hermes contract smoke only after the Owner approves provider/model spend, runtime, and tool scope.

## Scope of this proposal

This file preserves the H1.2 feature proposal and unimplemented acceptance cases. It documents verified #87/#90 offline evidence without turning unapproved v2 storage/section-read rules into shipped capability. Editing this proposal does not itself change code, runtime defaults, infrastructure or authorization. #56 still owns remaining product/scope choices; #46 and #62 own separately approved live validation.
