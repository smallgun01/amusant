# PR-C Design: Trusted Artifact Requirement for Result Contract v2

**Status:** Design-first proposal for Owner review. This PR changes documentation only; it does not change runtime behavior, execute a model, or authorize deployment.

**Base:** `main` after PR-A (`685db3bf5a48017b0a535354ec4efc64e89dc972`).

**Related:** Issue #105; PR-A #116.

## Decision to review

Result Contract v2 remains Amusant's only Hermes result contract. The artifact requirement is admission policy stored beside the v2 task, not a new result schema or a model-authored result field.

- New ordinary questions, comparisons, and quick research are admitted with `artifact_requirement=optional`. A bounded `speak` and `card` can complete without calling `save_report`.
- A clearly requested complete report, document, or long-form deliverable is admitted with `artifact_requirement=required` only after the Relay's server-side admission policy derives that expectation from the authenticated user's input turn.
- The requirement is immutable once admitted. Neither Hermes output nor `delegate_task` arguments can set it to optional, clear it, or replace its source.
- The Agent is instructed to call `save_report` and wait for its `committed=true` acknowledgement when required. The authoritative system evidence is Facade's independently verified, bound commit record; Relay can report private preservation only after finding it.
- A completed Hermes Run without a verified Artifact for a required task is `execution_status=completed` with result delivery failed. Amusant returns a bounded recovery result and does not say the document was saved.

This proposal preserves the existing v2 `speak`/`card` envelope, Facade-owned file and reference, length and SHA-256 verification, owner/task/session/Run binding, bounded Relay delivery, per-owner single-flight, and cancellation behavior. It adds no public report URL, database service, worker, or general Hermes capability restriction.

## Current evidence and gap

PR-A removed the v1 Artifact-mandatory runtime and made v2 the sole contract. The current v2 prompt asks Hermes to use `save_report` for a genuine document request, but a prompt cannot guarantee that the tool will be called. The existing plugin returns `{"committed":true}` only after Facade accepted the write; Facade owns the stored bytes and reference and records integrity and request binding.

The voice path has an admission source worth evaluating: Gemini Live is configured for `input_audio_transcription`, and its adapter emits final `TranscriptEvent`s with `direction="input"`. The current Relay bridge intentionally forwards only output transcripts to captions; `delegate_task` accepts only `task` and `task_kind`. Consequently, the current task ledger has no independent admission expectation. The input transcript is a provider transcription of user audio, not a cryptographically exact transcript; the design below limits the guarantee accordingly.

## Feasible signaling approaches

| Approach | Trust and voice UX | Decision |
| --- | --- | --- |
| Prompt Hermes to recognize document requests and call `save_report` | Natural conversation, but model compliance is probabilistic. It cannot enforce a server requirement or prevent omission. | Keep as guidance only; insufficient as policy. |
| Add `artifact_required`/`result_type` to `delegate_task` arguments or the result JSON | The same model that can omit the report controls the flag. It can misclassify or set `false`; a schema validator cannot make that source trusted. | Reject as the authority. Unknown/model-authored fields must not change policy. |
| Owner-controlled structured selection at task creation | Strongest explicit signal, with clear provenance and replay handling. A dedicated button or client mode does not currently exist for the StickS3 voice path and would add UX work. | Keep as a future or complementary high-assurance path; do not make users speak internal field names. |
| Server-side phrase policy over the final inbound transcript for the authenticated voice turn, with clarification on ambiguity | Lets an owner say ordinary words such as “Please write a complete report” or “請幫我整理成一份完整報告.” Relay, not Hermes, applies a deterministic rule and can make `required` monotonic. It depends on provider transcription and cannot classify every paraphrase perfectly. | **Recommended for this focused follow-up**, with the non-blocking gate and explicit availability trade-off below. |

The strongest long-term source is a client-originated structured choice bound to the authenticated owner and task. The recommended near-term path uses the existing Live input-transcription callback and no new service. It must not claim that provider transcription is infallible or that all voice intent is machine-deterministic.

