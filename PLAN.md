# Amusant — Current execution plan (2026-10-30 evidence snapshot)

Grok-first source selected; Owner-reported OCI second cutover completed. [#137](https://github.com/smallgun01/amusant/pull/137) H1 docs and [#138](https://github.com/smallgun01/amusant/pull/138) H2 optional-import isolation merged; neither means the current OCI images were upgraded. Optional legacy file relocation H3 is **not required** for M1.

## Execution sequence (no live authorization implied)

1. Check source [main@96ae859](https://github.com/smallgun01/amusant/commit/96ae8594d11f25f11349420a6979be9387a99630) against **actual OCI digest/config** before claiming #143/#144/#147/#149/#151 deployed. Preserve Owner-local two-file Compose and latest tested rollback posture.
2. Finish [#145](https://github.com/smallgun01/amusant/issues/145) routing policy acceptance: FAST public questions; Live-originated `delegate_task` for Agent-native context and substantive work; `check_task_status` only for Relay-admitted tasks. Current synthetic tests and #151 merge are not production PASS.
3. Re-read the **already saved** G4 Inbox result on physical StickS3 to verify [#143](https://github.com/smallgun01/amusant/pull/143) `read→spoken` after an authorized update; do **not** submit a new paid Grok task merely to retest playback.
4. Complete [#132](https://github.com/smallgun01/amusant/issues/132) negative authentication/reconnect and reboot/persistent-host-firewall checks under distinct Owner gates. Record device transmission, server ACK, real Callback and native state separately.
5. Review [#146](https://github.com/smallgun01/amusant/issues/146) residual webhook-key / Grok-hosted outbound access exposure and maintain bounded provider cost. Defer new services, app/admin UI and optional legacy module relocation until M1 acceptance.

## Evidence boundary

The [second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695) and [G4 10/28 OCI FAST physical PASS](https://github.com/smallgun01/amusant/issues/132#issuecomment-6465592941) were reported PASS. [G4 10/28 real callback and physical speech](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361) establishes real external Callback and audible saved-result readback, but durable `spoken` failed and this was **not** a Gemini Live-created Grok task. CI/merged source ≠ deployed ≠ physical acceptance. See the dated matrix in [STATUS](STATUS.md). Current main at review: [main@96ae859](https://github.com/smallgun01/amusant/commit/96ae8594d11f25f11349420a6979be9387a99630).

[STATUS](STATUS.md) owns acceptance facts; [SPEC](SPEC.md) owns v2 result and Owner/task contract. Hermes G0 and legacy SQLite migration [#124](https://github.com/smallgun01/amusant/pull/124) are not Grok staging prerequisites.
