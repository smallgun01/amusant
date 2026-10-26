# G3-P1 callback adapter offline evidence

Date: 2026-10-26 (Asia/Taipei)

Base: `main@fabf098d5cd0756e02fbb5cbf014c6029438e6e1` with the merged G2 spike.

## Local verification

| Check | Result |
| --- | --- |
| Focused callback suite, `unittest discover -p test_callback.py -v` | PASS — 17 tests, including mocked foreground-runner configuration and Host-with-port validation. |
| Full spike suite, `unittest discover -s spikes/005-grok-bot-validation/tests -v` | PASS — unittest reported 34 tests successful, 32 passed and 2 POSIX-only permission tests skipped on Windows. Existing G2 MCP tests passed unchanged. |
| Callback-mode send guard | PASS — missing/expired TTL made zero Mock Webhook calls and left the task `prepared`; an explicit finite TTL made one mock POST; the `BEGIN IMMEDIATE` transition rechecked expiry. |
| Capability echo hygiene | PASS — exact or embedded capability values in top-level/nested report strings were rejected before receipt insertion. |
| `uv.lock` direct runner metadata | PARTIAL — root project metadata pins `uvicorn==0.54.0`, already present at that version in the resolved lock graph. The `uv` CLI was unavailable locally, and current Draft CI skipped `uv sync --locked`; the full lock check remains pending. |
| `git diff --check` | PASS. |

The machine could not reach PyPI when creating a fresh environment; the locked sync stopped on the `jsonschema==4.26.0` download. To finish verification without relaxing the lock or contacting any provider, the tests used the pre-existing G2 virtual environment read-only with Python 3.13.15, `httpx2` 2.16.0, Starlette 1.10.0, Uvicorn 0.54.0, and MCP 2.3.0. The unittest result object reported success for all 34 tests (2 platform skips); the command wrapper returned exit code 1 after printing that successful result, so the recorded count is from unittest's result object. The checked-in CI job still installs the spike from its lockfile.

## Evidence covered

- SQLite migration adds nullable `tasks.result_token_expires_at` to a G2-format database without rewriting its rows. A legacy row with NULL expiry is rejected by the callback and gets no receipt; the existing G2 regression suite continues to pass.
- `prepare` has no implicit TTL. Explicit values are limited to 1–86,400 seconds. The task Owner, token hash, revocation state, and expiry are checked inside the existing `BEGIN IMMEDIATE` receipt transaction, before both first insert and duplicate handling.
- Tests cover just-before/at-expiry behavior, replay after expiry or revocation, receipt preservation after expiry/revocation, same-result idempotency, changed-result conflict, late result while still valid, Owner mismatch, storage failure, and concurrent duplicate submissions.
- The callback Golden Path used an ASGI Mock Webhook and in-process ASGI callback transport: **Prepare → Mock Webhook → `POST /callback/results` → SQLite receipt → CLI readback**. The Mock Webhook ran once, its fixed business fixture ran once, and replay returned the same receipt without another webhook or fixture execution. `native_state` remained `unknown`.
- HTTP boundary tests cover exact Host/port allowlisting, ignored spoofed forwarding headers, POST-only/no redirect, query rejection, one Bearer header, token-free body, capability echo rejection, duplicate/deep JSON keys, UTF-8, media type, streamed and declared body limits, and bounded in-flight requests.
- `serve-callback` configuration tests replace `uvicorn.run` and verify a single foreground worker bound to `127.0.0.1`, explicit Host/port/Owner/concurrency settings, disabled access logging and forwarded-header parsing, and no reload. No listener was started. `submit --live --callback-mode` has a separate TTL guard; ordinary G2 behavior remains unchanged when the mode is omitted.

## CI scope and untested boundaries

`.github/workflows/ci.yml` is unchanged. On Draft head `36a9541`, changed-path classification, lightweight documentation/diff, and the CI result gate passed. The dedicated/offline and heavyweight jobs were skipped, so CI did not run the spike's `uv sync --locked` or test suite. The existing offline job's command is `uv sync --locked --python 3.13 --project spikes/005-grok-bot-validation` followed by unittest discovery over `spikes/005-grok-bot-validation/tests`; the new and updated tests are included when that job runs. This work does not manually dispatch heavy CI, provider, tunnel, OCI, or live checks.

No listener was started. No real Routine, tunnel, public ingress, HTTPS proxy, OAuth flow, Grok account, provider, credential, production Relay/Facade/Hermes path, or G4 integration was used or verified. Callback reachability, proxy route rewrite/upstream Host mapping, HTTPS, rate limiting, request timeouts, Authorization preservation/redaction, provider-visible task capability context, and live rate policy remain outside this offline evidence and require separate G3-P2 review. `max_inflight` is only a concurrency bound, not a rate limit; the documented proxy requirements are not implemented or selected.
