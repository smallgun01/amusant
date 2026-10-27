# Amusant — Current execution plan (2026-10-27)

Grok-first source selected; Owner-reported OCI second cutover completed. [#137](https://github.com/smallgun01/amusant/pull/137) H1 docs and [#138](https://github.com/smallgun01/amusant/pull/138) H2 optional-import isolation merged; neither means the current OCI images were upgraded. Optional legacy file relocation H3 is **not required** for M1.

## Execution sequence

1. Preserve [#132 second cutover evidence](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695) and verified Owner-local two-file Compose configuration. Do not use an old single-file restart recipe.
2. Review [#139](https://github.com/smallgun01/amusant/pull/139) source-template settings separately; merge and actual OCI adoption are distinct decisions.
3. Under separate Owner authorization, complete secure public Callback route and independent StickS3 TCP/TLS-PSK ingress. No provider call is authorized by this document.
4. Run bounded real Grok task and later explicit PTT readback only after approved ingress and cost gates. Capture Callback receipt, TaskStore/Inbox, transmission `spoken`, unknown native state and failure/reconcile behavior separately.
5. Verify reboot recovery, off-host backup/restore, ingress disable/revocation and sustained resource/FAST latency under approved tests; do not expand tooling before M1.

## Evidence boundary

Owner-reported [#132 second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695): Grok Relay + Egress Proxy **2/2 Healthy**, fresh SQLite v10, legacy four containers removed without volumes, old v8 data retained. Installed images remain pinned to `e615c06`; newer main changes are not deployed by source merge. [#132 D2e](https://github.com/smallgun01/amusant/issues/132#issuecomment-6445970730) static mount/restart posture PASS, **REBOOT_RECOVERY=NOT_TESTED**. [Gate 2A](https://github.com/smallgun01/amusant/issues/132#issuecomment-6446503568) private callback 405/401/404 PASS; [Gate 2B](https://github.com/smallgun01/amusant/issues/132#issuecomment-6446621484) Cloudflare DNS/TCP 7844 PASS, not a configured tunnel. Public Callback/device ingress, real Routine/Provider E2E and new-PTT spoken Inbox remain NOT TESTED. Current OCI uses Owner-local two-file Compose (`compose.grok.yaml` + `compose.grok.local.yaml`); do not restart from the historical single-file example.

[STATUS](STATUS.md) owns acceptance facts; [SPEC](SPEC.md) owns v2 result and Owner/task contract. Hermes G0 and legacy SQLite migration [#124](https://github.com/smallgun01/amusant/pull/124) are not Grok staging prerequisites.
