# Hermes H1.2 Result Delivery Contract — Proposal

**Status (2026-10-19):** Design discussion merged by [PR #71](https://github.com/smallgun01/amusant/pull/71), not an integrated or accepted runtime specification. [#75](https://github.com/smallgun01/amusant/pull/75) and [#80](https://github.com/smallgun01/amusant/pull/80) proved a pinned, offline fake-provider Option D prototype; [#87](https://github.com/smallgun01/amusant/pull/87) is still a **Draft B1** and [#85](https://github.com/smallgun01/amusant/issues/85) B2 remains open. [SPEC v0.5](../../SPEC.md) and [ADR-0003](../adr/0003-oci-a1-hermes-sqlite.md) define the current product/architecture boundary. This proposal does **not** itself approve artifact hosting/retention, model spend, OCI deployment or public ingress.

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

## Current-code gaps

| Area | Current behavior on main | Gap against this proposal |
|---|---|---|
| [relay/hermes_result.py](../relay/hermes_result.py) | HermesRunResult v1 has speak, card, and details_md; plain-text and selected malformed-schema fallbacks are supported; fields are clipped to existing limits. | No artifact reference, has_more, section metadata, or outcome axes. Fallback and clipping can turn incomplete output into apparent success; v2 must reject invalid/oversized metadata and never truncate a full report into speech. |
| [relay/slow_path.py](../relay/slow_path.py) | delegate_task accepts task plus optional task_kind. Completed check_task_status results include stored details_md regardless of the speak flag. | No typed target/source/output context. Default Gemini response leaks the long-result field into the tool payload. No separate execution/artifact/presentation outcomes or explicit section-read operation. |
| [relay/live_bridge.py](../relay/live_bridge.py) | Sends the router result to Gemini as a tool response (8 KiB hard ceiling). The optional spoken path already uses a tool-disabled session and injects the saved speak value. | The tool response currently includes details_md; the new path must whitelist envelope fields and use only validated speak for speech. |
| [relay/task_store.py](../relay/task_store.py) and [relay/dynamodb_task_store.py](../relay/dynamodb_task_store.py) | SQLite and DynamoDB persist bounded speak, card, and details_md; lookup is owner-scoped and marks results read, while spoken state requires a separate confirmed audio path. | No envelope version, section metadata, outcome axes, or artifact pointer. Preserve owner-scoped lookup and truthful delivery state while storing only a small envelope/pointer, never the report itself. |
| [SPEC.md](../SPEC.md), [ADR-0002](adr/0002-external-task-store.md) | Normative text still describes bounded details_md in TaskStore; ADR-0002 rejects expanding the TaskStore into artifacts. | The new direction needs a reviewed delta and a separate artifact-storage decision. Do not silently change these accepted documents or expand the DynamoDB table into an artifact repository. |

The [#58/#61 implementation](https://github.com/smallgun01/amusant/issues/58) is now merged: preserve its durable polling, one-outstanding-task, immediate busy rejection, stop-confirmed cancellation, and restart behavior. Run deadline and HTTP/poll timing remain explicit configuration choices, not approved product defaults. This proposal adds no queue policy or new Worker/ECS/SQS service.

## Owner decisions before implementation

1. Select the durable artifact mechanism and its hosting boundary. Define encryption, allowed content types/maximum size, owner/task authentication, retrieval route, deletion, retention, and behavior when an artifact expires or is unavailable. Keep the existing TaskStore a ledger and pointer store.
2. Approve the input and result schema versions, required fields, outcome names, strict caps, section metadata shape, and migration/backward-compatibility behavior. The existing v1 limits are implementation behavior, not approved v2 limits.
3. Set a language-aware speak cap that targets roughly 15–25 seconds without silent truncation. Confirm whether cards and section titles use character or byte limits.
4. Confirm the bounded retry budget/deadlines and whether a user retry after presentation failure is a new task linked to the existing artifact.
5. Approve the pinned real-Hermes smoke model/provider, run count, spend cap, runtime, and tool/egress scope before any live call.

## Offline acceptance plan

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

Review-only. It changes no application code, storage, SPEC/ADR, runtime defaults, infrastructure, or production environment. Once the Owner approves the schema and storage boundaries, implementation can be split into result-contract, artifact-adapter, and section-read work while retaining the existing Relay TaskStore, Inbox, and #58 single-flight behavior.
