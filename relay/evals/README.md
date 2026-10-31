# Gemini Live routing evaluation

This opt-in evaluation observes Gemini Live's first decision with the Jellii persona, the actual Relay declarations and Grok status semantics. It uses the production provider factory with explicitly selected model, voice and output limit; Google Search defaults on. It sends synthetic text, including synthetic multi-turn history in one request, not physical PTT audio or real sequential turns. It does not exercise prior FunctionResponses, session lifecycle or reconnect behavior. A function call is intercepted and the session is closed: no tool executor, TaskStore, Grok Webhook or Callback is invoked. It does not measure tool arguments, later tool calls, speech delivery, native Search activity, or physical device behavior. A spoken promise without a function call counts as FAST, never as DELEGATE. Multiple calls in the first batch and unexpected functions are failures; session failures and timeouts are ERROR, not FAST.

Running the live mode consumes Gemini quota and may incur Search/grounding charges. There are 17 cases: each run opens `17 × repeat` sessions; a baseline plus candidate costs `34 × repeat` sessions. Actual token and Search usage, pricing and cost depend on the selected model, provider and output; measure or estimate them using the Owner's account before approval. No API key, transcripts, function arguments or Search queries are written by the harness. Use synthetic cases only. No real provider evaluation is authorized merely by this document.

## Offline tests

From the repository root, install the project dependencies in an existing approved environment and run:

```sh
python -m unittest relay.tests.test_routing_eval relay.tests.test_companion_persona relay.tests.test_slow_path.DelegateTaskToolDeclarationTests relay.tests.test_slow_path.CheckTaskStatusDescriptionTests fast_voice.tests.test_factory
python -m unittest fast_voice.tests.test_live_bridge.LiveBridgeTests.test_second_tool_action_is_rejected_before_router_in_either_order
```

## Owner-approved live comparison

Owner-reported [OCI cutover evidence](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695) recorded Jellii, Search=on and Zephyr, but this historical record does not confirm the current effective model or max-output-tokens setting. Verify the current non-sensitive model, voice, Search and max-output-tokens values with the Owner; do not assume a fully matched configuration. Explicitly provide the voice and output limit (use `unset` only if verified unset). Match SDK and other deployment settings separately: this text-only harness is not an exact reproduction of the physical runtime.

Only after the Owner explicitly approves the exact model, repetition count and cost budget, set `GEMINI_API_KEY` in the execution environment without printing it. Before the full comparison, obtain approval for and run one smoke case (`--only definition --repeat 1`) with the same settings; inspect its ERROR category and stop to investigate failures. That smoke costs one additional session. Check out the baseline `c038d61` and candidate in separate directories, then from the **candidate checkout**, run the harness as a script (not with `-m`):

```sh
python relay/evals/routing.py --run-live --model APPROVED_MODEL --voice Zephyr --max-output-tokens VERIFIED_LIMIT_OR_UNSET --search on --only definition --repeat 1 --source-root /path/to/candidate --source-revision CANDIDATE_SHA --output smoke.json
python relay/evals/routing.py --run-live --model APPROVED_MODEL --voice Zephyr --max-output-tokens VERIFIED_LIMIT_OR_UNSET --search on --repeat APPROVED_COUNT --source-root /path/to/baseline --source-revision c038d61 --output before.json
python relay/evals/routing.py --run-live --model APPROVED_MODEL --voice Zephyr --max-output-tokens VERIFIED_LIMIT_OR_UNSET --search on --repeat APPROVED_COUNT --source-root /path/to/candidate --source-revision CANDIDATE_SHA --output after.json
python relay/evals/routing.py --compare before.json after.json
```

`VERIFIED_LIMIT_OR_UNSET` is a placeholder; use a number or the literal `unset`. `--run-live` is an explicit execution switch, not Owner approval. The source revision is operator-supplied: verify checkout contents independently. Each output contains case IDs, expected/observed routes, repetition, latency and aggregate accuracy, confusion matrix, error count and safe categories (`timeout`, `connection`, `provider_4xx`, `provider_5xx`, `incomplete`, `other`), never raw exceptions. Comparison refuses mismatched selected IDs, case hash, SDK, model, voice, output limit, Search, timeout or repetition count. Errors remain in the denominator and are reported separately; do not interpret an infrastructure failure as a routing decision. Search is enabled in both runs; a Search-off comparison needs separate approval.

The first-call evaluation is evidence of tool choice, not successful durable admission or multi-turn UX. Keep #145 open until an independently approved physical StickS3 test proves one actual Live-originated durable admission and the sequential flow delegate → status → read result → edit → status, including truthful behavior. The existing bridge regression checks its one-action-per-turn gate separately.

## Pre-delegation clarification cases (#158)

`clarification_cases.jsonl` is a separate natural zh-TW evaluation set, usable with `--cases`. C01/C04 require a concise scheduling question; C02/C03/C05 and C08 expect one delegation; C06 is ordinary extended research; C07 is status; C09 has no prior context and must ask again. For C04/C08 verify both events individually. For C05/C08 manually inspect the function argument and confirm `商店` remains `商店`, without a fabricated business, date, time, permission, or location. Route classification alone cannot check the question, arguments, admission acknowledgement, or side effects. C08 supplies synthetic same-session history; C09 approximates missing context and does not verify real disconnect or reconnect behavior.

The fake-provider harness tests classification and fixture plumbing only: it does not execute Gemini, establish that the prompt reliably produces clarification, or prove physical routing. Instructions are probabilistic; tool choice and spoken output can still be wrong. Any real-model or device evaluation requires separate authorization and review. No provider calls or real Grok dispatch are needed for offline tests.
