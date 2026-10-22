# OCI A1 H2 host-readiness handoff (2026-10-21)

> **2026-10-22 live test supersession:** OCI four-service backend live functional PASS; physical StickS3 → temporary private SSH tunnel → OCI Relay → Gemini Live audible FAST PASS; physical Slow task admitted but timed out at 120s, then Hermes was independently confirmed `completed` without a saved report. **2026-10-23 Owner operator reconciliation PASS** as `failed / hermes_operator_reconciled_completed_no_result`; old single-flight reservation cleared, but physical Slow→Inbox speech not yet accepted. This is not full E3/E4 or permanent/public device ingress. See [GO 2/3 evidence](../../docs/oci-go2-go3-live-evidence-20261022.md); earlier GO 2/3 NOT STARTED handoff below is historical.

> **2026-10-22 UPDATE — #62:** GO 1 `READY_TO_COMPOSE_UP` is owner-reported PASS: Docker/Compose installed, persistent data mount reboot-checked, images pinned/pulled, file secrets and Hermes config prepared, preflight/Compose static validation PASS. The 2026-10-21 SSH-only inventory below is historical bootstrap guidance, not a checklist to repeat. **GO 2 four-service launch/OAuth/live backend and GO 3 device test NOT STARTED**; formal E3/off-host backup+restore, isolation, load, STOP/cost and #27 ingress pending. Issue #62 flags an N100 SSH alias possibly authenticating as OCI `ubuntu` with passwordless sudo: verify actual privilege boundaries before unattended operation. No new access or runtime authorization by these docs.


> **Evidence tier: owner-reported host SSH bootstrap, NOT E3 or OCI runtime readiness.** This is an operator handoff for [#62](https://github.com/smallgun01/amusant/issues/62). It does not authorize installing packages, changing cloud/host networking, adding sudo or Docker access, configuring credentials, starting containers, exposing ports, touching AWS resources or calling paid providers. [SPEC](../../SPEC.md), [ADR-0003](../../docs/adr/0003-oci-a1-hermes-sqlite.md) and [STATUS](../../STATUS.md) remain authoritative.

## What is known, and the limit of each observation

| Evidence source | Observation | What it does NOT prove |
| --- | --- | --- |
| GitHub main at the review baseline | #87/#90 merged Relay → authenticated Option D Facade → pinned Hermes + SQLite/Inbox synthetic **E1 GO**; #94 toolset Case A/B native ARM64 offline PASS | No running OCI app, real tool/model, active-Run STOP cost settlement or real-world ingress |
| Owner-provided OCI/Linux terminal output (2026-10-21) | Existing Ubuntu ARM64 host `amusant-oci-a1`; `uname -m=aarch64`, `nproc=2`, OS-visible RAM ~11 GiB, swap 0, boot disk ~46.6 GiB; root filesystem ~43 GiB available; no separately attached data disk visible in `lsblk` | Not a signed cloud inventory, OCI billed OCPU quota, usable persistent app volume, confirmed cost or endurance capacity |
| Owner + N100 OpenClaw SSH smoke | Noninteractive `BatchMode` SSH to a dedicated, unprivileged OCI Linux account returned `whoami`, `hostname`, `aarch64`, exit code 0. OCI public-subnet/Internet Gateway routing for management SSH was fixed | No sudo, Docker privilege, OCI API/IAM permission, app deployment, TCP 8443 ingress or hardware E2E |
| Local OpenClaw process listing | Gateway runs under the same N100 Unix user used for manual SSH | Different SSH **remote identities** do not isolate owner private keys from a process sharing the N100 Unix account; local key/sandbox separation still needs a reviewed design |

**Privacy and security:** Do not commit public IPs, client source IPs, private keys, credential filenames/contents, tokens, PoP, public endpoint details or machine-specific `.env` values. SSH public keys may be distributed through an operator-approved channel, not embedded in this repository. Preserve both Owner and Agent access while applying access controls.

## Proposed read-only H2 inventory (requires explicit Owner instruction)

Only run the following after the Owner delegates this scoped inventory; editing or reading this document is **not** permission to log in or execute commands. From N100, use the **existing dedicated Agent SSH key** and the existing remote unprivileged account; never ask OpenClaw to print or transfer the key. Confirm which Unix/service or container identity actually launches OpenClaw and what SSH config it can access. A successful human-terminal `ssh` is not proof of an agent tool permission; the owner's one-time OpenClaw BatchMode smoke did pass.

Inside that SSH session, collect and redact the results of:

```sh
id
uname -m
nproc
cat /etc/os-release
free -h
df -h /
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS
git --version 2>&1 || true
python3 --version 2>&1 || true
command -v docker || true
docker --version 2>&1 || true
docker compose version 2>&1 || true
```

Also inspect **read-only** current container/service presence, mount availability, disk free space, swap situation, selected OCI shape/billing/quota from **Owner-approved Console evidence**, and whether any persistent Block Volume or encrypted off-host backup location exists. Do not assume Docker, Python dependencies, container images or volumes have already been installed. State unknowns explicitly; do not convert an investigation into deployment.

## Review/approval gates before mutating host or cloud

1. Present a minimal proposed H2 install/permissions plan: distro/ARM64 compatibility, Docker+Compose packages and versions, who installs them, exact commands, rollback and privilege boundaries. No blanket `sudo NOPASSWD:ALL`; membership in `docker` is effectively root-equivalent. N100 Owner and OpenClaw running as the same Unix user are **not** true credential isolation.
2. Specify separate persistent storage and backup/restore for **Relay SQLite Ledger/Inbox**, **Facade artifact/ledger**, and **Hermes Home**. A boot disk/Block Volume alone is not an off-host backup. Require SQLite online backup, encrypted independent copy and a measured restore drill before E3 acceptance.
3. Confirm pinned `linux/arm64` image digests, `deploy/oci-a1/compose.yaml` private-only networks and the four non-overlapping Docker subnets; ensure no direct Relay→Hermes, public Facade/Hermes API, unrestricted host mounts, Docker socket or privileged container.
4. Pin secret ownership/mount policy outside Git/`.env`; document egress proxy, identity boundaries and rollback. Starting Compose, pulling a production image, writing secrets and changing OCI IAM/NSG/VNIC are **separate Owner-authorized operations**.
5. **Device ingress is still G1/#27**: present a reviewed transport for existing StickS3 TLS-PSK and Relay's **private-host-IP TCP 8443** binding before any public ingress. SSH TCP 22 proves only management access; it does **not** prove S3/FAST transport.
6. Only after #62 host checks, full off-host restore, security/usage caps and explicit Owner-approved bounded window: run an OCI E3 canary. Then #46 genuine software S1; only afterward #27/G2 and physical G3a. No real provider run, firmware flash, public ingress or automatic AWS resource/data migration is authorized by this document.

## Useful starting references

- [Current application status](../../STATUS.md) · [Execution order](../../PLAN.md) · [WS1–WS4](../../docs/project-management/workstreams.md)
- [OCI deployment package](README.md) · [SQLite operations](SQLITE-OPERATIONS.md) · [ADR-0003](../../docs/adr/0003-oci-a1-hermes-sqlite.md)
- [#62 host/canary](https://github.com/smallgun01/amusant/issues/62) · [#27 ingress](https://github.com/smallgun01/amusant/issues/27) · [#46 S1](https://github.com/smallgun01/amusant/issues/46) · [#56 result policy](https://github.com/smallgun01/amusant/issues/56) · [#58 timing/STOP](https://github.com/smallgun01/amusant/issues/58)
