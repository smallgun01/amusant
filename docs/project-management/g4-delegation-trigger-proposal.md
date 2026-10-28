# G4: explicit delegation signal proposal

Status: implementation and validation plan for [#145](https://github.com/smallgun01/amusant/issues/145); physical E2E remains unverified. Source baseline: main `e178725fa17f4ea011baa7f6bc81b8fa0a55cc4d`. Actual deployed image, model, SDK and settings must be verified separately.

## Problem and evidence

During physical PTT the Owner requested delegated work, but Live answered or looked up Relay's ledger without admitting a new SLOW task. A separate direct router test admitted work and a Grok Routine returned a durable Callback. This isolates an observed gap at the Live decision/tool-selection boundary; it does not establish why the model chose its response, or prove that Search caused the failure. End-to-end Live-originated admission remains unverified. See [#145](https://github.com/smallgun01/amusant/issues/145) and [G4 physical evidence](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361).

Before this change, Jellii instructions said to answer quick conversation directly, use Search for a single current fact when available, delegate background or agent-managed work, and check status when asked about earlier background tasks. Those broad instructions do not explicitly prioritize a user's direct request to hand work to Grok or distinguish an earlier task's status from a request to modify its output. This ambiguity is a hypothesis, not an established root cause. A longer list of task-specific prompt rules would remain probabilistic and grow without bound.

The bridge allows the first supported Relay action per turn; a later action is rejected before the slow router. In particular, a status lookup followed by delegation in the same turn cannot be assumed to work. The router's durable admission, same-call deduplication, single-flight and unknown-outcome handling remain authoritative. Search is Gemini-managed and may not expose a corresponding Relay function event.

## Proposed user interaction

Use a short explicit phrase, initially **「交給 Grok」**, as a positive signal that the Owner wants a new background task. This phrase is included in the proposed persona as an example, not a deterministic keyword or an exclusive list of supported wording.

- 「交給 Grok，整理這個 issue 並做成報告」: intend one `delegate_task` admission.
- 「Grok 上一個任務好了嗎？」: intend `check_task_status`, with no new admission.
- 「交給 Grok，把之前的法國行程改成五天」: intend a new delegated task, not an Inbox lookup.
- 「現在幾點？」: FAST answer, no SLOW task.

This PR adds a minimal instruction and tool-description signal for explicit handoff, without enumerating every task domain. This remains a model instruction, not a deterministic switch or a guarantee. Ordinary requests without the phrase remain usable; measure implicit requests separately rather than declaring them unsupported. If the request is unclear or missing its task, ask briefly instead of inventing one. A task's wording does not itself grant additional permissions.

Only claim that Grok was given the task after durable admission is confirmed. Busy means not accepted or queued; unknown admission means acceptance is unconfirmed and must not be automatically retried. An accepted task is not a completed task. A status lookup reads Relay's ledger/Inbox; it is not a Grok native cross-run search. A request to revise an earlier artifact is a new task, but native artifact recovery is independently unverified: do not invent the previous file, its contents or a v2 result.

## Alternatives and decision gates

1. **Prompt signal (first experiment):** clarify the meaning of the explicit handoff in the existing persona and function description. Small change, works with the existing Live workflow, but model selection remains probabilistic. A prompt-only “skill” has the same limitation.
2. **Program-controlled signal (conditional follow-up):** consider a dedicated user interaction or reliable locally recognized phrase that selects delegation in code. Do not implement or claim it is possible from this proposal. First establish when and whether complete input transcription is available, its quality and ordering relative to Live output/tool calls, how the exact task is extracted, and how interruption, empty input, false positives, quotes, duplicate requests and uncertain admission are handled. A deterministic action after recognition does not make speech recognition deterministic. Any new path must preserve authorization, single action, single-flight and durable deduplication.
3. **Forced function mode:** do not rely on Generate Content's function-calling modes as proof of Live support. Verify the exact deployed Live model, API and SDK version and their supported Live configuration before considering it. General [Live tool documentation](https://ai.google.dev/gemini-api/docs/live-api/tools) establishes function calling but does not by itself establish a forced-tool control for this deployment.

No permanent Search disable is proposed. Search-on versus Search-off is a controlled comparison, not proof of causation from one trial; configuration changes and real provider use require their respective Owner authorizations.

## Evidence and acceptance plan

This PR implements the minimal persona and tool-description wording and declaration tests. It does not implement the telemetry below; that remains necessary follow-up work before the complete #145 evidence plan can be claimed. Deployment, configuration changes and live provider calls require their separate existing Owner authorizations.

Proposed follow-up telemetry: record per PTT only bounded, allowlisted metadata: declared tool categories and Search enabled/disabled; first observed Relay function name or none; accepted, busy, rejected, unavailable or unknown outcome; blocked subsequent action count; and elapsed time from PTT end to first observed call or response. Distinguish no observed Relay function from Search not used: Search activity is unknown unless actually observable. Avoid raw audio, transcripts, task text, function arguments, search queries, identifiers that expose content, and credentials. Verify logging and exception paths also respect this boundary.

Offline tests should cover the configured declarations, safe telemetry with zero calls, accepted/busy/rejected/unknown outcomes, invalid or duplicate calls, multiple calls in a turn, and no second router action. They cannot establish the model's selection accuracy.

For an Owner-approved physical evaluation, use natural zh-TW phrases and vary task content and wording without recording it in telemetry. Test FAST, explicit handoff, status, and revision of an earlier artifact; include empty/unclear and busy/unknown cases where feasible. Specify the number of trials and approved provider budget in advance, keep conditions comparable, and report successes and failures with denominators and latencies. A Search-off session is temporary and restored after the experiment. Distinguish synthetic offline cases from paid, physical Grok admissions.

For an explicit handoff with a valid task and available SLOW slot, the target is exactly one new durable `delegate_task` admission per PTT, no additional action after the gate, and truthful acknowledgement. FAST requests must remain responsive and add no SLOW row; status must not create a task. Track false positives, misses, duplicate admissions, and whether speech-recognition or tool selection was observable. If acceptance is unknown, inspect durable state rather than re-submit. Do not infer Grok native Run state or cross-run artifact recovery from admission or a Callback. Reuse the existing Routine → Callback → Inbox → later requested spoken readback; the separate spoken delivery issue remains independent.

Owner review determines the required reliability and acceptable latency from the measured results. If the prompt signal is inadequate, return with a concrete program-controlled design and its evidence; do not keep adding task-specific rules by default.
