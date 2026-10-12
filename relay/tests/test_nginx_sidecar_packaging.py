import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SIDECAR = ROOT / "deploy" / "acp-wss-proxy"


class NginxSidecarPackagingTests(unittest.TestCase):
    def test_base_image_is_digest_pinned_and_runtime_nonroot(self):
        dockerfile = (SIDECAR / "Dockerfile").read_text()
        self.assertRegex(
            dockerfile,
            r"(?m)^FROM public\.ecr\.aws/nginx/nginx-unprivileged@sha256:[0-9a-f]{64}$",
        )
        self.assertIn("USER 101:101", dockerfile)
        self.assertIn("rm -f /etc/nginx/conf.d/default.conf", dockerfile)

    def test_only_acp_tls_listener_and_loopback_backend_are_configured(self):
        config = (SIDECAR / "acp.conf").read_text()
        self.assertIn("listen 8443 ssl default_server;", config)
        self.assertIn("location = /acp", config)
        self.assertIn("proxy_pass http://127.0.0.1:8080/acp;", config)
        self.assertNotRegex(config, r"(?m)^\s*listen\s+8080")
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", config)
        self.assertIn("proxy_set_header Connection $connection_upgrade;", config)
        self.assertIn("proxy_set_header Authorization $http_authorization;", config)
        self.assertIn("access_log off;", config)

    def test_entrypoint_handles_ephemeral_keys_without_logging(self):
        script = (SIDECAR / "start-proxy.sh").read_text()
        self.assertIn("umask 077", script)
        self.assertIn("unset TLS_CERT_PEM TLS_KEY_PEM", script)
        self.assertIn("nginx -t", script)
        self.assertIn('exec "$@"', script)
        self.assertNotIn("echo \"$TLS_KEY_PEM\"", script)

    def test_offline_smoke_binds_test_listener_to_loopback(self):
        smoke = (ROOT / "relay" / "devtools" / "nginx_sidecar_smoke.py").read_text()
        self.assertIn("--network", smoke)
        self.assertIn("127.0.0.1:{listen_port}", smoke)
        self.assertIn("AMUSANT_ACP_CA_PEM", smoke)
        self.assertIn("untrusted_ca", smoke)
        self.assertNotIn("boto3", smoke)
        self.assertNotIn('subprocess.run(["aws"', smoke)
        self.assertNotIn('subprocess.Popen(["aws"', smoke)

    def test_no_certificate_or_private_key_is_checked_in(self):
        suffixes = {p.suffix.lower() for p in SIDECAR.rglob("*") if p.is_file()}
        self.assertFalse(suffixes & {".key", ".crt", ".pem", ".p12"})


if __name__ == "__main__":
    unittest.main()
