# Relay OpenCC display-only caption normalization (Issue #84)

FAST Gemini Live output transcript deltas destined for the StickS3 LCD are normalized to Traditional Chinese (Taiwan words/phrases) by OpenCC `s2twp.json` in `relay/caption_script.py`.

This conversion is scoped only to the outgoing WebSocket control payload `{"type": "caption", "delta": "..."}`. It does **not** alter the provider's transcript event, Gemini model inputs, tool calls/results, Slow Task/Inbox storage, audio data, captions in historical records or the StickS3 firmware. English, Latin symbols, and already-Traditional characters pass through unchanged.

A single cached OpenCC converter is initialized when the Relay handler is built, not for each word or delta. No network/API requests are used by the converter. Default mode is `AMUSANT_CAPTION_SCRIPT=zh-TW`; rollback via the server-owned environment setting `AMUSANT_CAPTION_SCRIPT=raw` and restart Relay. Invalid values stop handler initialization.

**Important streaming limitation:** Live transcript deltas are processed independently to avoid delaying audio or waiting for complete sentences. When a multi-character term straddles adjacent deltas, phrase-sensitive Taiwanese lexical choices may be imperfect. We prioritize preventing missing Simplified glyph boxes without blocking live audio. Emoji, missing Traditional glyphs and non-BMP symbols remain outside scope.

## Validation performed in PR #86

- `uv.lock` was generated from the declared `opencc==1.4.2` dependency with uv 0.13.1; CI verifies `uv sync --locked`. No hashes were hand-written.
- [GitHub Actions CI run](https://github.com/smallgun01/amusant/actions/runs/39144883041) passed the Python 3.13 offline test suites, TLS-PSK/Opus loopback, and Hermes fake-provider E2E.
- The Linux ARM64 Relay image built successfully. Its runtime check initialized `OpenCC("s2twp.json")` and verified the expected Traditional Chinese conversion; ARM64 Opus, TLS-PSK, and egress-proxy smoke checks passed.

## Remaining owner acceptance before merge

- Confirm LCD output with PTT under normal voice load. The independent-delta phrase-boundary limitation described above still applies.
- No service deployment or firmware changes were performed. A local Relay restart is needed before owner-side visual acceptance.
