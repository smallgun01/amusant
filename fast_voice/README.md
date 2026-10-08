# Amusant Fast Voice contract (P2)

## Baseline and audio envelope

Gemini Live is the first real provider targeted. The app will select a provider explicitly; P2 is focused on one Gemini adapter, not a multi-provider UI or automatic fallback. Current code is a dependency-injected scaffold. An offline fake-session test with actual google-genai type objects passes; the SDK's real client connection and live session remain unverified.

For the initial Kai-compatible client boundary, internal audio is mono PCM16 little-endian with an explicit sample rate: 16 kHz input and 24 kHz output. The Gemini scaffold accepts 16 kHz input and requires output explicitly tagged as 24 kHz PCM; it rejects unknown/different output rates rather than silently mislabel audio. Resampling is not implemented yet; future adapters must normalize to this boundary. This matches pinned Kai v0.12.0's relay protocol and its `audio/pcm;rate=16000` Gemini input path; this is source-level evidence, not a new Amusant runtime test. See [Kai session.py at the pinned baseline](https://github.com/zbruceli/kai/blob/1681fb3c11acd28525d8987bba4b9bf4bc2a288f/relay/kai_relay/session.py).

## Contract semantics

- `start_turn`/`end_turn` represent PTT activity start/release. Kai v0.12.0 starts Gemini activity on PTT and its source comments that the activity start interrupts current speech. The adapter reports provider-reported interruption separately from turn completion.
- Output callbacks are dispatched serially in provider receive order; async callbacks are awaited. Transcripts label input/output and partial/final state.
- Tool calls carry provider call ID, name, and JSON arguments; results return against the same ID.
- `barge_in` and standalone `cancellation` are separate capabilities. Gemini's adapter marks barge-in supported but standalone cancellation unsupported; `cancel_response()` fails closed until a verified provider operation exists.
- `close` releases the live session and receive loop. Background receive failures surface as sanitized `ProviderError` events; no credentials/audio are included.
- Provider selection is exact and fail-closed: absent/unknown choice is an error, never implicit Gemini or alternate-provider fallback.

## Current implementation/verification boundary

The `GeminiLiveProvider` maps the inspected SDK surface (`connect`, `send_realtime_input`, `receive`, `send_tool_response`) through injected client/type objects. `google-genai>=2.0` is declared in the project and locked to 2.29.0; it is installed only in the repository `.venv`. Fourteen `uv run` tests pass, including one no-network fake-session check using actual SDK types. This confirms type construction, **not the real SDK client/session handshake or Gemini Live runtime**. No API credential was read, no external model call was made, and no API cost was incurred. Production wiring and live validation remain pending; live tests require separately approved credentials/budget. Google Live documentation fetch currently redirects unsuccessfully; implementation mapping was cross-checked against the pinned Kai source and public Python SDK source.

Run the no-network unit tests from repo root:

~~~bash
uv run --locked python -m unittest discover -s fast_voice/tests -v
~~~
