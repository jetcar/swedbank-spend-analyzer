"""Validation of Passi access tokens used by Finance MCP and REST APIs."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx
import jwt
from mcp.server.auth.provider import AccessToken


logger = logging.getLogger(__name__)
JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class PassiAccessTokenVerifier:
    """Verify audience-bound RS256 access tokens against Passi JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        allowed_emails: Iterable[str],
        jwks_fetcher: JwksFetcher | None = None,
        cache_ttl_seconds: int = 300,
        refresh_failure_backoff_seconds: int = 10,
        unknown_kid_refresh_interval_seconds: int = 30,
        negative_kid_ttl_seconds: int = 60,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience.rstrip("/")
        self.jwks_url = jwks_url
        self.allowed_emails = {email.strip().lower() for email in allowed_emails if email.strip()}
        self._jwks_fetcher = jwks_fetcher or self._fetch_jwks
        self._cache_ttl_seconds = max(1, cache_ttl_seconds)
        self._refresh_failure_backoff_seconds = max(1, refresh_failure_backoff_seconds)
        self._unknown_kid_refresh_interval_seconds = max(1, unknown_kid_refresh_interval_seconds)
        self._negative_kid_ttl_seconds = max(1, negative_kid_ttl_seconds)
        self._keys: dict[str, object] = {}
        self._keys_expire_at = 0.0
        self._refresh_retry_at = 0.0
        self._unknown_kid_refresh_at = 0.0
        self._missing_kids: dict[str, float] = {}
        self._keys_lock = asyncio.Lock()

    async def _fetch_jwks(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(self.jwks_url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Passi JWKS response is not an object")
        return payload

    async def _load_keys(self) -> bool:
        try:
            payload = await self._jwks_fetcher()
            if not isinstance(payload, dict):
                raise ValueError("Passi JWKS response is not an object")
            keys: dict[str, object] = {}
            for jwk in payload.get("keys", []):
                if not isinstance(jwk, dict):
                    continue
                kid = jwk.get("kid")
                if (
                    not isinstance(kid, str)
                    or not kid
                    or jwk.get("kty") != "RSA"
                    or jwk.get("alg") not in (None, "RS256")
                    or jwk.get("use") not in (None, "sig")
                ):
                    continue
                try:
                    keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
                except (KeyError, TypeError, ValueError):
                    continue
            if not keys:
                raise ValueError("Passi JWKS contains no usable RS256 signing keys")
        except (httpx.HTTPError, ValueError, TypeError):
            self._refresh_retry_at = time.monotonic() + self._refresh_failure_backoff_seconds
            logger.exception("Passi JWKS refresh failed")
            return False

        now = time.monotonic()
        self._keys = keys
        self._keys_expire_at = now + self._cache_ttl_seconds
        self._refresh_retry_at = 0.0
        self._missing_kids = {
            kid: expires_at for kid, expires_at in self._missing_kids.items() if expires_at > now and kid not in keys
        }
        return True

    async def _refresh_keys_if_stale(self) -> bool:
        now = time.monotonic()
        if self._keys and now < self._keys_expire_at:
            return True
        if now < self._refresh_retry_at or self._keys_lock.locked():
            return bool(self._keys)
        async with self._keys_lock:
            now = time.monotonic()
            if self._keys and now < self._keys_expire_at:
                return True
            if now < self._refresh_retry_at:
                return bool(self._keys)
            loaded = await self._load_keys()
            return loaded or bool(self._keys)

    async def _refresh_for_unknown_kid(self, kid: str) -> None:
        now = time.monotonic()
        if (
            self._missing_kids.get(kid, 0.0) > now
            or now < self._unknown_kid_refresh_at
            or now < self._refresh_retry_at
            or self._keys_lock.locked()
        ):
            return
        async with self._keys_lock:
            now = time.monotonic()
            if kid in self._keys or self._missing_kids.get(kid, 0.0) > now:
                return
            if now < self._unknown_kid_refresh_at or now < self._refresh_retry_at:
                return
            self._unknown_kid_refresh_at = now + self._unknown_kid_refresh_interval_seconds
            await self._load_keys()
            if kid not in self._keys:
                self._missing_kids[kid] = time.monotonic() + self._negative_kid_ttl_seconds

    async def signing_key_for(self, token: str) -> object | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
            return None
        if not await self._refresh_keys_if_stale():
            return None
        key = self._keys.get(kid)
        if key is not None:
            return key
        await self._refresh_for_unknown_kid(kid)
        return self._keys.get(kid)

    async def verify_token(self, token: str) -> AccessToken | None:
        key = await self.signing_key_for(token)
        if key is None:
            return None
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require": ["sub", "iss", "aud", "exp", "iat", "jti", "client_id", "email", "scope"],
                    "strict_aud": True,
                },
            )
        except jwt.PyJWTError:
            return None

        subject = claims.get("sub")
        client_id = claims.get("client_id")
        token_id = claims.get("jti")
        email = str(claims.get("email") or "").strip().lower()
        scope_value = claims.get("scope")
        if not all(isinstance(value, str) and value for value in (subject, client_id, token_id, scope_value)):
            return None
        scopes = scope_value.split()
        if "email" not in scopes or not email or email not in self.allowed_emails:
            return None

        normalized_claims = dict(claims)
        normalized_claims["email"] = email
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=subject,
            claims=normalized_claims,
        )
