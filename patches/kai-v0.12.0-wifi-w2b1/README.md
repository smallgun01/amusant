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

The combined production workflow performs these patch-apply checks from a clean pinned Kai checkout. W2b-1 is a downstream patch; it does not vendor or modify upstream Kai.

## Offline tests and build

The combined workflow runs the W1, W1.5, and W2b-1 host suites, the production CJK UTF-8 layout and real-font glyph-width checks, then builds only `sticks3`:

```sh
python3 patches/kai-v0.12.0-wifi-w1/tests/run_host_tests.py --firmware-root kai/firmware
python3 patches/kai-v0.12.0-wifi-w1.5/tests/run_host_tests.py --firmware-root kai/firmware
python3 patches/kai-v0.12.0-wifi-w2b1/tests/run_host_tests.py \
  --firmware-root kai/firmware \
  --wifi-tests patches/kai-v0.12.0-wifi-w1.5/tests
```

The W2b-1 C++ fake-NVS suite covers configured-profile entry, gesture/battery gates, credential commit ordering, cancellation, timeout, write failure, and teardown. `CredentialCommitGate` retains the existing NVS profile until Wi-Fi plus IP are verified. The shared `ManagerLifecycle` seam exercises the pinned SDK success path: leave SDK auto-stop enabled for the phone app, receive `NETWORK_PROV_END`, request manager deinit once, then become inactive on `NETWORK_PROV_DEINIT`. Tests cover idempotent stop/deinit, cancel teardown, exactly-once profile save, and a last-resort 45-second fallback (SDK's documented 30-second maximum plus 15 seconds of margin). This seam does not emulate Espressif's callback scheduler or replace hardware acceptance.

For a local build, use only the placeholder secrets file and the production profile:

```sh
cp kai/firmware/src/secrets.example.h kai/firmware/src/secrets.h
pio run -d kai/firmware -e sticks3
rm kai/firmware/src/secrets.h
```

The CI production check verifies the `sticks3` sources do not define `JELLII_CJK_DIAGNOSTICS`, contain `CJK SHOW`, or include the opt-in CJK diagnostic strings in the firmware image. The build uses placeholder secrets only; it does not upload firmware or contact Gemini.

## Footprint and verification

The pinned production `sticks3` build passed in [integration workflow run #29](https://github.com/smallgun01/amusant/actions/runs/39424197184). The workflow uses placeholder secrets only and does not upload firmware or contact Gemini.

| Profile | Firmware image | Program Flash | Static RAM |
| --- | ---: | ---: | ---: |
| `sticks3` | 2,430,192 B | 2,396,211 / 3,342,336 B (71.7%) | 79,616 / 327,680 B (24.3%) |

Runtime free heap, battery draw, Android interoperability, and reconnection to a real AP are not measurable from this offline build and remain part of owner-supervised physical acceptance. The separate CJK diagnostic patch is not applied or built by this production workflow.

## Rollback

On the reconstructed Kai tree, reverse only W2b-1 to return to the merged W1.5 + production CJK state:

```sh
git -C kai apply -R ../patches/kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch
```

This source rollback does not flash a device, erase NVS, or alter the saved W1 profile. Restoring a device image is a separate owner-authorized action.
