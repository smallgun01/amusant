# Issue #74 — Opt-in CJK runtime diagnostics

This downstream diagnostic layer is for determining why photographed live captions show missing-glyph boxes. It is not a font fix and does not normalize Simplified/Traditional text. It keeps the current CJK renderer and the W1.5 BLE/NVS flow intact.

## Pinned font matrix

The offline lookup reads the PlatformIO-resolved M5GFX **0.2.30** efontTW_14 data and follows its U8g2font::getGlyph and metric lookup logic.

| Scalar | Result |
| --- | --- |
| U+6703 會 | Present |
| U+4F1A 会 | Missing |
| U+5F8C 後 / U+540E 后 | Both present |
| U+88E1 裡 / U+91CC 里 | Both present |
| U+767C 發 | Present |
| U+53D1 发 | Missing |
| U+60A8 您 / U+653E 放 / U+5FC3 心 | All present |
| U+FF0C, U+3002, U+FF1F, U+3001, U+FF01 | Present |
| U+FFFD, U+FE0F, U+200D | Missing |
| U+1F642, U+1F9EA | Supplementary scalars, outside the U8g2 16-bit lookup; M5GFX callback/fallback path |

The existing 67-scalar Traditional corpus remains **67/67 present**. This matrix shows why the spoken word alone cannot identify the photographed missing glyph. It does not establish which scalar arrived in the original caption.

## Diagnostic firmware

The extra PlatformIO profile is opt-in:

    pio run -e sticks3_cjk_diagnostic

The normal sticks3 profile does not define JELLII_CJK_DIAGNOSTICS; its Serial parser and caption path remain unchanged.

On a separately authorized diagnostic device session, entering CJK SHOW through USB Serial draws the literal 會 / 会 fixture through the production reply renderer (face::drawReply). The command refuses while BLE provisioning, listening/thinking, the reply cooldown, or reply audio playback/queued voice is active. A completed reply marker alone is not enough to run the fixture: buffered audio must also be idle. A busy command prints `CJK DIAG BUSY` and never clears or interrupts playback. The fixture does not start a network request or play audio.

In the diagnostic profile, after ArduinoJson decodes an incoming caption.delta, the firmware checks each Unicode scalar against the pinned reply font. Unsupported scalars are written to Serial as classified code points only: `CJK MISSING U+4F1A`, `CJK NONBMP U+1F642`, `CJK FORMAT U+FE0F`, or `CJK INVALID U+FFFD`. Repeated unsupported scalars are logged once per boot using a fixed 64-entry table; logging stops after 64 unique scalars. The diagnostic never prints caption text, credentials, PoP, or other payload fields. C0 controls are handled as renderer controls and are not font lookups.

## Owner-side USB command (later, after separate flash authorization)

Connect the separately approved diagnostic firmware over USB and start a serial monitor, for example:

    pio device monitor --port COM5 --baud 115200

At the serial monitor prompt, send exactly this line and press Enter:

    CJK SHOW

No Wi-Fi command, password, PoP, or other credential is needed. The command only draws the fixture when voice playback and the pending voice queue are idle; otherwise it reports `CJK DIAG BUSY`. This project does not authorize or perform a device flash.

## Reproduce

From a clean Kai checkout at 1681fb3c11acd28525d8987bba4b9bf4bc2a288f, apply the existing Jellii, W1 and W1.5 patches, then integrate CJK and apply this layer:

    python3 patches/sticks3-w15-cjk/apply_integration.py --kai kai
    git -C kai apply --check ../patches/kai-v0.12.0-cjk-diagnostic/kai-v0.12.0-cjk-diagnostic.patch
    git -C kai apply ../patches/kai-v0.12.0-cjk-diagnostic/kai-v0.12.0-cjk-diagnostic.patch

Run offline tests and both build profiles:

    python3 patches/kai-v0.12.0-cjk-diagnostic/tests/run_host_tests.py \
      --firmware-root kai/firmware \
      --wifi-tests patches/kai-v0.12.0-wifi-w1.5/tests
    pio run -d kai/firmware -e sticks3
    pio run -d kai/firmware -e sticks3_cjk_diagnostic

CI builds with secrets.example.h placeholders only. It reports each firmware image size and enforces the existing 3,342,336-byte application-slot limit. Neither build uploads firmware or contacts Gemini.

## Limits and rollback

This work does not prove the original square's code point or claim a runtime fix. It does not change Relay, AWS, Gemini, BLE provisioning, Wi-Fi NVS, PTT, Opus, TLS-PSK, Bearer, or Deep Sleep. Issues #74 and #67 remain open for review and later owner-authorized LCD diagnosis.

To remove only this layer from a clean integrated Kai checkout:

    git -C kai apply -R ../patches/kai-v0.12.0-cjk-diagnostic/kai-v0.12.0-cjk-diagnostic.patch

Do not flash a device as part of this offline investigation.


## Measured firmware footprint

The following measurements are the pre-review-correction baseline from 2026-10-18 ([run #39119320625](https://github.com/smallgun01/amusant/actions/runs/39119320625)); the corrected revision is rebuilt by the current PR checks:

| Profile | firmware.bin | Program Flash | Static RAM |
| --- | ---: | ---: | ---: |
| `sticks3` | 2,427,184 bytes | 2,393,415 / 3,342,336 (71.6%) | 79,584 / 327,680 (24.3%) |
| `sticks3_cjk_diagnostic` | 2,429,200 bytes | 2,395,383 / 3,342,336 (71.7%) | 79,584 / 327,680 (24.3%) |

These baseline measurements predate the review fixes, including the bounded deduplication table. The current revision's final footprint is available in its combined PR check logs and PR summary after those checks finish. Both profiles must fit the 3,342,336-byte application slot. Runtime free heap is not measured because this task does not flash a device.
