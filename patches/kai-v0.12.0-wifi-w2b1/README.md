# Wi-Fi W2b-1 — configured BLE re-provisioning

This downstream firmware patch implements Issue #91 on the exact Kai v0.12.0 pin. It adds a 5-second Button B setup gesture and a controlled, one-shot reboot into authenticated BLE provisioning. The existing `amu_wifi/profile` record remains unchanged until the replacement has joined Wi-Fi and acquired a nonzero IP address. USB Serial remains the recovery path.

The architecture and owner review points are in [DESIGN.md](DESIGN.md). This package does not change AWS, Relay, cloud ingress, the PTT/Opus/TLS-PSK/Bearer path, or normal deep-sleep behavior. It does not flash a device or make a paid/live call.

## Pinned baseline and layer order

Target Kai commit: `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`.

From the Amusant repository root, apply layers in this order:

1. Kai v0.12.0 at the exact commit above.
2. `patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch`.
3. `patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch`.
4. `patches/kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch`.
5. Run the integrated CJK adapter: `python3 patches/sticks3-w15-cjk/apply_integration.py --kai kai`.
6. Apply the existing opt-in CJK diagnostic patch. The normal `sticks3` profile leaves its runtime diagnostic disabled; this is the source context used by the combined integration workflow.
7. Apply this W2b-1 patch.

```sh
git clone --no-tags https://github.com/zbruceli/kai.git kai
git -C kai checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C kai apply patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch
git -C kai apply patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch
git -C kai apply patches/kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch
python3 patches/sticks3-w15-cjk/apply_integration.py --kai kai
git -C kai apply patches/kai-v0.12.0-cjk-diagnostic/kai-v0.12.0-cjk-diagnostic.patch
git -C kai apply --check patches/kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch
git -C kai apply patches/kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch
git -C kai diff --check
```

The patch is based on merged W1/W1.5 and integrated CJK source. It is a downstream patch only; it does not vendor or modify upstream Kai.

## Offline tests and build

The combined GitHub Actions workflow applies every layer above and runs:

```sh
python3 patches/kai-v0.12.0-wifi-w1.5/tests/run_host_tests.py --firmware-root kai/firmware
python3 patches/kai-v0.12.0-cjk-diagnostic/tests/run_host_tests.py \
  --firmware-root kai/firmware \
  --wifi-tests patches/kai-v0.12.0-wifi-w1.5/tests
python3 patches/kai-v0.12.0-wifi-w2b1/tests/run_host_tests.py \
  --firmware-root kai/firmware \
  --wifi-tests patches/kai-v0.12.0-wifi-w1.5/tests
```

The W2b-1 C++ fake-NVS suite covers explicit configured-profile entry, the 5-second gesture eligibility gates, one-shot intent consumption, battery entry/abort thresholds, wrong-PoP no-event behavior, no write before association plus IP, three bad credential attempts, successful single-profile replacement, simulated NVS write failure, cancel/timeout, battery continuation after USB removal, and preservation of the existing USB-only first-boot unplug behavior. A 30-second manager-drain watchdog bounds teardown after success/cancel/error.

For a local build, use only the placeholder secrets file:

```sh
cp kai/firmware/src/secrets.example.h kai/firmware/src/secrets.h
pio run -d kai/firmware -e sticks3
pio run -d kai/firmware -e sticks3_cjk_diagnostic
rm kai/firmware/src/secrets.h
```

`secrets.h` is local and git-ignored. CI builds with `secrets.example.h` placeholders only. The build does not upload firmware or contact Gemini.

## Footprint and verification

The combined PR CI reports `firmware.bin` size and PlatformIO program-flash/static-RAM use for both profiles. Fill in the exact figures after the Draft PR checks finish:

| Profile | Firmware image | Program Flash | Static RAM |
| --- | ---: | ---: | ---: |
| `sticks3` | CI pending | CI pending | CI pending |
| `sticks3_cjk_diagnostic` | CI pending | CI pending | CI pending |

Both profiles must fit the 3,342,336-byte application slot. Runtime free heap, battery draw, Android interoperability, and reconnection to a real AP are not measurable from this offline build and remain part of owner-supervised physical acceptance.

## Rollback

On the reconstructed Kai tree, reverse only W2b-1 to return to the merged W1.5 + CJK state:

```sh
git -C kai apply -R patches/kai-v0.12.0-wifi-w2b1/kai-v0.12.0-wifi-w2b1.patch
```

This source rollback does not flash a device, erase NVS, or alter the saved W1 profile. Restoring a device image is a separate owner-authorized action.

