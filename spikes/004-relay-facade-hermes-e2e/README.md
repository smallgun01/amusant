# Issue #85 PR-B2: offline integrated E2E

This native ARM64 Compose harness runs the current Relay application and SQLite dispatcher, the production Option D Facade image, the pinned Hermes image, and a deterministic local fake model provider. The container networks are internal and separate:

- Relay joins only relay_api.
- Hermes and the fake provider join only hermes_api.
- The Facade joins both as the sole bridge.
- No service publishes a port or joins an outbound network. The test checks Hermes DNS, direct Hermes GET, direct Hermes SSE, and the Relay-facing Facade SSE route.

Hermes is pinned to docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7, tested as linux/arm64. The existing Hermes Option D proof remains separate; this test mounts deploy/oci-a1/hermes_plugin and exercises the production plugin and Facade contract.

Run on a native ARM64 Docker host:

    python3 spikes/004-relay-facade-hermes-e2e/run_e2e.py

The runner creates unique synthetic credentials and temporary named volumes, runs the actual Relay WSS app with a fake Gemini Live provider, submits slow work through delegate_task, recreates the Relay runtime and Dispatcher inside the same test container against the same SQLite database, reads the bounded Inbox result, and checks stop and uncertain recovery. This exercises in-process runtime recovery; it does not kill or recreate the Docker container. It also verifies that a wrong Facade key is rejected and another owner scope cannot read a completed Run. The fake Hermes model generates 10,000-byte and 30,000-byte reports. The report content and credentials are never written into the CI evidence file. Container logs are scanned in memory for synthetic full-report markers and are not printed.

The Relay container separately attempts raw Hermes DNS lookup, direct TCP access to its IP and port, an HTTP GET, and an SSE GET; the Facade's Relay-facing SSE route must also remain closed. The Facade read-only audit verifies committed artifact lengths, SHA-256 values, and trusted task/session/run bindings; it also checks that each Facade request produced exactly one Hermes upstream POST. The local contract tests check the fake model sizes and tool name, the pinned Hermes image, private Compose networks, route probes, and the native-ARM64 CI trigger. The existing CI job `Pinned Hermes Option D recovery proof` continues to run the Issue #79 fake-provider security cases, including spoofed identity fields, absent or damaged artifacts, malformed and oversized presentations, denied model-provided file writes, and wrong-owner access. The B2 job adds the real Relay path and network-routing proof.

details_md remains a bounded legacy Hermes presentation input (at most 512 characters). The Facade validates the presentation and removes details_md from the Relay envelope; Relay keeps its pre-#56 storage slot populated with the short speak value. This test does not claim the legacy field has been removed.

A PASS is offline E1 integration evidence only. It is not an OCI deployment, a live-provider test, an OCI canary, a production security ingress review, or a performance result. The Compose limits are CI guardrails; they do not establish capacity or 1 OCPU performance.
