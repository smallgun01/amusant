# Grok G4-D1 Offline Adapter

This note records the offline implementation authorized by Issue #127. It does not authorize mounting the Callback app, enabling a Routine, deploying a listener, or switching the current Hermes backend.

## Identity and delivery boundary

New Grok tasks are persisted with `backend='grok_bot'`, `executor_kind='grok_callback'`, and result contract v2. The existing Gemini Live tool remains `delegate_task(task)`; Gemini does not select a backend, URL, credential, or command. The Relay selects Grok from server configuration. StickS3 framing and its FAST Gemini path are unchanged.

The Grok executor sends one Routine Webhook POST through the explicitly configured HTTPS egress proxy. A SQLite transaction writes the per-task token hash, token expiry, callback wait deadline, and `dispatch_started_at` before the network call. Redirects are not followed. A timeout, non-200 reply, sender crash after that transaction, or lost ACK becomes acceptance unknown; no code path automatically retries the Webhook or falls back to Hermes.

## Callback receipt

`create_grok_callback_app()` returns an ASGI app for exactly `POST /callback/results`; it does not bind a socket. The app uses a server-owned Owner mapping, exact Host allowlist and service port, one Bearer capability, duplicate-key rejection, a 16 KiB body cap, a bounded in-flight cap, and the v2-compatible 3,000-byte report validator. It does not consume `Forwarded` or `X-Forwarded-*` headers.

The token is hashed at rest and has a bounded TTL. The callback wait deadline is stored separately, so an overdue task becomes uncertain and stays single-flight while its capability remains valid. A valid final Callback atomically writes the bounded result, receipt digest/ID, task status, and existing Inbox row in the existing SQLite TaskStore. An identical replay returns the same receipt; a different report conflicts. Callback-before-Webhook-ACK wins, and the later ACK cannot overwrite it. `reported_outcome=not_completed` is a failed task with its truthful short result retained for explicit status/audio readback.

The persisted result says “Grok reports …”; native Grok state remains `unknown`. Native `box:` locators are not mapped to Amusant Artifacts or URLs. A Callback is evidence submitted by the Agent, not independent proof of native completion or device delivery.

## Cancel, timeout and Owner Abandon

Before `dispatch_started_at`, Cancel wins atomically and is local. Once sending begins, the runtime never claims a remote Grok Stop. A missing Callback times out to `uncertain` and keeps the Owner slot. The operator-only reconciliation command supports explicit `abandoned`, requires operator/evidence fields and `--confirm-slot-release`, and releases only Relay's local slot. Unless the operator also selects `--revoke-grok-callback-token`, a later valid Callback is recorded as late metadata on that task row; it cannot recreate results/Inbox or reopen the task. Revoked or expired capabilities fail closed.

## Schema and rollback

SQLite v10 keeps Grok lifecycle and receipt columns on `slow_tasks`; it creates no second ledger. Existing runtime data is disposable test data, so a separately authorized staging setup may start with a clean v10 ledger; preserving or restoring legacy rows is not an acceptance requirement. The existing migration remains available but is not a gate. Never automatically resend uncertain work; an in-flight Grok task needs Owner reconciliation or abandon before its local slot is released.

## G4-D2 prerequisites

Before mounting the Callback app or running live work, review and approve the exact HTTPS ingress and reverse-proxy mapping to `/callback/results`, canonical upstream Host/port, TLS, rate and body limits, request timeouts, Authorization preservation/redaction, forwarded-header stripping, callback-token delivery/exposure, and disable/revocation procedures. Owner has designated existing runtime data disposable test data; a clean SQLite v10 database is acceptable for a separately authorized staging gate. Hermes physical G0 validation and legacy data restore drills are not G4 prerequisites. D2 requires explicit Owner environment, permission, quota/cost, and Provider-use GO. This PR performs none of those actions.
