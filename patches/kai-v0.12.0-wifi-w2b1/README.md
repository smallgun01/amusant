# Wi-Fi W2b-1 — configured BLE re-provisioning

This downstream firmware patch implements Issue #91 on the exact Kai v0.12.0 pin. It adds a 5-second Button B setup gesture and a controlled, one-shot reboot into authenticated BLE provisioning. The existing `amu_wifi/profile` record remains unchanged until replacement credentials have joined Wi-Fi and acquired a nonzero IP address. USB Serial remains the recovery path.

The architecture and owner review points are in [DESIGN.md](DESIGN.md); the separate future physical procedure is [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md). This package does not change AWS, Relay, cloud ingress, the PTT/Opus/TLS-PSK/Bearer path, or normal deep-sleep behavior. It does not flash a device or make a paid/live call.

## Pinned baseline and layer order

Target Kai commit: `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`.

From the Amusant repository root, apply layers in this order:

1. Kai v0.12.0 at the exact commit above.
2. Jellii patch.
3. W1 dynamic Wi-Fi / NVS patch.
4. W1.5 BLE provisioning patch.
5. Run the integrated production CJK adapter.
6. Apply this W2b-1 patch directly after production CJK.

The historic CJK runtime diagnostic patch and its tests remain available as a separate opt-in troubleshooting asset. They are not part of this production firmware chain. The production CJK Traditional Chinese font, UTF-8 layout, and glyph-width checks remain enabled.

```sh
git clone --no-tags https://github.com/zbruceli/kai.git kai
git -C kai checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C kai apply --check ../patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch
git -C kai apply ../patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch
git -C kai apply --check ../patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch
git -C kai apply ../patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch
git -C kai apply --check ../patches/kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch
git -C kai apply ../patches/kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch
python3 patches/sticks3-w15-cjk/apply_integration.py --kai kai
git -C kai apply --check ../patches/kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch
git -C kai apply ../patches/kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch
git -C kai diff --check
```

The combined production workflow performs these patch-apply checks from a clean pinned Kai checkout. It builds both the normal `sticks3` image and the opt-in `sticks3_ble_diag` image. W2b-1 is a downstream patch; it does not vendor or modify upstream Kai.

## Offline tests and build

The combined workflow runs the W1, W1.5, and W2b-1 host suites, the production CJK UTF-8 layout and real-font glyph-width checks, then builds `sticks3` and the opt-in `sticks3_ble_diag` diagnostics profile:

```sh
python3 patches/kai-v0.12.0-wifi-w1/tests/run_host_tests.py --firmware-root kai/firmware
python3 patches/kai-v0.12.0-wifi-w1.5/tests/run_host_tests.py --firmware-root kai/firmware
python3 patches/kai-v0.12.0-wifi-w2b1/tests/run_host_tests.py \
  --firmware-root kai/firmware \
  --wifi-tests patches/kai-v0.12.0-wifi-w1.5/tests
```

The W2b-1 C++ fake-NVS suite covers configured-profile entry, gesture/battery gates, credential commit ordering, cancellation, timeout, write failure, and teardown. `CredentialCommitGate` retains the existing NVS profile until Wi-Fi plus IP are verified. The shared `ManagerLifecycle` seam leaves SDK auto-stop enabled for the phone app, records `NETWORK_PROV_END`, and waits for the Arduino core's external `NETWORK_PROV_DEINIT`; the application loop does not deinitialize again. Tests cover external DEINIT ordering, terminal-event preservation during cancellation, exactly-once profile save, and the last-resort 45-second fallback. A source contract check enforces a single guarded direct deinit for provisioning-start failure. This seam does not emulate Espressif's callback scheduler or replace hardware acceptance.

For a local build, use only the placeholder secrets file and the production profile:

```sh
cp kai/firmware/src/secrets.example.h kai/firmware/src/secrets.h
pio run -d kai/firmware -e sticks3
pio run -d kai/firmware -e sticks3_ble_diag
rm kai/firmware/src/secrets.h
```

The CI checks that the production `sticks3` image excludes BLE lifecycle and CJK diagnostic strings, while `sticks3_ble_diag` contains the opt-in lifecycle markers and compiled SDK timeout. The diagnostic profile logs event timestamps, queue counts, lifecycle state, and active-state contributors without SSID, password, candidate profile, service name, or PoP. Both builds use placeholder secrets only; they do not upload firmware or contact Gemini.

## Footprint and verification

Both pinned builds passed in [integration workflow run #40](https://github.com/smallgun01/amusant/actions/runs/39522051614). The production firmware excludes lifecycle diagnostics. The opt-in build compiled `CONFIG_NETWORK_PROV_AUTOSTOP_TIMEOUT=30s` and derives its 45,000 ms drain fallback from that SDK value plus a 15-second margin. CI uses placeholder secrets only and does not upload firmware or contact Gemini.

| Profile | Firmware image | Program Flash | RAM |
| --- | ---: | ---: | ---: |
| `sticks3` | 2,430,896 B | 2,396,851 / 3,342,336 B (71.7%) | 79,800 / 327,680 B (24.4%) |
| `sticks3_ble_diag` | 2,433,488 B | 2,399,363 / 3,342,336 B (71.8%) | 79,936 / 327,680 B (24.4%) |

Runtime free heap, battery draw, Android status delivery, and reconnection to a real AP are not measurable from this offline build. The owner-supervised physical acceptance procedure remains pending; the CI run does not establish successful Android provisioning.

## Rollback

On the reconstructed Kai tree, reverse only W2b-1 to return to the merged W1.5 + production CJK state:

```sh
git -C kai apply -R ../patches/kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch
```

This source rollback does not flash a device, erase NVS, or alter the saved W1 profile. Restoring a device image is a separate owner-authorized action.

