# Amusant ACP WSS sidecar image

This is a deployable image source for the P4-T2 proposal; it is not deployed and does not authorize AWS changes. It uses the verified NGINX unprivileged linux/amd64 base image digest pinned in the Dockerfile, removes the base image's default 8080 site, and exposes only the /acp WSS listener on 8443.

## Local build and checks (no AWS)

From the repository root:

~~~bash
docker build --platform linux/amd64 \
  --build-arg GIT_SHA=$(git rev-parse HEAD) \
  -f deploy/acp-wss-proxy/Dockerfile \
  -t amusant-acp-nginx-sidecar:offline-test \
  deploy/acp-wss-proxy

# The smoke harness supplies temporary certs; the image entrypoint runs nginx -t.
PYTHONPATH=.:relay uv run --locked \
  python relay/devtools/nginx_sidecar_smoke.py \
  --image amusant-acp-nginx-sidecar:offline-test
~~~

The smoke harness runs the pinned NGINX image and fake ACP server on a local Linux host network. Its temporary config binds the test listener to 127.0.0.1 and targets the fake server; it opens no LAN/public listener. With disposable certificates, it completes one valid fake ACP prompt, then checks bad and missing Bearer rejection, a non-/acp 404, a wrong leaf SAN/hostname, and an untrusted CA; no negative probe may produce another fake ACP prompt. While the container is running, it captures `nginx -T` output and asserts the inherited /tmp PID/temp paths, exact ACP route, and absence of any 8080 listener. It also checks captured logs for bearer, prompt, and result sentinels. This is not a live Jellii/AWS result and sends no paid model request.

## Runtime contract

- The Fargate task shares an awsvpc network namespace across its containers. NGINX proxies to OpenAB at 127.0.0.1:8080; its only task port mapping is 8443.
- ECS injects TLS_CERT_PEM and TLS_KEY_PEM only into this container. The startup script writes them with mode 0600 in task-local ephemeral storage, unsets the environment values, validates the config, then starts NGINX in the foreground.
- access_log is off and error_log is warn-only. No request body, Authorization header, prompt, or result is logged.
- In AWS, a dedicated Jellii SG must permit 8443 only from the dedicated Relay SG and deny 8080 ingress. Keep the shared oab-agents SG untouched.
- Use a private Cloud Map A record with a DNS name matching the server certificate SAN. Relay's AMUSANT_ACP_CA_PEM adds the private root to its default trust context; hostname verification remains enabled.

A separate review/execute envelope is required for ECR mirroring, task-definition registration, Cloud Map, security groups, secrets/certificates, or a live one-off WSS test.
