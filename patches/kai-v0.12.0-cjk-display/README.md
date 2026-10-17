# Jellii StickS3 Traditional Chinese Display (Draft)

This is a firmware-only downstream patch on the pinned Kai v0.12.0 source. It is applied **after** the accepted Jellii avatar patch and WiFi W1 patch. Nothing modifies the Kai upstream repository or Amusant Relay, and this PR does not flash hardware or authorize AWS/API usage.

## Scope

- The 240x135 LCD's reply body and card title switch to a **single** M5GFX/LovyanGFX `fonts::efontTW_14` Traditional Chinese font. Status and battery indicators keep their existing ASCII fonts; Jellii six-state avatar rendering is unchanged.
- Unicode-safe wrapping for Hanzi, mixed English, line breaks and word boundaries; long unspaced words are split only at UTF-8 code-point boundaries.
- The title width cap and 2 KiB caption limit no longer cut a multibyte UTF-8 scalar. Complex grapheme clusters/emoji and rare Hanzi outside efontTW coverage can still fall back to missing-glyph boxes; those are separate font coverage concerns.
- No PTT, Wi-Fi, NVS, TLS-PSK, Opus, power, wake, Relay, Hermes or Gemini logic changes.

## Apply (Windows PowerShell; **do not upload/flash**)

From a **clean checkout of Kai** at `1681fb3c11acd28525d8987bba4b9bf4bc2a288f` and a current Amusant checkout, set the patch location and apply in order:

```powershell
$patches = "C:/path/to/amusant/patches"
git apply --check "$patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch"
git apply "$patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch"
git apply --check "$patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch"
git apply "$patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch"
git apply --check "$patches/kai-v0.12.0-cjk-display/kai-v0.12.0-cjk-display.patch"
git apply "$patches/kai-v0.12.0-cjk-display/kai-v0.12.0-cjk-display.patch"
```

Run `pio run -e sticks3` inside the Kai `firmware/` directory after creating the local, gitignored `src/secrets.h` from the example. **Do not run `pio run -t upload` until the owner approves a physical flash.** W1 uses NVS for Wi-Fi; do not erase flash or clear NVS.

To undo only this feature, reverse **this patch first**, retaining the Jellii and WiFi W1 patches: `git apply -R "$patches/kai-v0.12.0-cjk-display/kai-v0.12.0-cjk-display.patch"`.

## Offline tests and Flash budget

The host test is compilable without Arduino/M5GFX: `g++ -std=c++17 -O2 -Wall -Wextra -Werror -Ifirmware/src tests/test_cjk_layout.cpp -o /tmp/jellii-cjk-test && /tmp/jellii-cjk-test` (run from the Kai checkout with the test source path adjusted to this Amusant package).

Accepted previous W1 build: **1,616,983 / 3,342,336 bytes Flash** and **76,996 / 327,680 bytes static internal RAM** (see WiFi W1 README). This is a **baseline**, not an output measurement for the CJK patch. CJK Flash delta and RAM/PSRAM costs **must be established from the new PlatformIO build** before a physical flash. The efontTW font is linked in Flash; the existing M5Canvas sprite is 240×135×2 bytes (~63.3 KiB). The code does not allocate a second frame buffer.

## Validation gates

1. Git patch applies cleanly **after Jellii + W1**; host UTF-8 unit tests PASS.
2. PlatformIO `sticks3` build succeeds and resulting firmware fits the existing 3,342,336-byte app slot; measure before/after Flash and RAM (no partition change).
3. **Owner-supervised physical test, not run by CI:** Chinese caption, Chinese card title, Chinese/English mixed text, punctuation/newlines, long caption wrap and scroll, Jellii six states, audio/PTT, Wi-Fi recovery, deep sleep/wake and free-heap after repeated responses.
4. If font coverage is incomplete or performance unacceptable, retain the accepted previous firmware; do not upload partial patches to a real StickS3.
