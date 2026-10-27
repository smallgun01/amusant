# Project Amusant

**Current selection (2026-10-27): Grok-first source profile.** The selected source target is pinned to [main@e615c06](https://github.com/smallgun01/amusant/commit/e615c06259195496ef94d6a6d2c6a038ff06c497), after source PR #131 and image-release preparation PR #133. This is a source decision, not proof the Grok profile is running on OCI.

## Selected source profile

[deploy/oci-a1/compose.grok.yaml](deploy/oci-a1/compose.grok.yaml) explicitly sets `AMUSANT_SLOW_EXECUTOR=grok_callback` and runs only Relay plus Egress Proxy on the existing SQLite v10 TaskStore/Inbox. It does not start Hermes or Facade and does not use DynamoDB or OpenAB ACP.

The profile, not the Python fallback, selects Grok: [relay/application.py](relay/application.py) still defaults to `hermes_runs`. ACP/DynamoDB import separation is a separate H2 code PR under [#135](https://github.com/smallgun01/amusant/issues/135). Hermes remains an explicit alternative/rollback path, not a second backend in this profile and not an automatic fallback. Keep legacy code, tests, ADRs and AWS evidence available.

## Source, OCI and acceptance

| Area | Current evidence | Boundary |
| --- | --- | --- |
| Grok source | [PR #131](https://github.com/smallgun01/amusant/pull/131) merged; the cited Linux run reported 522 tests PASS and native ARM64 Grok Relay/Egress services healthy. | Runner CI does not establish OCI installation or ingress. |
| Image release | [PR #133](https://github.com/smallgun01/amusant/pull/133) merged; [#132](https://github.com/smallgun01/amusant/issues/132) records digest publication, pre-pull and static staging checks as Owner-reported PASS. | No Grok cutover follows from image publication or preflight. |
| Installed OCI runtime | The latest #132 inventory reports the legacy Relay/Facade/Hermes/Egress stack healthy; Grok Compose has not been started. | The legacy stack remains the installed runtime at this snapshot. |
| Live acceptance | Public Callback ingress, StickS3 ingress, Grok device E2E and native Run terminal state remain unverified. | A Callback receipt or Bot-reported outcome does not prove native Run termination. |

Submit one Grok Routine Webhook; do not retry an ambiguous send or switch it to Hermes. A committed Callback is the durable receipt, not proof of native completion or remote Stop. Keep uncertain tasks until a valid Callback or explicit Owner reconciliation. Speak only after a later explicit request.

## Current work and authority

See [SPEC](SPEC.md), [STATUS](STATUS.md), [PLAN](PLAN.md) and the [WS index](docs/project-management/workstreams.md). [#132](https://github.com/smallgun01/amusant/issues/132) owns OCI staging and cutover. Related decisions are in [#127](https://github.com/smallgun01/amusant/issues/127), [#130](https://github.com/smallgun01/amusant/issues/130) and [#134](https://github.com/smallgun01/amusant/issues/134). H1 is docs-only; H2 is offline code work. Neither authorizes Compose, firmware, credentials, network or live-provider changes.

## Historical architecture

The [history index](docs/history/20261025-documentation-baseline.md) pins the superseded Hermes-current docs at `main@e615c06` and earlier OpenAB/AWS decisions. ADRs and dated evidence remain unchanged.