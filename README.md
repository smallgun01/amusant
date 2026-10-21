# Project Amusant

Amusant is an independently maintained, **one-owner, PTT-first voice companion**. StickS3 is the physical edge; Gemini Live is the FAST conversational path; Hermes Native Runs performs delegated SLOW work. Kai is a pinned upstream interoperability reference. Muse Charm is a category/UX benchmark, not a compatibility or feature-parity promise.

> **Current implementation target (reviewed 2026-10-21; service not yet deployed):** a single OCI Ampere A1 host with isolated **Relay → Option D Facade → Hermes** roles, a persistent SQLite Task Ledger/Inbox and separate Hermes/Facade artifact state. The egress proxy is a network boundary, not another Agent/Worker. [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md) governs this target. AWS ECS/OpenAB/DynamoDB are historical proof/explicit legacy alternatives, **not** a second active M1 backend. An existing OCI Ubuntu ARM64 VM is now owner-reported SSH-reachable from N100, but no OCI service deployment, public device ingress, paid Hermes call, real S1 or physical cloud Slow E2E has passed. See [OCI host readiness](deploy/oci-a1/HOST-READINESS.md).

## Current delivery status

| Workstream | Verified | Outstanding product gate |
| --- | --- | --- |
| [WS1 / #47](https://github.com/smallgun01/amusant/issues/47) — StickS3 & FAST | Owner-reported local StickS3 FAST 10/10; Jellii six states; #69 BLE+CJK and #86 OpenCC merged. [#92](https://github.com/smallgun01/amusant/pull/92) W2b-1 configured BLE re-provisioning **merged / offline-tested** | [#91](https://github.com/smallgun01/amusant/issues/91) BLE teardown hardware regression remains unresolved ([#95](https://github.com/smallgun01/amusant/pull/95) **Draft**); QR display [#96](https://github.com/smallgun01/amusant/issues/96)/[#97](https://github.com/smallgun01/amusant/pull/97) **Draft**, not on main or physically accepted; #67 CJK/OpenCC, H0 audible latency/battery, #27 G1 cloud ingress. W2b is not an M1 G2 prerequisite |
| [WS2 / #48](https://github.com/smallgun01/amusant/issues/48) — Hermes | Native Runs/GET-only/single-flight #45/#60/#65; Option D #75/#80; [#87](https://github.com/smallgun01/amusant/pull/87)/[#90](https://github.com/smallgun01/amusant/pull/90) merged integrated **offline E1 GO**. [#94](https://github.com/smallgun01/amusant/pull/94) H1.2 **offline effective-toolset preflight** merged; Case A/B PASS with fake provider | [#56](https://github.com/smallgun01/amusant/issues/56) full input/v2/section-read and artifact lifecycle, [#58](https://github.com/smallgun01/amusant/issues/58) explicit real-provider timing/STOP/cost decisions and consented live validation. #94 does not authorize real tools, provider calls or production |
| [WS3 / #49](https://github.com/smallgun01/amusant/issues/49) — OCI & E2E | #70 offline OCI package, #83 native ARM64 image gate and [PR #87](https://github.com/smallgun01/amusant/pull/87)/[PR #90](https://github.com/smallgun01/amusant/pull/90) integrated Relay→Facade→Hermes→SQLite/Inbox E1 (Issue [#85](https://github.com/smallgun01/amusant/issues/85) closed) | Separately approved OCI E3 canary, off-host backup/restore, #46 real software S1, #27 ingress and physical G2/G3a |
| [WS4 / #50](https://github.com/smallgun01/amusant/issues/50) — operations | Offline regression, bounded shutdown, SQLite online backup/integrity/retention audit (#78) | Off-host encrypted backup/restore, load & cost, cancel behavior and E3/E4 operational gates |

**E1 evidence boundary (2026-10-19):** [native ARM64 E2E #39240814687](https://github.com/smallgun01/amusant/actions/runs/39240814687) and [regular CI #39240814701](https://github.com/smallgun01/amusant/actions/runs/39240814701) passed the synthetic-provider B2 integration. STOP handling was exercised, but pinned Hermes returned `failed / interrupt`; `stop_cancel_confirmed=false`. This does **not** establish successful active-run cancellation, billable-provider settlement or OCI availability.
The last full hardware/real-provider acceptance is **not** implied by a green CI or a merged PR. See [STATUS.md](STATUS.md) for evidence scope.

## Documentation authority (read in this order)

1. **[SPEC.md](SPEC.md) — v0.5 candidate**: current product requirements, non-goals, trust boundaries and acceptance. Its clearly marked v0.4 appendix is historical only.
2. **[ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md)**: accepted OCI/Hermes/SQLite decision and target trust boundary, subject to gates. [ADR-0001](docs/adr/0001-openab-acp-slow-path.md) and [ADR-0002](docs/adr/0002-external-task-store.md) document earlier AWS/OpenAB decisions, not the present target.
3. **[PLAN.md](PLAN.md)** and **[WS1–WS4 execution index](docs/project-management/workstreams.md)**: sequencing and responsible workstreams; they cannot silently redefine product requirements.
4. **[STATUS.md](STATUS.md)**: linked implementation/test/deployment evidence with dates. It never grants deployment authority.
5. **[Hermes result-delivery proposal](docs/hermes-h1.2-result-delivery-proposal.md)**: feature proposals versus shipped **offline** result boundary; [#94](https://github.com/smallgun01/amusant/pull/94) is a separate **toolset-only E1** preflight, not full H1.2 acceptance. [OCI offline package](deploy/oci-a1/README.md) and [host-readiness handoff](deploy/oci-a1/HOST-READINESS.md) are **not** a live canary runbook or authorization.

**Evidence vocabulary:** E1 merged/offline; E2 real local/physical; E3 bounded real cloud/integration; E4 sustained operational acceptance. Tests do not advance to a higher tier without matching evidence and explicit owner approval. Repository edits are **not** approval for OCI/AWS/Cloudflare changes, production ingress, hardware flashing, secret changes or paid model execution.

## M1 product loop

StickS3 PTT → authorized Relay/Gemini FAST → durable delegated task → Facade-sanitized Hermes result → owner-scoped Inbox → **new tool-disabled Live session reads bounded `speak` on explicit PTT**. No unsolicited voice, general task queue, third-party tenancy, extra Agent, raw report to Gemini, or automatic AWS data migration.

**Critical path (after #85 E1 GO):** accepted offline Facade integration [PR #87](https://github.com/smallgun01/amusant/pull/87)/[PR #90](https://github.com/smallgun01/amusant/pull/90) → [#62](https://github.com/smallgun01/amusant/issues/62) OCI preflight and separately approved bounded E3 canary → [#46](https://github.com/smallgun01/amusant/issues/46) genuine software S1 → device G2 then G3a physical Slow closed loop. [#27](https://github.com/smallgun01/amusant/issues/27) secure G1 edge ingress and #56/#58 product-policy preparation run **in parallel** with the canary prerequisites; no new feature backend or deploy permission is implied.

## Project references

- Upstream Kai firmware: [zbruceli/kai](https://github.com/zbruceli/kai), pinned revision `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`. Its source is not copied wholesale.
- [FAST provider and tests](fast_voice/README.md) · [Relay usage](relay/README.md) · [Historical AWS bounded test](docs/p4-t4b-live-acceptance.md) · [Historical P0 record](SPEC-P0-DELTA.md).
