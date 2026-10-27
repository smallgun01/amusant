# Amusant WS1–WS4 — Current workstreams (2026-10-27)

[STATUS](../../STATUS.md) owns evidence, [SPEC](../../SPEC.md) owns contracts and [#132](https://github.com/smallgun01/amusant/issues/132) owns OCI/ingress gates.

| Lane | Owner scope | Current gate |
| --- | --- | --- |
| WS1 / #47 | StickS3 provisioning, FAST, LCD and physical readback | Prior BLE/QR and FAST evidence retained; Grok external PTT/Spoken E2E unaccepted; [#136](https://github.com/smallgun01/amusant/issues/136) firmware consolidation and [#67](https://github.com/smallgun01/amusant/issues/67) card/display gate remain separate. |
| WS2 / #48 | Generic Slow delegation, one Ledger/Inbox, bounded v2 receipt | [#129](https://github.com/smallgun01/amusant/pull/129) merged Grok callback adapter; native Run state is unknown absent authoritative proof. |
| WS3 / #49 | OCI profile, secure Callback and device ingress | [#132](https://github.com/smallgun01/amusant/issues/132) second cutover 2/2 Healthy; public Callback/device ingress and physical E2E NOT TESTED. |
| WS4 / #50 | Dependencies, security, operations, CI, rollback | [#138](https://github.com/smallgun01/amusant/pull/138) merged optional imports; OCI runs older image. Static restart/mount PASS; reboot recovery NOT TESTED. |

Owner-reported [#132 second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695): Grok Relay + Egress Proxy **2/2 Healthy**, fresh SQLite v10, legacy four containers removed without volumes, old v8 data retained. Installed images remain pinned to `e615c06`; newer main changes are not deployed by source merge. [#132 D2e](https://github.com/smallgun01/amusant/issues/132#issuecomment-6445970730) static mount/restart posture PASS, **REBOOT_RECOVERY=NOT_TESTED**. [Gate 2A](https://github.com/smallgun01/amusant/issues/132#issuecomment-6446503568) private callback 405/401/404 PASS; [Gate 2B](https://github.com/smallgun01/amusant/issues/132#issuecomment-6446621484) Cloudflare DNS/TCP 7844 PASS, not a configured tunnel. Public Callback/device ingress, real Routine/Provider E2E and new-PTT spoken Inbox remain NOT TESTED. Current OCI uses Owner-local two-file Compose (`compose.grok.yaml` + `compose.grok.local.yaml`); do not restart from the historical single-file example.

Do not create a second TaskStore, run Hermes and Grok as an automatic chain, resend ambiguous Webhooks, relocate legacy modules merely for tidiness, or treat H1/H2 docs/CI as device acceptance. [PLAN](../../PLAN.md) gives the minimal next sequence.
