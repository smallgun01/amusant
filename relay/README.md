# Relay

## Current Grok-first source profile

[deploy/oci-a1/compose.grok.yaml](../deploy/oci-a1/compose.grok.yaml) explicitly selects `AMUSANT_SLOW_EXECUTOR=grok_callback`, runs Relay + Egress Proxy and uses SQLite v10 TaskStore/Inbox. Device WSS and Callback listeners are separate; Compose publishes no host ports. Public ingress remains an independent #132 Owner gate.

[#132 records](https://github.com/smallgun01/amusant/issues/132#issuecomment-6439306188) the first Owner Stop-and-Replace attempt failed when Grok `egress_proxy` became unhealthy and Relay remained `Created`; automatic rollback restored the legacy Relay/Facade/Hermes/Egress stack 4/4 healthy. An [isolated proxy diagnostic](https://github.com/smallgun01/amusant/issues/132#issuecomment-6439495767) passed after a local-only CPU-limit increase from 0.1 to 0.5; throttling is suspected, not confirmed. [Revised retry readiness](https://github.com/smallgun01/amusant/issues/132#issuecomment-6439563486) is recorded, but no later cutover. Legacy 4/4 healthy remains installed; no successful Grok deployment, public ingress, live task or device E2E PASS is recorded. See [STATUS](../STATUS.md).

## Explicit selection and compatibility

The Grok profile sets `AMUSANT_SLOW_EXECUTOR=grok_callback`; the application fallback remains `hermes_runs`. `openab_acp` and `hermes_runs` remain explicit compatibility/rollback selections. No ambiguous Routine send is retried or sent to Hermes.

The Grok profile uses `AMUSANT_TASK_STORE=sqlite`. `dynamodb` remains an explicit legacy AWS path, not part of the Grok profile. Do not point Grok at the legacy ledger or migrate schemas for source tidiness.

## H2 import separation

At `main@e615c06`, `application.py` imports ACP even when Grok is selected, `hermes_result.py` gets shared limits from the ACP module, and `store_factory.py` imports `DynamoDbTaskStore` at module load. Boto3 is currently a base dependency. H2 separately defers backend imports, moves shared limits to a neutral module and makes Boto3 optional. It keeps old modules/tests and does not relocate them.

A Callback is a durable receipt, not proof of native Run termination, remote Stop or document download from a `box:` path. Keep bounded results and require an explicit later request before speech.

The previous ACP/DynamoDB/Hermes/P1 mock and dated AWS Relay instructions are pinned in the [immutable e615c06 README](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/relay/README.md). ADRs, tests, templates and historical evidence remain available from the [history index](../docs/history/20261025-documentation-baseline.md).
