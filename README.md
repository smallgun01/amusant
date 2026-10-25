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

Runtime source baseline: `685db3bf5a48017b0a535354ec4efc64e89dc972`, after [#116](https://github.com/smallgun01/amusant/pull/116). **New Hermes Runs are v2-only; Artifacts are optional.** A merged source revision does not identify the deployed OCI bundle.

| Area | Recorded evidence | Remaining boundary |
| --- | --- | --- |
| Device / FAST | Local FAST and BLE/QR owner tests; OCI tunneled FAST functional evidence | Formal latency/battery, W2a and permanent ingress remain separate gates |
| Backend pipeline | Native Runs, Task Ledger/Inbox, private Facade, bounded v2, offline integration; dated GO 2 backend functional evidence | Latest deployed images/plugin/config and full physical Slow result readback require matching evidence |
| General Agent integration | Owner-managed architecture accepted | Research-oriented delegation interface still needs neutralization; native management access is not yet accepted |
| Reliability | Existing recovery/single-flight code; [#118](https://github.com/smallgun01/amusant/pull/118) is a separate unmerged candidate at this snapshot | Its review findings and execution-versus-delivery semantics must be resolved |

[STATUS.md](STATUS.md) separates source, offline tests, deployment, physical acceptance, and native management. Preserve the [2026-10-22/23 live record](docs/oci-go2-go3-live-evidence-20261022.md); do not erase earlier functional evidence or invent a new PASS.

## Scope boundaries

- Add an ordinary Hermes Skill/MCP/tool through Hermes configuration, without changing Relay/Facade code. New hardware or interaction protocols may still require an explicitly scoped adapter change.
- Relay authenticates, submits, tracks and cancels its own Runs; it does not classify document intent, duplicate tool allowlists, or schedule all Hermes work.
- Facade validates the Amusant channel's identity, format, bounds and stored-artifact integrity. It is not a business-success judge or a gateway for every native Hermes conversation.
- Single-flight applies to **Amusant-admitted Runs**. No unsolicited speech applies to **Amusant device audio**, not Owner-configured native Telegram/Cron notifications.
- Known Runs recover without blind resubmission. A STOP acknowledgement is not terminal confirmation, and stopping a Run does not undo a previously completed external action.

## Documentation and next work

Read [SPEC](SPEC.md) for requirements, [ADR-0004](docs/adr/0004-owner-managed-hermes.md) for the responsibility decision, [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md) for hosting/isolation, [PLAN](PLAN.md) and [WS index](docs/project-management/workstreams.md) for sequence, and [STATUS](STATUS.md) for evidence.

The [handoff index](docs/handoffs/20261025-owner-managed-hermes.md) contains separate **Codex** and **OpenClaw** work orders for the Owner to forward. This documentation PR does not execute those orders, merge runtime PRs, change OCI, or authorize paid calls.

[#117](https://github.com/smallgun01/amusant/pull/117)'s transcript-derived Artifact admission policy is **superseded by the Owner decision and must not be implemented or merged**. The GitHub PR lifecycle is tracked separately; a document is not evidence that a PR was closed.

Kai is a pinned interoperability reference (`1681fb3c11acd28525d8987bba4b9bf4bc2a288f`); Muse Charm is a category/UX benchmark, not a compatibility promise. [FAST](fast_voice/README.md) · [Relay](relay/README.md) · [OCI package](deploy/oci-a1/README.md) · [Historical evidence index](docs/history/20261025-documentation-baseline.md).
