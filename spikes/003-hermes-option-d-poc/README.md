# Hermes Option D offline proof

This proof answers Issue [#73](https://github.com/smallgun01/amusant/issues/73): can the pinned, unmodified Hermes server write a full report through a native plugin, use its output hook for a short presentation, and expose only a bounded, validated result through a private facade?

## Pinned runtime

- Hermes image: `docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7`
- Source revision: `f97608f178d1ffeca59860195ab7da295f7c8e5f`
- The run script rejects any Compose configuration that does not use the exact image digest.
- Before the matrix starts, the runner confirms the pinned gateway registered both the artifact tool and output hook, and that the API server exposes the enabled toolset. Plugin debug output stays inside the runner; evidence keeps only registration booleans and a bounded exception type.

The fake provider is a deterministic local OpenAI-compatible server. The proof creates random, throwaway API keys and configures no external model provider. The host must download the pinned image before the run; all proof containers themselves have no external network. The proof does not call a paid provider.

## Run

From the repository root:

```sh
python3 spikes/003-hermes-option-d-poc/run_poc.py
```

Requirements: Docker Engine and Docker Compose v2. The runner uses a fresh Compose project name, isolated internal networks, no host-published ports, and per-service memory limits. It stops only the proof services when finished. It never runs `docker compose down`, removes volumes, prunes Docker state, or restarts other containers. The three proof volumes remain under the unique project name for inspection.

The same command runs automatically in the `issue-73-option-d-poc` GitHub Actions job for this Draft PR branch. The job uploads the sanitized `phase1.json` as a workflow artifact. It does not upload Docker logs or report bodies.

## Proof cases

The harness drives Amusant's real `HermesRunsExecutor`, `SQLiteTaskStore`, and `AsyncDispatcher` through the facade. The fake provider follows the pinned server's native Tool Search route when plugin or file tools are deferred: it searches for the tool, loads its schema, then invokes it through `tool_call`. The plugin receives Hermes's server-provided `session_id`, looks up a facade-created mapping, and does not accept model-supplied owner/task/run IDs, paths, or artifact references. The isolated API-server toolset enables the file tools only for the negative late-mutation case, which attempts a denied write under `/proc`.

Cases cover exact 10,000- and 30,000-byte reports; duplicate writer delivery; no writer; failed and delayed writes; spoofed identity fields; malformed, oversized, missing, and valid short presentations; hook exception, empty return, and timeout; a denied file-tool call after report commit, followed by a final envelope tied to that committed artifact; lost final-envelope delivery; wrong-owner access; idempotent replay and changed-body conflict; GET/SSE bypass; Relay restart with GET-only recovery; ambiguous POST acceptance without replay; and stop.

The Relay audit reads the actual HTTP response body bytes returned by the facade. It keeps only byte counts, HTTP metadata, marker-presence booleans, outcome summaries, and SHA-256 fingerprints of opaque references. It does not retain response bodies or reference values. The private state check verifies file bytes against the committed digest and verifies owner/task/session/run binding, exact report lengths, artifact commit time before terminal observation, and that references seen by Relay match committed artifact rows.

## Evidence and verdict

The workflow result is **PASS** only when every pinned-server check passes. A Docker/image/startup problem is **INCONCLUSIVE**; a completed proof that violates an invariant is **FAIL**. The evidence file labels the runtime and records each exercised case, response cap, artifact check, restart/stop outcome, log-marker scan, and resource result.

| Claim | Source-verified | Pinned-server check | Untested or limited |
|---|---|---|---|
| Plugin tool and output hook are available without a core patch | Plugin registration and hook API are used | Plugin tool call and transform run on the pinned HTTP server | No claim about other Hermes versions |
| Trusted task/run/artifact binding | Facade creates the task/session mapping before the Hermes POST and joins the accepted Run ID | Tool calls resolve only through the Hermes-provided session ID; model-supplied identity fields are rejected | Does not prove production key rotation or multi-host database failover |
| Durable write before success | Plugin fsyncs the file and directory, then commits metadata | SHA, byte length, binding, duplicate delivery, and commit-before-terminal are checked | Local Docker volume is not a replicated production store |
| Fail-closed Relay egress | Facade allowlists submit/status/stop and validates the final presentation | Actual Relay-facing response bodies are capped at 4 KiB; marker scans, wrong-owner, events, and raw-Hermes network tests run | Does not evaluate Gemini Live rendering or production ingress policy |
| Recovery and cancellation | Existing Amusant executor/store/dispatcher are exercised | Restart uses GET after persisted Run ID; ambiguous POST is not replayed; stop is observed through terminal GET | Does not test machine power loss or cross-region recovery |
| Privacy of diagnostic output | Runner stores no raw HTTP response or Docker logs in evidence | Relay-adjacent logs and wire bodies are scanned for synthetic report/final/tool markers | Hermes internal logs are inspected in memory and represented only by booleans; they are not uploaded |

This is an experimental feasibility proof only. It does not change production configuration, `SPEC.md`, ADRs, or runtime defaults, and does not authorize deployment, migration, Ready/Merge, or adoption of Envelope v2.