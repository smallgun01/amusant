# Project Amusant

**Current evidence snapshot (2026-10-30): Grok-first source and OCI staging.** Source HEAD at review: [main@96ae859](https://github.com/smallgun01/amusant/commit/96ae8594d11f25f11349420a6979be9387a99630). Last explicitly recorded OCI image baseline is `e615c06`; current live image digest has not been re-inspected in this documentation review.

Grok profile: [compose.grok.yaml](deploy/oci-a1/compose.grok.yaml) selects `grok_callback`, Relay + Egress Proxy, SQLite v10 TaskStore/Inbox; no active Hermes or Facade. [#138](https://github.com/smallgun01/amusant/pull/138) merged ACP/DynamoDB lazy-import separation and optional Boto3; it is source-only until explicitly released. Hermes/ACP remain explicit compatibility choices; never automatic fallback.

## OCI and acceptance

The [second Grok OCI cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695) reported two healthy containers and SQLite v10. Subsequent [G4 10/28 OCI FAST physical PASS](https://github.com/smallgun01/amusant/issues/132#issuecomment-6465592941) established audible Gemini FAST on StickS3. In a **separate direct-Relay** Grok task, [G4 10/28 real callback and physical speech](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361) establishes a real public HTTPS Callback, durable Inbox and audible result on a later physical PTT; the observed `spoken` acknowledgment **failed** (`read`, not `spoken`). This is **not** a complete Live-originated SLOW E2E PASS. [#143](https://github.com/smallgun01/amusant/pull/143), [#144](https://github.com/smallgun01/amusant/pull/144), [#147](https://github.com/smallgun01/amusant/pull/147), [#149](https://github.com/smallgun01/amusant/pull/149) and [#151](https://github.com/smallgun01/amusant/pull/151) are merged source changes; no post-patch installed bundle or physical acceptance is inferred. See [STATUS](STATUS.md) for the dated gate matrix.

A Callback receipt is not native Grok terminal state or remote Stop; unknown dispatch is not retried. Result Contract v2 remains bounded; speak only on explicit later PTT. [SPEC](SPEC.md) · [STATUS](STATUS.md) · [PLAN](PLAN.md) · [WS Index](docs/project-management/workstreams.md) · [Historical baseline](docs/history/20261025-documentation-baseline.md).

[Merged PR #139](https://github.com/smallgun01/amusant/pull/139) updated source templates; actual OCI adoption of its 0.5 CPU / Zephyr / Search settings cannot be inferred from a merge. Preserve Owner-local effective Compose until separately approved replacement.
