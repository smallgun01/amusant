# Amusant — Current source, deployment and acceptance status

Reconciled 2026-10-27 against `main@e615c06`, [#132](https://github.com/smallgun01/amusant/issues/132) and linked source/release evidence. This file distinguishes selected source, installed OCI state and acceptance; it does not assert an unreported action.

## Selected source target

| Item | Current state |
| --- | --- |
| SLOW backend | Grok Compose profile explicitly sets `AMUSANT_SLOW_EXECUTOR=grok_callback`. |
| Services/store | Relay + Egress Proxy; SQLite v10 TaskStore/Inbox. No Hermes, Facade, DynamoDB or ACP in this profile. |
| Source fallback | `relay/application.py` still defaults to `hermes_runs`; Compose selects Grok explicitly. |
| Import separation | ACP loader and DynamoDB adapter are still imported at this baseline. Separate H2 tracks import/dependency isolation. |
| Source CI | PR #131 merged; cited Linux run reported 522 tests PASS; native ARM64 runner reported both Grok services healthy. |
| Image readiness | PR #133 merged; #132 reports Owner-approved image publication, pre-pull and static staging config checks PASS. |

## Installed OCI state

[#132 records](https://github.com/smallgun01/amusant/issues/132#issuecomment-6439306188) the first Owner Stop-and-Replace attempt failed when Grok `egress_proxy` became unhealthy and Relay remained `Created`; automatic rollback restored legacy Relay/Facade/Hermes/Egress Proxy 4/4 healthy. An [isolated proxy diagnostic](https://github.com/smallgun01/amusant/issues/132#issuecomment-6439495767) passed after a local-only CPU-limit increase from 0.1 to 0.5; throttling is suspected, not confirmed. [Revised retry readiness](https://github.com/smallgun01/amusant/issues/132#issuecomment-6439563486) is recorded, but no later cutover. The legacy stack remains installed; no successful Grok deployment, public ingress, live task or device E2E PASS is recorded.

Public Callback, StickS3 TCP/443 ingress, native Grok Run status and physical Grok E2E remain NOT TESTED. #132 owns later staging gates.

## Remaining work

1. **H1:** documentation-only alignment, with immutable links to the superseded Hermes-current source docs.
2. **H2:** separate offline code PR for ACP/DynamoDB import/dependency separation; preserve legacy modules and all explicit executor/store selections.
3. **Optional H3:** relocate modules only if H2 proves isolation and the Owner explicitly accepts rollback consequences. Leaving compatibility modules in place is an acceptable result.

Hermes G0 and PR #124 are not prerequisites for Grok staging. H1/H2 are not the #132 cutover GO and do not establish live/device acceptance. The former Hermes-current docs are pinned in the [history index](docs/history/20261025-documentation-baseline.md); older evidence and ADRs remain intact.
