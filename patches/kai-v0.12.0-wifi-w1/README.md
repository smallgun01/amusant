# WiFi W1 — Dynamic Wi-Fi / NVS

This package adds one USB-provisioned Wi-Fi profile to the Kai v0.12.0 firmware as a reproducible downstream patch. It does not vendor Kai into Amusant or change the Relay path.

## Pinned sources

- Kai v0.12.0: `1681fb3c11acd28525d8987bba4b9bf4bc2a288f` (MIT)
- Accepted Jellii patch: `patches/kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch`
- This W1 patch: `patches/kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch`
- PlatformIO Core 6.2.0; pioarduino platform 55.03.312-1; Arduino-ESP32 3.3.12
- Pinned libraries: M5Unified 0.2.23, M5GFX 0.2.30, WebSockets 2.7.3, ArduinoJson 7.4.3, and codec-opus commit `c02de6316dddbb9dffdf976c159c4eaacecdf12f`

## What changes

- Reads one Wi-Fi profile from the `amu_wifi/profile` NVS key. The fixed-size record is written as one blob and has a CRC32 check for damaged or incomplete data.
- Removes `WIFI_SSID` and `WIFI_PASS` compile-time macros. A missing or invalid record leaves the device Offline and does not start Wi-Fi. The USB console remains available for recovery.
- Adds bounded USB Serial commands: `WIFI STATUS`, `WIFI CLEAR`, and `WIFI SET <ssid-byte-length> <password-byte-length>` followed by exactly that many raw UTF-8 SSID and ASCII password bytes. Status identifies configured/connected/offline state without revealing the SSID, and includes current free internal-heap and PSRAM byte counts.
- Adds a local Python helper. It collects the password with hidden terminal input, sends it only over the selected USB serial port, and filters device output to sanitized status responses.
- A profile change disconnects the old session and clears RTC BSSID/channel and Relay-IP hints. The existing cached fast path remains for the unchanged profile after deep sleep.
- Keeps Relay host, TLS-PSK, Bearer, PTT, Opus, screen and power code paths intact.

SSID input is limited to 1–32 UTF-8 bytes without control characters. Password input is limited to 8–63 printable ASCII bytes. Empty/open-network passwords are rejected. The password crosses USB Serial in plaintext, so provision from a trusted local host and cable. NVS is not encrypted by this patch; the CRC detects accidental damage and does not provide secrecy. Do not enable flash encryption or burn eFuses as part of W1.

`WIFI STATUS` returns one of `WIFI OK CONFIGURED CONNECTED`, `WIFI OK CONFIGURED OFFLINE`, `WIFI OK UNCONFIGURED OFFLINE`, or `WIFI OK INVALID OFFLINE`, followed by `WIFI MEM <internal-free-bytes> <psram-free-bytes>`. A configured/offline result means the profile is stored but the device has not associated with an access point; it does not expose credential bytes. A normal application flash does not erase NVS, so a previously provisioned Wi-Fi profile can remain after flashing this firmware. Use `WIFI CLEAR` to remove only `amu_wifi/profile` and confirm `WIFI OK UNCONFIGURED OFFLINE`; a full-chip erase is not required. Reconfigure with `WIFI SET` over USB.

## Apply and build

From the Amusant checkout, set the patch directory, then start from a clean Kai checkout:

```powershell
$env:AMUSANT_PATCHES = Join-Path (Get-Location) "patches"
$jelliiPatch = Join-Path $env:AMUSANT_PATCHES "kai-v0.12.0-jellii/kai-v0.12.0-jellii.patch"
$wifiW1Patch = Join-Path $env:AMUSANT_PATCHES "kai-v0.12.0-wifi-w1/kai-v0.12.0-wifi-w1.patch"
git clone https://github.com/zbruceli/kai.git
cd kai
git checkout --detach 1681fb3c11acd28525d8987bba4b9bf4bc2a288f
git switch -c downstream/wifi-w1-v0.12.0
git apply --check $jelliiPatch
git apply $jelliiPatch
git apply --check $wifiW1Patch
git apply $wifiW1Patch
Copy-Item firmware/src/secrets.example.h firmware/src/secrets.h
pio run -e sticks3
```

`secrets.h` is local and git-ignored. Fill only the existing Relay settings locally; Wi-Fi values no longer belong in that file. The build command above does not upload firmware.

Install the helper dependency in a local Python environment, then use:

```powershell
$requirements = Join-Path $env:AMUSANT_PATCHES "kai-v0.12.0-wifi-w1/tools/requirements.txt"
$provisioner = Join-Path $env:AMUSANT_PATCHES "kai-v0.12.0-wifi-w1/tools/provision_wifi.py"
python -m pip install -r $requirements
python $provisioner --port COM6 status
python $provisioner --port COM6 set
python $provisioner --port COM6 clear
```

