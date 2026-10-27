# G4-D2c Grok ARM64 image release and staging runbook

This is a preparation guide. It does not publish images, access OCI, create secret files or data directories, stop a service, configure ingress, or submit a Grok task. Issue #132 Gate 0C still has unresolved read-only evidence, and no staging or release GO is implied by this PR.

## Manual image release

The workflow `.github/workflows/grok-arm64-image-release.yml` only supports `workflow_dispatch` from `main`. Its defaults leave publication disabled. An unapproved/default dispatch succeeds as a no-op and does not run the image job. Publication requires all of the following:

1. Separate Owner GO for publishing the two images.
2. The Owner account `smallgun01` starts the workflow against `main`.
3. Set `publish_images=true` and select `publish_confirmation=PUBLISH`.

The publish job alone receives `packages: write` (and `contents: read`) through its short-lived `GITHUB_TOKEN`. It pushes only Relay and Egress Proxy, each tagged with the source commit SHA. The Actions job summary and outputs contain immutable `ghcr.io/...@sha256:...` references. No registry visibility change, provider credential, OCI secret, or deployment is part of the workflow. Before a later cutover, the Owner must separately confirm the OCI host has authorized GHCR read access; do not put a pull credential in this repository or the env file.

Copy those two digest references into `deploy/oci-a1/grok-staging.env.example` when preparing the Owner-controlled environment. Treat a Grok Routine URL as sensitive if its path embeds a bearer-like value; keep actual values in Owner-managed files and out of logs, PRs, and issues.

## Staging configuration

The env example is intentionally non-runnable: it contains invalid digest and identity placeholders, `.invalid` hostnames, candidate CIDRs, and secret file paths only. It must not be copied to a live `.env` unchanged. Before use, the Owner must:

- Confirm the exact legacy Compose file/project, image refs, active-task state, host/VNIC addresses, Docker networks and OCI route overlap. The reported legacy networks overlap the earlier Grok sample ranges; never start old and Grok projects side-by-side on those ranges.
- Select/verify non-overlapping private CIDRs and actual bind IPs. The candidate ranges in the template are examples only, not an allocation or network-change approval.
- Use the new empty `/mnt/amusant-data/grok/relay` path for a fresh SQLite Schema v10 ledger. Keep `/mnt/amusant-data/relay` and its existing SQLite v8 database unchanged; never point Grok at that path. Create and assign the new directory only after separate staging GO.
- Provision secret files separately after GO, check the permitted `0400` or `0440` file modes and owner/group expected by the Compose profile, and protect the env file because the Routine URL could itself be sensitive.
- Replace both image placeholders with the exact digest refs from the release workflow and set the Owner identity plus approved canonical URLs.

Before stopping anything, run the Grok preflight and static Compose validation locally on the Owner's host. Do not print rendered Compose/environment or secret values:

```sh
python3 deploy/oci-a1/preflight.py --profile grok --env-file deploy/oci-a1/.env
docker compose --project-name amusant-oci-a1-grok \
  --env-file deploy/oci-a1/.env -f deploy/oci-a1/compose.grok.yaml config --quiet
```

## Stop-and-Replace (after a separate exact Owner GO)

1. Record the verified old Compose path/project, old image IDs/digests, restart command, data mount, and a sanitized health snapshot. Inspect active work and reconcile/abandon any uncertain task before taking Relay offline. Do not disclose raw inspect/env/log output.
2. Pull the digest-pinned Grok images and validate the Grok config before cutover. Confirm the fresh v10 data path is distinct and the old Docker networks are known.
3. In the Owner's authorized SSH session, stop and remove only the verified legacy Compose project, without deleting volumes or data:

   ```sh
   docker compose --project-name "$OLD_PROJECT" -f "$OLD_COMPOSE_FILE" stop
   # After confirming the stop and exact project/file:
   docker compose --project-name "$OLD_PROJECT" -f "$OLD_COMPOSE_FILE" down
   ```

   Do not use `down --volumes`, `docker rm`, `docker network rm`, or prune commands. Verify the overlapping project networks were released and the v8 data directory is still present and unchanged.
4. Only after the new data path and secrets are ready, start the Grok-only two-service project:

   ```sh
   docker compose --project-name amusant-oci-a1-grok \
     --env-file deploy/oci-a1/.env -f deploy/oci-a1/compose.grok.yaml pull
   docker compose --project-name amusant-oci-a1-grok \
     --env-file deploy/oci-a1/.env -f deploy/oci-a1/compose.grok.yaml up -d
   docker compose --project-name amusant-oci-a1-grok \
     --env-file deploy/oci-a1/.env -f deploy/oci-a1/compose.grok.yaml ps
   ```

   Confirm only Relay and Egress Proxy are present, both healthy, and the new ledger reports schema v10. Startup health is not Callback public-ingress, StickS3 external-ingress, or Provider acceptance. Keep Gate 2 Callback routing and Gate 3 device ingress disabled until separately approved. Do not dispatch a real task as part of image release.

## Rollback

If the Grok-only startup fails, or Owner elects to revert:

1. Stop the Grok project using its verified env file and Compose path. `down` without `--volumes` releases its networks and leaves the new v10 directory intact for Owner review.
2. Confirm no Grok process/listener remains and the legacy network ranges are available.
3. Restore the verified legacy Compose file and recorded image references, using the Owner's recorded command and `--pull never` where those exact images remain locally available. Confirm the legacy project is healthy and points to its original v8 data path.
4. Preserve both data directories. Do not point legacy services at the new v10 ledger, delete either directory, or automatically replay any uncertain Grok Webhook. Reconcile or explicitly abandon a task whose remote state is unknown.

If the old Compose source, images, or restart command cannot be verified before cutover, do not stop the legacy project: the rollback prerequisite is not met. This runbook does not authorize deployment, secret provisioning, listener/tunnel/DNS changes, or live Grok/Gemini activity.
