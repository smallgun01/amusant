# P4-T2 — Private ACP NGINX Sidecar Deployment Plan

**Status:** review-only design proposal. No AWS resource, task, certificate, secret, DNS record, or security-group change is authorized by this document. A live WSS smoke requires a separate bounded execute envelope.

**Scope:** Encrypt the private ECS A Relay → ECS B OpenAB/Jellii ACP hop. This is not public device ingress, Kai TLS-PSK termination, a permanent service, or a 24/7 Relay decision.

## Proposed decision

Use an **unprivileged NGINX sidecar in the existing Jellii task**, not a third ECS Service. NGINX terminates WSS on task port 8443 and proxies only /acp over loopback HTTP to OpenAB on port 8080. The plaintext hop ends within the same Fargate task network namespace; it is not carried over the VPC.

Choose NGINX over Caddy for this first deployment slice because the path, WebSocket upgrade headers, Authorization forwarding, timeouts, and access-log policy are explicit in a short reviewable config. Caddy remains viable, but automatic certificate/CA state management offers no advantage for the proposed static short-lived certificate flow. This is a recommendation for review, not an accepted production decision.

No ALB, NLB, Service Connect, NAT, new hosted zone, new ECS Service, or ACM Private CA is proposed for the one-off. The one-off plaintext ws exception does not carry forward.

## Verified baseline (read-only, 2026-10-12)

- Jellii is desired/running/pending 0/0/0, task-definition revision :9. The Fargate task uses awsvpc, 512 CPU / 1024 MiB, one OpenAB container, TCP 8080, GATEWAY_LISTEN=0.0.0.0:8080, ACP enabled, no container health check, no task role, an execution role, and two injected secrets.
- No load balancer, Service Connect configuration, or ECS service registry is configured for Jellii. Public IP assignment is enabled for existing outbound access.
- The current oab-agents security group allows TCP 8080 from itself and has broad IPv4 egress. It is attached to six ECS service definitions (all currently stopped); do **not** edit this shared rule for the sidecar.
- Two private DNS zones and two matching Cloud Map private namespaces are associated with the VPC, but Jellii is not registered in service discovery. Their names are intentionally omitted; use only a namespace whose owner approves the name.
- No active ACM Private CA was found in us-east-1. The Gateway task definition also has a LINE_WEBHOOK_PROXY_URL setting containing port 8080; its target was not established, so a reference to Jellii must not be assumed absent.
- The deployed OpenAB image digest has no OCI source labels. Native loopback-bind behavior in that exact image remains unverified; this plan does not depend on native TLS support.

## Traffic and trust path

~~~text
Relay one-off task (dedicated Relay SG)
  wss://REPLACE_PRIVATE_ACP_FQDN:8443/acp
        │ TLS 1.2+; Relay verifies CA chain and DNS SAN
        ▼
NGINX sidecar (same Jellii Fargate task; dedicated Jellii SG)
        │ http://127.0.0.1:8080/acp — same task localhost only
        ▼
OpenAB container (existing task; ACP bearer validation remains enabled)
~~~

AWS documents that containers in an awsvpc task share the task network namespace and can communicate over localhost. NGINX must not forward other paths, log Authorization headers, or log ACP frames. Relay keeps the existing fail-closed TLS verification; no CERT_NONE or plaintext override.

## NGINX configuration proposal

Use the official NGINX unprivileged image, pinned by digest and mirrored to private ECR before any execute. The unprivileged image's default site listens on 8080; remove that default site to avoid colliding with OpenAB. Keep the vendor main nginx.conf, including pid /tmp/nginx.pid, client_body_temp_path /tmp/client_temp, proxy_temp_path /tmp/proxy_temp, and its other writable temp-path directives. Install the following **HTTP-context fragment** as /etc/nginx/conf.d/acp.conf. Do not put this fragment in nginx.conf because it has no top-level worker_processes/events/http sections. Before accepting a pinned image, run nginx -T to verify the main config includes conf.d inside http and retains the /tmp pid/temp paths. Then run nginx -t against the rendered config using only a disposable generated certificate/key in a local test container; do not commit or use the AWS secret for this offline check.

~~~nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 8443 ssl;
    server_name REPLACE_PRIVATE_ACP_FQDN;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_certificate     /tmp/amusant-acp/server.crt;
    ssl_certificate_key /tmp/amusant-acp/server.key;
    access_log off;
    error_log stderr warn;

    location = /acp {
        proxy_pass http://127.0.0.1:8080/acp;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Authorization $http_authorization;
        proxy_read_timeout 130s;
        proxy_send_timeout 130s;
        proxy_buffering off;
    }

    location / { return 404; }
}
~~~

