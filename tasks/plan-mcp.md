# Implementation Plan: MCP interface

## Task List

1. [x] Add the official MCP SDK and a Docker service.
2. [x] Implement a REST-backed MCP adapter with read and mutation tools.
3. [x] Add authorization initiation and connection status tools.
4. [x] Build and run MCP protocol smoke tests.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| MCP SDK API changes | Pin to the v2 major line and verify with the container build. |
| Duplicated business logic | MCP calls the existing REST endpoints. |
| Bank authorization needs a browser | Return the existing authorization URL; keep the callback in the app. |
| Accidental destructive changes | Expose delete only as an explicit tool and do not add bulk deletion. |
