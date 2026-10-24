# PR-C Design: Trusted Artifact Requirement for Result Contract v2

**Status:** Design-first proposal for Owner review. This PR changes documentation only; it does not change runtime behavior, execute a model, or authorize deployment.

**Base:** `main` after PR-A (`685db3bf5a48017b0a535354ec4efc64e89dc972`).

**Related:** Issue #105; PR-A #116.

## Decision to review

Result Contract v2 remains Amusant's only Hermes result contract. The artifact requirement is admission policy stored beside the v2 task, not a new result schema or a model-authored result field.

- New ordinary questions, comparisons, and quick research are admitted with `artifact_requirement=optional`. A bounded `speak` and `card` can complete without calling `save_report`.
- A clearly requested complete report, document, or long-form deliverable is admitted with `artifact_requirement=required` only after the Relay's server-side admission policy derives that expectation from the authenticated user's input turn or a direct owner confirmation.
- The requirement is immutable once admitted. Neither Hermes output nor `delegate_task` arguments can set it to optional, clear it, or replace its source.
- The Agent is instructed to call `save_report` and wait for its `committed=true` acknowledgement when required. That acknowledgement is not by itself the system's proof: Facade must independently find and verify its committed, bound Artifact before Relay can report successful delivery.
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
| Server-side phrase policy over the final inbound transcript for the authenticated voice turn, with clarification on ambiguity | Lets an owner say ordinary words such as “Please write a complete report” or “請幫我整理成一份完整報告.” Relay, not Hermes, applies a deterministic rule and can make `required` monotonic. It depends on provider transcription and cannot classify every paraphrase perfectly. | **Recommended for this focused follow-up**, with the limits and fail-closed clarification below. |

The strongest long-term source is a client-originated structured choice bound to the authenticated owner and task. The recommended near-term path uses the existing Live input-transcription callback and no new service. It must not claim that provider transcription is infallible or that all voice intent is machine-deterministic.

## Admission policy and normal voice UX

1. Keep a bounded, in-memory buffer of final `direction="input"` transcript segments for the active authenticated Live session and user turn. Bind each segment to the callback's current provider epoch and turn; ignore output captions, stale callbacks, non-final fragments, and transcripts from another turn. Do not persist raw transcript text.
2. Before durably admitting a `delegate_task` request, run a deterministic, versioned Relay policy over that user-turn transcript. Recognize explicit create/write/prepare requests for complete reports, documents, or long-form deliverables in supported languages. Ordinary questions, comparisons, and quick research stay optional. The request text in model-produced tool arguments may describe the work, but it is not itself the authority for this policy.
3. If the transcript contains a clear deliverable request, the Relay sets `required`; there is no model-supplied downgrade field. The task instruction may still be summarized by Hermes, but the policy is already fixed before the task enters the durable queue.
4. If wording is ambiguous (for example, “write this up,” “put the findings together,” or “summarize this”), ask in ordinary language: “Would you like a short answer, or a complete document saved for you?” A clear owner answer sets the policy before task admission. No internal commands such as `artifact_required=true` are exposed.
5. If the final inbound transcript is absent, incomplete, unsupported, or the clarification is not understood, do not silently admit the possible document request as optional. Ask the owner to repeat or clarify. If no reliable confirmation arrives, return a bounded “I couldn't confirm whether you wanted a saved document” response and do not claim a document task was admitted.
6. The policy is raise-only while resolving a turn: a clear required signal cannot be undone by later task text, tool arguments, retrieved content, or a final result. An explicit owner clarification may resolve an ambiguous pre-admission choice; it is not a model-controlled reset after admission.

The provider can emit a function call before the input transcription callback for the same Live message is processed. The runtime change must account for this event ordering: buffer the admission call until its user-turn transcript is resolved, or use an equivalent Relay-owned pre-admission gate. It must not infer that “no transcript yet” means optional.

### Ambiguity and guarantee boundary

The deterministic guarantee is conditional: when a supported explicit request appears in the final inbound transcript attached to the authenticated user turn, the Relay records `required` and downstream code cannot downgrade it. Natural voice works without technical phrasing for those recognized forms.

The current provider callback does not provide a verified verbatim transcript or a confidence score. Noise, code-switching, an unsupported language, an omitted/incorrect transcription, or an indirect request can defeat recognition. Ambiguous language such as “make this presentable” or “write it up” has no reliable universal interpretation. Those cases require clarification; if the transcript or confirmation remains unavailable, the system cannot guarantee complete-document preservation and must not present the ordinary optional path as though it had satisfied the request. A client-originated structured choice would improve provenance but is not available in the current voice UX.

## Durable policy record and v2 result behavior

Add an additive policy record to existing task and Facade request ledgers; do not add a field to the model-authored v2 result:

```text
artifact_requirement: optional | required
artifact_requirement_source: default_optional | explicit_input_rule | owner_clarification
artifact_requirement_policy_version: bounded integer
```

