# P4 private ACP WSS — offline PoC

**Status:** local-only prototype; not a Jellii runtime result and not an AWS deployment approval. See [Issue #22](https://github.com/smallgun01/amusant/issues/22).

## Source/provenance check

The deployed Jellii task definition pins image digest **sha256:5a41809b512b5a767d10d2a64ac51f3096bd2ad0fba8cfb1fdd8dd771f8601d5**. Its OCI config exposes no org.opencontainers.image source labels, so the digest could not be tied conclusively to a source commit from image metadata. The available OpenAB release tag openab-0.9.0-beta.8 points to source commit **4f28c2dfb36b8f6083756b2b04f6202443d136e9**; its src/main.rs listener uses tokio::net::TcpListener::bind and axum::serve, but a release-tag source inspection is **not proof that this exact image digest came from that commit**.

The live task definition has GATEWAY_LISTEN=0.0.0.0:8080, only a TCP 8080 port mapping, and no TLS-related environment configuration. Therefore native TLS support in the exact deployed binary remains **unverified**; this PoC uses the provisional sidecar direction and does not claim otherwise.

## Local PoC

relay/devtools/wss_proxy.py is a dev-only reverse proxy. It:

- binds only to loopback;
- accepts only /acp;
- terminates WSS with TLS 1.2+;
- forwards only the Authorization header and WebSocket frames to a loopback ws://.../acp fake server;
- emits no request headers, bearer values, prompts, or result bodies in logs.

The WSS connection uses the existing OpenAbAcpExecutor with its default certificate and hostname verification enabled. Tests generate a short-lived private CA and CA-issued server certificates in a temporary directory; no certificates or private keys are checked in. The test CA is supplied to the test process through SSL_CERT_FILE.

Run from the repository root:

~~~bash
PYTHONPATH=.:relay uv run --locked python -m unittest relay.tests.test_wss_proxy -v
~~~

The fake ACP test exercises initialize, session/new, session/update, and one terminal session/prompt. It is an offline protocol/TLS test, **not** live OpenAB/Jellii, AWS, or model evidence.

## Eventual AWS design gates (not implemented here)

- Use a stable private DNS name covered by the sidecar certificate SAN; Relay must trust the issuing CA through its normal trust store or an explicitly reviewed CA-bundle mechanism.
- Store and rotate server private keys through an approved secret/certificate lifecycle; never bake a private key into an image or repository.
- Allow only Relay-to-sidecar TLS ingress (candidate port 8443); deny task-network ingress to OpenAB's plaintext 8080. The sidecar-to-OpenAB hop must remain loopback-only inside the same ECS task.
- Account for proxy CPU/memory and ECS container health/startup dependency behavior. Preserve Jellii desired/running/pending 0/0/0 outside separately approved one-off tests.
- Verify negative cases: wrong CA, hostname mismatch, missing/bad Bearer, timeout, and disconnect fail closed; no CERT_NONE, public ACP, or plaintext override.
- Any live WSS smoke requires a new, bounded execution envelope and explicit authorization. Device ingress (Kai TLS-PSK), public exposure, and 24/7 Relay remain separate decisions.