## Admission gate and normal voice UX

### Non-blocking transcript resolution

The provider receive loop awaits each event callback. Therefore, `on_tool_call` must not await a transcript future: that would prevent the same receive loop from processing the transcript event that resolves it. The runtime proposal is:

1. On a `delegate_task` call, synchronously validate and reserve the one action for that turn, then enqueue a bounded `PendingAdmission` keyed by `(provider_epoch, turn_id, call_id)`. Return from the provider callback immediately. A Relay-owned background task waits for resolution; the provider receive loop remains free to process more messages.
2. The active epoch/turn's input-transcript callback appends bounded final `direction="input"` text to its buffer and signals the resolver. A same-message transcript and a later-message transcript use the same path. Non-final fragments may be buffered, but only a final event resolves the gate. Output transcripts, stale epochs, other turns, and late events after timeout are ignored.
3. Use a 2-second transcript deadline from tool-call receipt. The background resolver sends at most one `FunctionResponse`, using the original call ID and function name. While it is pending, model audio/captions for the admission turn stay suppressed. A completed admission goes through the existing durable store and its existing acceptance timeout/idempotency behavior.
4. A PTT supersede, provider-epoch retirement, or socket disconnect cancels the unresolved transcript waiter. If invalidation happens before durable admission starts, discard it without creating a task; there is no task ID for `cancel_task` to cancel and no Hermes Run to stop. If the existing durable `admit` operation has already started, its result may be ambiguous; preserve existing same-call deduplication/single-flight recovery, report unknown if the response cannot be delivered, and never send a late response into a newer provider epoch.
5. After timeout, send one correlated `FunctionResponse` with a bounded rejection code and create no task. A transcript arriving after the deadline cannot reopen that call. A superseded/closed provider cannot receive a response; its callback is canceled and its late transcript is discarded. No task has been admitted unless the existing durable store confirms or later reconciles it.

The pending call is callback-local and short-lived; it is not a durable task or an indefinitely awaited future. An unanswered/ambiguous request is never represented as accepted.

### Exact outcomes and clarification UX

Use a versioned deterministic Relay phrase policy over the final input transcript. The initial supported set for this proposal is English and Taiwan Traditional Chinese. The policy matches explicit create/write/prepare intent plus a complete-report/document/long-form deliverable; mention of the word “report” alone is not enough. The model-produced `task`/`task_kind` values are not authority for setting the output policy.

| Input observed by admission gate | Admission result | User-facing behavior |
| --- | --- | --- |
| Clear explicit document/report request in a supported final transcript | `required` | Admit normally; no technical command is exposed. |
| Clear ordinary question, comparison, or quick research request in a supported final transcript | `optional` | Admit normally; no report is required. |
| Ambiguous supported request such as “write this up,” “put the findings together,” or “summarize this” | No task admitted | Return one correlated `FunctionResponse` with `status=rejected`, `code=clarification_required`; because the current bridge suppresses model speech after rejection, Relay shows a bounded card asking the owner to repeat the full request and say whether they want a complete document or a short answer. |
| No final transcript within 2 seconds, only non-final text, or incomplete transcript | No task admitted | Return one `input_transcript_unavailable` rejection and a bounded card asking the owner to repeat the request. |
| Final transcript in an unsupported language | No task admitted | Return one `unsupported_intent_language` rejection and a bounded card asking for English or Taiwan Traditional Chinese. |
| Transcript arrives after timeout, supersede, epoch retirement, or disconnect | No new admission | Ignore the late event; it cannot complete or modify the old call. |

This deliberately chooses availability loss over silently downgrading an unclassified voice request. Because every current `delegate_task` call comes from the same voice stream, missing or unsupported transcription can block an ordinary research/comparison task too. It does not create an Artifact requirement for ordinary tasks; it rejects admission until the owner repeats in a supported form. The limitation and the 2-second bound must be explicit in the product/design review. A different fail-open choice (admit as optional when transcript is unavailable) would preserve availability but could silently miss an explicit document request, so it does not meet this proposal's preservation goal.

