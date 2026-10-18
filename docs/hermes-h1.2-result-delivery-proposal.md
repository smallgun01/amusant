# Hermes H1.2 Result Delivery Contract — Proposal

**Status:** Review-only proposal for [Issue #56](https://github.com/smallgun01/amusant/issues/56), based on the 2026-10-18 Owner direction. This document does not approve a storage provider, change the normative SPEC/ADR, or authorize implementation, deployment, paid model calls, or new infrastructure.

## Decision boundary

Hermes owns long-form reasoning, tools, report creation, summarization, and content transformation. Gemini Live handles conversational FAST turns and reads only a short, already prepared answer. Relay stays deterministic: authenticate, admit, route, validate bounds and ownership, persist task state and a pointer, and report delivery truth.

No complete report, source bundle, code block, or long plain-text fallback may enter a Relay or Gemini Live tool payload. Relay and Gemini Live do not summarize artifacts. Invalid or missing speech text fails closed with a truthful non-spoken status; show artifact availability only when a trusted reference exists.

## Proposed result flow

1. Relay admits a bounded explicit request and binds it to authenticated owner and task IDs. It does not append the Live transcript or guess what “that PR” means.
2. Hermes performs the approved task and writes the complete report to a durable artifact mechanism selected after Owner review. Hermes Run history alone is not the artifact-retention contract.
3. A trusted server-side artifact adapter validates the write and issues an opaque `artifact_ref` bound to the owner and task. Model-produced paths, URLs, object keys, or references are never accepted as trusted references.
4. Hermes returns a small versioned result. The trusted adapter adds the server-issued reference, if present. Relay validates the envelope and persists only the bounded envelope, task status, and opaque pointer in the existing Task Ledger/Inbox.
5. `check_task_status` returns a field allowlist: bounded status, `speak`, `card`, safe artifact availability, `has_more`, and bounded section metadata. It does not return `details_md` or artifact content to Gemini by default.
6. For a completed task, Relay may open a tool-disabled Live speech turn and supply only the validated `speak` text with a direct-read instruction. Gemini does not receive the report and is not asked to summarize or rewrite it.

Candidate envelope shape (field caps and exact schema remain Owner decisions):

```json
{
  "schema_version": 2,
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
```

`artifact_ref` is minted by trusted server code, never trusted from Hermes output, and is not a bearer credential. Every retrieval must re-authenticate the owner and verify the owner/task binding. The reference resolves through a server-side adapter; no storage URL or path is exposed to Gemini or accepted as authority.

## Input and section-read contracts

Replace the free-form-only admission shape with a bounded, explicit context envelope. Candidate fields:

- `schema_version`
- `objective`: non-empty bounded task description
- `target`: optional typed target such as a GitHub issue/PR with owner, repository, number, and optional immutable revision
- `source_refs`: bounded typed references, validated against server policy
- `expected_output`: requested format and language
- `requested_scope`: an intent hint such as read-only research, not a permission grant

Relay validates structure, size, identity, target resolution, and allowed source types before admission. Missing or ambiguous references require clarification or a safe rejection. The envelope must not contain arbitrary tools, commands, credentials, storage paths, or backend URLs. The server selects the actual read-only tool allowlist; text in the request cannot widen it. Do not forward the full Live conversation as implicit context.

Reading a report section is a separate, explicit user-requested Hermes retrieval/summarization action. The caller supplies the owner-scoped task and a section ID from validated metadata; Relay resolves the trusted artifact reference and grants Hermes only the narrow section-read operation. It follows the existing one-outstanding-Slow-Task / immediate `slow_busy` contract from [Issue #61](https://github.com/smallgun01/amusant/issues/61). Gemini never receives the full artifact to summarize. Exact UX, result linkage, and whether section reads are represented as child tasks remain open.

## Current-code gaps

| Area | Current behavior on `main` | Gap against this proposal |
|---|---|---|
| [`relay/hermes_result.py`](../relay/hermes_result.py) | HermesRunResult v1 has `speak`, `card`, and `details_md`; plain-text and selected malformed-schema fallbacks are supported; fields are clipped to existing limits. | No artifact reference, `has_more`, or section metadata. Fallback and clipping can turn incomplete output into apparent success; v2 should reject invalid/oversized metadata and never truncate a full report into speech. |
| [`relay/slow_path.py`](../relay/slow_path.py) | `delegate_task` accepts `task` plus optional `task_kind`. Completed `check_task_status` results include stored `details_md` regardless of the `speak` flag. | No typed target/source/output context. Default Gemini response leaks the long-result field into the tool payload. No explicit section-read operation. |
| [`relay/live_bridge.py`](../relay/live_bridge.py) | Sends the router result to Gemini as a tool response (8 KiB hard ceiling). The optional spoken path already uses a tool-disabled session and injects the saved `speak` value. | The tool response currently includes `details_md`; the new path must whitelist envelope fields and use only validated `speak` for speech. |
| [`relay/task_store.py`](../relay/task_store.py) and [`relay/dynamodb_task_store.py`](../relay/dynamodb_task_store.py) | SQLite and DynamoDB persist bounded `speak`, `card`, and `details_md`; lookup is owner-scoped and marks results read, while spoken state requires a separate confirmed audio path. | No envelope version, section metadata, or artifact pointer. Preserve owner-scoped lookup and truthful delivery state while storing only a small envelope/pointer, never the report itself. |
| [`SPEC.md`](../SPEC.md), [ADR-0002](adr/0002-external-task-store.md) | Normative text still describes bounded `details_md` in TaskStore; ADR-0002 rejects expanding the TaskStore into artifacts. | The new direction needs a reviewed delta and a separate artifact-storage decision. Do not silently change these accepted documents or expand the DynamoDB table into an artifact repository. |

The [#58/#61 implementation](https://github.com/smallgun01/amusant/issues/58) is now merged: preserve its durable polling, one-outstanding-task, immediate busy rejection, stop-confirmed cancellation, and restart behavior. Run deadline and HTTP/poll timing remain explicit configuration choices, not approved product defaults. This proposal adds no queue policy or new Worker/ECS/SQS service.

## Owner decisions before implementation

1. Select the durable artifact mechanism and its hosting boundary. Define encryption, allowed content types/maximum size, owner/task authentication, retrieval route, deletion, retention, and behavior when an artifact expires or is unavailable. Keep the existing TaskStore a ledger and pointer store.
2. Approve the input and result schema versions, required fields, strict caps, section metadata shape, and migration/backward-compatibility behavior. The existing v1 limits are implementation behavior, not approved v2 limits.
3. Set a language-aware `speak` cap that targets roughly 15–25 seconds without silent truncation. Confirm whether cards and section titles use character or byte limits.
4. Decide section-read UX and task linkage. It must be explicit, owner-authorized, read-only, single-flight, cancellable, and auditable.
5. Approve the pinned real-Hermes smoke model/provider, run count, spend cap, runtime, and tool/egress scope before any live call.

## Offline acceptance plan

- Input: resolved earlier-PR context succeeds; ambiguous “that PR”, wrong owner, unsupported source, oversized envelope, and unauthorized scope fail safely without using implicit transcript context.
- Result parsing: valid versioned envelope passes; malformed JSON, unsupported version, missing/invalid `speak`, oversized fields, fences, and 10k/30k plain-text outputs fail closed. No long content or silent truncation reaches Relay/Gemini.
- Tool boundary: default status exposes only the field allowlist; verify actual serialized Live payloads contain no `details_md`, report, code, URL/path, or raw storage locator.
- Artifact lifecycle: durable write/read, owner/task isolation, invalid/expired/unavailable reference, Relay and Hermes restart, retention, deletion, and remote Run expiry do not rely on Run history.
- Voice: `speak` is directly readable in the requested language and targets 15–25 seconds; card/status-only cases, interruption, and audio-confirmed `spoken` state are covered. A section request causes a new Hermes-side action, never Gemini summarization.
- Keep real-provider testing separate. Run a limited pinned Hermes contract smoke only after the Owner approves provider/model spend, runtime, and tool scope.

## Scope of this proposal

Review-only. It changes no application code, storage, SPEC/ADR, runtime defaults, infrastructure, or production environment. Once the Owner approves the schema and storage boundaries, implementation can be split into result-contract, artifact-adapter, and section-read work while retaining the existing Relay TaskStore, Inbox, and #58 single-flight behavior.
