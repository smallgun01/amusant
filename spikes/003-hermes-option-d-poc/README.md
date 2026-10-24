# Retired Hermes Option D v1 proof

This directory preserves a sanitized historical summary of the isolated Result Contract v1 proof for Issue [#73](https://github.com/smallgun01/amusant/issues/73). The executable v1 harness, Facade, plugin, fake provider, and Compose files were removed when the Owner retired Result Contract v1. Their source remains available in repository history; this directory is not runnable and is not a supported runtime, cutover path, CI job, or current acceptance gate.

The former proof exercised report writes through a private Facade, bounded presentation projection, owner/task/session/Run and Artifact binding, malformed and oversized output rejection, writer failure handling, replay protection, GET-only recovery, ambiguous submission behavior, Stop, and report privacy. Those results apply only to the historical v1 proof and do not establish v2 behavior.

Current v2 security and recovery evidence lives in the active [Relay, Facade, and Hermes E2E suites](../004-relay-facade-hermes-e2e/README.md), [Result Contract v2 cutover runbook](../../docs/result-contract-v2-cutover.md), and targeted tests. Do not use this historical summary to infer the state of the live OCI deployment.
