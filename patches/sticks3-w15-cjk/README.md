# StickS3 W1.5 BLE + Traditional Chinese display integration

Integration-only layer for merged PR #64 (BLE Wi-Fi W1.5) and PR #66 (Traditional Chinese Jellii captions). Does not modify either merged source patch. Related: #68, #63, #67.

## Layering and validation

Start with **clean Kai v0.12.0** commit `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`.

1. Apply existing Jellii, W1, W1.5 patches in that order.
2. Run `python patches/sticks3-w15-cjk/apply_integration.py --kai <kai-dir>` from Amusant repository root.
3. The script safely inserts the CJK header into W1.5's updated include context, discards only the obsolete first CJK include hunk, applies remaining untouched CJK hunks, validates symbols, and fails closed on source/patch drift.
4. Run W1.5 host C++/Python tests, CJK C++ UTF-8 tests, then `platformio run -e sticks3` with the usual **local** (gitignored) `secrets.h`.

**Never commit local secrets or an executable firmware image.** CI substitutes `secrets.example.h` and builds only, with no physical device and no Google/API calls.

## Windows PowerShell (owner later)

From `C:\Amusant-H0\amusant`, after updating main and cloning pinned Kai to a *new* directory (not the existing W1 rollback checkout):

```powershell
$patches = (Resolve-Path .\patches).Path
$kai = 'C:\Amusant-H0\kai-wifi-w15-cjk'
git -C $kai apply --check "$patches\kai-v0.12.0-jellii\kai-v0.12.0-jellii.patch"
git -C $kai apply "$patches\kai-v0.12.0-jellii\kai-v0.12.0-jellii.patch"
git -C $kai apply --check "$patches\kai-v0.12.0-wifi-w1\kai-v0.12.0-wifi-w1.patch"
git -C $kai apply "$patches\kai-v0.12.0-wifi-w1\kai-v0.12.0-wifi-w1.patch"
git -C $kai apply --check "$patches\kai-v0.12.0-wifi-w1.5\kai-v0.12.0-wifi-w1.5.patch"
git -C $kai apply "$patches\kai-v0.12.0-wifi-w1.5\kai-v0.12.0-wifi-w1.5.patch"
py .\patches\sticks3-w15-cjk\apply_integration.py --kai $kai
```

Copy `firmware/src/secrets.h` locally from the known-good W1 checkout, without displaying/printing it, then build via `py -m platformio run -d "$kai\firmware" -e sticks3`.

**No Flash or NVS clear authorized at this stage.** Preserve the known-good W1 full 8 MB backup and working USB recovery. Hardware PoC must separately validate BLE PoP on the physical LCD, Android app interoperability, Wi-Fi connection/recovery, CJK glyph display and normal PTT/deep sleep.

## Rollback

For source-only rollback, use a fresh clean pinned Kai checkout, reapply only the Jellii + W1 patches. Restore prior known-good W1 **image** to the physical device only after owner approval, avoiding full-chip erase (it would erase NVS).
