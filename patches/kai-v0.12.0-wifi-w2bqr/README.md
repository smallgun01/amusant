# Wi-Fi W2b-QR — on-screen BLE provisioning QR

This downstream patch implements Issue #96 on the pinned Kai v0.12.0 firmware. It shows the current W2b-1 BLE service name and its existing 26-character PoP as an Espressif provisioning QR on the owner-triggered setup boot. Button A short-click switches to the existing manual PoP view; Button B short-click cancels setup. First-boot USB recovery and normal PTT behavior keep their existing views.

The QR uses Espressif QR v1 JSON with numeric `security: 1`, so the official Espressif Android provisioning app selects Security 1 instead of its Security 2 default. QR data comes from the active W2b-1 session; this patch does not generate another service name or PoP. USB Serial `WIFI SET` remains available as the recovery path.

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

The QR payload is exactly 99 ASCII bytes for the W2b-1 service name and 26-character PoP. Fixed QR version 6-M holds it in 41×41 modules. Four white quiet-zone modules and 2×2-pixel modules produce a 98×98-pixel image at x=71, y=18 on the 240×135 landscape display. The code draws modules directly from the encoded matrix; it does not allocate a second bitmap.

Nayuki's C QR encoder is vendored from commit `3c6d0b3cefb4e049dc337e82237c9644399716a8` under its MIT license. Encoding uses two fixed 212-byte buffers (424 bytes total) and a 128-byte stack payload, with no heap allocation. The encoded matrix, payload scratch, and manual PoP display are cleared on setup cancel, success, timeout, and battery abort. CI reports the before/after PlatformIO firmware image, flash, and static RAM measurements in its run summary; physical free-heap margin still needs device measurement.

## Offline verification

The integration workflow applies all production layers and merged PR #95, runs the W1/W1.5/W2b-1 regressions, builds that merged state as the footprint baseline, then applies this QR-only patch and repeats the production and diagnostic builds. It reports image, flash, and static RAM deltas against merged #95. The QR test decodes only a fake generated payload with `zbarimg`; it captures and compares output internally without printing or uploading the decoded payload or image. Builds use only `secrets.example.h` placeholders.

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
