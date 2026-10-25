# Amusant — Source, deployment and acceptance evidence

Snapshot aligned 2026-10-25. This file records evidence, not new scope or live authorization. [SPEC](SPEC.md) / [ADR-0004](docs/adr/0004-owner-managed-hermes.md) contain the Owner decision; [PLAN](PLAN.md) contains work not yet completed.

## Source baseline and open candidates

Runtime baseline: [main at 685db3bf5a48017b0a535354ec4efc64e89dc972](https://github.com/smallgun01/amusant/tree/685db3bf5a48017b0a535354ec4efc64e89dc972), after [#116](https://github.com/smallgun01/amusant/pull/116). New Hermes Runs are **v2-only** with optional Artifacts. There is no current source-side v1/v2 rollout selector for new Hermes Runs; historical records are preserved, not rewritten.

| Item | Evidence at this snapshot | What it does not prove |
| --- | --- | --- |
| #107 / #116 | Optional-Artifact v2 implemented, then v1 runtime retired; #116 records offline/CI evidence | Current OCI images/plugin/config or new physical acceptance |
| #112 / #113 / #114 | Merged Live injection, spoken-session handoff and useful short-result guidance | Post-fix physical new-PTT audio/Inbox `spoken` acceptance |
| #117 at `0ca1824` | Open Draft, design-only; superseded by the new Owner scope decision | Runtime implementation, merge or actual GitHub closure |
| #118 at `f80e7b9` | Open Draft; polling/recovery candidate with two P1 review findings and result-state alignment work | Corrected/full runtime acceptance; Draft heavy jobs were skipped in the cited review |
| Generic delegation / native Owner access | Requirements accepted; source still has research-type hints and operator-owned config | These follow-ups implemented or deployed |

[#118 review](https://github.com/smallgun01/amusant/pull/118#pullrequestreview-5641201516) identifies deadline bypass after uncertainty and a concurrent Cancel lost during recovery. Do not describe them as fixed until the reviewed revision and tests establish that.

## Existing acceptance evidence, retained

| Evidence category | Recorded result | Outstanding / limitation |
| --- | --- | --- |
| Device local / E2 | Local FAST 10/10; #95 BLE teardown and #97 QR owner tests, including official Android 5/5 QR recognition | W2a multi-SSID, full CJK/negative/heap coverage and formal audible-latency/battery gates remain separate |
| Integrated offline / E1 | #87/#90 native ARM64 fake-provider Relay -> Facade -> Hermes -> Ledger/Inbox; #85 closed for E1 | Not live model, public ingress, host reboot or provider-billing proof |
| Effective toolsets / E1 | #94 Case A/B fake-provider preflight | Not Owner's current production toolset or approval to enable real tools |
| OCI backend functional | Owner-reported GO 2 backend result path, 2026-10-22 | Not formal E3/E4 or proof of the latest deployed release |
| Physical FAST functional | Owner-reported StickS3 -> temporary private SSH-forwarded OCI -> Gemini audible FAST, 2026-10-22 | Not permanent public G1 ingress |
| Physical SLOW historical | Accepted Run hit 120s deadline; later native completed with zero recorded tools/no report; Owner reconciled on 2026-10-23 as failed/no-result and released slot | Does not establish a completed physical Slow -> new-PTT spoken loop |
| Native Hermes Owner channel | No linked acceptance evidence in this source snapshot | TG access, configuration updates, channel isolation and generic tool use need verification |
| Operations | SQLite maintenance/offline durability work exists | Encrypted off-host restore, sustained load, actual cancellation/cost and E4 remain separate |

Primary live record: [GO 2/GO 3 evidence](docs/oci-go2-go3-live-evidence-20261022.md), [#106](https://github.com/smallgun01/amusant/pull/106), [#62](https://github.com/smallgun01/amusant/issues/62). Offline references: [#90 CI](https://github.com/smallgun01/amusant/actions/runs/39240814687), [#94 CI](https://github.com/smallgun01/amusant/actions/runs/39426021105).

The #90 fake-provider run observed `failed / interrupt` with `stop_cancel_confirmed=false`. Other dated fixtures may have different outcomes; report each with its revision/environment. Do not combine them into a claim of confirmed real-provider cancellation or billing cessation.

## Current deployment: verify, do not infer

This alignment did not inspect OCI. The current deployed image digests, mounted plugin revision and effective private config are **not reverified here**. Source v2-only does not prove deployed v2; old 'v1 deployment default' prose does not prove deployed v1. OpenClaw's [O1 work order](docs/handoffs/20261025-openclaw.md) reconciles existing evidence first, then only authorized read-only metadata.

Likewise, 'no linked acceptance evidence' does not mean the Owner never performed a test. Reuse dated logs/comments and request only missing evidence before scheduling repeat paid/device tests. Do not publish credentials, raw configs, prompt bodies, private host IDs or report contents to establish status.

## How to update this file

Record separately: source commit; deployed release bundle; test date/environment; approval/evidence reference; result and limitations. Use `PASS`, `FAIL`, `BLOCKED` or `NOT TESTED` for the actual check, with E1/E2/E3/E4 scope. A green aggregate gate with skipped runtime jobs is not runtime PASS.

The next product evidence is the complete v2 voice loop plus independently usable native Hermes access. Nothing in this documentation PR changes code, starts services, grants tools, calls models or closes acceptance issues.

Older snapshots and historical AWS #26 evidence remain accessible through the [history index](docs/history/20261025-documentation-baseline.md) and [P4-T4b record](docs/p4-t4b-live-acceptance.md). They are not a current alternative deployment.
