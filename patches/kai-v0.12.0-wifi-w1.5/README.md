# WiFi W1.5 — Secure BLE provisioning feasibility PoC

This package adds an isolated downstream patch on top of Kai v0.12.0, Jellii, and merged WiFi W1. It keeps W1's USB Serial setup as the recovery path and writes successful BLE credentials only to W1's `amu_wifi/profile` record.

## Compatibility decision

The exact locally installed baseline was inspected before implementation:

- Kai v0.12.0: `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`
- pioarduino PlatformIO platform: `55.03.312-1` (installed manifest reports `55.03.312`)
- Arduino-ESP32: `3.3.12`
- ESP-IDF libraries: `5.5.5+sha.b774170ff46`
- board profile: StickS3 / ESP32-S3, `qio_opi`, 8 MB flash, 8 MB octal PSRAM, `default_8MB.csv`

The pinned SDK includes `WiFiProv.h`, native `network_provisioning` manager and BLE headers, ESP32-S3 NimBLE, Wi-Fi provisioning, and the Security 1 compile option. The exact Arduino `WiFiProv.cpp` wrapper logs its PoP at info level and decides whether to start from Espressif Wi-Fi's own provisioned flag. For those reasons the implementation compiles against `WiFiProv.h` but calls the same pinned native provisioning manager directly. It avoids wrapper logging and does not use the wrapper's NVS-based provisioning decision. The selected BLE service UUID matches the pinned Arduino wrapper and official Espressif app protocol.

The official Espressif provisioning Android source supports Security 1 and a caller-supplied proof of possession. Security 1 authenticates the device with PoP and encrypts provisioning traffic; Security 0 is not used. See [ESP-IDF provisioning security](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32/api-reference/provisioning/provisioning.html) and the [official Android app source](https://github.com/espressif/esp-idf-provisioning-android).

## Provisioning and storage behavior

- BLE starts only when there is no valid W1 profile and USB power is present: on first unconfigured boot or after the owner's USB `WIFI CLEAR` command. It does not start on battery.
- Each setup session gets 128 bits from the ESP RNG after Wi-Fi RF startup, encoded as 26 Crockford Base32 characters. The PoP is shown on the local display only, is never printed to Serial or included in a QR code, and is wiped when the session ends. It is unique to this device's setup session; it is not a shared or hard-coded value.
- The setup window is limited to 120 seconds and ends early if USB power is removed. The BLE scheme uses the `FREE_BLE` handler. ESP-IDF's default success flow allows its Android client to query the connection result, then automatically stops provisioning (up to 30 seconds); the firmware waits for manager deinitialization before resuming normal Wi-Fi.
- Wi-Fi storage is set to RAM before provisioning. A blank RAM-only station config prevents a legacy `esp_wifi` profile from being used. Received credentials are held in RAM and copied into `amu_wifi/profile` only after `NETWORK_PROV_WIFI_CRED_SUCCESS`. Failed Wi-Fi credentials remain retryable and are not written. No NVS erase API is called.
- BLE blocks normal Relay connection management while its manager is active. After successful teardown, the W1 profile is loaded into the ordinary Wi-Fi path; PTT, Opus, TLS-PSK, Bearer, Relay, LCD, and Deep Sleep code paths are otherwise unchanged.
- `WIFI SET` cancels an active BLE session and takes precedence. `WIFI CLEAR` removes only `amu_wifi/profile`; it starts a fresh BLE window when USB remains attached. Serial commands and the W1 helper remain available as recovery.

The W1 first-boot NVS case is handled before BLE auto-start: a read-only open of a missing `amu_wifi` namespace is followed by a non-writing writable open and ordinary key validation. Missing key means unconfigured; malformed data remains invalid. No NVS key is committed during boot reads.

## Reproducible patch application

From the Amusant repository root:

```sh
git clone --no-tags https://github.com/zbruceli/kai.git kai
git -C kai checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git -C kai apply patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch
git -C kai apply patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch
git -C kai apply --check patches/kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch
git -C kai apply patches/kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch
```

Build only; do not upload or flash:

```sh
cp kai/firmware/src/secrets.example.h kai/firmware/src/secrets.h
pio run -d kai/firmware -e sticks3
```

Delete the local `secrets.h` after the build. It is git-ignored and must not be committed. The offline host suite is:

```sh
python3 patches/kai-v0.12.0-wifi-w1.5/tests/run_host_tests.py --firmware-root kai/firmware
```

The C++ fake-NVS suite covers first boot with no namespace, no credential write before success, wrong-PoP no-event behavior, failed Wi-Fi credential retry, success persistence, PoP encoding, timeout, USB removal, and the configured-profile BLE gate. Python helper tests cover the existing USB recovery protocol. The host tests do not contact hardware or a network. The CI workflow verifies the W1.5 patch applies after Jellii and W1, then runs both suites.

## Footprint and test results

The exact pinned `sticks3` build completed successfully:

| Image | Flash | Static RAM |
| --- | ---: | ---: |
| Merged W1, as recorded in [PR #59](https://github.com/smallgun01/amusant/pull/59) | 1,616,983 B | 76,996 B |
| W1.5 PoC | 1,909,551 B | 79,536 B |
| Change | +292,568 B (+18.1%) | +2,540 B (+3.3%) |

The W1.5 image uses 57.1% of the 3,342,336-byte application partition. The linker RAM number is static image usage; NimBLE is configured for internal memory, but dynamic BLE heap/free-heap, scan time, power, and sleep behavior need the separately authorized physical acceptance and are not inferred from a build. The local build used the exact checked-out Opus commit `c02de6316dddbb9dffdf976c159c4eaacecdf12f` because this Windows host's Git credential provider refused the HTTPS fetch. The temporary build config pointed to that local checkout; the tracked pinned `platformio.ini` was unchanged.

> Results: PlatformIO `sticks3` build **PASS** (Arduino-ESP32 3.3.12 / ESP-IDF libraries 5.5.5); Python USB-helper tests **5/5 PASS** locally; GitHub Actions W1.5 host workflow **PASS** (C++ fake-NVS/BLE-flow and Python suites). This workstation has no host C++ compiler.

## Limits and rollback

This is a compile-tested, offline-flow PoC. It does not claim Android-to-device operation, wrong-PoP behavior on hardware, AP retry timings, dynamic heap margin, power behavior, or real PTT regression. Those checks remain for the separately gated physical acceptance. No device is flashed by this package.

Rollback W1.5 by reversing only this patch:

```sh
git -C kai apply -R patches/kai-v0.12.0-wifi-w1.5/kai-v0.12.0-wifi-w1.5.patch
```

The merged W1 code, its USB recovery commands, its one-profile NVS data, and the Relay configuration remain in place. No AWS, Relay, or cloud ingress files are part of this package.
