# G3-P1 callback adapter offline evidence

Date: 2026-10-26 (Asia/Taipei)

Base: `main@fabf098d5cd0756e02fbb5cbf014c6029438e6e1` with the merged G2 spike.

## Local verification

| Check | Result |
| --- | --- |
| Focused callback suite, `unittest discover -p test_callback.py -v` | PASS — 14 tests. |
| Full spike suite, `unittest discover -s spikes/005-grok-bot-validation/tests -v` | PASS — 29 tests, 27 passed and 2 POSIX-only permission tests skipped on Windows. Existing G2 MCP tests passed unchanged. |
| `uv lock --check --offline --project spikes/005-grok-bot-validation` | PASS — 31 locked packages resolved. |
| `git diff --check` | PASS. |

The machine could not reach PyPI when creating a fresh environment; the locked sync stopped on the `jsonschema==4.26.0` download. To finish verification without relaxing the lock or contacting any provider, the tests used the pre-existing G2 virtual environment read-only with Python 3.13.15, `httpx2` 2.16.0, Starlette 1.10.0, and MCP 2.3.0. The checked-in CI job still installs the spike from its lockfile.

## Evidence covered

- SQLite migration adds nullable `tasks.result_token_expires_at` to a G2-format database without rewriting its rows. A legacy row with NULL expiry is rejected by the callback and gets no receipt; the existing G2 regression suite continues to pass.
- `prepare` has no implicit TTL. Explicit values are limited to 1–86,400 seconds. The task Owner, token hash, revocation state, and expiry are checked inside the existing `BEGIN IMMEDIATE` receipt transaction, before both first insert and duplicate handling.
- Tests cover just-before/at-expiry behavior, replay after expiry or revocation, receipt preservation after expiry/revocation, same-result idempotency, changed-result conflict, late result while still valid, Owner mismatch, storage failure, and concurrent duplicate submissions.
- The callback Golden Path used an ASGI Mock Webhook and in-process ASGI callback transport: **Prepare → Mock Webhook → `POST /callback/results` → SQLite receipt → CLI readback**. The Mock Webhook ran once, its fixed business fixture ran once, and replay returned the same receipt without another webhook or fixture execution. `native_state` remained `unknown`.
- HTTP boundary tests cover exact Host allowlisting, ignored spoofed forwarding headers, POST-only/no redirect, query rejection, one Bearer header, token-free body, duplicate JSON keys, UTF-8, media type, streamed and declared body limits, and bounded in-flight requests.

## CI scope and untested boundaries

`.github/workflows/ci.yml` is unchanged. Its existing isolated Grok job runs `uv sync --locked --python 3.13 --project spikes/005-grok-bot-validation` followed by unittest discovery over `spikes/005-grok-bot-validation/tests`; the new `test_callback.py` is included automatically. This CI job has not yet run for the Draft PR.

No listener was started. No real Routine, tunnel, public ingress, HTTPS proxy, OAuth flow, Grok account, provider, credential, production Relay/Facade/Hermes path, or G4 integration was used or verified. Callback reachability, proxy rewrite/preservation of Authorization, provider-visible task capability context, and live rate policy remain outside this offline evidence and require separate G3-P2 review.
