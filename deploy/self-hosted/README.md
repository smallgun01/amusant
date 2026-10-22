# AI-first host bootstrap - Issue #101 / PR-B1

**Agent instructions:** read this runbook, inspect the approved target, propose a
bounded plan, obtain Owner approval, then apply through the approved privileged
execution path. The agent operates SSH from N100; the Owner is not a command copier.
Reading this file or merging its PR does not authorize an OCI/N100 operation.

## Scope and prerequisites

B1 prepares a **Ubuntu 22.04/24.04 ARM64** host for the existing
[Compose package](../oci-a1/README.md); these releases have offline fixtures, not
live-host certification. Use Python 3.10+, systemd, dpkg/apt, iproute2, util-linux
(`findmnt`, `lsblk`), and the host's existing TLS CA trust. No pip/uv dependency is
needed on the target. No new CLI framework or second Compose stack is introduced.

Supply an approved SSH target, a reviewed source artifact preserving repository
paths, a selected mounted data root and its filesystem UUID, and explicit versions
for missing Docker packages. Resolve versions as the agent from Docker's official
Ubuntu ARM64 package repository; never use fake test versions or assume `latest`.
Private source access must be scoped and approved; do not publish the private repo.
B1 requires no model/device key. B2/B3 own credential injection and runtime inputs.

The data root must be an already-mounted, read/write ext4 filesystem on a disk
separate from the boot disk, root-owned and not group/world-writable. It must not
be a symlink. The 1 GiB free-space floor is for bootstrap only, not image/runtime
sizing. If no such disk exists, report `EXPECTED_DATA_MOUNT_UNAVAILABLE` and ask for
an approved mounted volume or a separately reviewed storage policy; never silently
use the boot disk. Do not create, attach, format or mount disks in this workflow.

## 1. Inspect and plan (read-only)

Run only within delegated read-only access. A missing/denied command, inaccessible
Docker daemon or unidentifiable mount is a blocker, not evidence of absence.
Inventory reports host OS, mount/disk identity, metadata, package state, IPs/routes,
and existing Docker networks/containers. It does not read Hermes learning state or
secret files. Existing `.env` is read only when explicitly selected with `--env-file`;
values are never printed. Without it, use the unchanged package example's networks.

```sh
python3 deploy/self-hosted/bootstrap_host.py --plan \
  --data-dir /mnt/amusant-data --expected-fs-uuid "$AMUSANT_DATA_UUID" \
  --package "docker-ce=$DOCKER_VERSION" --package "docker-ce-cli=$DOCKER_VERSION" \
  --package "containerd.io=$CONTAINERD_VERSION" \
  --package "docker-buildx-plugin=$BUILDX_VERSION" \
  --package "docker-compose-plugin=$COMPOSE_VERSION" \
  --plan-out /tmp/amusant-host-plan.json
```

The agent resolves the variables; they are not credentials or Owner homework.
Omit package arguments when the existing Docker/Compose installation is usable.
The default is `--plan`; no apt/network/systemctl mutation is performed. Only the
explicit plan output is written, atomically, mode 0600, without overwriting files.
The JSON contains private host paths/IPs/UUIDs and must stay outside Git and logs.
The console shows safe action names, versions, relative directories and blockers.
`PLAN_READY_FOR_REVIEW` is not proof of installation or permission to apply.

## 2. Review the authority and exact changes

The Owner approves target, source revision, package versions, disk identity,
changes and stop/retry notes once, rather than approving each SSH command.
An administrator must already have provisioned a **bounded privileged launcher**
for the agent; B1 neither installs that launcher nor edits sudoers/groups. It must
invoke only this reviewed script and a reviewed plan, not an arbitrary shell,
Python file, writable checkout, arbitrary apt invocation or Docker command.

Stage the approved repository artifact under an immutable root-owned directory;
the script, preflight, example, reviewed plan and their ancestors must be root-owned
and not group/world-writable. The reviewed JSON must be mode 0600. Bind the launcher
to the approved source and plan paths for a bounded installation window. Invoke
Python in isolated mode (`-I`). A root-owned plan is an enforced prerequisite,
**not a claim that the script can prove human consent**. Configure/revoke the
launcher separately with Owner approval; no implicit sudo or docker-group access.
If this authority is absent, report `EXPLICIT_PRIVILEGED_EXECUTION_REQUIRED`.

