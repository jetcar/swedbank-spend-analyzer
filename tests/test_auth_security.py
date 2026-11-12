import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import passi_auth


class MemorySessionStore:
    def __init__(self):
        self.sessions = {}

    async def create(self, sid, owner_id, expires_at):
        self.sessions[sid] = {"owner_id": owner_id, "expires_at": expires_at, "revoked": False}

    async def is_active(self, sid, owner_id, now):
        session = self.sessions.get(sid)
        return bool(session and session["owner_id"] == owner_id and session["expires_at"] >= now and not session["revoked"])

    async def revoke(self, sid):
        if sid in self.sessions:
            self.sessions[sid]["revoked"] = True


def build_app() -> FastAPI:
    app = FastAPI()
    app.state.auth_session_store = MemorySessionStore()
    app.add_middleware(passi_auth.PassiAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-only-session-secret")
    app.include_router(passi_auth.router)

    @app.get("/api/protected")
    def protected():
        return {"ok": True}

    @app.get("/auth/test-login")
    async def test_login(request: Request):
        expires_at = int(time.time()) + passi_auth.PASSI_SESSION_MAX_AGE_SECONDS
        await app.state.auth_session_store.create("copied-session-id", "alice@example.com", expires_at)
        request.session.clear()
        request.session.update(
            {
                "user": {"sub": "passi-subject", "email": "alice@example.com"},
                "sid": "copied-session-id",
                "authenticated_at": int(time.time()),
            }
        )
        return {"ok": True}

    return app


class AuthSecurityTest(unittest.TestCase):
    def setUp(self):
        passi_auth._pending.clear()
        passi_auth._revoked_sessions.clear()
        self.patchers = [
            patch.object(passi_auth, "AUTH_ENABLED", True),
            patch.object(passi_auth, "PASSI_CLIENT_ID", "finance-client"),
            patch.object(passi_auth, "PASSI_ALLOWED_EMAILS", {"alice@example.com"}),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = build_app()
        self.client = TestClient(self.app, follow_redirects=False)
        self.addCleanup(self.client.close)

    def test_empty_allowlist_fails_closed(self):
        with patch.object(passi_auth, "PASSI_ALLOWED_EMAILS", set()):
            with self.assertRaises(RuntimeError):
                passi_auth.validate_auth_configuration()
            self.assertEqual(self.client.get("/api/protected").status_code, 503)

    def test_logout_revokes_a_copied_session_cookie(self):
        self.assertEqual(self.client.get("/auth/test-login").status_code, 200)
        stolen_cookie = self.client.cookies.get("session")
        second = TestClient(self.app, follow_redirects=False)
        self.addCleanup(second.close)
        second.cookies.set("session", stolen_cookie)

        self.assertEqual(second.get("/api/protected").status_code, 200)
        self.assertEqual(self.client.get("/auth/logout").status_code, 307)
        self.assertEqual(second.get("/api/protected").status_code, 401)

    def test_oidc_callback_rejects_unknown_state_before_token_exchange(self):

        class NoNetworkClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, *_args, **_kwargs):
                raise AssertionError("callback reached token exchange with unknown state")

        second = TestClient(self.app, follow_redirects=False)
        self.addCleanup(second.close)
        with patch.object(passi_auth.httpx, "AsyncClient", return_value=NoNetworkClient()):
            response = second.get("/auth/callback?code=valid-looking-code&state=unknown")
        self.assertEqual(response.status_code, 400)

    def test_oidc_callback_rejects_expired_state_before_token_exchange(self):
        passi_auth._pending["expired-state"] = ("verifier", "nonce", "/", False, time.time() - 1)
        response = self.client.get("/auth/callback?code=valid-looking-code&state=expired-state")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("expired-state", passi_auth._pending)

    def test_oidc_start_uses_s256_pkce_and_nonce(self):
        response = self.client.get("/auth/start")
        self.assertEqual(response.status_code, 307)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        state = query["state"][0]
        verifier, nonce, *_ = passi_auth._pending[state]
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["code_challenge"], [passi_auth._pkce_challenge(verifier)])
        self.assertEqual(query["nonce"], [nonce])

    def test_oidc_error_callback_consumes_state(self):
        passi_auth._pending["denied-state"] = ("verifier", "nonce", "/", False, time.time() + 60)
        response = self.client.get("/auth/callback?error=access_denied&state=denied-state")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("denied-state", passi_auth._pending)

    def test_pending_oidc_states_are_bounded_without_locking_out_login(self):
        expires = time.time() + 60
        passi_auth._pending.update({str(index): ("v", "n", "/", False, expires) for index in range(passi_auth.PASSI_PENDING_STATE_LIMIT)})
        response = self.client.get("/auth/start")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(len(passi_auth._pending), passi_auth.PASSI_PENDING_STATE_LIMIT)

    def test_local_logout_does_not_require_session_middleware(self):
        local = FastAPI()
        local.include_router(passi_auth.router)
        with patch.object(passi_auth, "AUTH_ENABLED", False):
            with TestClient(local, follow_redirects=False) as client:
                self.assertEqual(client.get("/auth/logout").status_code, 307)


if __name__ == "__main__":
    unittest.main()
