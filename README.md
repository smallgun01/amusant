# Project Amusant

**2026-10-30 milestone: Owner-observed M1 physical SLOW functional E2E PASS.** Source HEAD at review: [main@eecd4c2](https://github.com/smallgun01/amusant/commit/eecd4c20c4e721d7dcac9f0fae96f6d2fc728d91). The Owner reports the post-#151 OCI Relay release deployed with Relay + Egress Proxy healthy; this is not blanket operational readiness or independent verification of every live setting.

Grok profile: [compose.grok.yaml](deploy/oci-a1/compose.grok.yaml) selects `grok_callback`, Relay + Egress Proxy, SQLite v10 TaskStore/Inbox; no active Hermes or Facade. [#138](https://github.com/smallgun01/amusant/pull/138) merged ACP/DynamoDB lazy-import separation and optional Boto3; it is source-only until explicitly released. Hermes/ACP remain explicit compatibility choices; never automatic fallback.

## OCI and acceptance

The [second Grok OCI cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695) established two healthy services and SQLite v10. The [2026-10-28 FAST physical PASS](https://github.com/smallgun01/amusant/issues/132#issuecomment-6465592941) and [direct-Relay Grok readback](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361) were earlier, partial gates; the latter did not produce a server-side `spoken` ACK.

**2026-10-30 physical M1 functional PASS (Owner-observed):** real StickS3 PTT → Gemini Live selected `delegate_task` → Relay durable admission → genuine Grok Routine → authenticated HTTPS Callback → SQLite v10 Inbox → later explicit PTT → audible Gemini Live result. For one newly admitted task, a read-only SQLite check confirmed `status=completed`, `reported_outcome=completed`, callback present, saved speech present and `delivery_state=spoken`. This is a real post-#143 Spoken ACK observation, not a hardware playout ACK. Additional Owner-observed tasks covered a follow-up report revision, read-only Gmail summary and continuation of an earlier Grok-native itinerary; these prove useful functional behavior, but independent native history access and per-task `spoken` ACKs were not audited for every case.

**Still open:** #132 operational DNS/firewall persistence, reconnect/reboot, negative-auth and sustained-ingress gates; #145 repeatability/telemetry; native Grok terminal state remains `unknown`. A successful Callback is the Bot's report, not independent native completion.

A Callback receipt is not native Grok terminal state or remote Stop; unknown dispatch is not retried. Result Contract v2 remains bounded; speak only on explicit later PTT. [SPEC](SPEC.md) · [STATUS](STATUS.md) · [PLAN](PLAN.md) · [WS Index](docs/project-management/workstreams.md) · [Historical baseline](docs/history/20261025-documentation-baseline.md).

[Merged PR #139](https://github.com/smallgun01/amusant/pull/139) updated source templates; actual OCI adoption of its 0.5 CPU / Zephyr / Search settings cannot be inferred from a merge. Preserve Owner-local effective Compose until separately approved replacement.