### Two-PTT behavior and guarantee boundary

Choose the bounded rejection path, not a pending-intent continuation. For an ambiguous request, the first `delegate_task` call receives exactly one rejection, the original model-authored task text and call ID are discarded after the turn, and Relay shows the clarification card. There is no pending task object, no durable task ID, and no task-store/single-flight reservation. The pending call expires with the two-second transcript gate; `cancel_task` cannot target it because no task ID exists. A PTT supersede cancels the pre-admission resolver. If the store admission has already started, retain the existing unknown/duplicate and single-flight behavior instead of assuming cancellation succeeded. The next PTT starts a new provider epoch and must contain a new, self-contained request; “yes” alone does not resume or admit the old request. A complete rephrasing such as “Please write a full report about X” creates a new call ID and is classified independently. Disconnect, timeout, provider restart, or process restart leaves no task to recover. This avoids carrying model-authored task text or an old function call across voice turns.

At admission, the policy can guarantee that a recognized explicit supported phrase is recorded `required` and cannot be downgraded. It cannot guarantee that Gemini transcription is verbatim or that every natural-language paraphrase is recognized; the callback carries no transcription confidence. Noise, code-switching, omitted/misrecognized words, indirect language, or unsupported languages can produce a false negative or a bounded rejection. A stronger exact-utterance guarantee needs a higher-provenance owner-controlled signal. Before rollout, a separately approved real-provider smoke must verify actual input-transcript ordering and finality. That smoke is not authorized or performed by this design PR.

## Durable policy record and v2 result behavior

Add an additive policy record to existing task and Facade request ledgers; do not add a field to the model-authored v2 result:

```text
artifact_requirement: optional | required
artifact_requirement_source: default_optional | explicit_input_rule
artifact_requirement_policy_version: bounded integer
```

Relay derives the values during admission and includes them in the task's idempotency fingerprint. Store them with the existing task identity and lifecycle fields in both SQLite and DynamoDB TaskStore adapters. Persist only the source and rule/version metadata needed to audit the decision, not the raw spoken transcript. Duplicate admission retries must carry the same policy; a mismatch is a conflict, not an update.

Relay passes the requirement through its existing authenticated server-to-server submit path. Facade pins the policy, source, and policy version in the request ledger **before** it submits to Hermes and before a Hermes Run ID is known. It rejects mismatched first-submit retries and later `(owner, task, session, run)` bindings; an upstream timeout does not make the policy mutable. Hermes receives the requirement as trusted task context/instructions, never as a caller-editable authorization flag. `schema_version` stays `2`; no v1 compatibility or v3 contract is introduced.

For historical Hermes rows, backfill `default_optional` only when they are eligible and already pinned to Result Contract v2. Historical Hermes v1 rows keep their v1 version and historical metadata; they are not dispatched by the v2 runtime and are never reclassified as v2 optional. ACP rows retain their independent execution and contract handling. New Hermes v2 admissions alone use the new policy source/version.

| Admission policy | Verified Facade state | Result |
| --- | --- | --- |
| `optional` | No writer attempt (`none`) | Return bounded v2 `speak` and `card`; no report is required. |
| `optional` | Valid committed Artifact | Preserve the Facade-owned reference and integrity metadata; deliver only the bounded presentation through Relay. |
| `required` | Valid committed Artifact in Facade's ledger | Permit successful private-preservation status. The durable Facade commit record is the authoritative system evidence; a model claim alone is not enough. |
| `required` | No verified commit at projection time; write rejected/conflicting/over-cap; or integrity/binding check fails | Keep Hermes execution outcome distinct, mark result delivery failed with a fixed bounded message, and do not claim the document was saved. A lost/malformed tool response is not itself failure if Facade can recover a valid committed record; without that record it fails closed. |

