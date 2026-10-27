# Relay

## Current Grok-first source profile

[deploy/oci-a1/compose.grok.yaml](../deploy/oci-a1/compose.grok.yaml) explicitly selects `AMUSANT_SLOW_EXECUTOR=grok_callback`, runs Relay + Egress Proxy and uses SQLite v10 TaskStore/Inbox. Device WSS and Callback listeners are separate; Compose publishes no host ports. Public ingress remains an independent #132 Owner gate.

The last Owner-reported host inventory still has the legacy Relay/Facade/Hermes/Egress stack installed. Grok images were published/pre-pulled and static staging configuration was checked, but no Grok Compose cutover is reported. See [STATUS](../STATUS.md).

## Explicit selection and compatibility

The Grok profile sets `AMUSANT_SLOW_EXECUTOR=grok_callback`; the application fallback remains `hermes_runs`. `openab_acp` and `hermes_runs` remain explicit compatibility/rollback selections. No ambiguous Routine send is retried or sent to Hermes.

The Grok profile uses `AMUSANT_TASK_STORE=sqlite`. `dynamodb` remains an explicit legacy AWS path, not part of the Grok profile. Do not point Grok at the legacy ledger or migrate schemas for source tidiness.

## H2 import separation

At `main@e615c06`, `application.py` imports ACP even when Grok is selected, `hermes_result.py` gets shared limits from the ACP module, and `store_factory.py` imports `DynamoDbTaskStore` at module load. Boto3 is currently a base dependency. H2 separately defers backend imports, moves shared limits to a neutral module and makes Boto3 optional. It keeps old modules/tests and does not relocate them.

A Callback is a durable receipt, not proof of native Run termination, remote Stop or document download from a `box:` path. Keep bounded results and require an explicit later request before speech.

The previous ACP/DynamoDB/Hermes/P1 mock and dated AWS Relay instructions are pinned in the [immutable e615c06 README](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/relay/README.md). ADRs, tests, templates and historical evidence remain available from the [history index](../docs/history/20261025-documentation-baseline.md).