# Gemini Live routing evaluation

This opt-in evaluation observes Gemini Live's first decision with the Jellii persona, the actual Relay declarations and Grok status semantics. It uses the production provider factory with Google Search enabled. It sends synthetic text (including optional prior conversation), not physical PTT audio. A function call is intercepted and the session is closed: no tool executor, TaskStore, Grok Webhook or Callback is invoked. It does not measure tool arguments, later tool calls, speech delivery, native Search activity, or physical device behavior. A spoken promise without a function call counts as FAST, never as DELEGATE. Multiple calls in the first batch and unexpected functions are failures; session failures and timeouts are ERROR, not FAST.

Running the live mode consumes Gemini quota and may incur Search/grounding charges. There are 17 cases: each run opens `17 × repeat` sessions; a baseline plus candidate costs `34 × repeat` sessions. Actual token and Search usage, pricing and cost depend on the selected model, provider and output; measure or estimate them using the Owner's account before approval. No API key, transcripts, function arguments or Search queries are written by the harness. Use synthetic cases only. No real provider evaluation is authorized merely by this document.

## Offline tests

From the repository root, install the project dependencies in an existing approved environment and run:

```sh
python -m unittest relay.tests.test_routing_eval relay.tests.test_companion_persona relay.tests.test_slow_path.DelegateTaskToolDeclarationTests relay.tests.test_slow_path.CheckTaskStatusDescriptionTests fast_voice.tests.test_factory
python -m unittest fast_voice.tests.test_live_bridge.LiveBridgeTests.test_second_tool_action_is_rejected_before_router_in_either_order
```

## Owner-approved live comparison

Only after the Owner explicitly approves the exact model, repetition count and cost budget, set `GEMINI_API_KEY` in the execution environment without printing it. Keep the SDK, model, cases and Search setting identical. Check out the baseline `c038d61` and candidate in separate directories, then from the **candidate checkout**, run the harness as a script (not with `-m`):

```sh
python relay/evals/routing.py --run-live --model APPROVED_MODEL --repeat APPROVED_COUNT --source-root /path/to/baseline --source-revision c038d61 --output before.json
python relay/evals/routing.py --run-live --model APPROVED_MODEL --repeat APPROVED_COUNT --source-root /path/to/candidate --source-revision CANDIDATE_SHA --output after.json
python relay/evals/routing.py --compare before.json after.json
```

`--run-live` is an explicit execution switch, not Owner approval. The source revision is operator-supplied: verify checkout contents independently. Each output contains case IDs, expected/observed routes, repetition, latency and aggregate accuracy, confusion matrix and error counts. Comparison refuses mismatched case hash, SDK, model, Search, timeout or repetition count. Errors remain in the denominator and are reported separately; do not interpret an infrastructure failure as a routing decision. Search is enabled in both runs; a Search-off comparison needs separate approval.

The first-call evaluation is evidence of tool choice, not successful durable admission. Keep #145 open until an independently approved physical StickS3 test proves one actual Live-originated durable admission, status lookup, Inbox readback and truthful behavior. The existing bridge regression checks its one-action-per-turn gate separately.
