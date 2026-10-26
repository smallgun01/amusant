# Project Amusant

Amusant is a **one-owner, PTT-first personal AI companion** integrating a dedicated device, real-time voice communication, and an independently managed Agent backend. StickS3 is the edge device; Gemini Live handles FAST conversation; **Hermes is the Owner-managed personal Agent**, not a report-only executor or an Agent platform reimplemented by Amusant.

**Owner decision, 2026-10-25:** Hermes owns reasoning, tools, Skills, MCP, memory, external-service credentials, authorizations, and execution workflows. Users manage these through Hermes-native interfaces. Amusant owns the low-friction voice experience and reliable, bounded task/result delivery. See [SPEC](SPEC.md) and [ADR-0004](docs/adr/0004-owner-managed-hermes.md).

## Two interfaces, one Agent backend

```text
Owner -- StickS3 <--> Relay <--> Gemini Live (FAST)
                       |
                       +-- Task Ledger / Inbox
                       +-- private Facade <--> Hermes Gateway / Agent
Owner -- Hermes-native Telegram / CLI / UI ------^
                                               |-- Owner-managed tools / Skills / MCP
                                               |-- memory / credentials / approvals
                                               +-- Cron / native content delivery
```

The native-management path is an integration requirement, **not a claim it is deployed**. The same backend/profile does not imply the same conversation session, automatic approval forwarding, or automatic access to Facade-owned files. Telegram is the initial direct conversational channel; secrets and OAuth use appropriate Hermes-native secure setup, not bot chat messages.

The selected hosting remains one OCI A1 host with four isolated services: Relay, Facade, Hermes, and Egress Proxy. This decision does not merge services, add an Admin API, expose Hermes publicly, or give an Agent host/Docker privileges. See [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md).

## Current implementation and evidence

