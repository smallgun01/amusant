# Relay OpenCC display-only caption normalization (Issue #84)

FAST Gemini Live output transcript deltas destined for the StickS3 LCD are normalized to Traditional Chinese (Taiwan words/phrases) by OpenCC `s2twp.json` in `relay/caption_script.py`.

This conversion is scoped only to the outgoing WebSocket control payload `{"type": "caption", "delta": "..."}`. It does **not** alter the provider's transcript event, Gemini model inputs, tool calls/results, Slow Task/Inbox storage, audio data, captions in historical records or the StickS3 firmware. English, Latin symbols, and already-Traditional characters pass through unchanged.

A single cached OpenCC converter is initialized when the Relay handler is built, not for each word or delta. No network/API requests are used by the converter. Default mode is `AMUSANT_CAPTION_SCRIPT=zh-TW`; rollback via the server-owned environment setting `AMUSANT_CAPTION_SCRIPT=raw` and restart Relay. Invalid values stop handler initialization.

**Important streaming limitation:** Live transcript deltas are processed independently to avoid delaying audio or waiting for complete sentences. When a multi-character term straddles adjacent deltas, phrase-sensitive Taiwanese lexical choices may be imperfect. We prioritize preventing missing Simplified glyph boxes without blocking live audio. Emoji, missing Traditional glyphs and non-BMP symbols remain outside scope.

## Required review gates before merge

- After updating `pyproject.toml`, regenerate and commit `uv.lock` using the trusted dependency toolchain: `uv lock`, then verify `uv sync --locked`; no manual invented hashes.
- Run offline Fast Voice and Relay tests; verify Linux ARM64 OCI Relay image builds and can import and initialize OpenCC `s2twp.json` on target runtime.
- Preserve PCM/Opus bytes, response completion, fencing of interrupted turns and Slow Task/Inbox payloads.
- No automatic deployment, cloud cost, or physical flash. Local Relay restart is required before owner-side PTT visual acceptance.
