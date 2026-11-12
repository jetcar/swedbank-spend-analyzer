import base64
import hashlib
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import build_resource_metadata_url
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .device_session import REMEMBER_DEVICE_SECONDS
from . import demo
from .frontend import frontend_page
from .passi_token import PassiAccessTokenVerifier


router = APIRouter(prefix="/auth")
AUTH_ENABLED = os.getenv("PASSI_AUTH_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
PASSI_ISSUER = os.getenv("PASSI_ISSUER", "https://passi.cloud/openidc").rstrip("/")
PASSI_AUTHORIZATION_ENDPOINT = os.getenv(
    "PASSI_AUTHORIZATION_ENDPOINT", f"{PASSI_ISSUER}/login"
)
PASSI_TOKEN_ENDPOINT = os.getenv(
    "PASSI_TOKEN_ENDPOINT", f"{PASSI_ISSUER}/connect/token"
)
PASSI_JWKS_ENDPOINT = os.getenv(
    "PASSI_JWKS_ENDPOINT", f"{PASSI_ISSUER}/.well-known/jwks"
)
PASSI_RESOURCE = os.getenv("PASSI_RESOURCE", "https://finance.passi.cloud/mcp").rstrip("/")
PASSI_EGRESS_BASE_URL = os.getenv("PASSI_EGRESS_BASE_URL", "").rstrip("/")
PASSI_CLIENT_ID = os.getenv("PASSI_CLIENT_ID", "")
PASSI_CLIENT_SECRET = os.getenv("PASSI_CLIENT_SECRET", "")
PASSI_LOCAL_OWNER_ID = os.getenv("PASSI_LOCAL_OWNER_ID", "local").strip().lower()
PASSI_SESSION_MAX_AGE_SECONDS = int(os.getenv("PASSI_SESSION_MAX_AGE_SECONDS", "1209600"))
PASSI_PENDING_STATE_LIMIT = max(1, int(os.getenv("PASSI_PENDING_STATE_LIMIT", "1024")))
PASSI_ALLOWED_EMAILS = {
    value.strip().lower()
    for value in os.getenv("PASSI_ALLOWED_EMAILS", "").split(",")
    if value.strip()
}
PASSI_REDIRECT_URI = os.getenv(
    "PASSI_REDIRECT_URI", "http://localhost:8000/auth/callback"
)

_pending: dict[str, tuple[str, str, str, bool, float]] = {}
_revoked_sessions: dict[str, float] = {}
logger = logging.getLogger(__name__)


def validate_auth_configuration() -> None:
    if AUTH_ENABLED and not PASSI_ALLOWED_EMAILS:
        raise RuntimeError("PASSI_ALLOWED_EMAILS must contain at least one address when authentication is enabled")


def _session_store(request: Request) -> Any:
    store = getattr(request.app.state, "auth_session_store", None)
    if store is None:
        raise HTTPException(503, "Authentication session storage is unavailable")
    return store


def _server_endpoint(endpoint: str) -> str:
    """Keep public OIDC URLs in config while routing server calls through egress."""
    parsed = urlsplit(endpoint)
    if PASSI_EGRESS_BASE_URL and parsed.scheme == "https" and parsed.netloc == "passi.cloud":
        return f"{PASSI_EGRESS_BASE_URL}/passi{parsed.path}"
    return endpoint


PASSI_ACCESS_TOKEN_VERIFIER = PassiAccessTokenVerifier(
    issuer=PASSI_ISSUER,
    audience=PASSI_RESOURCE,
    jwks_url=_server_endpoint(PASSI_JWKS_ENDPOINT),
    allowed_emails=PASSI_ALLOWED_EMAILS,
)
PASSI_RESOURCE_METADATA_URL = str(build_resource_metadata_url(PASSI_RESOURCE))


def _random_urlsafe(size: int = 32) -> str:
    return secrets.token_urlsafe(size)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _token_request_data(code: str, verifier: str) -> dict[str, str]:
    data = {
        "grant_type": "authorization_code",
        "client_id": PASSI_CLIENT_ID,
        "code": code,
        "redirect_uri": PASSI_REDIRECT_URI,
        "code_verifier": verifier,
    }
    if PASSI_CLIENT_SECRET:
        data["client_secret"] = PASSI_CLIENT_SECRET
    return data


def _safe_return_url(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") and not any(c in value for c in "\\\r\n") else "/"


def _cleanup_pending() -> None:
    now = time.time()
    for state, (*_, expires) in list(_pending.items()):
        if expires < now:
            _pending.pop(state, None)
    for sid, expires in list(_revoked_sessions.items()):
        if expires < now:
            _revoked_sessions.pop(sid, None)


@router.get("/login")
async def login(request: Request, return_url: str = "/") -> Response:
    if not AUTH_ENABLED or request.session.get("user"):
        return RedirectResponse(_safe_return_url(return_url))
    return frontend_page("login.html")


@router.get("/start")
async def start_login(request: Request, return_url: str = "/", remember_device: bool = False) -> RedirectResponse:
    if not AUTH_ENABLED:
        return RedirectResponse(_safe_return_url(return_url))
    if not PASSI_CLIENT_ID:
        raise HTTPException(503, "PASSI_CLIENT_ID is not configured")
    _cleanup_pending()
    while len(_pending) >= PASSI_PENDING_STATE_LIMIT:
        oldest_state = min(_pending, key=lambda item: _pending[item][4])
        _pending.pop(oldest_state, None)
    state = _random_urlsafe()
    nonce = _random_urlsafe()
    verifier = _random_urlsafe(48)
    _pending[state] = (verifier, nonce, _safe_return_url(return_url), remember_device, time.time() + 600)
    query = urlencode(
        {
            "client_id": PASSI_CLIENT_ID,
            "redirect_uri": PASSI_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(f"{PASSI_AUTHORIZATION_ENDPOINT}?{query}")


async def _jwks_key(token: str) -> object:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise HTTPException(401, "Invalid Passi ID token") from error
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(_server_endpoint(PASSI_JWKS_ENDPOINT))
        response.raise_for_status()
    except httpx.HTTPError as error:
        logger.exception("Passi JWKS request failed")
        raise HTTPException(502, "Passi signing keys are unavailable") from error
    for key in response.json().get("keys", []):
        if key.get("kid") == header.get("kid"):
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    raise HTTPException(401, "Passi signing key not found")


@router.get("/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None) -> Response:
    if not AUTH_ENABLED:
        return RedirectResponse("/")
    if error:
        if state:
            _pending.pop(state, None)
        raise HTTPException(401, f"Passi authorization failed: {error}")
    if not code or not state:
        raise HTTPException(400, "Passi authorization callback is incomplete")
    pending = _pending.get(state)
    if pending is None or pending[4] < time.time():
        _pending.pop(state, None)
        raise HTTPException(400, "Invalid or expired Passi authorization state")
    verifier, nonce, return_url, remember_device, _ = _pending.pop(state)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                _server_endpoint(PASSI_TOKEN_ENDPOINT),
                data=_token_request_data(code, verifier),
            )
    except httpx.HTTPError as error:
        logger.exception("Passi token request failed")
        raise HTTPException(502, "Passi token endpoint is unavailable") from error
    if response.is_error:
        logger.error("Passi token endpoint returned %s: %s", response.status_code, response.text[:500])
        raise HTTPException(401, "Passi token exchange failed")
    tokens = response.json()
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(401, "Passi did not return an ID token")
    key = await _jwks_key(id_token)
    try:
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=["RS256"],
            audience=PASSI_CLIENT_ID,
            issuer=PASSI_ISSUER,
            options={"require": ["sub", "iss", "aud", "exp", "iat"]},
        )
    except jwt.PyJWTError as error:
        logger.warning("Passi ID token validation failed: %s", error)
        raise HTTPException(401, "Invalid Passi ID token") from error
    email = str(claims.get("email") or "").strip().lower()
    if not PASSI_ALLOWED_EMAILS or email not in PASSI_ALLOWED_EMAILS:
        raise HTTPException(403, "This Passi account is not allowed")
    if claims.get("nonce") != nonce:
        raise HTTPException(401, "Passi nonce validation failed")
    request.session.clear()
    request.session["user"] = {
        "sub": claims["sub"],
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("preferred_username"),
    }
    authenticated_at = int(time.time())
    sid = _random_urlsafe()
    lifetime = REMEMBER_DEVICE_SECONDS if remember_device else PASSI_SESSION_MAX_AGE_SECONDS
    await _session_store(request).create(sid, email, authenticated_at + lifetime)
    request.session["sid"] = sid
    request.session["authenticated_at"] = authenticated_at
    request.session["remember_device"] = remember_device
    return RedirectResponse(return_url)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    if not AUTH_ENABLED:
        return RedirectResponse("/")
    sid = str(request.session.get("sid") or "")
    if sid:
        await _session_store(request).revoke(sid)
        _revoked_sessions[sid] = time.time() + max(PASSI_SESSION_MAX_AGE_SECONDS, REMEMBER_DEVICE_SECONDS)
    request.session.clear()
    return RedirectResponse("/")


@router.get("/status")
async def status(request: Request) -> JSONResponse:
    if demo.owner_from_request(request):
        return JSONResponse({"enabled": AUTH_ENABLED, "authenticated": False, "demo": True})
    authenticated = bool(request.session.get("user")) if AUTH_ENABLED else False
    return JSONResponse({"enabled": AUTH_ENABLED, "authenticated": authenticated})


class PassiAuthMiddleware(BaseHTTPMiddleware):
    @staticmethod
    def _api_auth_error(description: str = "Authentication required") -> JSONResponse:
        return JSONResponse(
            {"detail": description},
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    'Bearer error="invalid_token", '
                    f'error_description="{description}", '
                    f'resource_metadata="{PASSI_RESOURCE_METADATA_URL}"'
                )
            },
        )

    async def _bearer_identity(self, request: Request) -> AccessToken | None:
        headers = request.headers.getlist("authorization")
        if not headers:
            return None
        if len(headers) != 1:
            return None
        scheme, separator, token = headers[0].partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            return None
        return await PASSI_ACCESS_TOKEN_VERIFIER.verify_token(token.strip())

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path == "/auth/demo" or path.startswith("/auth/demo/"):
            return await call_next(request)
        demo_owner = demo.owner_from_request(request)
        if demo_owner and path.startswith("/static/"):
            return await call_next(request)
        if demo_owner and (path == "/" or path == "/connect" or path == "/callback" or path.startswith("/api/") or path == "/auth/status"):
            try:
                demo.store.get(demo_owner)
            except HTTPException as error:
                if path == "/auth/status":
                    return JSONResponse({"enabled": AUTH_ENABLED, "authenticated": False, "demo": True, "expired": True})
                if path == "/":
                    return RedirectResponse("/auth/demo")
                return JSONResponse({"detail": error.detail}, status_code=401)
            request.state.owner_id = demo_owner
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        if not AUTH_ENABLED:
            request.state.owner_id = PASSI_LOCAL_OWNER_ID
            return await call_next(request)
        if not PASSI_ALLOWED_EMAILS:
            return JSONResponse({"detail": "Authentication allowlist is not configured"}, status_code=503)
        _cleanup_pending()
        path = request.url.path
        authorization_headers = request.headers.getlist("authorization")
        if path.startswith("/api/") and authorization_headers:
            access_token = await self._bearer_identity(request)
            if access_token is None:
                return self._api_auth_error("Invalid Passi access token")
            request.state.owner_id = (access_token.claims or {})["email"]
            return await call_next(request)
        user = request.session.get("user")
        if user:
            owner_id = str(user.get("email") or "").strip().lower()
            sid = str(request.session.get("sid") or "")
            authenticated_at = request.session.get("authenticated_at")
            lifetime = REMEMBER_DEVICE_SECONDS if request.session.get("remember_device") is True else PASSI_SESSION_MAX_AGE_SECONDS
            now = int(time.time())
            active = (
                owner_id in PASSI_ALLOWED_EMAILS
                and bool(sid)
                and isinstance(authenticated_at, int)
                and 0 <= now - authenticated_at < lifetime
                and sid not in _revoked_sessions
            )
            if active:
                try:
                    active = await _session_store(request).is_active(sid, owner_id, now)
                except HTTPException:
                    active = False
            if active:
                request.state.owner_id = owner_id
            else:
                if sid:
                    try:
                        await _session_store(request).revoke(sid)
                    except HTTPException:
                        pass
                request.session.clear()
                user = None
        public = (
            path == "/health"
            or path in {"/privacy", "/terms", "/callback"}
            or path.startswith("/auth/")
            or path.startswith("/static/")
        )
        if public or user:
            return await call_next(request)
        if path.startswith("/api/"):
            return self._api_auth_error()
        return RedirectResponse(f"/auth/login?return_url={_safe_return_url(path)}")
