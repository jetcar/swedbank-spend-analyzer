# ADR-002: Authenticate MCP at its private-network boundary

## Status

Superseded by ADR-003

## Date

2026-11-06

## Context

The MCP container was reachable without credentials by every workload on its Docker
network. MCP then used its own privileged REST service token, so network access alone
was enough to list and invoke mutation tools.

## Decision

Require a dedicated Bearer token on every MCP HTTP request. Reject unlisted Host values
and reject every present Origin that is not explicitly allowed. Keep MCP off host and
public ports. The incoming MCP token must differ from the MCP-to-REST service token.

Bank authorization is initiated at the browser-authenticated `/connect` endpoint rather
than by the MCP service identity, preserving browser and data-owner binding.

## Alternatives considered

- Network isolation alone: insufficient after compromise of any workload on the private network.
- Reuse `PASSI_SERVICE_TOKEN`: rejected because the MCP ingress and REST service boundary have different trust domains.
- Passi login for MCP clients: stronger user identity but not supported by the current non-browser MCP clients.

## Consequences

- MCP clients and deployment health checks must send `Authorization: Bearer <MCP_CLIENT_TOKEN>`.
- Token rotation is independent at the two trust boundaries.
- Requests without Origin remain supported for non-browser MCP clients; a supplied Origin is always allowlisted.
