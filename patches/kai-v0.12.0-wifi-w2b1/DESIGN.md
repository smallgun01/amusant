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

Association has a 15-second IP-acquisition deadline. The 180-second outer window bounds PoP entry and retries; manager teardown gets at most 30 additional seconds before a software restart returns to normal boot. USB removal does not end an owner-triggered session; it continues on battery and is subject to the low-battery abort policy. The legacy first-boot USB provisioning behavior still closes if USB is removed.

The provisioning service uses a small portable `CredentialCommitGate` after it dequeues `CRED_SUCCESS`. Its `onProviderDeinitialized()` transition keeps a successful candidate pending while Wi-Fi association/IP is checked, even if the native provider's `DEINIT` callback is dequeued first. The host suite drives that same gate through success → deinit → IP, success → deinit → IP timeout, and cancellation/low-battery/window-expiry sequences. It verifies one commit maximum and old-profile retention. This is a deterministic event-order seam around the real service decision; it does not model Espressif's callback scheduling or certify radio behavior.

The profile remains the single authoritative credential record. Provisioning explicitly uses RAM-only ESP Wi-Fi storage and starts with an empty station config, so the candidate is not separately committed by `esp_wifi`. `wifi_config::save()` writes the complete profile as one NVS blob only after association and IP acquisition. If the save reports failure, the application keeps its old profile state and returns to the normal boot. No Relay/cloud reachability is required to accept Wi-Fi.

## Review points and limits

Please review the reboot-based one-shot setup boot, the 5-second Button B gesture, the 20%/15% battery thresholds, and the 180-second/three-attempt limits before physical acceptance. Existing host tests and pinned builds can verify the policy and firmware integration; they cannot establish Android UX, NimBLE heap margin, battery draw, RF timing, or real old-profile reconnection. Those remain on the owner-supervised hardware checklist. No flash or live call is part of this PR.