The startup wrapper will write the certificate and key from NGINX-container-only secret environment variables to a task-local directory with umask 077, unset those variables, then run nginx -t and exec nginx -g 'daemon off;'. Do not put certificate or private-key values in the image, command line, repository, access logs, or OpenAB container. The unprivileged NGINX UID must own/read those files. Fargate ephemeral storage encryption is available on platform 1.4.0+; the execution envelope must pin/verify a compatible platform. The image/config is not yet built or validated against the pinned NGINX image; that remains a pre-deployment offline gate.

## Private DNS and certificates

1. **DNS:** Create the Cloud Map private service with DnsConfig.DnsRecords = [{Type: A, TTL: 10}] in an owner-approved existing private DNS namespace. Use the task's private ENI IPv4; Relay uses the fixed port 8443 in wss://REPLACE_PRIVATE_ACP_FQDN:8443/acp, so it performs normal A/AAAA lookup, not SRV. The ECS serviceRegistries entry should contain only registryArn; omit containerName/containerPort because those are for SRV-style discovery. Do not create another Route 53 private hosted zone by default. No Service Connect proxy is needed for this direct DNS path. Before any prompt, wait until DiscoverInstances returns the current task and DNS from the Relay task resolves to its private IPv4. At cleanup, verify deregistration and allow the 10-second DNS TTL/cache window to expire; desiredCount 0 alone is not DNS cleanup evidence.
2. **Certificate:** For the bounded one-off, use a short-lived, operator-controlled private CA; keep the CA private key off AWS and out of GitHub. Issue a leaf certificate whose SAN exactly matches the selected private DNS name. Store only the leaf certificate/key in a dedicated Secrets Manager secret and inject them into the NGINX container. Expire within the agreed short window; delete the leaf secret after the test unless the execute envelope explicitly chooses to keep it.
3. **Relay trust prerequisite:** Current OpenAbAcpExecutor builds the system default TLS context; it has no explicit private-CA configuration. Before AWS execution, a separate code change must add an optional PEM trust anchor (proposed AMUSANT_ACP_CA_PEM) using SSLContext.load_verify_locations(cadata=...) so system roots remain intact. Root CA PEM is public trust material, not a private key. Add tests for valid CA/SAN and wrong CA/name; never replace the system bundle with a private-only bundle or disable hostname verification.
4. **Rotation:** ECS Secrets Manager environment injection is a task-start snapshot; changing a secret does not update a running task. For this one-off, rotate by replacing the task and use short-lived credentials. Automated renewal/rotation for an always-on service is out of scope and requires a separate design.

## ECS task and service configuration proposal

This is a **patch design**, not a registrable task definition. Preserve the current task family, OpenAB image/config, existing OpenAB secrets, and 512/1024 task size. The full task definition must be re-read and merged at execution time; do not replace it from this fragment. The JSON below is a review aid, not an AWS API payload; OpenAB fields are elided where they must be preserved from the live definition.

~~~json
{
  "taskDefinitionChanges": {
    "networkMode": "awsvpc",
    "cpu": "512",
    "memory": "1024",
    "executionRoleArn": "REPLACE_JELLII_EXECUTION_ROLE_ARN",
    "openabContainer": {
      "removePortMapping": 8080,
      "keepACPEnabled": true
    },
    "sidecarContainer": {
      "name": "acp-wss-proxy",
      "image": "REPLACE_PRIVATE_ECR_URI@sha256:REPLACE_PINNED_DIGEST",
      "essential": true,
      "entryPoint": ["/opt/amusant/start-proxy.sh"],
      "cpu": 64,
      "memoryReservation": 64,
      "memory": 128,
      "portMappings": [{"containerPort": 8443, "protocol": "tcp"}],
      "dependsOn": [{"containerName": "openab", "condition": "START"}],
      "secrets": [
        {"name": "TLS_CERT_PEM", "valueFrom": "REPLACE_CERT_SECRET_JSON_KEY"},
        {"name": "TLS_KEY_PEM", "valueFrom": "REPLACE_KEY_SECRET_JSON_KEY"}
      ]
    }
  },
  "serviceChanges": {
    "cloudMapDnsConfig": {"DnsRecords": [{"Type": "A", "TTL": 10}]},
    "serviceRegistries": [{"registryArn": "REPLACE_CLOUD_MAP_SERVICE_ARN"}],
    "securityGroups": ["REPLACE_DEDICATED_JELLII_SG"],
    "desiredCountOutsideTest": 0
  }
}
~~~

