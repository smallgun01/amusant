# W2b-1 / WS1 design: configured Wi-Fi BLE re-provisioning

## Decision for owner review

Use a **controlled software reboot into a one-shot setup boot**. The merged W1.5 path starts the pinned Espressif provisioning manager with `NETWORK_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BLE` and intentionally permits one BLE start attempt per boot. Starting BLE in-place from the ordinary voice runtime would require proving NimBLE teardown/reinitialization and would couple setup to live PTT, audio, Relay, and Wi-Fi state. A dedicated boot avoids that lifecycle assumption and keeps BLE off in the normal configured runtime.

The physical gesture writes a short magic value to RTC no-init memory, clears stale BSSID/channel/Relay hints, and calls `esp_restart()`. On the next boot the value is accepted only with a software-reset reason and is consumed before provisioning starts. The setup boot reloads the existing W1 profile, skips normal Wi-Fi/voice startup, and starts the existing Security 1 manager with an owner-triggered policy. No credentials are copied into the boot intent.

## Gesture and conflicts

- Front Button A remains PTT / interrupt. A held or active PTT, early-talk state, active voice playback, and any active BLE provisioning session reject the setup gesture.
- Side Button B keeps its click and hold behavior. In an eligible Idle or Offline state, a **continuous 5-second hold** enters setup. At 2.5 seconds the LCD says “Hold 5s; release cancels.” Releasing before the trigger retains the existing status-card action for a hold; a short click keeps its existing behavior.
- When the display is asleep, the first B press remains a wake-only action. The user starts the 5-second setup hold after the display is awake.
- If battery power is used, entry requires at least 20% indicated charge and setup aborts below 15%. USB power bypasses those thresholds. This is a conservative firmware policy, not a measured runtime guarantee.

## State and storage transaction

| State | Behavior | W1 profile |
| --- | --- | --- |
| Normal | BLE is not started for a valid configured profile; PTT, Opus, TLS-PSK, Bearer, Relay, and sleep run as before. | Read from `amu_wifi/profile` |
| Setup boot | BLE Security 1 / per-session 128-bit PoP; one attempt per boot; 180-second setup window; USB Serial recovery remains active. | Read and kept unchanged |
| Candidate | The native Wi-Fi stack receives credentials in RAM. Wrong PoP produces no credential event. Bad Wi-Fi credentials are limited to three attempts. | No write |
| Accepted | Require provisioning success, `WL_CONNECTED`, and a nonzero local IP. Then save the single W1 profile blob. | Replace only after association and IP |
| Cancel / failure | Stop provisioning, invalidate cached BSSID/channel/Relay IP, and reboot through the ordinary W1 path. | Existing profile is reloaded |

Association has a 15-second IP-acquisition deadline. The 180-second outer window bounds PoP entry and retries. On `CRED_SUCCESS`, the pinned `network_provisioning` manager starts its auto-stop timer using the compiled `CONFIG_NETWORK_PROV_AUTOSTOP_TIMEOUT` value; the setup drain fallback now derives from that same compile-time value plus a 15-second margin instead of assuming the Kconfig default is 30 seconds. Arduino-ESP32 remains the sole owner of manager deinitialization on `NETWORK_PROV_END`. The native loop records END and waits for the externally delivered DEINIT; its only direct manager deinit remains guarded startup-failure cleanup.

The provisioning service uses a small portable `CredentialCommitGate` after it dequeues `CRED_SUCCESS`. Its `onProviderDeinitialized()` transition keeps a successful candidate pending while Wi-Fi association/IP is checked, including if DEINIT is delivered before the loop consumes END. `ManagerLifecycle` models initialized → stop-requested → service-ended → externally deinitialized; observing END never requests a firmware-owned deinit. A separate bounded queue lane retains END/DEINIT even when the credential queue fills, and preserves callback order with credential events. Overflow fails closed, wipes queued candidate data, requests stop, and still drains terminal events. Cancellation retains both terminal events. Host tests cover queue overflow, terminal ordering and duplicates, reverse END/DEINIT order, cancel, the compiled-timeout fallback boundary, one profile commit, IP timeout, and old-profile retention.

