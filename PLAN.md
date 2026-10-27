# Amusant — Current execution plan

Reconciled 2026-10-27 to the Grok-first decision in [#130](https://github.com/smallgun01/amusant/issues/130), [#132](https://github.com/smallgun01/amusant/issues/132) and [#135](https://github.com/smallgun01/amusant/issues/135). #132 owns staging and cutover; source selection is not installed state.

## Work sequence

| Order | Deliverable | Scope |
| --- | --- | --- |
| H1 — docs first | Align current README/SPEC/STATUS/PLAN/WS/Relay docs; pin superseded architecture. | Docs-only Draft PR, lightweight checks, no code or infrastructure. |
| H2 — separate code PR | Audit and isolate ACP/DynamoDB optional imports and packaging. | Offline-only, preserve old modules/tests and selectors. Verify Grok startup isolation plus `openab_acp`, `hermes_runs` and `grok_callback`. |
| H3 — optional | Move truly inactive legacy modules only if safe. | Separate Owner rollback decision after H2; retention in place is acceptable. |
| #132 Owner gates | Grok OCI staging, Callback ingress, StickS3 ingress and device acceptance. | Owner-controlled; not performed by H1/H2. |

## H2 review inventory

- `application.py` currently imports and loads ACP for Grok; `hermes_result.py` imports shared limits from the ACP module.
- `store_factory.py` imports `DynamoDbTaskStore` at module load; it loads the Boto3 ledger in the explicit DynamoDB branch.
- `boto3` is a base dependency although Grok Compose uses SQLite. Move it to an explicit optional dependency and retain it in dev test dependencies.
- Audit compatibility tests, helper CLIs, Docker/package guards, deploy templates and CI references.
- Preserve Token/Owner/SQLite v10 behavior, one-send semantics, explicit selection and source default. Never auto-fallback to Hermes or migrate data for source tidiness.

PR #131 supplies 522-test Linux offline evidence and a healthy Grok-only ARM64 runner smoke. PR #133/#132 report image publication, OCI pre-pull and static config readiness; latest reported OCI runtime remains the legacy four-service stack. No Grok cutover or public ingress PASS is recorded.

Hermes G0 and PR #124 are not prerequisites for Grok staging. Keep old AWS/Hermes decisions and evidence linked in the [history index](docs/history/20261025-documentation-baseline.md).