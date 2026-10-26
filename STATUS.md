# Amusant — Source, deployment and acceptance evidence

Snapshot reconciled 2026-10-26 against `main@a74cfb5` and linked GitHub PR/Issue evidence. This file records evidence, not new scope or live authorization. [SPEC](SPEC.md) / [ADR-0004](docs/adr/0004-owner-managed-hermes.md) contain the Owner decision; [PLAN](PLAN.md) contains work not yet completed.

## Source baseline and open candidates

Source baseline: [`main@a74cfb5b90a2116df624b6d5445d571c2118768d`](https://github.com/smallgun01/amusant/commit/a74cfb5b90a2116df624b6d5445d571c2118768d), after merged #116/#118/#120/#121/#122/#125. New Hermes Runs are **v2-only** with optional Artifacts; #120 adds generic `agent` task admission and SQLite v9 source. There is no new Hermes v1/v2 selector; historical records are preserved, not silently reclassified.

| Item | Evidence at this snapshot | What it does not prove |
| --- | --- | --- |
| #107 / #116 | Optional-Artifact v2 implemented, then v1 runtime retired; #116 records offline/CI evidence | Current OCI images/plugin/config or new physical acceptance |
| #112 / #113 / #114 | Merged Live injection, spoken-session handoff and useful short-result guidance | Post-fix physical new-PTT audio/Inbox `spoken` acceptance |
| [#117](https://github.com/smallgun01/amusant/pull/117) | Closed without merge, 2026-10-25; transcript-derived requirement superseded | Any classifier, mandatory Artifact or alternative deployed policy |
| [#118](https://github.com/smallgun01/amusant/pull/118) / #58 | Merged 2026-10-25 after P1 deadline/Cancel race fixes and offline recovery tests | Actual deployed bundle, real-provider STOP/billing behavior and full physical E2E |
| [#120](https://github.com/smallgun01/amusant/pull/120) / #105 | Generic `delegate_task` implementation merged 2026-10-25; SQLite v9 and compatible historical-row handling in source | Actual host schema, active Hermes tools, native Owner access, real action success or content export |
| [#121](https://github.com/smallgun01/amusant/pull/121) / [#122](https://github.com/smallgun01/amusant/pull/122) | Grok proposal and G2 isolated spike merged; G1 Owner-reported 4/4, G2 Ready CI 15 focused Ubuntu tests PASS and offline-only G2 accepted (see #123) | No real Grok Routine, remote OAuth/MCP or G4 product integration |
| [#125](https://github.com/smallgun01/amusant/pull/125) / #123 | G3-P1 loopback callback source merged 2026-10-26; local offline tests reported; draft CI did not run heavyweight tests | N100 pilot isolation/setup, public routing, callback live reachability and real Grok calls |
| [#124](https://github.com/smallgun01/amusant/pull/124) / [#102](https://github.com/smallgun01/amusant/pull/102) | #124 v8→v9 SQLite release-readiness PR is open/Ready (CI PASS); #102 bootstrap PR is open/Draft, on hold per #101 | #124 not merged or deployed; #102 not authorization to resume installer work |

The earlier [#118 P1 review](https://github.com/smallgun01/amusant/pull/118#pullrequestreview-5641201516) is historical context, not an active unmerged blocker. Its corrected code merged, but neither source merge nor offline CI is proof of live OCI recovery, remote STOP settlement or device delivery.

## Existing acceptance evidence, retained

| Evidence category | Recorded result | Outstanding / limitation |
| --- | --- | --- |
| Device local / E2 | Local FAST 10/10; #95 BLE teardown and #97 QR owner tests, including official Android 5/5 QR recognition | W2a multi-SSID, full CJK/negative/heap coverage and formal audible-latency/battery gates remain separate |
| Integrated offline / E1 | #87/#90 native ARM64 fake-provider Relay -> Facade -> Hermes -> Ledger/Inbox; #85 closed for E1 | Not live model, public ingress, host reboot or provider-billing proof |
| Effective toolsets / E1 | #94 Case A/B fake-provider preflight | Not Owner's current production toolset or approval to enable real tools |
| OCI backend functional | Owner-reported GO 2 backend result path, 2026-10-22 | Not formal E3/E4 or proof of the latest deployed release |
| Physical FAST functional | Owner-reported StickS3 -> temporary private SSH-forwarded OCI -> Gemini audible FAST, 2026-10-22 | Not permanent public G1 ingress |
| Physical SLOW historical | Accepted Run hit 120s deadline; later native completed with zero recorded tools/no report; Owner reconciled on 2026-10-23 as failed/no-result and released slot | Does not establish a completed physical Slow -> new-PTT spoken loop |
| Native Hermes Owner channel | No linked live acceptance evidence in this snapshot | Telegram/native policy, secure configuration updates, channel isolation and real generic tool use need verification |
| Isolated Grok validation | G1 Owner-reported 4/4 accepted; G2 accepted offline (see #123); G3-P1 source merged | G3-P2 N100 setup reported BLOCKED before mutation (privileged account step), no localhost callback smoke, tunnel/DNS or live Routine PASS; G4 remains gated by Hermes G0 |
| Operations | SQLite maintenance/offline durability work exists | Encrypted off-host restore, sustained load, actual cancellation/cost and E4 remain separate |

Primary live record: [GO 2/GO 3 evidence](docs/oci-go2-go3-live-evidence-20261022.md), [#106](https://github.com/smallgun01/amusant/pull/106), [#62](https://github.com/smallgun01/amusant/issues/62). Offline references: [#90 CI](https://github.com/smallgun01/amusant/actions/runs/39240814687), [#94 CI](https://github.com/smallgun01/amusant/actions/runs/39426021105).

The #90 fake-provider run observed `failed / interrupt` with `stop_cancel_confirmed=false`. Other dated fixtures may have different outcomes; report each with its revision/environment. Do not combine them into a claim of confirmed real-provider cancellation or billing cessation.

## Current deployment: verify, do not infer

This alignment did not inspect OCI. The current deployed image digests, mounted plugin revision and effective private config are **not reverified here**. Source v2-only does not prove deployed v2; old 'v1 deployment default' prose does not prove deployed v1. OpenClaw's [O1 work order](docs/handoffs/20261025-openclaw.md) reconciles existing evidence first, then only authorized read-only metadata.

Likewise, 'no linked acceptance evidence' does not mean the Owner never performed a test. Reuse dated logs/comments and request only missing evidence before scheduling repeat paid/device tests. Do not publish credentials, raw configs, prompt bodies, private host IDs or report contents to establish status.

## How to update this file

Record separately: source commit; deployed release bundle; test date/environment; approval/evidence reference; result and limitations. Use `PASS`, `FAIL`, `BLOCKED` or `NOT TESTED` for the actual check, with E1/E2/E3/E4 scope. A green aggregate gate with skipped runtime jobs is not runtime PASS.

The next product evidence is the complete **deployed** v2 voice loop plus independently usable native Hermes access. Grok G3-P2 is an independent, separately authorized pilot gate, not a substitute for that M1 acceptance. Nothing in this documentation PR changes code, starts services, grants tools, calls models or closes acceptance issues.

Older snapshots and historical AWS #26 evidence remain accessible through the [history index](docs/history/20261025-documentation-baseline.md) and [P4-T4b record](docs/p4-t4b-live-acceptance.md). They are not a current alternative deployment.

## 2026-10-27 addendum — G3 native acceptance and G4 design GO

The **2026-10-26 source snapshot above is historical**: it predates later Owner live evidence. Source reviewed for this addendum: `main@e776495` (merged #126 documentation alignment). No OCI deployed-digest, mounted plugin, active DB schema, or current G0 physical Slow→Inbox→spoken acceptance was independently reconfirmed.

- **G1:** native Grok App 4/4 Owner-reported PASS and accepted. **G2:** locked offline spike/CI accepted; callback implementation merged #125.
- **G3-P2:** one true native Webhook-triggered fixed synthetic Routine returned HTTP Callback and N100 matched durable receipt. **G3-Q1:** dynamic 15 open Issues / 2 PRs verified against GitHub; bounded Callback and N100 receipt PASS. **G3-Q2:** 4 open WS2 Issues (#105/#58/#56/#48) independently verified; fresh task, unchanged Routine, matching receipt PASS. **G3-D1:** 14-day/13-night France native Markdown document Owner-confirmed; native App same-work v1→v2 revision Owner-confirmed; original immutable v1 short report, callback and N100 receipt independently matched. Four live Webhook submissions total reported; no production device path tested.
- **Proof boundary:** the private G3 N100 pilot proves useful **Grok task → callback → isolated SQLite**, not a production Relay Inbox, download endpoint, verified native Run termination, native Stop, arbitrary actions or long-term uptime. `reported_outcome=completed` is an Agent claim, `native_state=unknown`; a native workspace locator is not a Facade artifact or public download. G3-R callback-failure and G3-A approval live cases remain NOT TESTED, as do measured cost/usage and maintenance burden.
- **G4:** Owner authorized [#127](https://github.com/smallgun01/amusant/issues/127) **design/source review and existing-access read-only operations planning**, not G4 code deployment, paid calls, OCI changes or Hermes retirement. Conditional design GO based on [#123 closeout](https://github.com/smallgun01/amusant/issues/123#issuecomment-6419151666). G0 physical v2 Slow→Inbox→spoken remains an explicit G4 deployed/physical gate; #124 still open as of this addendum.

[Agent handoff](docs/handoffs/20261027-grok-g4-agents.md) · [Grok validation stage addendum](docs/project-management/grok-bot-validation-v0.3.md) · [G4 tracker](https://github.com/smallgun01/amusant/issues/127).
