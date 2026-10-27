# Documentation baseline before Owner-managed Hermes alignment

These immutable links preserve the original documents, embedded historical appendices and dated snapshots at runtime baseline **685db3bf5a48017b0a535354ec4efc64e89dc972**. Current documents were consolidated rather than stacking another contradictory Current overlay. Git history and underlying evidence records are not rewritten.

| Prior document | Immutable snapshot |
| --- | --- |
| README | [Original README](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/README.md) |
| SPEC v0.5 and v0.4 appendix | [Original SPEC](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/SPEC.md) |
| PLAN and historical execution plans | [Original PLAN](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/PLAN.md) |
| STATUS and historical evidence snapshots | [Original STATUS](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/STATUS.md) |
| ADR-0003 and dated annotations | [Original ADR-0003](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/docs/adr/0003-oci-a1-hermes-sqlite.md) |
| Workstream index | [Original WS index](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/docs/project-management/workstreams.md) |
| H1.2 delivery proposal | [Original proposal](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/docs/hermes-h1.2-result-delivery-proposal.md) |
| Single-flight implementation notes | [Original lifecycle notes](https://github.com/smallgun01/amusant/blob/685db3bf5a48017b0a535354ec4efc64e89dc972/docs/slow-task-single-flight.md) |

Historical claims remain claims as of their dates. Old B0/OpenAB/AWS choices and embedded proposal requirements do not override current [SPEC](../../SPEC.md) or [STATUS](../../STATUS.md). ADR-0004 is retained as dated decision context.

The separate [2026-10-22/23 OCI evidence](../oci-go2-go3-live-evidence-20261022.md), [AWS T4b evidence](../p4-t4b-live-acceptance.md), ADR-0001/0002, test harnesses and PR discussions are retained. This documentation alignment neither deletes operational data nor promotes any test to a higher acceptance tier.



## 2026-10-27 — Superseded Hermes-current documentation snapshot

The following immutable files pin the last root-document baseline before the Grok-first alignment in #135. They describe the former Hermes-current source documentation at main@e615c06; they are historical provenance, not present deployment evidence or current instructions.

| Prior current-truth document | Immutable snapshot |
| --- | --- |
| README | [README at e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/README.md) |
| SPEC v0.6 (Hermes-current, dated G4 addendum) | [SPEC at e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/SPEC.md) |
| STATUS | [STATUS at e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/STATUS.md) |
| PLAN | [PLAN at e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/PLAN.md) |
| WS index | [Workstreams at e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/docs/project-management/workstreams.md) |
| Relay README | [Relay README at e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/relay/README.md) |
| Grok validation gate text | [v0.3 at e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/docs/project-management/grok-bot-validation-v0.3.md) |
| Grok image-release runbook | [D2c runbook at e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/docs/ops/grok-g4-d2c-image-release.md) |

The prior four-service Hermes/Facade diagram is retained here for provenance. The current source profile is Grok-first. The first #132 Stop-and-Replace attempt failed and automatic rollback restored the legacy four-service stack 4/4 healthy; #132 records revised retry readiness but no later cutover. The legacy stack remains installed, with no successful Grok deployment or ingress/device E2E PASS recorded.
