# Spec: MCP interface for Swedbank Spend Analyzer

## Objective

Expose the application's data and mutations through an MCP server so an MCP host can perform the same meaningful operations as the UI: bank connection initiation, sync, account monitoring, transaction and expense reading, category management, assignment, splitting, summaries, and daily breakdowns.

## Tech Stack

- Python 3.12
- Official MCP Python SDK v2 (`mcp`)
- Streamable HTTP transport
- Existing FastAPI REST API as the single business-logic boundary
- PostgreSQL via the existing Docker Compose stack

## Commands

- Build: `docker compose up -d --build`
- Health: `curl http://localhost:8000/health`
- MCP endpoint: `http://localhost:8001/mcp`

## Project Structure

- `app/mcp_server.py` — MCP tools and REST client adapter
- `docker-compose.yml` — MCP service
- `requirements.txt` — MCP SDK dependency
- `tasks/plan-mcp.md` — implementation plan

## Testing Strategy

- Build both application and MCP containers.
- Verify MCP endpoint responds to initialize/list-tools.
- Invoke representative read and mutation tools without changing persistent user data.
- Verify existing UI and REST health endpoints remain available.

## Boundaries

- Always: reuse existing REST business rules; validate tool inputs; keep bank credentials inside the app container.
- Ask first: exposing MCP beyond localhost, adding authentication, or enabling destructive bulk operations.
- Never: return private keys or raw Enable Banking JWTs through MCP; silently change monitored accounts or categories.

## Success Criteria

- MCP lists tools for all meaningful UI operations.
- MCP can read accounts, transactions, expenses, categories, summaries, and daily breakdowns.
- MCP can sync, change account monitoring, create/update/delete categories, assign and split expenses.
- MCP can start bank authorization by returning the browser-authenticated `/connect` URL.
- MCP runs in Docker and is reachable at `localhost:8001/mcp`.
- MCP rejects requests without the dedicated client Bearer token and rejects unlisted Host/Origin values.
