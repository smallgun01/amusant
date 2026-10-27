# Amusant — Current source, OCI runtime and acceptance

Source baseline: [main@84ac24b](https://github.com/smallgun01/amusant/commit/84ac24bd236cff6222f1281814afa39486225774). [#131](https://github.com/smallgun01/amusant/pull/131) Grok adapter, [#133](https://github.com/smallgun01/amusant/pull/133) image release and [#138](https://github.com/smallgun01/amusant/pull/138) optional-import isolation are merged. New main is **not** the installed e615c06 image. Hermes/ACP/DynamoDB compatibility is retained but not part of Grok Compose.

## OCI current evidence

Owner-reported [#132 second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695): Grok Relay + Egress Proxy **2/2 Healthy**, fresh SQLite v10, legacy four containers removed without volumes, old v8 data retained. Installed images remain pinned to `e615c06`; newer main changes are not deployed by source merge. [#132 D2e](https://github.com/smallgun01/amusant/issues/132#issuecomment-6445970730) static mount/restart posture PASS, **REBOOT_RECOVERY=NOT_TESTED**. [Gate 2A](https://github.com/smallgun01/amusant/issues/132#issuecomment-6446503568) private callback 405/401/404 PASS; [Gate 2B](https://github.com/smallgun01/amusant/issues/132#issuecomment-6446621484) Cloudflare DNS/TCP 7844 PASS, not a configured tunnel. Public Callback/device ingress, real Routine/Provider E2E and new-PTT spoken Inbox remain NOT TESTED. Current OCI uses Owner-local two-file Compose (`compose.grok.yaml` + `compose.grok.local.yaml`); do not restart from the historical single-file example.

## Product gates

G3 native Grok feasibility and isolated callback were previously Owner-reported PASS; this does not establish current Relay-to-public-callback-to-device operation. M1 requires StickS3 PTT → Gemini FAST delegation → Relay → Grok Routine → authenticated Callback → SQLite Inbox → later PTT speech and server-side `spoken` transmission acknowledgment. No current end-to-end PASS is recorded. Device hearing and native Grok terminal state are not proven by receipt or `spoken` flag.

## Work boundaries

[#132](https://github.com/smallgun01/amusant/issues/132) owns ingress/physical acceptance; [#135](https://github.com/smallgun01/amusant/issues/135) owns docs and optional-import hygiene; [#136](https://github.com/smallgun01/amusant/issues/136) owns firmware baseline. [#139](https://github.com/smallgun01/amusant/pull/139) template work is separate from installed config. No automatic fallback, no blind resend, no provider/host action authorized by this file. [SPEC](SPEC.md) · [PLAN](PLAN.md) · [WS Index](docs/project-management/workstreams.md).
