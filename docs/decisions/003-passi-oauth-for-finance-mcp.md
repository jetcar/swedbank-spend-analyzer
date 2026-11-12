# ADR-003: Use Passi OAuth for Finance MCP and API

## Status

Accepted

## Date

2026-11-09

## Context

ADR-002 introduced two static secrets: one at the MCP ingress and another between
MCP and the Finance REST API. They authenticated a deployment, not the Passi user,
and prevented standard remote MCP OAuth discovery and dynamic client registration.

Passi now issues RS256 access tokens bound to the resource
`https://finance.passi.cloud/mcp`, includes the signed user email, supports PKCE S256,
refresh rotation and dynamic client registration, and publishes its signing keys.

## Decision

Finance is an OAuth protected resource. Its MCP endpoint publishes RFC 9728 metadata
and points clients to `https://passi.cloud/openidc`. The MCP SDK validates Passi access
tokens using JWKS and requires audience `https://finance.passi.cloud/mcp` and scope
`email`. Finance additionally requires the signed email to be in its allowlist.

MCP forwards the same bearer access token to the REST API. The API independently
validates its signature, issuer, audience, lifetime, required claims, scope and email,
then selects the tenant using the normalized signed email. Neither boundary checks or
requires `email_verified`. Browser sessions remain cookie-based after their existing
OIDC login and do not expose access tokens to browser JavaScript.

## Alternatives considered

- Keep both static secrets: rejected because they carry no end-user identity and do
  not support MCP OAuth discovery.
- Exchange the Passi token for a Finance token: rejected because it adds another
  issuer, key lifecycle and token store without improving the single-resource flow.
- Trust MCP and skip API validation: rejected because a compromised MCP container
  would regain deployment-wide REST access.

## Consequences

- Passi is the only token issuer; Finance remains a resource server.
- MCP and REST requests are confined to the same signed Passi identity and tenant.
- Production no longer needs `MCP_CLIENT_TOKEN` or `PASSI_SERVICE_TOKEN`.
- Local development without Passi retains the old MCP bearer as an explicit fallback.
- JWKS access remains constrained to the existing allowlisted egress proxy.
