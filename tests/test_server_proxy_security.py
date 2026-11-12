import unittest
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


class ServerProxySecurityTest(unittest.TestCase):
    def run_server_env_update(self, env_text: str) -> str:
        script = Path("deploy/finance-deploy.sh").read_text(encoding="utf-8")
        payload = script.split("python3 - \"$stage\" <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "previous").mkdir()
            (stage / "previous" / ".env.server").write_text(env_text, encoding="utf-8")
            for component in ("app", "nginx", "postgres"):
                (stage / f"{component}.digest").write_text(
                    f"jetcar/bank-{component}@sha256:{'0' * 64}", encoding="utf-8"
                )
            with patch.object(sys, "argv", ["update-server-env", str(stage)]):
                exec(compile(payload, "deploy/finance-deploy.sh", "exec"), {})
            return (stage / ".env.server").read_text(encoding="utf-8")

    def test_hsts_is_only_emitted_for_tls_forwarded_requests(self):
        config = Path("deploy/nginx-server.conf").read_text(encoding="utf-8")
        self.assertIn("map $http_x_forwarded_proto $hsts_header", config)
        self.assertIn('https "max-age=31536000";', config)
        self.assertIn("add_header Strict-Transport-Security $hsts_header always;", config)

    def test_deploy_health_check_uses_oauth_metadata_and_challenge(self):
        script = Path("deploy/finance-deploy.sh").read_text(encoding="utf-8")
        self.assertIn("/.well-known/oauth-protected-resource/mcp", script)
        self.assertIn("Expected OAuth challenge from MCP", script)

    def test_deploy_retries_transient_gateway_errors(self):
        script = Path("deploy/finance-deploy.sh").read_text(encoding="utf-8")
        health_check = script.split("# Exercise the proxy", 1)[1].split("/auth/status", 1)[0]
        self.assertIn("--retry-all-errors", health_check)
        self.assertIn("--retry-max-time 60", health_check)
        self.assertIn("health_response=$(curl", health_check)
        self.assertIn('<<< "$health_response"', health_check)
        self.assertIn("auth_response=$(curl", script)
        self.assertIn('<<< "$auth_response"', script)
        self.assertIn("metadata_response=$(curl", script)
        self.assertIn('<<< "$metadata_response"', script)
        self.assertIn("for _ in {1..30}; do", script)

    def test_deploy_removes_obsolete_static_service_credentials(self):
        updated = self.run_server_env_update(
            "PASSI_SERVICE_TOKEN=service-secret\n"
            "MCP_CLIENT_TOKEN=replace-with-independent-random-mcp-client-token\n"
            "PASSI_SERVICE_OWNER_ID=admin@passi.cloud\n"
            "PASSI_ALLOWED_EMAILS=admin@passi.cloud\n"
        )
        self.assertNotIn("PASSI_SERVICE_TOKEN=", updated)
        self.assertNotIn("MCP_CLIENT_TOKEN=", updated)
        self.assertNotIn("PASSI_SERVICE_OWNER_ID=", updated)
        self.assertIn("PASSI_ALLOWED_EMAILS=admin@passi.cloud", updated)


if __name__ == "__main__":
    unittest.main()
