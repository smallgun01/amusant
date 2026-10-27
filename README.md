# Project Amusant

**Current (2026-10-27): Grok-first source and Owner-reported OCI cutover.** Source baseline [main@84ac24b](https://github.com/smallgun01/amusant/commit/84ac24bd236cff6222f1281814afa39486225774); installed image baseline e615c06.

Grok profile: [compose.grok.yaml](deploy/oci-a1/compose.grok.yaml) selects `grok_callback`, Relay + Egress Proxy, SQLite v10 TaskStore/Inbox; no active Hermes or Facade. [#138](https://github.com/smallgun01/amusant/pull/138) merged ACP/DynamoDB lazy-import separation and optional Boto3; it is source-only until explicitly released. Hermes/ACP remain explicit compatibility choices; never automatic fallback.

## OCI and acceptance

Owner-reported [#132 second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695): Grok Relay + Egress Proxy **2/2 Healthy**, fresh SQLite v10, legacy four containers removed without volumes, old v8 data retained. Installed images remain pinned to `e615c06`; newer main changes are not deployed by source merge. [#132 D2e](https://github.com/smallgun01/amusant/issues/132#issuecomment-6445970730) static mount/restart posture PASS, **REBOOT_RECOVERY=NOT_TESTED**. [Gate 2A](https://github.com/smallgun01/amusant/issues/132#issuecomment-6446503568) private callback 405/401/404 PASS; [Gate 2B](https://github.com/smallgun01/amusant/issues/132#issuecomment-6446621484) Cloudflare DNS/TCP 7844 PASS, not a configured tunnel. Public Callback/device ingress, real Routine/Provider E2E and new-PTT spoken Inbox remain NOT TESTED. Current OCI uses Owner-local two-file Compose (`compose.grok.yaml` + `compose.grok.local.yaml`); do not restart from the historical single-file example.

A Callback receipt is not native Grok terminal state or remote Stop; unknown dispatch is not retried. Result Contract v2 remains bounded; speak only on explicit later PTT. [SPEC](SPEC.md) · [STATUS](STATUS.md) · [PLAN](PLAN.md) · [WS Index](docs/project-management/workstreams.md) · [Historical baseline](docs/history/20261025-documentation-baseline.md).

[PR #139](https://github.com/smallgun01/amusant/pull/139) changes source templates; adoption of its 0.5 CPU / Zephyr / Search settings is not inferred from merge. Preserve Owner-local effective Compose until separately approved replacement.