The pinned platform release is pioarduino `55.03.312-1`; its manifest selects Arduino-ESP32 3.3.12 and ESP-IDF 5.5.5. Arduino 3.3.12's component manifest selects `espressif/network_provisioning` 1.0.2 for IDF versions below 6.0, which resolves to source commit `ce7246d167afd522bb7af570edff22e0e163a091`. That exact manager emits END to the app callback, posts END to the default event loop, and then emits DEINIT when its deinit owner runs. Arduino-ESP32 3.3.12 registers `_arduino_event_cb` for `NETWORK_PROV_EVENT` when Wi-Fi is initialized; with `CONFIG_NETWORK_PROV_NETWORK_TYPE_WIFI`, its END branch calls `network_prov_mgr_deinit()`. W1.5 starts the Wi-Fi STA before the manager, so this registered core handler is active in the pinned StickS3 build. The default auto-stop remains enabled to give the phone app its completion-query window. See the [pinned platform manifest](https://github.com/pioarduino/platform-espressif32/blob/55.03.312-1/platform.json), [Arduino 3.3.12 component manifest](https://github.com/espressif/arduino-esp32/blob/3.3.12/idf_component.yml), [Arduino Wi-Fi event handler](https://github.com/espressif/arduino-esp32/blob/3.3.12/libraries/WiFi/src/WiFiGeneric.cpp#L141-L199), [Registry record for network_provisioning 1.0.2](https://components.espressif.com/components/espressif/network_provisioning/versions/1.0.2/readme?language=en), and the [exact pinned manager source](https://github.com/espressif/idf-extra-components/blob/ce7246d167afd522bb7af570edff22e0e163a091/network_provisioning/src/manager.c#L662-L673) plus its [deinit callback path](https://github.com/espressif/idf-extra-components/blob/ce7246d167afd522bb7af570edff22e0e163a091/network_provisioning/src/manager.c#L1952-L2024).

The profile remains the single authoritative credential record. Provisioning explicitly uses RAM-only ESP Wi-Fi storage and starts with an empty station config, so the candidate is not separately committed by `esp_wifi`. `wifi_config::save()` writes the complete profile as one NVS blob only after association and IP acquisition. If the save reports failure, the application keeps its old profile state and returns to the normal boot. No Relay/cloud reachability is required to accept Wi-Fi.

## Teardown diagnostics and Android completion

The opt-in `sticks3_ble_diag` PlatformIO environment compiles serial diagnostics behind `AMUSANT_WIFI_BLE_DIAGNOSTICS`. Logs include callback/dequeue timestamps and counts for credential-success, END, and DEINIT events; overflow/drop counts; manager and session states; active-state contributors; and the exact compiled auto-stop/fallback values. The normal `sticks3` production environment excludes these strings. The diagnostics never print SSID, password, candidate profile contents, service name, or PoP.

In the upstream Android client, `applyWiFiConfig()` waits 2 seconds before its first `get_status` request, then polls every 5 seconds while the station reports Connecting. The activity marks provisioning complete only after a Connected response reaches `deviceProvisioningSuccess()`; a BLE disconnect before that flag is set shows an error. The pinned manager's app callback does not expose each `get_status` endpoint request or encrypted ACK, so device Serial output can bracket the success/END/DEINIT timeline but cannot prove the phone received the status ACK. The physical acceptance checklist therefore requires a real Android success result and retains no raw credential-bearing phone logs.

## Review points and limits

Please review the reboot-based one-shot setup boot, the 5-second Button B gesture, the 20%/15% battery thresholds, and the 180-second/three-attempt limits before physical acceptance. Existing host tests and pinned builds can verify the policy and firmware integration; they cannot establish Android UX, NimBLE heap margin, battery draw, RF timing, or real old-profile reconnection. Those remain on the owner-supervised hardware checklist. No flash or live call is part of this PR.

