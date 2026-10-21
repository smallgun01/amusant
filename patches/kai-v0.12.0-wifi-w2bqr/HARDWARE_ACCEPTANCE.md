# W2b-QR optical experiment — pending owner acceptance

The PR #95 BLE teardown path has passed owner-supervised hardware acceptance. This revision is a combined optical experiment: QR v5-L at 3 pixels per module (135×135 with a full four-module white quiet zone) and QR-page backlight 45. The manual PoP page returns to the normal awake brightness of 90. Because size and brightness change together, this first run cannot attribute an improvement to either factor independently. No physical StickS3 was flashed for this patch, and the QR remains **physical scan NO-GO / production acceptance pending**.

1. In an owner-supervised run, enter setup with the existing five-second Button B gesture while on USB power. Confirm the QR fills the 135px display height, is horizontally centered, has no text or overlays, and retains a complete white quiet zone.
2. With the official Espressif Android provisioning app, attempt several direct scans from practical distances and times under ordinary room lighting. Do not type the PoP. A camera-recognition result or `invalid format` alone is not a pass; the app must accept the QR and connect using Security 1 with the active session PoP.
3. Tap Button A briefly; confirm the manual 26-character PoP page appears at normal awake brightness. Tap again; confirm the same QR and session return with QR-only brightness.
4. Tap Button B briefly; confirm setup cancels, the QR and manual PoP disappear, and the saved Wi-Fi profile is retained.
5. Repeat entry after a successful Wi-Fi replacement and confirm the next session QR differs without exposing it in screenshots or logs.
6. On a QR-assisted success, confirm the PR #95 sequence reaches CRED_SUCCESS → END → DEINIT → setup return without drain fallback; the QR and manual PoP must clear after success. Also check timeout and battery abort cleanup.
7. Confirm first-boot USB recovery, ordinary PTT, Opus audio, TLS-PSK, Bearer authorization, and deep sleep still behave as before.

Record a physical scan as **PASS** only after the Android app accepts the displayed QR and establishes the provisioning session; then confirm END/DEINIT/setup return. A host decoder test verifies the payload and QR encoding only. If any direct scan fails, keep manual PoP as the recovery path and record the experiment as NO-GO. This is not a production PASS until the owner accepts the physical results.

Do not copy the QR, PoP, or Wi-Fi credentials into logs, screenshots, issue comments, or PR artifacts.
