import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
import jwt
from starlette.requests import Request

from app import passi_auth as auth
from tests.test_auth_security import MemorySessionStore


class OidcValidationTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def callback(self, overrides):
        now = int(time.time())
        claims = dict(sub="user", iss=auth.PASSI_ISSUER, aud="test-client", exp=now + 60,
                      iat=now, email="owner@example.com", nonce="nonce")
        for name, value in overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        token = jwt.encode(claims, self.key, algorithm="RS256")
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=MagicMock(is_error=False, json=lambda: {"id_token": token}))
        app = FastAPI()
        self.store = app.state.auth_session_store = MemorySessionStore()
        request = Request({"type": "http", "app": app, "session": {}})
        auth._pending["validation-state"] = ("verifier", "nonce", "/", False, time.time() + 60)
        with (
            patch.object(auth, "AUTH_ENABLED", True),
            patch.object(auth, "PASSI_CLIENT_ID", "test-client"),
            patch.object(auth, "PASSI_ALLOWED_EMAILS", {"owner@example.com"}),
            patch.object(auth.httpx, "AsyncClient", return_value=client),
            patch.object(auth, "_jwks_key", AsyncMock(return_value=self.key.public_key())),
        ):
            return await auth.callback(request, "code", "validation-state", None)

    async def test_required_lifetime_claims_cannot_be_omitted(self):
        for name in ("exp", "iat"):
            with self.subTest(claim=name):
                with self.assertRaises(HTTPException) as error:
                    await self.callback({name: None})
                self.assertEqual(error.exception.status_code, 401)
                self.assertFalse(self.store.sessions)

    async def test_expired_and_future_tokens_cannot_create_sessions(self):
        for claims in ({"exp": int(time.time()) - 60}, {"iat": int(time.time()) + 60}):
            with self.assertRaises(HTTPException) as error:
                await self.callback(claims)
            self.assertEqual(error.exception.status_code, 401)
            self.assertFalse(self.store.sessions)

    async def test_valid_id_token_creates_session(self):
        self.assertEqual((await self.callback({})).status_code, 307)
        self.assertEqual(len(self.store.sessions), 1)

    async def test_malformed_token_is_an_authentication_error(self):
        with self.assertRaises(HTTPException) as error:
            await auth._jwks_key("not-a-jwt")
        self.assertEqual(error.exception.status_code, 401)
