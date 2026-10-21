# Wi-Fi W2b-QR — on-screen BLE provisioning QR

This downstream patch implements Issue #96 on the pinned Kai v0.12.0 firmware. The current revision is an **optical-readability experiment**, following the owner report that the official Espressif app did not recognize the 98px version-6 QR. It shows the active W2b-1 BLE service name and existing 26-character PoP as a larger QR on owner-triggered setup boot. Physical scan acceptance is still pending; this experiment is not a production PASS.

The unchanged 99-byte payload uses Espressif QR v1 JSON with numeric `security: 1`, so the official Espressif Android provisioning app selects Security 1 instead of its Security 2 default. QR data comes from the active W2b-1 session; this patch does not generate another service name or PoP. QR v5-L uses 37×37 modules, four pure-white quiet-zone modules on every side, and 3 pixels per module for a 135×135 image. It is horizontally centered on the 240×135 canvas and fills its height. The QR page has a white background with no header, footer, or overlay. Its backlight is 45; the manual PoP page restores the normal awake level of 90. Button A short-click switches between the QR and existing manual PoP; Button B short-click cancels setup. Size and backlight are changed together in this first optical experiment, so their effects are not independently attributed. First-boot USB recovery, normal PTT behavior, and USB Serial `WIFI SET` remain unchanged.

## Pinned baseline and patch order

Target Kai v0.12.0 commit: `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`.

The W2b-1 baseline is the final patch merged by PR #95 at `cd80c50583c293f419ca8c87189f30fef26bdff2`. The QR patch is regenerated after that lifecycle change: it reads the service name only while `managerLifecycle` is `Running` and the provisioning session is active. The #95 END/DEINIT queue handling, Arduino-core-owned deinit, and fresh `drainNow` fallback remain in the W2b-1 layer.

Apply this patch after the merged W2b-1 layer:

1. Kai v0.12.0 at the exact commit above.
2. Jellii patch.
3. W1 dynamic Wi-Fi / NVS patch.
4. W1.5 BLE provisioning patch.
5. Production CJK integration.
6. Merged W2b-1 patch.
7. This W2b-QR patch.

```sh
git clone --no-tags https://github.com/zbruceli/kai.git kai
git -C kai checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C kai apply ../patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch
git -C kai apply ../patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch
git -C kai apply ../patches/kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch
python3 patches/sticks3-w15-cjk/apply_integration.py --kai kai
git -C kai apply ../patches/kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch
git -C kai apply --check ../patches/kai-v0.12.0-wifi-w2bqr/kai-v0.12.0-wifi-w2bqr.patch
git -C kai apply ../patches/kai-v0.12.0-wifi-w2bqr/kai-v0.12.0-wifi-w2bqr.patch
git -C kai diff --check
```

## QR and memory design

The QR payload is exactly 99 ASCII bytes for the W2b-1 service name and 26-character PoP. Fixed QR version 5-L holds it in 37×37 modules (106-byte capacity). Four white quiet-zone modules and 3×3-pixel modules produce a 135×135-pixel image at x=52, y=0 on the 240×135 landscape display; the remaining right margin is 53 pixels. The code draws modules directly from the encoded matrix; it does not allocate a second bitmap. Encoder ECC is explicitly LOW and ECC boosting is disabled.

Nayuki's C QR encoder is vendored from commit `3c6d0b3cefb4e049dc337e82237c9644399716a8` under its MIT license. Encoding uses two fixed 173-byte buffers (346 bytes total) and a 128-byte stack payload, with no heap allocation. The encoded matrix, payload scratch, and manual PoP display are cleared on setup cancel, success, timeout, and battery abort. The integrated workflow reports this revision's before/after PlatformIO image, flash, and static RAM measurements. Physical free-heap margin still needs device measurement.

## Offline verification

The integrated workflow applies all production layers, runs the merged W1/W1.5/W2b-1/CJK regressions, builds the merged baseline, then applies this display-only optical revision and runs QR host/layout/page checks, a fake-only independent `zbarimg` decode, and production plus diagnostic builds. The decoder captures and compares output internally without printing or uploading the decoded payload or image. All firmware builds use only `secrets.example.h` placeholders. See the matching latest PR workflow run for this commit; physical scanning remains pending owner-supervised acceptance.

The optical revision's exact firmware image, program flash, static RAM, and delta against merged PR #95 are recorded in the matching CI summary and will be copied here from that run before review. The previous 98px version-6 measurements are intentionally not presented as measurements of this revision.

The static RAM increase matches the fixed QR matrix and scratch buffers. Runtime free-heap margin still requires device measurement.

Manual verification command, after applying the patch:

```sh
sudo apt-get install zbar-tools
python3 patches/kai-v0.12.0-wifi-w2bqr/tests/run_host_tests.py --firmware-root kai/firmware
python3 patches/kai-v0.12.0-wifi-w2bqr/tests/check_owner_setup_wiring.py --firmware-root kai/firmware
```

No device is flashed and no live Gemini request is made by this patch or its workflow. Physical phone-to-screen QR scanning is pending owner-supervised hardware acceptance.

## Rollback

From the reconstructed Kai tree, reverse only W2b-QR to return to the merged W2b-1 state:

```sh
git -C kai apply -R ../patches/kai-v0.12.0-wifi-w2bqr/kai-v0.12.0-wifi-w2bqr.patch
```