`save_report` must remain enabled in Hermes' effective plugin/toolset. For required tasks the task instructions direct Hermes to call it and wait for exactly `{"committed":true}` before composing any “saved” claim. This is model guidance; the server cannot prove Hermes received the response because it may be lost after a durable write. The enforceable predicate is the verified Facade commit record: at projection time Facade verifies its committed ledger entry, file bytes, content length, SHA-256 digest, Facade-owned reference, and owner/task/session/Run binding. That record is the system's verified commit acknowledgement; it does not assert that Hermes received the tool response. A valid commit recovered after a lost response passes this predicate; GET/reprojection or same-content retry preserves the existing reference. If the durable commit is absent, a timeout or model-authored acknowledgement is not success. Do not accept an Artifact reference or requirement value from Hermes output.

The existing report-size cap remains 64 KiB. A requested document beyond that cap cannot be preserved under this design and must fail truthfully; it must not be clipped or routed through Live. “Committed in the private Facade store” does not mean delivered to, readable by, or downloadable by the owner. This PR-C does not add the separate user-facing report retrieval channel (Issue #105 Follow-up C); it guarantees private storage status and truthful bounded result delivery, not a public URL or complete-document UX.

## Proposed implementation scope after Owner approval

The follow-up implementation should be limited to:

- Relay Live input-transcript buffering, event ordering, deterministic phrase policy, and pre-admission clarification state.
- Additive Relay task and Facade request metadata, idempotency/replay rules, and authenticated Relay-to-Facade propagation.
- v2-only Hermes instructions and plugin availability checks for `save_report` on required tasks.
- Facade enforcement of the immutable requirement, verified-commit gate, and bounded failure projection; Relay delivery failure mapping.
- Offline fake-provider and integrated regressions. Keep the existing Facade writer boundary and artifact location.

Do not change Hermes core, add a database/worker/service, broaden tool permissions, add Telegram/Discord or a public URL, redesign general task admission, change single-flight/cancellation, run a live provider, or deploy. The implementation remains a focused Result Contract v2 follow-up.

## Acceptance tests to add with implementation

1. **Ordinary request:** factual question, comparison, and quick research produce `optional`; a valid bounded result completes with no `save_report` call.
2. **Natural voice request:** final inbound fixtures for explicit English and Taiwan Traditional Chinese report/document phrases produce `required` before durable admission without exposing technical field names.
3. **Transcript callback liveness:** tool call then transcript in the same provider message, transcript in a later message, non-final-only input, no input within 2 seconds, late-after-timeout input, stale provider epoch, cancel/PTT supersede before admission, and cancellation while the durable store commit is ambiguous all complete without blocking the receive loop. Each call whose provider epoch remains active through resolution gets exactly one `FunctionResponse` with its original call ID/name; a canceled/retired call gets no late response in a new epoch. An already-started admission with ambiguous outcome retains existing deduplication and single-flight semantics.
4. **Two-PTT clarification:** an ambiguous first utterance receives one `clarification_required` rejection and a bounded Relay card; no task ID or pending task exists. The next PTT creates a new provider epoch and must repeat a complete self-contained task. A clear restatement creates one new call/task; “yes” alone, disconnect, restart, or expiry creates none.
5. **Exact classification / availability:** explicit document, ordinary, ambiguous, missing/non-final, and unsupported-language fixtures follow the outcome table. Missing transcript and unsupported-language cases reject ordinary research too; the system never waits indefinitely or silently defaults those cases to optional.
6. **Trust boundary:** model tool arguments, a forged `artifact_required=false`, final JSON, retrieved instructions, and output transcripts cannot clear a server-required task. Unknown fields remain rejected/ignored per the v2 boundary.
7. **Durability and replay:** policy/source/version survive restart and both Relay store adapters; Facade pins them before the Hermes Run ID exists; same-call retries are idempotent; changed policy on replay conflicts. Only eligible v2 rows may backfill to optional; historical v1 rows remain pinned and ineligible for v2 dispatch.
8. **Commit acknowledgement and recovery:** Hermes receives `committed=true` on a normal successful write. If bytes commit but the response is lost, Facade GET/reprojection and a same-content retry recover the same valid reference without a duplicate Artifact. Model acknowledgement without a matching commit never passes; a recovered verified commit passes even if Hermes did not receive the response.
9. **Integrity and identity:** altered file bytes/length/digest, forged reference, and wrong owner/task/session/Run are rejected. A committed Artifact remains bound and recoverable if later presentation delivery fails.
10. **Bounded delivery and lifecycle:** only bounded `speak`/`card` reach Relay/Live; raw report text never enters Relay or Gemini Live result delivery. Private storage is not described as owner retrieval/download. Required-result failure, retry, cancellation, single-flight, and status lookup remain truthful and deterministic.

These are proposed offline tests only. A separately gated real-provider smoke must later confirm actual input-transcript ordering/finality before rollout; no paid Live smoke, runtime tests, OCI mutation, or deployment is performed by this design PR.

## Threat analysis

| Threat | Control and residual risk |
| --- | --- |
| Hermes omits `save_report`, says it saved a document, or invents `artifact_required=false` | Requirement is a server-owned immutable ledger value. Facade checks its own verified commit state; result text cannot waive it. Missing commit becomes delivery failure. |
| Prompt injection in task/research content asks Hermes or Relay to downgrade policy | Treat retrieved/tool text as untrusted. Admission policy comes only from the authenticated user turn; commit gate reads persisted policy. |
| Forged/replayed task or writer request borrows another Artifact | Preserve the authenticated Facade writer session and exact owner/task/session/Run binding; immutable first-submit mapping and same-policy idempotency checks. |
| ASR drops or alters a document request | Residual limitation: provider transcription is not a verbatim guarantee. Require clarification for missing, incomplete, ambiguous, or unsupported input; do not claim a complete-document guarantee outside recognized/confirmed input. A higher-assurance client-originated choice is a future option. |
| Phrase policy treats an ordinary mention of a report as a deliverable | Match explicit create/write/prepare intent plus deliverable type, not noun occurrence alone; keep ordinary requests optional and exercise false-positive fixtures. Some false positives may still cause unnecessary report work. |
| A committed report is incomplete or semantically wrong | SHA-256 and byte length prove stored-byte integrity, not semantic completeness or factual correctness. `committed=true` means those bytes were committed, not that the report is correct. The bounded presentation must not overclaim semantic verification. |
| A requested report exceeds the existing storage cap or the writer is unavailable | Fail delivery truthfully; do not clip, summarize into Live, claim preservation, or drop the requirement. |

The trust boundary excludes a compromised Relay/Facade host or an inaccurate/compromised transcription provider. A stronger exact-utterance guarantee requires an owner-controlled input signal with stronger provenance; the current StickS3/Gemini path does not provide one.

## Rollout and rollback

Roll out only after Owner review and existing #62 baseline coordination. Use an additive schema migration; only eligible Hermes rows already pinned to v2 may backfill to `optional`. Historical Hermes v1 rows retain their version/metadata and remain ineligible for v2 dispatch; ACP rows keep their independent handling. Release Relay, Facade, and the mounted Hermes plugin as one compatible v2 bundle. Recognized ordinary requests remain Artifact-optional; the transcript gate's bounded rejection of missing or unsupported input is the documented availability trade-off. No live provider test is part of this PR.

Rollback stays on Result Contract v2. Stop creating new `required` admissions, then drain or explicitly reconcile every task whose durable requirement is `required`. Keep a v2-capable reader/enforcer active until none remain. Do not roll back to a runtime that ignores the policy field, clear a required flag, rewrite a failed result as successful, release an uncertain single-flight slot by time alone, or restore v1. If required tasks cannot be shown terminal and accounted for, keep the policy-capable release and ingress closed for affected admissions. Preserve existing SQLite and Facade artifact data; no deletion is part of rollback.

## Owner review gate

Please review the chosen voice signal, supported phrase/language boundaries, ambiguity behavior, and the `optional|required` durable policy before runtime implementation begins. This Draft PR is intentionally design-only. No production activation, live model execution, provider spend, or deployment is requested here.
