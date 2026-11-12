import asyncio
import json
import time
import unittest

import jwt
import httpx
from cryptography.hazmat.primitives.asymmetric import rsa

from app.passi_token import PassiAccessTokenVerifier


class PassiAccessTokenVerifierTest(unittest.TestCase):
    def setUp(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        self.jwk.update({"kid": "passi-test-key", "alg": "RS256", "use": "sig"})
        self.fetch_count = 0

        async def fetch_jwks():
            self.fetch_count += 1
            return {"keys": [self.jwk]}

        self.verifier = PassiAccessTokenVerifier(
            issuer="https://passi.cloud/openidc",
            audience="https://finance.passi.cloud/mcp",
            jwks_url="https://passi.cloud/openidc/.well-known/jwks",
            allowed_emails={"owner@example.com"},
            jwks_fetcher=fetch_jwks,
        )

    def token(self, **overrides):
        now = int(time.time())
        claims = {
            "iss": "https://passi.cloud/openidc",
            "aud": "https://finance.passi.cloud/mcp",
            "sub": "passi-subject",
            "client_id": "mcp_dynamic_client",
            "email": "Owner@Example.com",
            "scope": "email profile",
            "jti": "unique-token-id",
            "iat": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "passi-test-key"})

    def verify(self, token):
        return asyncio.run(self.verifier.verify_token(token))

    def test_accepts_valid_resource_token_without_email_verified(self):
        verified = self.verify(self.token())

        self.assertIsNotNone(verified)
        self.assertEqual(verified.resource, "https://finance.passi.cloud/mcp")
        self.assertEqual(verified.claims["email"], "owner@example.com")
        self.assertEqual(verified.subject, "passi-subject")

    def test_rejects_wrong_issuer_audience_email_or_scope(self):
        cases = (
            {"iss": "https://attacker.invalid"},
            {"aud": "https://finance.passi.cloud"},
            {"email": "attacker@example.com"},
            {"scope": "profile"},
        )
        for claims in cases:
            with self.subTest(claims=claims):
                self.assertIsNone(self.verify(self.token(**claims)))

    def test_rejects_missing_required_claims(self):
        now = int(time.time())
        claims = {
            "iss": "https://passi.cloud/openidc",
            "aud": "https://finance.passi.cloud/mcp",
            "sub": "passi-subject",
            "client_id": "mcp_dynamic_client",
            "email": "owner@example.com",
            "scope": "email",
            "iat": now,
            "exp": now + 3600,
        }
        token = jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "passi-test-key"})

        self.assertIsNone(self.verify(token))

    def test_rejects_non_rs256_and_unknown_key(self):
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        hs_token = jwt.encode(claims, "a" * 32, algorithm="HS256", headers={"kid": "passi-test-key"})
        unknown_key = jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "unknown"})

        self.assertIsNone(self.verify(hs_token))
        self.assertIsNone(self.verify(unknown_key))

    def test_caches_jwks_and_does_not_refetch_for_unknown_kids(self):
        self.assertIsNotNone(self.verify(self.token()))
        unknown_key = jwt.encode(
            jwt.decode(self.token(), options={"verify_signature": False}),
            self.private_key,
            algorithm="RS256",
            headers={"kid": "unknown"},
        )

        self.assertIsNone(self.verify(unknown_key))
        self.assertEqual(self.fetch_count, 2)
        self.assertIsNone(self.verify(unknown_key))
        self.assertEqual(self.fetch_count, 2)

    def test_unknown_kid_triggers_rate_limited_rotation_refresh(self):
        self.assertIsNotNone(self.verify(self.token()))
        rotated_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        rotated_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rotated_private_key.public_key()))
        rotated_jwk.update({"kid": "rotated-key", "alg": "RS256", "use": "sig"})
        self.jwk = rotated_jwk
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        rotated_token = jwt.encode(
            claims,
            rotated_private_key,
            algorithm="RS256",
            headers={"kid": "rotated-key"},
        )

        self.assertIsNotNone(self.verify(rotated_token))
        self.assertEqual(self.fetch_count, 2)

    def test_failed_jwks_fetches_are_backed_off(self):
        fetch_count = 0

        async def unavailable_jwks():
            nonlocal fetch_count
            fetch_count += 1
            raise httpx.ConnectError("JWKS unavailable")

        verifier = PassiAccessTokenVerifier(
            issuer="https://passi.cloud/openidc",
            audience="https://finance.passi.cloud/mcp",
            jwks_url="https://passi.cloud/openidc/.well-known/jwks",
            allowed_emails={"owner@example.com"},
            jwks_fetcher=unavailable_jwks,
        )
        token = self.token()

        self.assertIsNone(asyncio.run(verifier.verify_token(token)))
        self.assertIsNone(asyncio.run(verifier.verify_token(token)))
        self.assertEqual(fetch_count, 1)


if __name__ == "__main__":
    unittest.main()