The `set` action prompts for SSID and hidden password input; credentials are not command-line arguments. The firmware emits only generic responses such as configured, saved, cleared, or an error code. Status output does not include the SSID or password.

## Verification

- Patch application check after Jellii: **PASS**. Reverse-apply check for rollback: **PASS**.
- Offline host tests: run both the C++ fake-NVS/parser/power-policy suite and Python provisioning-helper suite with `python patches/kai-v0.12.0-wifi-w1/tests/run_host_tests.py --firmware-root <kai>/firmware`; this requires a host `g++` compiler. The C++ suite covers CRC corruption, invalid/missing NVS recovery, USB clear while the network is offline, configured-but-offline status, the 45-second battery sleep boundary, USB-powered awake behavior, and parser timeout polling/resynchronization for partial lines and payloads.
- GitHub Actions runs the same offline suite on PR changes to the Jellii/W1 patch packages. The latest PR run passed: C++ fake-NVS/parser/power-policy suite plus **5 Python helper tests**. The PlatformIO build below separately compiles the full ESP32-S3 firmware.
- PlatformIO `sticks3` build: **PASS** for both the Jellii-only baseline and W1.
- The build used the example's placeholder Relay values; no Wi-Fi credentials or real secrets were used or committed. This environment's Git for Windows could not run its submodule shell, so codec-opus was staged from its exact pinned commit into a local PlatformIO library path for the build. The committed `platformio.ini` remains unchanged.

### Firmware footprint

PlatformIO reports the application image usage against the default 8 MB StickS3 partition layout:

| Build | Flash used / 3,342,336 bytes | Internal RAM used / 327,680 bytes |
| --- | ---: | ---: |
| Kai v0.12.0 + Jellii | 1,611,863 (48.2%) | 76,692 (23.4%) |
| Kai v0.12.0 + Jellii + W1 | 1,616,983 (48.4%) | 76,996 (23.5%) |
| W1 delta | +5,120 | +304 |

No firmware binary is included. These are build measurements, not evidence of a successful physical-device flash or live connection.

## Owner-supervised device checklist

Run only after separate approval to flash and test hardware:

1. Before or after a normal application flash, query `WIFI STATUS`; verify any pre-existing NVS profile is expected. Run `WIFI CLEAR`, confirm `WIFI OK UNCONFIGURED OFFLINE`, then provision again. Do not use full-chip erase for this recovery check.
2. On battery with Wi-Fi unconfigured or an unavailable AP, leave Kai idle for more than 45 seconds and confirm it enters its normal deep-sleep path. Deliberately wake with button A or B and confirm the USB console responds during the next awake window. If the device is already asleep, plugging in USB alone is not a guaranteed wake source; press a button to wake it. With USB/VBUS power connected while running, leave it idle past 45 seconds and confirm only the display sleeps while the USB console remains responsive. Repeat as needed; this bounded button-wake path avoids endless battery drain.
3. Try both a wrong password and an unavailable AP. Confirm status reports `WIFI OK CONFIGURED OFFLINE`, the device remains recoverable through `WIFI CLEAR`/`WIFI SET` after a deliberate wake or on USB power, and the password never appears in helper output, serial output, or device logs.
4. Provision one 2.4 GHz network and verify the device reaches the same frozen local Amusant Relay using the existing TLS-PSK and Bearer configuration.
5. Cold-restart and enter/exit deep sleep; confirm the profile persists and the same-profile cached reconnect remains functional. Change the profile and verify old BSSID/channel and Relay-IP hints are not reused.
6. Record `WIFI MEM` free internal-heap and PSRAM values at idle, after Wi-Fi/Relay connection, and after PTT playback. Compare these live measurements with the accepted baseline; the PlatformIO static section figures above do not predict runtime heap.
7. Perform a PTT/Opus playback regression against the accepted baseline: press/hold/release PTT, confirm uplink and reply audio complete, then repeat after Wi-Fi recovery. Record audio interruptions, reconnect outcome, and the `WIFI MEM` readings.

These are owner-supervised physical checks and require separate authorization. No runtime heap/PSRAM values or PTT results are claimed here: this PR did not flash a StickS3 or perform a live Relay/PTT test.

## Rollback

From the Kai checkout, remove W1 first:

```powershell
git apply -R $wifiW1Patch
```

To return all the way to the pinned Kai source, then reverse Jellii:

```powershell
git apply -R $jelliiPatch
```

The already accepted Kai/Jellii source and H0 firmware remain the rollback baseline. Any later physical rollback flash requires its own explicit approval.
