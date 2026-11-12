# ADR-001: Scope finance data by Passi identity

## Status

Accepted

## Date

2026-11-06

## Context

Passi authentication originally protected one shared finance database. Adding more allowed emails would therefore expose the same bank data to every allowed user.

## Decision

Use the normalized email claim from the verified Passi ID token as the tenant key. Put each tenant's complete finance model in a separate PostgreSQL schema whose name is a non-identifying SHA-256 derivative. Bind each SQLAlchemy session to one schema with `schema_translate_map`, and restrict literal SQL with a transaction-local `search_path` containing only that schema and `pg_catalog`. Keep owner columns, query filters, relationship triggers, and owner-tagged notifications as defense in depth. The whitelist remains the independent, fail-closed admission boundary.

OAuth sessions and one-time bank callback state belong to a shared `control` schema. Existing finance tables in `public` are moved atomically to the configured legacy owner's schema; indexes, constraints, and owned sequences move with their tables. New tenant schemas are created lazily under a PostgreSQL advisory lock.

Existing rows are assigned to a configured legacy owner. MCP requests use one configured owner because their service token has no browser identity. Local development uses a dedicated local owner. Pending bank authorization state is stored in PostgreSQL so callbacks keep their owner across restarts and are consumed once.

## Alternatives considered

- Passi subject identifier: more stable, but the existing administrator's subject is unavailable during database migration.
- Separate database per user: stronger isolation against an explicitly wrong-schema query, but unnecessary operational complexity for this private service.
- Owner column on every table: easier filtering but duplicates identity and permits inconsistent ownership across related rows.

## Consequences

- Email changes require an explicit owner migration.
- Cross-owner foreign IDs return not found.
- Adding an email to the whitelist permits login but does not expose another user's data.
- A missing owner predicate cannot expose another tenant because the other tenant's tables are absent from the bound schema.
- The application database role still owns all tenant schemas; an explicit bug that deliberately selects a different fully qualified schema is outside this boundary. Separate database credentials per tenant would be required to prevent that class of defect.
