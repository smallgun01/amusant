# G4: explicit delegation signal proposal

Status: implementation and validation plan for [#145](https://github.com/smallgun01/amusant/issues/145); physical E2E remains unverified. Source baseline: main `e178725fa17f4ea011baa7f6bc81b8fa0a55cc4d`. Actual deployed image, model, SDK and settings must be verified separately.

## Problem and evidence

During physical PTT the Owner requested delegated work, but Live answered or looked up Relay's ledger without admitting a new SLOW task. A separate direct router test admitted work and a Grok Routine returned a durable Callback. This isolates an observed gap at the Live decision/tool-selection boundary; it does not establish why the model chose its response, or prove that Search caused the failure. End-to-end Live-originated admission remains unverified. See [#145](https://github.com/smallgun01/amusant/issues/145) and [G4 physical evidence](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361).

Before this change, Jellii instructions said to answer quick conversation directly, use Search for a single current fact when available, delegate background or agent-managed work, and check status when asked about earlier background tasks. Those broad instructions do not explicitly prioritize a user's direct request to hand work to Grok or distinguish an earlier task's status from a request to modify its output. This ambiguity is a hypothesis, not an established root cause. A longer list of task-specific prompt rules would remain probabilistic and grow without bound.

The bridge allows the first supported Relay action per turn; a later action is rejected before the slow router. In particular, a status lookup followed by delegation in the same turn cannot be assumed to work. The router's durable admission, same-call deduplication, single-flight and unknown-outcome handling remain authoritative. Search is Gemini-managed and may not expose a corresponding Relay function event.

## Proposed user interaction

Users speak naturally to Jellii. Classify the requested work and output: substantive background work and artifact revisions should be delegated; short immediate questions and searches stay FAST. Naming Grok is not required. An explicit handoff remains an optional signal, not a deterministic keyword.

| Natural zh-TW request or condition | Expected first route / response |
| --- | --- |
| 「潔莉，幫我整理 #134 的問題，寫一份報告」 | `delegate_task` first |
| 「請深入比較這幾個方案，整理來源和限制」 | `delegate_task` first |
| 「幫我把之前的法國行程改成五天」 | `delegate_task` first; do not claim access to the old artifact |
| 「幫我查一下時間」 | FAST, no new task |
| 「今天天氣怎樣？」 | FAST / short grounded lookup |
| 「幫我查一下最新新聞」 | FAST / short grounded lookup |
| 「這兩個字有什麼差別？」 | FAST / short comparison |
| 「剛剛那份報告好了嗎？」 | `check_task_status`; no new task |
| 「最近台灣旅遊有哪些趨勢？」 without a deliverable | brief FAST answer and optional offer to investigate |
| After an offer: 「好，幫我深入查」 | `delegate_task` with the self-contained agreed request |
| 「幫我做一份報告」 with no topic | clarify essential missing detail |
| Delegation returns `busy` or `unknown` | report busy or acceptance unconfirmed; no claim of work underway and no automatic retry |

For a requested deliverable, a spoken “好，我去處理” is **not** a delegation: the model must actually issue `delegate_task`. Only `status=accepted` permits a brief acknowledgement; do not produce the delegated report in the same Live turn. An offer is not admission. A voluntary explicit Grok handoff remains valid, but is not the default invocation.

This PR guides the persona and tool description using the intended work and expected output rather than requiring a backend name or matching a generic phrase such as 「幫我」. This remains a model instruction, not a deterministic switch or a guarantee. Natural requests are the primary acceptance cases; do not require a backend name. If the request is unclear or missing its task, ask briefly instead of inventing one. A task's wording does not itself grant additional permissions.

Only claim that Grok was given the task after durable admission is confirmed. Busy means not accepted or queued; unknown admission means acceptance is unconfirmed and must not be automatically retried. An accepted task is not a completed task. A status lookup reads Relay's ledger/Inbox; it is not a Grok native cross-run search. A request to revise an earlier artifact is a new task, but native artifact recovery is independently unverified: do not invent the previous file, its contents or a v2 result.

## Alternatives and decision gates

1. **Prompt guidance (first experiment):** clarify the difference between substantive background work and immediate answers in the existing persona and function description. Small change, works with the existing Live workflow, but model selection remains probabilistic. A prompt-only “skill” has the same limitation.
2. **Program-controlled signal (conditional follow-up):** consider a dedicated user interaction or reliable locally recognized phrase that selects delegation in code. Do not implement or claim it is possible from this proposal. First establish when and whether complete input transcription is available, its quality and ordering relative to Live output/tool calls, how the exact task is extracted, and how interruption, empty input, false positives, quotes, duplicate requests and uncertain admission are handled. A deterministic action after recognition does not make speech recognition deterministic. Any new path must preserve authorization, single action, single-flight and durable deduplication.
3. **Forced function mode:** do not rely on Generate Content's function-calling modes as proof of Live support. Verify the exact deployed Live model, API and SDK version and their supported Live configuration before considering it. General [Live tool documentation](https://ai.google.dev/gemini-api/docs/live-api/tools) establishes function calling but does not by itself establish a forced-tool control for this deployment.

No permanent Search disable is proposed. Search-on versus Search-off is a controlled comparison, not proof of causation from one trial; configuration changes and real provider use require their respective Owner authorizations.

## Evidence and acceptance plan

This PR implements the minimal persona and tool-description wording and declaration tests. It does not implement the telemetry below; that remains necessary follow-up work before the complete #145 evidence plan can be claimed. Deployment, configuration changes and live provider calls require their separate existing Owner authorizations.

Proposed follow-up telemetry: record per PTT only bounded, allowlisted metadata: declared tool categories and Search enabled/disabled; first observed Relay function name or none; accepted, busy, rejected, unavailable or unknown outcome; blocked subsequent action count; and elapsed time from PTT end to first observed call or response. Distinguish no observed Relay function from Search not used: Search activity is unknown unless actually observable. Avoid raw audio, transcripts, task text, function arguments, search queries, identifiers that expose content, and credentials. Verify logging and exception paths also respect this boundary.

Offline tests should cover the configured declarations, safe telemetry with zero calls, accepted/busy/rejected/unknown outcomes, invalid or duplicate calls, multiple calls in a turn, and no second router action. They cannot establish the model's selection accuracy.

### Proposed Live routing evaluation (not yet run)

Following [Kai's routing eval](https://github.com/zbruceli/kai/blob/main/relay/evals/routing.py), exercise genuine Gemini Live first decisions using the actual configured Jellii system instruction and declared tools, including Search configuration. Replace the tool executor with a fake: observe the call and never dispatch to Grok. Preapprove the exact provider model, case count, repetitions and budget before any paid calls. Repeat each natural utterance and report first-route outcomes, per-category successes and denominators, SLOW misses (including merely offering or claiming to work without a function call), false handoffs, status mistakes, and ambiguous/clarification cases. Score accepted-offer follow-ups with their preceding conversation context. Separately feed synthetic accepted/busy/unknown results to assess truthful post-call speech without creating a real task. Report Search activity as unknown when not observable.

Kai's [v0.12.0 results](https://github.com/zbruceli/kai/blob/main/CHANGELOG.md) report 104 cases repeated three times and 94% overall under its own rubric; Kai's offer-acceptable cases are not equivalent to Amusant's required function call or durable admission. This proposed eval does not establish physical PTT or durable admission; those need a separate Owner-approved physical test. No provider call has been made for this PR.

For an Owner-approved physical evaluation, use natural zh-TW phrases and vary task content and wording without recording it in telemetry. Test FAST, explicit handoff, status, and revision of an earlier artifact; include empty/unclear and busy/unknown cases where feasible. Specify the number of trials and approved provider budget in advance, keep conditions comparable, and report successes and failures with denominators and latencies. A Search-off session is temporary and restored after the experiment. Distinguish synthetic offline cases from paid, physical Grok admissions.

For a valid background-work request with an available SLOW slot, the target is exactly one new durable `delegate_task` admission per PTT, no additional action after the gate, and truthful acknowledgement. FAST requests must remain responsive and add no SLOW row; status must not create a task. Track false positives, misses, duplicate admissions, and whether speech-recognition or tool selection was observable. If acceptance is unknown, inspect durable state rather than re-submit. Do not infer Grok native Run state or cross-run artifact recovery from admission or a Callback. Reuse the existing Routine → Callback → Inbox → later requested spoken readback; the separate spoken delivery issue remains independent.

Owner review determines the required reliability and acceptable latency from the measured results. If the prompt signal is inadequate, return with a concrete program-controlled design and its evidence; do not keep adding task-specific rules by default.
