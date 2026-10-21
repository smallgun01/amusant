# W2b-QR hardware acceptance — pending

The PR #95 BLE teardown path has passed owner-supervised hardware acceptance. No physical StickS3 was flashed for the W2b-QR patch; the QR-specific checks remain pending and require owner-authorized hardware acceptance:

1. Enter owner setup with the existing five-second Button B gesture while on USB power.
2. Confirm the screen shows one stable QR for the active BLE session.
3. Scan with the official Espressif Android provisioning app without typing the PoP. Confirm it discovers the current `PROV_XXXXXX` service and uses Security 1 with the active session PoP.
4. Tap Button A briefly; confirm the manual 26-character PoP page appears. Tap again; confirm the same QR returns without changing.
5. Tap Button B briefly; confirm setup cancels, the QR and manual PoP disappear, and the saved Wi-Fi profile is retained.
6. Repeat the entry after a successful Wi-Fi replacement and confirm the next session QR differs.
7. On a QR-assisted success, confirm the PR #95 sequence reaches CRED_SUCCESS → END → DEINIT → setup return without drain fallback; the QR and manual PoP must clear after success.
8. Confirm first-boot USB recovery, ordinary PTT, Opus audio, TLS-PSK, Bearer authorization, and deep sleep still behave as before.

Record a physical scan as **PASS** only after the Android app establishes the provisioning session. A successful host decoder test verifies payload and QR encoding only; it does not claim that the StickS3 display is scannable at the intended distance or lighting.

Do not copy the QR, PoP, or Wi-Fi credentials into logs, screenshots, issue comments, or PR artifacts.
