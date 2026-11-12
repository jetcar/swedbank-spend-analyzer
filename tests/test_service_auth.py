"""Authorization boundaries for the private MCP-to-API credential."""
import unittest
import time
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from mcp.server.auth.provider import AccessToken

from app import passi_auth


class MemorySessionStore:
    def __init__(self):
        self.sessions = {}

    async def create(self, sid, owner_id, expires_at):
        self.sessions[sid] = (owner_id, expires_at, False)

    async def is_active(self, sid, owner_id, now):
        return sid in self.sessions and self.sessions[sid][0] == owner_id and self.sessions[sid][1] >= now

    async def revoke(self, sid):
        self.sessions.pop(sid, None)


class ServiceAuthTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.state.auth_session_store = MemorySessionStore()
        app.add_middleware(passi_auth.PassiAuthMiddleware)
        app.add_middleware(SessionMiddleware, secret_key="test-only-session-secret")

        @app.get("/api/protected")
        @app.get("/connect")
        @app.get("/")
        def protected(request: Request):
            return {"ok": True, "owner_id": getattr(request.state, "owner_id", None)}

        @app.get("/auth/test-login")
        async def test_login(request: Request):
            await app.state.auth_session_store.create("test-session", "alice@example.com", 4102444800)
            request.session["user"] = {"sub": "passi-subject", "email": "Alice@Example.com"}
            request.session["sid"] = "test-session"
            request.session["authenticated_at"] = int(time.time())
            return {"ok": True}

        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)
        for name, value in (
            ("AUTH_ENABLED", True),
            ("PASSI_ALLOWED_EMAILS", {"alice@example.com"}),
        ):
            patcher = patch.object(passi_auth, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_legacy_service_credential_is_rejected(self):
        response = self.client.get("/api/protected", headers={"X-Service-Token": "legacy-secret"})
        self.assertEqual(response.status_code, 401)

    def test_passi_bearer_token_authorizes_its_signed_email_owner(self):
        token = AccessToken(
            token="signed-access-token",
            client_id="mcp-client",
            scopes=["email"],
            expires_at=4102444800,
            resource="https://finance.passi.cloud/mcp",
            subject="passi-subject",
            claims={"iss": "https://passi.cloud/openidc", "email": "alice@example.com"},
        )
        with patch.object(
            passi_auth.PASSI_ACCESS_TOKEN_VERIFIER,
            "verify_token",
            AsyncMock(return_value=token),
        ):
            response = self.client.get(
                "/api/protected",
                headers={"Authorization": "bearer signed-access-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["owner_id"], "alice@example.com")

    def test_invalid_or_duplicate_bearer_token_gets_oauth_challenge(self):
        with patch.object(
            passi_auth.PASSI_ACCESS_TOKEN_VERIFIER,
            "verify_token",
            AsyncMock(return_value=None),
        ):
            invalid = self.client.get("/api/protected", headers={"Authorization": "Bearer invalid"})
        duplicate = self.client.get(
            "/api/protected",
            headers=[("Authorization", "Bearer one"), ("Authorization", "Bearer two")],
        )

        for response in (invalid, duplicate):
            self.assertEqual(response.status_code, 401)
            self.assertIn("resource_metadata=", response.headers["www-authenticate"])

    def test_browser_session_uses_normalized_passi_email_as_owner(self):
        with patch.object(passi_auth, "PASSI_ALLOWED_EMAILS", {"alice@example.com"}):
            self.assertEqual(self.client.get("/auth/test-login").status_code, 200)
            response = self.client.get("/api/protected")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["owner_id"], "alice@example.com")

    def test_removed_whitelist_user_loses_existing_session(self):
        with patch.object(passi_auth, "PASSI_ALLOWED_EMAILS", {"alice@example.com"}):
            self.assertEqual(self.client.get("/auth/test-login").status_code, 200)
        with patch.object(passi_auth, "PASSI_ALLOWED_EMAILS", {"admin@passi.cloud"}):
            response = self.client.get("/api/protected")
        self.assertEqual(response.status_code, 401)
        with patch.object(passi_auth, "PASSI_ALLOWED_EMAILS", {"alice@example.com"}):
            self.assertEqual(self.client.get("/api/protected").status_code, 401)

    def test_bank_connection_requires_a_browser_session(self):
        self.assertEqual(self.client.get("/connect").status_code, 307)
        self.assertEqual(
            self.client.get("/connect", headers={"X-Service-Token": "legacy-secret"}).status_code,
            307,
        )

    def test_token_exchange_includes_configured_client_secret(self):
        with patch.object(passi_auth, "PASSI_CLIENT_SECRET", "website-secret"):
            data = passi_auth._token_request_data("authorization-code", "pkce-verifier")

        self.assertEqual(data["client_secret"], "website-secret")

    def test_token_exchange_omits_empty_client_secret_for_public_clients(self):
        with patch.object(passi_auth, "PASSI_CLIENT_SECRET", ""):
            data = passi_auth._token_request_data("authorization-code", "pkce-verifier")

        self.assertNotIn("client_secret", data)


if __name__ == "__main__":
    unittest.main()
