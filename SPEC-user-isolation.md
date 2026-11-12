# Spec: Passi user data isolation

## Objective

Keep every allowed Passi user's bank sessions, accounts, transactions, categories, budgets, and splits isolated from every other user while retaining the explicit email whitelist.

## Identity and migration

- The tenant key is the normalized email from the verified Passi ID token.
- Existing rows belong to `PASSI_LEGACY_OWNER_ID` (`admin@passi.cloud` in production, `local` locally).
- MCP and REST bearer requests belong to the normalized signed email in the validated Passi access token.
- Local Docker with Passi authentication disabled continues to use the `local` tenant without login.
- Finance tables live in a deterministic, non-identifying PostgreSQL schema derived from a SHA-256 hash of the tenant key.
- OAuth sessions and pending bank callback state live in the shared `control` schema; no finance rows live there.
- Existing `public` finance tables are moved atomically to the legacy owner's schema during startup.

## Success criteria

- Every finance ORM session is bound to exactly one tenant schema; unqualified SQL uses only that schema and `pg_catalog`.
- Every read and mutation remains owner-scoped as defense in depth.
- Foreign IDs belonging to another owner behave as not found.
- PostgreSQL rejects cross-owner transaction/category and split/category links even for direct database writes.
- Live refresh events are delivered only to the owner whose data changed.
- Pending bank authorization keeps its initiating owner in PostgreSQL and is single-use.
- Category names are unique per owner and month, not globally.
- New users receive their own default monthly categories.
- Existing production data remains visible only to `admin@passi.cloud`.
- Automated tests prove cross-owner reads and mutations are blocked.
- `public` contains no finance tables after migration.
