# Owner-supervised W2b-1 physical acceptance checklist

Use this checklist only after PR review and separate owner authorization for the physical firmware/device session. This PR and checklist do not authorize a flash, NVS erase, Wi-Fi credential entry, or live service call by the coding agent.

## Before the session

- [ ] Confirm the exact reviewed firmware and that the device is still on the known-good W1 recovery baseline.
- [ ] Preserve the known-good W1 app image, current NVS state, and an 8 MiB full-device backup before the session. Do not erase NVS or use a full-chip erase.
- [ ] For a separately authorized flash, use `sticks3_ble_diag` and write only the app image at offset `0x10000`; preserve the bootloader, partition table, and NVS.
- [ ] Record battery level, USB power state, free internal/PSRAM heap if available, and the connected Wi-Fi state without recording SSID passwords or PoP in shared logs.
- [ ] Use an isolated test AP or the owner-selected phone hotspot. Do not infer Relay/cloud reachability from a Wi-Fi association.

## Gesture and conflicts

- [ ] From configured and connected Wi-Fi, hold side Button B continuously for 5 seconds while Idle. Confirm the midpoint prompt and dedicated setup reboot.
- [ ] Confirm a B click and a shorter B hold retain their existing side-button actions. When the display is asleep, confirm the first B press only wakes the display; use a second hold for setup.
- [ ] Confirm Button B does not enter setup during active PTT/listening, early talk, voice playback, or an already active BLE session. Confirm front Button A still starts/interrupts PTT normally.
- [ ] Confirm an ordinary reboot after the setup boot does not advertise BLE.

## Setup, rollback, and success

- [ ] In the dedicated setup boot, verify BLE Security 1 and the current 26-character per-session PoP on the local LCD. Confirm Serial logs do not contain PoP or Wi-Fi credentials.
- [ ] Reject an incorrect PoP; verify no Wi-Fi profile change and that the bounded setup window remains available for retry.
- [ ] Submit an incorrect AP password. After the allowed failures or cancellation, verify the previous profile reconnects after normal reboot and remains the sole saved profile.
- [ ] Cancel with a B click; separately allow the 180-second window to expire. Verify the old profile reconnects in both cases.
- [ ] If USB is removed during an owner-triggered session, verify setup continues on battery above the low-battery abort point; verify setup exits below 15% indicated battery. Confirm USB Serial recovery remains available when attached.
- [ ] Submit valid credentials for a test AP/hotspot. Verify association and a nonzero IP before the firmware reports saved success, then verify normal reboot loads the replacement profile.
- [ ] On home Wi-Fi, verify PTT/Opus/TLS-PSK/Bearer and deep-sleep reconnect. When using a phone hotspot, treat public Relay reachability as a separate WS3/cloud gate.

## Post-success BLE teardown regression

For a diagnostic run, use the separately authorized `sticks3_ble_diag` image, not normal `sticks3`. It reports the compiled SDK auto-stop timeout, callback/dequeue timestamps, queue overflow/drop counts, and manager/session state without credentials or PoP. The CRED_SUCCESS timestamp is when its callback was observed; it does not reveal the SDK's internal timer start or firing. If no END callback arrives after the configured timeout, the log labels it “missing END.” Do not retain raw Android Logcat; record only whether the activity showed success or reported a disconnect. Do not repeat an owner provisioning loop solely to reproduce the unchanged fallback symptom.

- On an owner-approved test session after review of this queue correction, use the Android Espressif provisioning app and a designated 2.4 GHz test AP. Correlate the phone's status response outcome with the CRED_SUCCESS callback observation and END/DEINIT timestamps. Record the `WIFI BLE AUTOSTOP`, `WIFI BLE EVT callback/dequeue`, `WIFI BLE STATE`, save, setup-return/fallback lines, and whether Android displayed success. Keep PoP and Wi-Fi credentials out of logs and notes.
- After valid credentials, confirm Serial reports `WIFI OK SAVED BLE`, then `WIFI BLE SETUP RETURN` and a normal software restart. `WIFI BLE DRAIN FALLBACK` must not appear on the successful path.
- Verify the replacement profile reconnects after normal boot. Also cancel a separate setup attempt and confirm the old profile remains and normal boot returns.
- If END/deinit does not complete before the fallback, record which active-state term remains true and whether each terminal callback was observed versus dequeued. If the app does not receive completion, record the app result relative to its first status poll and device END/DEINIT timestamps. Stop testing and preserve only the redacted Serial sequence; do not erase NVS or flash automatically.

## Record and restore

- [ ] Record time to enter setup, PoP readability/app steps, time to connect, free heap, battery/USB state, and reconnect result. Keep credentials and PoP out of notes and logs.
- [ ] If any rollback check fails, stop acceptance and restore the separately preserved known-good W1 image only under the owner's authorization. Do not clear NVS as a recovery shortcut.

