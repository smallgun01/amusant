# Issue #82 Phase 1A ARM64 decision memo

Date: 2026-10-18  
Scope: offline CI only; parent architecture review #62 Phase 0; Option D accepted in PR #75.

## Current recommendation

**GO for the Phase 1A native ARM64 image and runtime gates only.** Workflow run [#39137736138](https://github.com/smallgun01/amusant/actions/runs/39137736138) on PR head `9c3808c59616f94537dad9679c0d740379a3dec4` reported GO with all 12 checks PASS: native ARM64 runner; exact Hermes manifest and pull; image architecture; Python/native ELF imports; Hermes CLI; Relay native build/runtime imports; Opus and TLS-PSK tests; persistent Relay entrypoint; and Option D fake-provider proof. The sanitized `arm64-gate.json` and `phase2.json` are in the [workflow artifact](https://github.com/smallgun01/amusant/actions/runs/39137736138/artifacts/12140328236).

This GO does not approve OCI deployment or establish 1-OCPU performance. The source-only upstream study in [issue #82](https://github.com/smallgun01/amusant/issues/82#issuecomment-6250887411) did not establish exact-digest runtime compatibility; the native run now supplies that missing evidence.

Pinned Hermes candidate:

- Image: `docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7`
- Upstream source: `f97608f178d1ffeca59860195ab7da295f7c8e5f`

The gate distinguishes registry manifest evidence from the selected image config, requires a native Linux ARM64 runner and Docker daemon, explicitly pulls `linux/arm64`, imports the pinned Hermes runtime modules, checks representative ELF machine IDs, and runs Hermes CLI checks. A passing manifest alone is not a GO.

## Phase 1A gates

The CI command is `python3 deploy/oci-a1/arm64_gate.py` on `ubuntu-24.04-arm`. It records PASS/FAIL/UNKNOWN for each claim and uploads sanitized evidence.

1. **Hermes digest:** exact registry digest and source commit above; ARM64 manifest or pulled config; `/opt/hermes/.venv/bin/python`; imports `aiohttp`, `cryptography`, PyNaCl, `pydantic_core`, `websockets`, and PyYAML; AArch64 ELF for the cryptography, Sodium, and Pydantic native extensions; `hermes --help` and `hermes --version`.
2. **Option D compatibility:** run the merged PR #75 proof on the same digest and `linux/arm64`, exercising the private facade/plugin, fake-provider Runs submit/status/stop/restart, recovery, and result-envelope boundary. No paid model or real provider is configured.
3. **Relay image:** build `deploy/oci-a1/Dockerfile.relay` natively for ARM64 from the locked root `uv.lock` with Python 3.13 and `hardware-opus`, then verify that PyAV exposes its `libopus` decoder/encoder. Import PyAV/NumPy, create the Opus codec and TLS-PSK context, run the existing Opus and TLS-PSK tests, then start the real StickS3 Relay entrypoint on an internal Docker bridge with synthetic file-backed secrets. The listener must remain healthy, not log synthetic secret values, and stop cleanly.
4. **Evidence:** `arm64-gate.json` records image/source pins, platform, Python version and interpreter, native modules and ELF checks, Relay build/config IDs, selected executable checks, and an overall verdict. The separate Option D runner writes `spikes/003-hermes-option-d-poc/evidence/phase2.json`.

Decision rule:

- **GO:** every required native, Relay, and Option D gate passes.
- **NO-GO:** the exact digest lacks ARM64, contains incompatible native modules, fails its CLI/runtime checks, or the Relay image/startup contract fails.
- **UNKNOWN:** registry/runner infrastructure prevents proof or any required evidence is incomplete. UNKNOWN blocks Phase 1B.

The GitHub-hosted `ubuntu-24.04-arm` label is available for private repositories according to the [GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). The job verifies both host and Docker daemon architecture before claiming native evidence; it does not install QEMU.

## Option D packaging gap inventory

PR #75's proof topology is an isolation reference, not a deployment package:

- The POC places Hermes and its fake provider on an internal network, with the facade connected to that network and a separate internal Relay-facing network. Relay reaches only the facade.
- Its `poc_state` SQLite volume and deterministic fake provider exist only for proof. Do not copy either into the production Compose package.
- The current `deploy/oci-a1/compose.yaml` predates Option D and still gives Relay a direct Hermes Runs route on `relay_api`. It is **not eligible for canary** under the accepted boundary.

Minimum Phase 1B packaging change: add a private facade service; put Hermes and facade on a dedicated internal Hermes API network; put facade and Relay on a distinct internal Relay-facing network; attach Relay to no Hermes API network and remove its raw Hermes Runs/SSE URL. Keep the Hermes bearer credential between facade and Hermes, enforce the facade result-envelope contract, and test that a compromised Relay cannot route to Hermes directly. Select production Relay Ledger/Inbox and Hermes Home/artifact persistence independently; do not reuse POC SQLite or provider state. This PR does not modify the deployment Compose topology.

## Remaining gates

A CI GO means image/runtime compatibility only. It does **not** establish that Oracle A1 with 1 OCPU / 6 GiB meets FAST voice latency or contention targets. Phase 1B still needs the owner-approved private Compose topology, persistence/restart/recovery, ingress/security and backup/restore design, fake-provider end-to-end flow, and resource measurements. Any OCI host access, cloud changes, canary, provider credential, or real model call requires separate owner approval.

If the exact published image is NO-GO, stop and review a native ARM64 rebuild from the same pinned upstream source commit with an architecture-isolated dependency cache. Do not switch tags or fork Hermes core silently.
