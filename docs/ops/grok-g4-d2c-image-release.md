# G4-D2c Grok ARM64 image release and staging runbook

This is an Owner-operated staging and rollback guide; it performs no actions. Owner reports in [#132](https://github.com/smallgun01/amusant/issues/132) record PR #133 image publication, digest pre-pull and static staging configuration checks as PASS. The first Stop-and-Replace attempt failed when Grok `egress_proxy` became unhealthy and Relay remained `Created`; automatic rollback restored the legacy four services, all healthy. An isolated proxy diagnostic passed at a local 0.5 CPU limit (up from 0.1); throttling is suspected, not confirmed. #132 records revised retry readiness, but no later cutover. The legacy stack remains installed; no successful Grok deployment, public ingress, live task or device E2E PASS is recorded. The Owner's separate Stop-and-Replace authorization and prerequisites remain in force.

## Recorded release and staging status

The manual workflow was run by the Owner on `main` after PR #133 merged. The published Relay and Egress Proxy digests were pre-pulled and checked as `linux/arm64`; #132 also records the isolated v10 staging path/secrets and static environment audit as PASS. The failed first attempt and successful automatic rollback demonstrate the legacy recovery path; the later proxy-only diagnostic and retry-readiness record do not establish a running Grok profile or a second cutover. Keep the restored legacy stack intact until the documented cutover prerequisites and Owner-controlled sequence are complete.

## Manual image release

The workflow `.github/workflows/grok-arm64-image-release.yml` only supports `workflow_dispatch` from `main`. Its defaults leave publication disabled. An unapproved/default dispatch succeeds as a no-op and does not run the image job. Publication requires all of the following:

1. Separate Owner GO for publishing the two images.
2. The Owner account `smallgun01` starts the workflow against `main`. Both `github.actor` and `github.triggering_actor` are checked in the guard and publish-job condition, so an unauthorized rerun cannot reuse an earlier guard result.
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

1. In one Owner-local Bash session, capture the legacy project name, explicit working directory, ordered config-file paths (`compose.yaml` first, host-local Relay override second), and effective env sources from the running Relay container's Compose labels and verified launch command. Read the `com.docker.compose.project`, `com.docker.compose.project.working_dir`, `com.docker.compose.project.config_files`, and, when present, `com.docker.compose.project.environment_file` labels; verify them against the Owner's original invocation and service configuration. Record env-file paths and names of inherited variables without copying secret values. Keep paths and outputs Owner-local. Do not paste them or raw inspect/config/env output into the issue. Keep the override and working directory for every old-project command. The following placeholders must be filled with the verified values before use:

   ```sh
   OLD_PROJECT='<verified project name>'
   OLD_WORKDIR='<verified absolute working directory>'
   OLD_BASE_COMPOSE='<verified absolute path to compose.yaml>'
   OLD_RELAY_OVERRIDE='<verified absolute path to Relay override>'
   # If the original invocation used --env-file, set OLD_ENV_ARGS to that exact path.
   # If Compose used the working-directory .env, retain it there and use an empty array.
   OLD_ENV_ARGS=(--env-file '<verified env file>')
   # OLD_ENV_ARGS=()  # only when verified against the original invocation
   set -euo pipefail
   cd "$OLD_WORKDIR"
   old_compose=(docker compose --project-name "$OLD_PROJECT" \
     --project-directory "$OLD_WORKDIR" "${OLD_ENV_ARGS[@]}" \
     -f "$OLD_BASE_COMPOSE" -f "$OLD_RELAY_OVERRIDE")
   "${old_compose[@]}" config --quiet
   ```

   Inspect active tasks and reconcile/abandon any uncertain task before taking Relay offline. Verify that all four old image refs resolve locally, compare each ref's local image ID with the running container image ID, and record each ID, RepoDigest, and architecture for rollback. Do not disclose the output:

   ```sh
   set -euo pipefail
   mapfile -t old_images < <("${old_compose[@]}" config --images)
   test "${#old_images[@]}" -eq 4
   for image in "${old_images[@]}"; do
     docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} {{json .RepoDigests}} {{json .RepoTags}}' "$image"
   done
   "${old_compose[@]}" images
   ```

   If any old image is missing, not `linux/arm64`, or its effective Compose ref cannot be shown to resolve to the image ID used by the running container, do not start cutover.
2. Build the Grok command using the reviewed env file and repository working directory. Run Grok preflight and static Compose validation, then pull the digest-pinned images **before downtime**. Verify that Compose resolves exactly two images, each is `linux/arm64`, and each local RepoDigest matches its configured `image@sha256:digest`. Reconfirm the legacy rollback images remain present after the pull. These are Owner-authorized staging actions, not part of this PR:

   ```sh
   set -euo pipefail
   GROK_WORKDIR='<verified Grok source checkout>'
   GROK_ENV_FILE="$GROK_WORKDIR/deploy/oci-a1/.env"
   cd "$GROK_WORKDIR"
   grok_compose=(docker compose --project-name amusant-oci-a1-grok \
     --project-directory "$GROK_WORKDIR" --env-file "$GROK_ENV_FILE" \
     -f "$GROK_WORKDIR/deploy/oci-a1/compose.grok.yaml")
   python3 deploy/oci-a1/preflight.py --profile grok --env-file "$GROK_ENV_FILE"
   "${grok_compose[@]}" config --quiet
   mapfile -t grok_images < <("${grok_compose[@]}" config --images)
   test "${#grok_images[@]}" -eq 2
   "${grok_compose[@]}" pull
   for image in "${grok_images[@]}"; do
     test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = linux/arm64
     docker image inspect --format '{{json .RepoDigests}}' "$image" | grep -F -- "$image"
   done
   ```

   If either Grok digest or any legacy rollback image is absent or unverified, stop here. Next, stop and remove only the verified old project with the same two ordered files, project directory, and env args. Do not use `down --volumes`, `docker rm`, `docker network rm`, or prune commands. Verify old project containers are stopped and its overlapping network CIDRs are released. If any remain attached/in use, do not start Grok; Owner must resolve that condition under the separate GO.

   ```sh
   set -euo pipefail
   "${old_compose[@]}" stop
   # After confirming the exact project is stopped and the separate GO remains valid:
   "${old_compose[@]}" down
   ```

3. Only after the new data path and secrets are ready, start Grok from the pre-pulled local digest refs. Do not allow Compose to pull another tag during cutover:

   ```sh
   set -euo pipefail
   "${grok_compose[@]}" up -d --pull never
   "${grok_compose[@]}" ps
   ```

   Confirm only Relay and Egress Proxy are present, both healthy, and the new ledger reports schema v10. Startup health is not Callback public-ingress, StickS3 external-ingress, or Provider acceptance. Keep Gate 2 Callback routing and Gate 3 device ingress disabled until separately approved. Do not dispatch a real task as part of image release.

## Rollback

If the Grok-only startup fails, or Owner elects to revert:

1. Stop the Grok project using the same verified Grok env file and Compose path. `down` without `--volumes` releases its networks and leaves the new v10 directory intact for Owner review.
2. Confirm no Grok process/listener remains and the legacy network ranges are available.
3. Restore the verified legacy project using the exact same working directory, env references, project name, and ordered base-plus-Relay-override files captured above. Use the recorded old image refs and `--pull never` so rollback uses the verified local images:

   ```sh
   "${old_compose[@]}" up -d --pull never
   "${old_compose[@]}" ps
   ```

   Confirm the legacy project is healthy and points to its original v8 data path.
4. Preserve both data directories. Do not point legacy services at the new v10 ledger, delete either directory, or automatically replay any uncertain Grok Webhook. Reconcile or explicitly abandon a task whose remote state is unknown.

If the old Compose source, two-file order, working directory, env references, images, or restart command cannot be verified before cutover, do not stop the legacy project: the rollback prerequisite is not met. This runbook does not authorize deployment, secret provisioning, listener/tunnel/DNS changes, or live Grok/Gemini activity.
