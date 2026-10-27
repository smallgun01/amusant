# Amusant WS1–WS4 — Current workstreams

Reconciled 2026-10-27 to the Grok-first source selection. [STATUS](../../STATUS.md) owns evidence; [SPEC](../../SPEC.md) owns current requirements; [#132](https://github.com/smallgun01/amusant/issues/132) owns OCI staging and cutover gates.

## Workstreams

| Lane | Responsibility | Next scoped work |
| --- | --- | --- |
| WS1 / #47 | StickS3 and physical acceptance | Preserve the current device path; G1 ingress and Grok readback remain separate #132 gates. |
| WS2 / #48 | SLOW admission and result delivery | One Relay TaskStore/Inbox; Callback, Bot report and native Run state remain distinct. |
| WS3 / #49 | Grok source profile and OCI evidence | Compose selects Relay + Egress Proxy with SQLite v10; legacy four-service stack remains installed per latest report. |
| WS4 / #50 | Persistence, dependencies, secrets and rollback | H2 separates ACP/DynamoDB imports; keep schema behavior and regression tests; no H2 file relocation. |

## Sequence and boundaries

1. **H1:** docs-only current-truth alignment and immutable history pointers.
2. **H2:** separate code PR, lazy-load explicit legacy backends, isolate optional package dependencies, test Grok plus all three executor selections.
3. **#132:** Owner reviews the exact staging config and separately controls OCI, ingress and device gates.
4. **Optional H3:** consider module relocation only after H2 import independence and explicit Owner rollback review.

Grok Compose explicitly sets `AMUSANT_SLOW_EXECUTOR=grok_callback`; the Python fallback remains `hermes_runs`. Hermes is a separate alternative/rollback path, not co-running or an automatic fallback. OpenAB ACP, DynamoDB, their tests, ADRs and AWS evidence stay in the repository.

Do not retry an ambiguous Routine Webhook, add a second TaskStore, add a global scheduler or speak without a later explicit request. PR #131 gives source/ARM64 runner evidence; PR #133/#132 report image publication/pre-pull and static settings. Current Grok runtime, public Callback/StickS3 ingress and physical Grok E2E remain unverified. The prior Hermes-current index is pinned in the [history record](../history/20261025-documentation-baseline.md).