Read-only task-definition baseline: the OpenAB container has cpu=0 and no explicit memory or memoryReservation; task-level CPU/memory are 512/1024. The proposed proxy cpu=64, memoryReservation=64 MiB, memory=128 MiB fits nominally, but before RegisterTaskDefinition re-read the exact values and validate ECS/Fargate reservation arithmetic. If invalid, get a separately costed task-size approval before registration. The dedicated task execution role must preserve existing Jellii ECR/log/secret reads and add only the certificate secret; do not widen a shared execution policy. Keep taskRoleArn omitted, ECS Exec disabled, and the OpenAB agent’s tool/egress boundary unchanged.

## 8080 isolation and network rules

- Create a Jellii-specific security group **in place of**, not in addition to, shared oab-agents. Inbound: TCP 8443 only from a dedicated one-off Amusant Relay SG. No 8080 ingress and no public CIDR. The NGINX container is the only task container with a network port mapping; OpenAB remains reachable to the sidecar via shared task localhost.
- The Relay SG may egress TCP 8443 to the Jellii SG; preserve the already-required AWS HTTPS egress path for the one-off. No NAT or public ACP.
- Remove the OpenAB 8080 task port mapping. NGINX reaches the listener through task localhost. Optionally bind OpenAB to 127.0.0.1:8080 only after verifying that the exact deployed image honors that setting; it is defense in depth, not a substitute for the SG boundary.
- Do not modify the shared oab-agents SG: all six service definitions use it. Before replacing Jellii’s SG, resolve whether any service depends on Jellii’s existing 8080 ingress. The current task-definition audit found a separate 8080 webhook-proxy setting but did not prove it targets Jellii. Treat this as a **blocking dependency check**, not as evidence that the path is unused.
- The current task receives a public IP for outbound connectivity. Public-IP assignment is not public listener authorization; the dedicated SG still denies internet ingress.

## Future bounded one-off acceptance (not authorized here)

A separate owner-signed execution envelope must fill cost/time caps, image digest, private DNS name, CA/certificate expiry, new execution-role policy, SG rules, rollback, and keep/delete choices. The previous ws:// exception is not reusable.

Proposed run order: read-only preflight → register sidecar task revision and private service registry → Jellii 1 and wait for private DNS/8443 readiness → run one Relay task using wss:// and the verified CA → exactly one ACP session/prompt with no retry → capture A STOPPED → use the existing after-stop store_get → always stop A and return Jellii to 0/0/0. No NAT, ALB/NLB, PCA, Service Connect, public ACP, device ingress, 24/7 Relay, ECS Exec, or extra ECS Service.

**Functional PASS:** verified WSS handshake and hostname, authenticated ACP initialize/session/new/session/prompt with terminal end_turn, completed bounded Inbox result, A STOPPED, after-stop store_get succeeds, and Jellii is back at 0/0/0. TLS failures, wrong CA/name, missing/bad Bearer, or direct 8080 access must fail closed. The local PR #23 fake-ACP WSS result is not this runtime proof.

## Rollback and cleanup proposal

On failure: stop A, set Jellii desiredCount to its preflight value (0), restore task-definition revision :9 and its prior service networking, remove temporary Cloud Map service/SG/certificate secret if created, and confirm 0/0/0. Keep or delete the sidecar image/task-definition revision only as named in the future envelope. No table mutation is part of the WSS test.

## Review decisions before any execute envelope

1. Approve NGINX unprivileged as the sidecar implementation, or choose Caddy instead.
2. Select the owner-approved existing private DNS namespace/service name.
3. Approve the one-off short-lived private-CA flow and the prerequisite Relay CA-PEM code change.
4. Resolve the possible Jellii 8080 consumer and approve replacing the shared SG with dedicated Jellii/Relay SGs.
5. Confirm the dedicated execution-role scope, test resource reservation, secret cleanup, and spend/time caps.

## References

- [AWS ECS awsvpc networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-networking-awsvpc.html) — same-task localhost and per-task ENI.
- [AWS ECS service discovery](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-discovery.html), [Cloud Map DnsRecord](https://docs.aws.amazon.com/cloud-map/latest/api/API_DnsRecord.html), and [ECS ServiceRegistry](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_ServiceRegistry.html) — private A-record registration, TTL, and SRV fields.
- [ECS Secrets Manager environment injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html) — task execution role and task replacement required after rotation.
- [Fargate task storage](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-storage.html) — ephemeral storage encryption behavior.
- [NGINX WebSocket proxying](https://nginx.org/en/docs/http/websocket.html) and [NGINX unprivileged image](https://github.com/nginx/docker-nginx-unprivileged).
- Project protocol/privacy constraints: SPEC.md § Device ingress constraint and § ACP trust boundary; offline proof: docs/p4-private-acp-wss-poc.md.
