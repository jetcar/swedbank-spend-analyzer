import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken

from app import mcp_server
from app.mcp_server import MCPIngressMiddleware


class MCPIngressSecurityTest(unittest.TestCase):
    def setUp(self):
        inner = FastAPI()

        @inner.post("/mcp")
        def endpoint():
            return {"ok": True}

        self.client = TestClient(
            MCPIngressMiddleware(
                inner,
                client_token="mcp-client-secret",
                allowed_hosts={"mcp:8001"},
                allowed_origins={"https://finance.passi.cloud"},
            )
        )
        self.addCleanup(self.client.close)

    def request(self, **headers):
        return self.client.post("/mcp", headers={"host": "mcp:8001", **headers})

    def test_requires_a_dedicated_bearer_token(self):
        self.assertEqual(self.request().status_code, 401)
        self.assertEqual(self.request(authorization="Bearer wrong").status_code, 401)
        self.assertEqual(self.request(authorization="Bearer mcp-client-secret").status_code, 200)

    def test_rejects_unknown_host_and_origin(self):
        auth = {"authorization": "Bearer mcp-client-secret"}
        self.assertEqual(self.client.post("/mcp", headers={"host": "audit.invalid", **auth}).status_code, 403)
        self.assertEqual(self.request(origin="https://audit.invalid", **auth).status_code, 403)
        self.assertEqual(self.request(origin="null", **auth).status_code, 403)
        self.assertEqual(self.request(origin="https://finance.passi.cloud", **auth).status_code, 200)

    def test_rejects_duplicate_security_headers(self):
        request = [("host", "mcp:8001"), ("authorization", "Bearer mcp-client-secret")]
        self.assertEqual(
            self.client.post("/mcp", headers=[*request, ("host", "audit.invalid")]).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/mcp",
                headers=[*request, ("origin", "https://audit.invalid"), ("origin", "https://finance.passi.cloud")],
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/mcp",
                headers=[*request, ("authorization", "Bearer wrong")],
            ).status_code,
            401,
        )

    def test_production_mcp_uses_oauth_without_static_credentials(self):
        compose = Path("docker-compose.server.yml").read_text(encoding="utf-8")
        mcp_service = compose.split("\n  mcp:\n", 1)[1].split("\n  postgres:\n", 1)[0]
        self.assertIn('MCP_OAUTH_ENABLED: "1"', mcp_service)
        self.assertIn("PASSI_RESOURCE:", mcp_service)
        self.assertIn("PASSI_ALLOWED_EMAILS:", mcp_service)
        self.assertNotIn("MCP_CLIENT_TOKEN:", mcp_service)
        self.assertNotIn("PASSI_SERVICE_TOKEN:", mcp_service)

    def test_production_mcp_and_app_remain_on_the_private_network(self):
        compose = Path("docker-compose.server.yml").read_text(encoding="utf-8")
        mcp_service = compose.split("\n  mcp:\n", 1)[1].split("\n  postgres:\n", 1)[0]
        app_service = compose.split("\n  app:\n", 1)[1].split("\n  mcp:\n", 1)[0]
        self.assertIn("networks: [private]", mcp_service)
        self.assertIn("networks: [private]", app_service)
        self.assertNotIn("ports:", mcp_service)
        self.assertNotIn("ports:", app_service)

    def test_production_gateway_is_bound_to_loopback(self):
        compose = Path("docker-compose.server.yml").read_text(encoding="utf-8")
        gateway = compose.split("\n  gateway:\n", 1)[1].split("\n  bank-egress:\n", 1)[0]
        self.assertIn('"127.0.0.1:${FINANCE_PORT:-18000}:8000"', gateway)

    def test_production_gateway_routes_mcp_and_metadata_to_mcp_service(self):
        nginx = Path("deploy/nginx-server.conf").read_text(encoding="utf-8")
        self.assertIn("location = /mcp", nginx)
        self.assertIn("location = /.well-known/oauth-protected-resource/mcp", nginx)
        self.assertIn("set $mcp_backend mcp:8001;", nginx)

    def test_oauth_configuration_publishes_protected_resource_metadata(self):
        with patch.object(mcp_server, "PASSI_ALLOWED_EMAILS", {"owner@example.com"}):
            server = MCPServer("oauth-test", **mcp_server.oauth_configuration(True))
        client = TestClient(server.streamable_http_app(host="finance.passi.cloud"))
        self.addCleanup(client.close)

        response = client.get("/.well-known/oauth-protected-resource/mcp")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resource"], "https://finance.passi.cloud/mcp")
        self.assertEqual(response.json()["authorization_servers"], ["https://passi.cloud/openidc"])
        self.assertEqual(response.json()["scopes_supported"], ["email"])
        unauthorized = client.post("/mcp", json={})
        self.assertEqual(unauthorized.status_code, 401)
        self.assertIn(
            'resource_metadata="https://finance.passi.cloud/.well-known/oauth-protected-resource/mcp"',
            unauthorized.headers["www-authenticate"],
        )

    def test_mcp_forwards_the_authenticated_access_token_to_the_api(self):
        token = AccessToken(
            token="signed-access-token",
            client_id="mcp-client",
            scopes=["email"],
            expires_at=4102444800,
            resource="https://finance.passi.cloud/mcp",
            subject="passi-subject",
            claims={"iss": "https://passi.cloud/openidc", "email": "owner@example.com"},
        )
        response = Mock(is_error=False)
        response.json.return_value = {"ok": True}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.request.return_value = response

        with (
            patch.object(mcp_server, "MCP_OAUTH_ENABLED", True),
            patch.object(mcp_server, "get_access_token", return_value=token),
            patch.object(mcp_server.httpx, "AsyncClient", return_value=client) as client_factory,
        ):
            result = asyncio.run(mcp_server.api("GET", "/api/accounts"))

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client_factory.call_args.kwargs["headers"], {"Authorization": "Bearer signed-access-token"})


if __name__ == "__main__":
    unittest.main()
