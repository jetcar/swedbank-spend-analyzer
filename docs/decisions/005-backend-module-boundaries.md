# ADR-005: Separate application composition, HTTP and use cases

## Status

Accepted

## Date

2026-11-09

## Context

`app/main.py` combined application startup, database upgrades, bank HTTP calls,
authorization, transaction import, financial queries, HTML and API routing.
Tests and maintenance scripts imported its internals. Moving any dependency
could silently invalidate mocks or introduce an import cycle.

## Decision

Keep the existing FastAPI and SQLAlchemy stack and the `app.main:app` entrypoint.
Use [FastAPI routers](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
with explicit module dependencies:

| Module | Responsibility |
| --- | --- |
| `main.py` | Compose middleware and routers; start and stop workers and storage |
| `routers/` | HTTP parameters, identity binding, responses, pages and event streaming |
| `services/accounts.py` | Account balances, monitoring and duplicate merging |
| `services/categories.py` | Monthly defaults, budgets and category mutations |
| `services/transactions.py` | Transaction queries, statistics flags, expense assignment and splits |
| `services/reports.py` | Summary and daily aggregation |
| `services/banking.py` | Single-use consent records and importing connected accounts |
| `services/bank_sync.py` | Persist imported pages and coordinate account imports |
| `services/auto_sync.py` | Due-owner discovery, durable sync claims and worker loop |
| `services/sync_state.py` | Shared sync result timestamps and notifications; memory-only state for demos |
| `transaction_details.py` | Counterparty presentation rules shared by transaction and report responses |
| `frontend.py` | Built asset manifest, page responses and cache policy |
| `bank_client.py` | Enable Banking credentials, sandbox checks, HTTP calls and pagination |
| `database.py` | Engine, session factories, tenant routing and schema readiness |
| `schema.py` | PostgreSQL DDL and legacy schema upgrade operations |
| `startup.py` | Initialize schemas, seed initial categories and merge duplicate accounts |
| `auth_sessions.py` | Persistent browser-session store |
| `schemas.py`, `dates.py`, `dependencies.py` | Request types, date rules and authenticated HTTP identity |
| `config.py` | Existing environment-based database and banking configuration |
| `static/privacy.html`, `static/terms.html` | Public legal-page content |

Dependencies flow from composition to HTTP adapters, services and infrastructure.
No application module imports `main`; services never import routers or bind
FastAPI `Request`, `Query` or `Depends` objects. Services receive explicit values
and own their transaction boundaries. SQLAlchemy models and queries remain in
the services that use them; a generic repository layer would add indirection
without hiding a second persistence implementation.

The existing HTTPException error contract is retained. Existing Passi and demo
modules remain self-contained feature adapters. Demo database routing stays in
one place, so every service uses the same memory-only isolation behavior.

There is one shared engine and one owner of each session factory. Consumers
access mutable infrastructure through its module; tests patch that owner,
not re-exported symbols on `main`. The bank page stream is closed explicitly
if persistence fails. Startup commands import `app.startup.create_tables`.

## Alternatives considered

- Split route functions into files while keeping imports through `main`:
  retains hidden dependencies and risks circular imports.
- Introduce repositories, a dependency container and a new migration framework:
  changes more behavior than required for separating the existing responsibilities.

## Consequences

The public OpenAPI schema and existing database operations remain unchanged.
Internal Python imports change; HTTP clients and `app.main:app` consumers do not.
Schema upgrades still run at startup; versioned migrations are a separate change.

Architecture tests enforce acyclic application imports and the HTTP/service
boundary. Regression checks cover real PostgreSQL isolation, callback replay
protection, financial calculations, sandbox-only demos and worker shutdown.