Mutations are limited to missing, explicitly pinned Docker packages and their
required dependencies; official signed apt source/key files only when absent;
`systemctl enable --now docker`; and these **absent** data directories:
`relay` 10001:10001/0700, `hermes` 10000:10000/0700, `results` root:root/0755,
`results/facade` 10003:10003/0700. No recursive ownership changes or existing-file
replacement. Existing incompatible metadata, conflicting apt sources or packages,
and package changes with existing containers require separate review.

Review [Docker's official apt procedure](https://docs.docker.com/engine/install/ubuntu/).
The script uses that repository and HTTPS signing-key endpoint, not `curl | sh`.
Before a Docker package install or service start, it checks the default Docker and
containerd data directories, Docker/containerd configuration, and systemd unit
overrides. Existing state or an opaque custom startup configuration is `BLOCKED`;
the bootstrap does not manage those settings. This check also covers package
post-install hooks that may start the daemon automatically. It simulates the APT
transaction, rejects unfinished `dpkg` configuration and unrelated pending
package configuration, forbids removals/upgrades of installed packages, and uses
bounded command timeouts. Installing/starting Docker can create its ordinary
bridge/firewall rules; those daemon side effects belong in this approval. B1 does
not open application ports, edit cloud networking, disable a firewall, or start
containers (including `hello-world`). A stopped daemon with existing unverified
state is blocked rather than auto-starting potentially unrelated workloads.

## 3. Apply, recheck and report

Through the already-approved launcher, equivalent to this command **as root**:

```sh
/usr/bin/python3 -I /opt/amusant/bootstrap/REVIEWED_REF/deploy/self-hosted/bootstrap_host.py \
  --apply --plan-file /etc/amusant/bootstrap/REVIEWED_PLAN.json
```

Do not paste the illustrative paths unchanged. Recompute the complete plan before
any mutation, compare its code/input/state hashes and allowed actions, and reject
stale/tampered plans. JSON is never evaluated as a shell program. Recheck the mount
before every action and check package versions, daemon, Compose and directory
metadata afterward. Existing app data/config/keys remain untouched.

On success report **`HOST_PREPARED`**, the bounded changes, and B2/B3 next steps.
On error report **`BLOCKED`**, completed action count and the possibly partial
failed phase. Stop; preserve state; inspect and produce a new reviewed plan before
retrying. Atomic plan/key/source writes do not clobber existing files. A partially
created directory with wrong metadata needs an explicitly reviewed repair: do not
silently chown it. Do not automatically uninstall Docker, restart existing apps,
remove host configuration, delete data, or run `compose down -v`. Privilege revocation
and any removal of bootstrap-created host files require separate approval.

## Evidence boundaries and tests

B2 supplies Hermes seed, preserve-on-update tests and effective tool/plugin policy.
B3 verifies actual image digests/ARM64 compatibility, starts the **existing** four
services and tests the deployed-stack synthetic path before `READY_OFFLINE`.
Static preflight only checks input syntax/networks; incomplete B2/B3 inputs are
explicitly deferred. Real providers, #27 ingress, off-host restore and #62 E3 are
separate gates. No host-readiness result is an E2E result.

```sh
python3 -m unittest discover -s deploy/self-hosted/tests -v
python3 -m unittest discover -s deploy/oci-a1/tests -v
```

Tests mock host/package/daemon/network calls and privileged identities; only
throwaway filesystem fixtures are changed. CI retains existing regression jobs
and adds one offline bootstrap step. Fake fixtures do not prove a real apt install.
A fresh and repeat installation on an approved Ubuntu ARM64 host still needs a
separately authorized acceptance window; no live-host success is recorded here.

Borrowed approach: OpenAB's [AI-owned orchestration](https://github.com/openabdev/openab/blob/main/DESIGN.md)
and [staged install guide](https://github.com/openabdev/openab/blob/main/docs/ai-install-upgrade.md).
Borrow the runbook/success/failure discipline, not Kubernetes, Helm or `oabctl`.

