# Project Amusant — Voice Companion Specification v0.7

**Current source decision (Owner, 2026-10-27): Grok-first SLOW profile.** This specifies the selected source target; it does not claim OCI installation or acceptance. [#132](https://github.com/smallgun01/amusant/issues/132) owns staging evidence and cutover. [#127](https://github.com/smallgun01/amusant/issues/127), [#130](https://github.com/smallgun01/amusant/issues/130) and [#134](https://github.com/smallgun01/amusant/issues/134) preserve design and review decisions. Previous SPECs are pinned in the [history index](docs/history/20261025-documentation-baseline.md).

## A. Product contract

Amusant is a one-owner, PTT-first voice companion. StickS3 supplies the device interface; Gemini Live handles FAST conversation; a delegated SLOW task returns a bounded result to the existing Relay TaskStore/Inbox for later explicit readback.

The selected SLOW profile uses Grok Bot through one Routine submission and a task-bound HTTPS Callback. Do not route Grok through Hermes. Use one selected backend per admitted task, no automatic fallback, no blind resubmission after an ambiguous send, and no unsolicited device speech.

## B. Selected source profile and installed state

`deploy/oci-a1/compose.grok.yaml` explicitly sets `AMUSANT_SLOW_EXECUTOR=grok_callback` and runs only Relay and Egress Proxy using the SQLite v10 TaskStore/Inbox. Device WSS and the private Callback listener are separate; neither port is published by this Compose profile. Public Callback and device ingress have independent reviewed paths.

The Python fallback remains `hermes_runs`; Compose explicitly overrides it to `grok_callback`. Hermes is a separately selectable alternative/rollback source path. OpenAB ACP and DynamoDB are legacy compatibility paths, not part of the Grok profile.

The last Owner-reported OCI inventory in #132 found the legacy four-service Relay/Facade/Hermes/Egress stack healthy. Grok images were published and pre-pulled, and static staging settings were checked, but the Grok Compose profile has not been started. Source, installed runtime, ingress and physical acceptance are separate evidence classes.

## C. Delegation and result lifecycle

- Admit at most one outstanding Amusant SLOW task for the authenticated Owner; retain one TaskStore and Inbox.
- Submit the Grok Routine once. HTTP 200 means Webhook accepted, not task completion.
- Commit a valid task-bound Callback before acknowledging receipt. Callback receipt, Bot-reported outcome and native Run terminal state are distinct facts.
- Do not claim remote Stop, native completion or cancellation from Callback text alone. Native state remains unknown without an authoritative verified signal.
- After an ambiguous send, do not retry or switch to Hermes. Keep the task uncertain until a valid Callback or explicit Owner reconciliation/abandonment.
- Keep results bounded. A native `box:` path is not an Amusant download URL or verified artifact reference.

## D. Voice, privacy and persistence

Keep FAST independent of SLOW. Task text cannot grant tools, credentials, backend URLs or Owner permissions. Callback capabilities are task-scoped, hashed, expiring and revocable; never log secrets, raw tasks or full reports. Keep Callback ingress separate from device TLS-PSK/WSS.

The Grok profile uses the existing SQLite v10 TaskStore. Do not migrate or rewrite old schemas for repository tidiness; keep legacy OCI state separate from a fresh Grok test ledger. Retain ACP/DynamoDB/Hermes code and regression tests while they have compatibility or rollback value. H2 may isolate imports/dependencies but must preserve Token/Owner/TaskStore behavior and explicit selection. Relocation or retirement is optional and needs measured isolation plus an Owner rollback decision.

## E. Acceptance and change control

| Evidence | Establishes | Does not establish |
| --- | --- | --- |
| PR #131 Linux/ARM64 CI | Grok source tests and two Grok-profile services healthy on a runner | OCI deployment, ingress, native Run state or device E2E |
| PR #133 and #132 Owner reports | Images published/pulled; static staging settings checked | Grok Compose running or public ingress |
| #132 host inventory | Legacy stack and preparation facts reported | Grok cutover or live task acceptance |
| Callback receipt | Durable receipt for one task | Native Run terminal state, remote Stop or document download |

Review H1 as a docs-only PR and H2 separately for optional-import isolation, explicit executor selections and offline regressions. No docs/code PR authorizes OCI, DNS, network, secret, paid-provider, firmware or data-reset actions. Keep those operations under #132 Owner gates.

## F. Historical record

The prior Hermes-current SPEC and embedded v0.4 appendix are pinned at [main@e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/SPEC.md). Earlier OpenAB/AWS material is linked from the history index. ADR-0001 through ADR-0004 remain unchanged.