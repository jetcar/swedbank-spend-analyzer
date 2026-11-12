# ADR-004: Keep browser demos in memory

## Status

Accepted

## Date

2026-11-09

## Context

Visitors need a Demo button, access to Enable Banking sandbox and repeatable imports
from scratch without saving demo data in PostgreSQL.

## Decision

An opaque HttpOnly session cookie selects a private, process-local demo. Each demo uses
SQLite `:memory:` with temporary storage in memory and serialized access to its single
connection. The existing account, transaction, category and budget handlers are reused.
Demo identities, bank authorization states and change notifications also stay in memory;
they do not create PostgreSQL schemas, control rows or authentication sessions.

Demo starts empty. Reset creates a new identity and destroys the old state, including
pending bank callbacks. Sessions expire daily at 00:00 UTC, are swept within one minute,
and disappear on server restart. Expired demo cookies never fall through to real data.
Exit removes only the demo cookie, preserving any existing Passi login.

Before every banking operation, `GET /application` must report `SANDBOX`. Production
credentials cannot authorize a demo connection. Callback state is single-use, expires
after ten minutes and is bound to the demo browser cookie.

## Configuration

If the existing Enable Banking application is a sandbox, no separate credentials are
needed. Otherwise set `DEMO_ENABLE_BANKING_APP_ID`, `DEMO_BANK_KEY_FILE` to the sandbox
PEM on the host, and `DEMO_ENABLE_BANKING_PRIVATE_KEY_PATH` to
`/run/secrets/enablebanking_demo_private_key`. Compose mounts that key read-only.
`DEMO_BANK_NAME` and `DEMO_BANK_COUNTRY` select the sandbox bank (default Swedbank/EE).
Register the existing `ENABLE_BANKING_REDIRECT_URL` in the sandbox application too.
Rebuild the app image and reload the egress proxy for the added `/application` route.

## Alternatives considered

- A PostgreSQL demo tenant: rejected because imported demo data would be persisted.
- A separate browser implementation of financial calculations: rejected because it
  would duplicate the existing API and risk inconsistent results.

## Consequences

- Deployment must use one app worker; multiple workers would require sticky routing.
- At most 100 active demos are allowed per process; data is intentionally disposable.
- A reset or daily expiration requires reconnecting the sandbox and importing again.
- Sandbox credentials and provider availability are needed for a real import.

## References

- [Enable Banking application environment](https://enablebanking.com/docs/api/reference/#getapplicationresponse)
- [SQLAlchemy in-memory SQLite and concurrency](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)
