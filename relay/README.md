# Relay

## Current Grok-first source profile

**Current evidence (Owner-reported, 2026-10-30):** [#132 second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695) established OCI Grok Relay + Egress Proxy (2/2 Healthy) and SQLite v10. The Owner subsequently reported a post-[#151](https://github.com/smallgun01/amusant/pull/151) Relay release deployed; the exact live image digest was **not independently inspected** here. [#153 physical M1 evidence](https://github.com/smallgun01/amusant/pull/153) records real StickS3 PTT → Gemini Live-selected delegation → Grok Routine → authenticated Callback → SQLite Inbox → later audible PTT result, with one confirmed server-side `delivery_state=spoken` (not hardware playout ACK). The 10/28 direct-Relay readback/ACK failure is historical. **Operational acceptance remains OPEN** under [#132](https://github.com/smallgun01/amusant/issues/132) for DNS-only device hostname, dynamic source-IP policy, persistent host firewall, negative auth, reconnect/reboot and capacity. Preserve the Owner-local two-file Compose; never restart from the historical single-file recipe. See [STATUS](../STATUS.md).

[deploy/oci-a1/compose.grok.yaml](../deploy/oci-a1/compose.grok.yaml) explicitly selects `AMUSANT_SLOW_EXECUTOR=grok_callback`, runs Relay + Egress Proxy and uses SQLite v10 TaskStore/Inbox. Device WSS and Callback listeners are separate; Compose publishes no host ports. Public ingress remains an independent #132 Owner gate.

The first Owner Stop-and-Replace attempt [failed and rolled back](https://github.com/smallgun01/amusant/issues/132#issuecomment-6439306188) to the legacy Relay/Facade/Hermes/Egress stack. An [isolated proxy diagnostic](https://github.com/smallgun01/amusant/issues/132#issuecomment-6439495767) passed after a local-only CPU-limit increase from 0.1 to 0.5; throttling is suspected, not confirmed. That historical attempt predates the second cutover reported above. It does not establish public ingress, a live task or physical device E2E acceptance; see [STATUS](../STATUS.md).

## Explicit selection and compatibility

The Grok profile sets `AMUSANT_SLOW_EXECUTOR=grok_callback`; the application fallback remains `hermes_runs`. `openab_acp` and `hermes_runs` remain explicit compatibility/rollback selections. No ambiguous Routine send is retried or sent to Hermes.

The Grok profile uses `AMUSANT_TASK_STORE=sqlite`. `dynamodb` remains an explicit legacy AWS path, not part of the Grok profile. Do not point Grok at the legacy ledger or migrate schemas for source tidiness.

## H2 import separation and historical image

At the historical `main@e615c06` image baseline, `application.py` imported ACP even when Grok was selected, `hermes_result.py` obtained shared limits from the ACP module, and `store_factory.py` imported `DynamoDbTaskStore` at module load. H2 ([#138](https://github.com/smallgun01/amusant/pull/138)) subsequently merged lazy imports, neutral shared limits and optional Boto3 into source. The Owner later reported a post-#151 OCI Relay release deployed, after #138 merged; the live image digest and effective imports were not independently inspected in this documentation review. The e615c06 comparison is historical, not a claim about today's installed image. Old modules and tests remain in the tree.

For the source profiles, entrypoints, dependencies and validation boundaries, see the [runtime profile index](../docs/architecture/runtime-profiles.md).

A Callback is a durable receipt, not proof of native Run termination, remote Stop or document download from a `box:` path. Keep bounded results and require an explicit later request before speech.

The previous ACP/DynamoDB/Hermes/P1 mock and dated AWS Relay instructions are pinned in the [immutable e615c06 README](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/relay/README.md). ADRs, tests, templates and historical evidence remain available from the [history index](../docs/history/20261025-documentation-baseline.md).
