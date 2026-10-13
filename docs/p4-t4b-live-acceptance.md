# P4-T4b — Bounded Live WSS/ACP acceptance (2026-10-13)

**Functional acceptance: PASS (Window 1/2). Operational runbook conformance: exceptions recorded / not fully verified.** This is a one-off deployed integration proof; it does **not** certify a persistent Relay ECS Service, device audio, 24/7 operation, cost, or production readiness.

## Primary evidence (Issue #26)

- [Actual Live run outcome](https://github.com/smallgun01/amusant/issues/26#issuecomment-6148650538)
- [Read-only after-STOPPED durability proof and procedural receipts](https://github.com/smallgun01/amusant/issues/26#issuecomment-6149983722)
- [Independent functional acceptance review](https://github.com/smallgun01/amusant/issues/26#issuecomment-6150189393)
- [Final disposition and Owner monitoring-risk acknowledgement](https://github.com/smallgun01/amusant/issues/26#issuecomment-6150655257)

These are operator-reported AWS results, not new AWS requests made by this documentation change. Preserve infrastructure identifiers, task IDs, secret ARNs/values, private model inputs/outputs and log-stream names outside the repository.

## What passed

| Gate | Evidence result |
|---|---|
| Jellii existing ECS Service temporarily ran task-definition Revision 10 with OpenAB + NGINX on FARGATE 1.4.0 | PASS |
| Real Jellii task ENI/SG allowed only dedicated Relay SG → Jellii TCP 8443 ingress | PASS (controller) |
| Private Cloud Map A record matched actual Jellii task IP | PASS |
| Single Relay FARGATE 1.4.0 RunTask: fresh client TASK# partition + DNS, wrong CA, SAN/SNI, missing/invalid Bearer, non-/acp path and TCP 8080 checks | 8/8 PASS |
| One ACP session/prompt ended successfully; task and Inbox status completed, has_result true | PASS, Relay exit 0 |
| After Relay was independently observed STOPPED, existing reader role queried the **same task ID** with strongly consistent DynamoDB GetItem | PASS, exit 0 / completed / has_result=true / delivery_state=pending / error=null |
| Rollback restored Jellii Revision 9, desired/running/pending 0/0/0, original SG and empty service registry; Cloud Map ListInstances 0 and no active Relay | PASS |

Delivery state `pending` means the completed result is still waiting in Inbox, **not** that execution failed. It is **not** a spoken delivery proof. There was no second RunTask or ACP prompt to obtain after-stop evidence.

## Exceptions and limits

- Independent watchdog and backup operator **were not armed**. Owner explicitly accepted full-window manual monitoring/no backup in direct Telegram at 2026-10-13 20:01 GMT+8 (“全程人工監看並接受無備援風險”; recorded in the linked final-disposition comment). Actual all-window console presence was **not independently recorded**. Never claim original watchdog-gate PASS.
- `deploy/t4b_security_group_gate.py` was **not invoked**; equivalent live-controller revision/sidecar/ENI/SG checks were reported PASS.
- The exact-task emergency StopTask fallback passed offline request-shape tests but was **not needed or exercised live** (run tag verified correct).
- ECS Service registry and Cloud Map final instances were verified empty; deregistration OperationId and TTL/negative-cache wait were **not recorded**.
- Actual AWS/provider billing was **not independently measured**; the US$5 AWS and US$1 provider test budgets were procedural, not real-time hard stops.
- Temporary execution roles, TLS Secrets, log groups, images and task definitions were retained; no destructive cleanup was performed. A prior short-lived leaf certificate cannot be assumed valid for the next live window.

## Follow-on scope

- Target runtime remains **two ECS Services**: Relay Service A (with in-process Async Dispatcher) and existing OpenAB/Jellii Service B. The one-off Relay RunTask used here does not prevent later persistent service deployment.
- Persistent Relay Service A requires a service-appropriate entrypoint, lifecycle/observability/restart validation and an approved private ACP certificate/secret strategy. Do not turn the T4b one-off preflight smoke into an always-on loop by accident.
- Device/Kai-sim PTT → Gemini Live → delegate_task → Relay/ACP → DynamoDB Inbox → user-visible/voice retrieval is **not** yet one verified end-to-end chain.
- Edge transport and StickS3 firmware decisions remain separate (Issues #27, #11, #14).
- Window 2/2 is unused; any new AWS deployment, prompt, retained-resource deletion or long-running service requires its own owner-approved action scope.