Relay derives the values during admission and includes them in the task's idempotency fingerprint. Store them with the existing task identity and lifecycle fields. Persist only the source and rule/version metadata needed to audit the decision, not the raw spoken transcript. Duplicate admission retries must carry the same policy; a mismatch is a conflict, not an update.

Relay passes the requirement through its existing authenticated server-to-server submit path. Facade pins it to the first `(owner, task, session, run)` mapping and rejects mismatched retries. Hermes receives the requirement as trusted task context/instructions, never as a caller-editable authorization flag. `schema_version` stays `2`; no v1 compatibility or v3 contract is introduced.

| Admission policy | Verified Facade state | Result |
| --- | --- | --- |
| `optional` | No writer attempt (`none`) | Return bounded v2 `speak` and `card`; no report is required. |
| `optional` | Valid committed Artifact | Preserve the Facade-owned reference and integrity metadata; deliver only the bounded presentation through Relay. |
| `required` | Valid committed Artifact and acknowledged write | Permit successful document-preservation delivery. A model claim alone is not enough. |
| `required` | Missing write, failed/conflicting write, invalid/over-cap write, missing or malformed acknowledgement, or failed integrity/binding check | Keep Hermes execution outcome distinct, mark result delivery failed with a fixed bounded message, and do not claim the document was saved. |

`save_report` must remain enabled in Hermes' effective plugin/toolset. For required tasks the task instructions direct Hermes to call it and wait for exactly the successful commit acknowledgement before composing any “saved” claim. Facade then independently verifies the committed file, content length, SHA-256 digest, reference, and owner/task/session/Run binding when projecting the result. A timeout or missing acknowledgement is not success. Do not accept an Artifact reference or requirement value from Hermes output.

The existing report-size cap remains 64 KiB. A requested document beyond that cap cannot be preserved under this design and must fail truthfully; it must not be clipped or routed through Live. This PR-C does not add the separate user-facing report retrieval channel (Follow-up C); it guarantees private Facade storage and truthful status, not a public URL or new download UX.

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
3. **Ambiguous voice request:** “write this up” and “summarize this” require a natural clarification. Clear owner confirmation sets required; a clear short-answer choice sets optional; no/invalid transcript does not silently fall back to optional.
4. **Trust boundary:** model tool arguments, a forged `artifact_required=false`, final JSON, retrieved instructions, and output transcripts cannot clear a server-required task. Unknown fields remain rejected/ignored per the v2 boundary.
5. **Live ordering and binding:** function call before transcript, partial/final transcript, missing transcript, stale provider epoch, overlapping turns, and duplicate calls bind policy only to the correct authenticated owner/session/turn.
6. **Durability and replay:** policy/source survive restart and both ledger adapters; same-call retries are idempotent; changed policy on replay conflicts; legacy/migrated rows default to optional without rewriting their v2 result contract.
7. **Writer acknowledgement:** valid `save_report` receives `committed=true`; absent call, timeout, rejected/over-cap/conflicting write, false/malformed acknowledgement, or no matching Facade commit cannot produce a saved-document claim.
8. **Integrity and identity:** altered file bytes/length/digest, forged reference, and wrong owner/task/session/Run are rejected. A committed Artifact remains bound and recoverable if later presentation delivery fails.
9. **Bounded delivery and lifecycle:** only bounded `speak`/`card` reach Relay/Live; raw report text never enters Relay or Gemini Live result delivery. Required-result failure, retry, cancellation, single-flight, and status lookup remain truthful and deterministic.

These are proposed offline tests only. No runtime tests, provider execution, OCI mutation, or deployment are performed by this design PR.

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

Roll out only after Owner review and existing #62 baseline coordination. Use an additive schema migration; migrated/existing tasks receive `optional` and are not reinterpreted. Release Relay, Facade, and the mounted Hermes plugin as one compatible v2 bundle. Initially keep ordinary admission optional and enable the required-intent policy only after offline tests demonstrate that the transcript gate fails closed on missing/ambiguous input. No live provider test is part of this PR.

Rollback stays on Result Contract v2. Stop creating new `required` admissions, then drain or explicitly reconcile every task whose durable requirement is `required`. Keep a v2-capable reader/enforcer active until none remain. Do not roll back to a runtime that ignores the policy field, clear a required flag, rewrite a failed result as successful, release an uncertain single-flight slot by time alone, or restore v1. If required tasks cannot be shown terminal and accounted for, keep the policy-capable release and ingress closed for affected admissions. Preserve existing SQLite and Facade artifact data; no deletion is part of rollback.

## Owner review gate

Please review the chosen voice signal, supported phrase/language boundaries, ambiguity behavior, and the `optional|required` durable policy before runtime implementation begins. This Draft PR is intentionally design-only. No production activation, live model execution, provider spend, or deployment is requested here.