Source snapshot (2026-10-26): [`main@a74cfb5`](https://github.com/smallgun01/amusant/commit/a74cfb5b90a2116df624b6d5445d571c2118768d), including merged reliability [#118](https://github.com/smallgun01/amusant/pull/118), generic delegation [#120](https://github.com/smallgun01/amusant/pull/120) and isolated Grok spike [#125](https://github.com/smallgun01/amusant/pull/125). **New Hermes Runs are v2-only; Artifacts are optional.** This source snapshot does not identify the deployed OCI bundle, database version or physical acceptance.

| Area | Recorded evidence | Remaining boundary |
| --- | --- | --- |
| Device / FAST | Local FAST and BLE/QR owner tests; OCI tunneled FAST functional evidence | Formal latency/battery, W2a and permanent ingress remain separate gates |
| Backend pipeline | Native Runs, Task Ledger/Inbox, private Facade, bounded v2, offline integration; dated GO 2 backend functional evidence | Latest deployed images/plugin/config and full physical Slow result readback require matching evidence |
| General Agent integration | Tool-agnostic `delegate_task` source merged in #120 (SQLite v9 and compatibility tests) | Actual OCI bundle, Hermes-native Owner access and real authorized tool use still require separate acceptance |
| Reliability | [#118](https://github.com/smallgun01/amusant/pull/118) merged after deadline/Cancel recovery review and offline regressions | No new deployed/physical STOP, billing, restart or E4 PASS is inferred |
| Isolated Grok candidate | G1 Owner-reported 4/4 accepted; G2 [#122](https://github.com/smallgun01/amusant/pull/122) offline CI accepted; G3-P1 [#125](https://github.com/smallgun01/amusant/pull/125) source merged | [#123](https://github.com/smallgun01/amusant/issues/123) G3-P2 setup/live gates remain open; this is not a replacement for the Hermes product path |

[STATUS.md](STATUS.md) separates source, offline tests, deployment, physical acceptance, and native management. Preserve the [2026-10-22/23 live record](docs/oci-go2-go3-live-evidence-20261022.md); do not erase earlier functional evidence or invent a new PASS.

## Scope boundaries

- Add an ordinary Hermes Skill/MCP/tool through Hermes configuration, without changing Relay/Facade code. New hardware or interaction protocols may still require an explicitly scoped adapter change.
- Relay authenticates, submits, tracks and cancels its own Runs; it does not classify document intent, duplicate tool allowlists, or schedule all Hermes work.
- Facade validates the Amusant channel's identity, format, bounds and stored-artifact integrity. It is not a business-success judge or a gateway for every native Hermes conversation.
- Single-flight applies to **Amusant-admitted Runs**. No unsolicited speech applies to **Amusant device audio**, not Owner-configured native Telegram/Cron notifications.
- Known Runs recover without blind resubmission. A STOP acknowledgement is not terminal confirmation, and stopping a Run does not undo a previously completed external action.

## Documentation and next work

Read [SPEC](SPEC.md) for requirements, [ADR-0004](docs/adr/0004-owner-managed-hermes.md) for the responsibility decision, [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md) for hosting/isolation, [PLAN](PLAN.md) and [WS index](docs/project-management/workstreams.md) for sequence, and [STATUS](STATUS.md) for evidence.

The [2026-10-25 handoff index](docs/handoffs/20261025-owner-managed-hermes.md) preserves the original Codex/OpenClaw work orders; C1/C2 in those dated orders have since merged. Use [PLAN](PLAN.md) for the current queue, including [#124](https://github.com/smallgun01/amusant/pull/124) release readiness and [#123](https://github.com/smallgun01/amusant/issues/123) isolated G3. Documentation changes do not deploy, alter host privileges or authorize paid/live tests.

[#117](https://github.com/smallgun01/amusant/pull/117)'s transcript-derived Artifact admission policy is **superseded**; the PR was **closed without merge** on 2026-10-25. Its historical discussion is retained; do not revive its classifier or mandatory-Artifact policy.

Kai is a pinned interoperability reference (`1681fb3c11acd28525d8987bba4b9bf4bc2a288f`); Muse Charm is a category/UX benchmark, not a compatibility promise. [FAST](fast_voice/README.md) · [Relay](relay/README.md) · [OCI package](deploy/oci-a1/README.md) · [Historical evidence index](docs/history/20261025-documentation-baseline.md).

## Grok Bot as a candidate **replacement** SLOW backend (G4, 2026-10-27)

The Hermes architecture above remains the **selected/deployed-baseline target**, not a permanent product requirement. The Owner is evaluating **managed Grok Bot as an alternative to Hermes**, not Grok-as-a-Hermes tool. Neither backend is switched in this documentation change.

- [G3 validation #123](https://github.com/smallgun01/amusant/issues/123#issuecomment-6419151666): G1 native 4/4 Owner-accepted; G2 isolated offline spike; one synthetic and three substantive Grok Routine → authenticated HTTPS Callback → durable N100 SQLite receipts. Q1/Q2 independent GitHub answer verification; D1 Markdown file delivered and later revised in the same Grok App work. The original callback remains immutable after native file edits.
- **Limits:** Grok `reported_outcome` is the Agent's claim, not proven native Run termination; native Run state remains unknown. The native `box:` workspace locator is not an authenticated Amusant download URL. Native Stop, approval, callback failure recovery, production ingress hardening, quotas and G4 device loop are not accepted.
- [G4 #127](https://github.com/smallgun01/amusant/issues/127) starts **read-only design/evidence work**; proposed path: StickS3 → Gemini Live → Relay generic delegation → **selected Grok backend** → Routine → authenticated result → Relay Inbox → later explicit PTT spoken retrieval. **Do not route through Hermes, auto-fallback, add a second Agent platform or retire Hermes now.**
- [Grok v0.2](docs/project-management/grok-bot-validation-v0.2.md) remains the product/contract baseline; [v0.3](docs/project-management/grok-bot-validation-v0.3.md) maintains G0 as a G4 live/device gate. [G4 agent handoff](docs/handoffs/20261027-grok-g4-agents.md) captures Codex/OpenClaw boundaries. See [STATUS](STATUS.md) for observed vs unverified evidence.
