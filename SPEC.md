# Project Amusant — Voice Companion Specification v0.7

**Current source decision (Owner, 2026-10-27): Grok-first SLOW profile.** Source and installed state are separately evidenced: [#132 second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695) reports OCI Grok Relay + Proxy 2/2 Healthy with SQLite v10; public Callback/device ingress and real physical/provider E2E remain unaccepted. [#132](https://github.com/smallgun01/amusant/issues/132) owns staging evidence and cutover. [#127](https://github.com/smallgun01/amusant/issues/127), [#130](https://github.com/smallgun01/amusant/issues/130) and [#134](https://github.com/smallgun01/amusant/issues/134) preserve design and review decisions. Previous SPECs are pinned in the [history index](docs/history/20261025-documentation-baseline.md).

## A. Product contract

Amusant is a one-owner, PTT-first voice companion. StickS3 supplies the device interface; Gemini Live handles FAST conversation; a delegated SLOW task returns a bounded result to the existing Relay TaskStore/Inbox for later explicit readback.

The selected SLOW profile uses Grok Bot through one Routine submission and a task-bound HTTPS Callback. Do not route Grok through Hermes. Use one selected backend per admitted task, no automatic fallback, no blind resubmission after an ambiguous send, and no unsolicited device speech.

## B. Selected source profile and installed state

`deploy/oci-a1/compose.grok.yaml` explicitly sets `AMUSANT_SLOW_EXECUTOR=grok_callback` and runs only Relay and Egress Proxy using the SQLite v10 TaskStore/Inbox. Device WSS and the private Callback listener are separate; neither port is published by this Compose profile. Public Callback and device ingress have independent reviewed paths.

The Python fallback remains `hermes_runs`; Compose explicitly overrides it to `grok_callback`. Hermes is a separately selectable alternative/rollback source path. OpenAB ACP and DynamoDB are legacy compatibility paths, not part of the Grok profile.

The first Owner cutover failed and automatically rolled back. The [second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695) succeeded: Grok 2/2 Healthy, old containers removed without volumes and old v8 data preserved. Installed images remain from `e615c06`; [#138](https://github.com/smallgun01/amusant/pull/138) optional-import isolation is merged source, not automatically deployed. Static mount/restart posture PASS but `REBOOT_RECOVERY=NOT_TESTED`. The live environment depends on Owner-local `compose.grok.local.yaml` alongside `compose.grok.yaml`; historical single-file restart examples are unsafe for this runtime. Do not infer root cause solely from the 0.1→0.5 CPU proxy override. Public Callback and device ingress, real Grok Routine, physical readback and native terminal state remain NOT TESTED.

## C. Delegation and result lifecycle

- Admit at most one outstanding Amusant SLOW task for the authenticated Owner; retain one TaskStore and Inbox.
- Submit the Grok Routine once. HTTP 200 means Webhook accepted, not task completion.
- Commit a valid task-bound Callback before acknowledging receipt. Callback receipt, Bot-reported outcome and native Run terminal state are distinct facts.
- Do not claim remote Stop, native completion or cancellation from Callback text alone. Native state remains unknown without an authoritative verified signal.
- After an ambiguous send, do not retry or switch to Hermes. Keep the task uncertain until a valid Callback or explicit Owner reconciliation/abandonment.
- Keep results bounded. A native `box:` path is not an Amusant download URL or verified artifact reference.

### M1 contracts shared by every SLOW backend

| Area | Normative contract |
| --- | --- |
| Result bounds | Use Result Contract v2: `speak` ≤ 240 characters; `card.title` ≤ 80; 1–4 `card.lines`, each ≤ 120 characters; the complete trusted result envelope ≤ 3,000 UTF-8 bytes. Never clip output or pass raw reports through Live. |
| Owner and task scope | `delegate_task`, `check_task_status` and `cancel_task` are scoped to the authenticated Owner and task. Generic task text is data, not tool authorization. Admit at most one active Amusant task per Owner; do not pass raw reports through Live. |
| Device and voice | Require authenticated TLS-PSK/WSS and enforce turn/epoch ownership, interruption, and stale/replay rejection. FAST audio remains independent of SLOW work. Read results only on an explicit later PTT. Mark `spoken` only after the server observes the complete transmission; this does not prove the Owner heard it. |
| Retention target | Retain eligible terminal Inbox/Ledger records for a 30-day target and protect unresolved, uncertain, or reconciliation records until resolved. This is a requirement target, not evidence that retention or backup is implemented or accepted. |

## D. Voice, privacy and persistence

Keep FAST independent of SLOW. Task text cannot grant tools, credentials, backend URLs or Owner permissions. Callback capabilities are task-scoped, hashed, expiring and revocable; never log secrets, raw tasks or full reports. Keep Callback ingress separate from device TLS-PSK/WSS.

The Grok profile uses the existing SQLite v10 TaskStore. Do not migrate or rewrite old schemas for repository tidiness; keep legacy OCI state separate from a fresh Grok test ledger. Retain ACP/DynamoDB/Hermes code and regression tests while they have compatibility or rollback value. H2 may isolate imports/dependencies but must preserve Token/Owner/TaskStore behavior and explicit selection. Relocation or retirement is optional and needs measured isolation plus an Owner rollback decision.

## E. Acceptance and change control

| Evidence | Establishes | Does not establish |
| --- | --- | --- |
| PR #131 Linux/ARM64 CI | Grok source tests and two Grok-profile services healthy on a runner | OCI deployment, ingress, native Run state or device E2E |
| PR #133 and #132 Owner reports | Images published/pulled, second OCI cutover 2/2 Healthy, SQLite v10, static mount/restart PASS | Public ingress, reboot recovery or device E2E |
| #132 host inventory and second cutover | Old four containers removed without volumes; v8 retained, Grok v10 active | Live provider task or spoken acceptance |
| Callback receipt | Durable receipt for one task | Native Run terminal state, remote Stop or document download |

Review H1 as a docs-only PR and H2 separately for optional-import isolation, explicit executor selections and offline regressions. No docs/code PR authorizes OCI, DNS, network, secret, paid-provider, firmware or data-reset actions. Keep those operations under #132 Owner gates.

## F. Historical record

The prior Hermes-current SPEC is pinned at [main@e615c06](https://github.com/smallgun01/amusant/blob/e615c06259195496ef94d6a6d2c6a038ff06c497/SPEC.md); the earlier v0.4 appendix is indexed from baseline `685db3b` in the history index. Earlier OpenAB/AWS material is also linked there. ADR-0001 through ADR-0004 remain unchanged.
