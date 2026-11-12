# ADR-003: Remember a signed-in device for 30 days

## Status
Accepted — 2026-11-06.

## Context
The user wants to keep a personal device signed in for 30 days and explicitly
declined binding the OIDC callback to the initiating browser cookie.

## Decision
`/auth/login` presents a Russian/English/Estonian sign-in form with an unchecked
“Remember this device for 30 days” checkbox. `/auth/start` keeps that choice in
the single-use OIDC state alongside PKCE and nonce. The callback cannot change it.
No browser fingerprint or IP address is collected or checked.

After successful authentication, the signed Secure/HttpOnly/SameSite=Lax cookie
and the existing PostgreSQL auth session have an absolute 30-day lifetime when
remembering is selected. Requests do not extend that deadline. Without the
checkbox, the cookie has no persistence attributes and the existing configured
session TTL remains the server-side upper bound. Browser session restoration
can preserve session cookies, so closing the browser is not a guaranteed logout.

The PostgreSQL session store survives application restarts. Logout revokes the
session in the store; every request still checks expiry and the account allowlist.
The app exposes a Sign out button when authentication is enabled.

## Consequences
A remembered cookie is an authentication credential until expiry or revocation.
It is not an OIDC password or token stored in localStorage. Rotating
PASSI_SESSION_SECRET invalidates remembered sessions, so routine deploys must
preserve that secret. Clearing browser cookies or private browsing removes
the remembered login. OIDC state, nonce and PKCE checks remain; requiring the
initiating browser cookie is deliberately omitted as requested.

References: [Starlette SessionMiddleware](https://starlette.dev/middleware/#sessionmiddleware).
