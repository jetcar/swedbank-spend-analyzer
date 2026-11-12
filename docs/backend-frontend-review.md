# Backend and frontend review — 2026-11-09

## Confirmed issues addressed

| Area | Reproduction and correction | Regression coverage |
| --- | --- | --- |
| Fixture database access | `seed_demo` queried the unmapped `tenant` schema; both fixture scripts now use the shared tenant session router. The MCP categorizer rejects an owner that differs from the local API owner, and rejects OAuth mode before touching data. | Two isolated PostgreSQL tenants, repeated seeding, early owner/mode rejection |
| OIDC lifetime validation | A correctly signed ID token without `exp` or `iat` created a browser session. Both claims are now required. A malformed JWT header now returns 401 instead of an uncaught decode error. | Real RSA signatures; omitted, expired, future and valid claims; malformed header |
| Frontend dependencies | The dashboard entry point owned all state, requests and feature renderers. Factories now receive explicit state and callbacks. | Import-cycle, feature-boundary and DOM-independence tests; browser interactions |
| Month loading | An older failed request could report an error after a newer month loaded. The loader suppresses obsolete successes and failures and publishes complete snapshots. | Controlled out-of-order promises and current-request failure |
| Mutation failures | Category and monitoring handlers could reject without restoring controls. They now use shared action handling; category PATCH requests only include the edited property. | Browser failure/retry scenarios for category edits/deletion, monitoring and balances |
| Demo event lifecycle | The browser ignored the server's `expired` event and retained its EventSource. Expiry now closes the stream and returns to demo entry; stopped refreshes cannot retry after failure. | Fake-timer SSE tests and actual browser EventSource navigation |

## Coverage and boundaries

Backend review covered composition, routers, services, tenant storage/schema
initialization, models/request types, date handling, bank transport/import,
scheduled synchronization, Passi browser and bearer authentication, device
sessions, demo storage, MCP ingress/tools, fixture scripts and logging.

Frontend review covered dashboard scripts, domain helpers, localization,
authentication/demo controls, theme/icons, page markup and stylesheet boundaries.
CSS retains the existing split between shared design primitives, dashboard and
sign-in layouts. MCP remains an HTTP adapter; Passi and demo modules retain
their feature-specific infrastructure. Their size alone does not require more
layers.

The cross-browser OIDC callback behavior in ADR-003 remains intentional; this
review does not reintroduce an initiating-browser cookie requirement. Database
upgrades remain startup DDL as recorded in ADR-005; introducing a versioned
migration system needs its own migration/backfill plan.

## Verification

- 112 backend tests passed against disposable PostgreSQL after integrating the
  current master branch, including tenant
  isolation, authentication, demo sandbox restrictions, financial calculations,
  worker lifecycle and module boundaries.
- 56 frontend unit and architecture tests passed, covering requests, races, monetary helpers,
  localization and SSE lifecycle.
- Browser regression uses installed Edge and synthetic API responses: 24
  width/theme/view combinations, filtering, assignment, splitting, statistics,
  negative budgets, failed mutations, language changes, demo expiry, sync
  timestamps, counterparty search and dialog dismissal without saving drafts.
- The production Docker image builds successfully with the current dependency
  versions and fingerprinted frontend assets. HTML and asset cache tests pass.
- One live SSE integration test passed with two listeners, commit notification
  and rollback silence. Its fixture owner now matches the local API owner;
  the first run exposed the test's incorrect reliance on the legacy migration
  owner when those configurations differ.
- Every existing operation and schema in master's OpenAPI contract is preserved;
  the only new routes are the four demo entry/start/exit/connect endpoints.

The full Compose gateway/egress network-security suite was not run: the isolated
application environment does not include that stack. Browser API responses and
upstream bank/OIDC responses were synthetic; no production bank data was used.

## Security scan follow-up

The first post-refactor Semgrep run reported 32 interpolated SQL statements
and four Django CSRF-template findings. The moved SQL was previously outside
the changed-code baseline. Queries now use SQLAlchemy expressions and bound
values; schema creation uses `CreateSchema`. Custom PostgreSQL DDL uses static
templates, validates generated tenant schema names, and quotes identifiers and
trigger arguments with the PostgreSQL compiler. See the
[SQLAlchemy DDL API](https://docs.sqlalchemy.org/en/20/core/ddl.html#sqlalchemy.schema.DDL).

The four HTML forms are static FastAPI pages, so Django template tokens do not
apply. Each form has a rule-specific annotation documenting the existing gateway
Origin/Fetch Metadata protection. No scanner rule or gateway protection was
disabled globally. Live nginx tests verify that demo POSTs reject foreign/null
origins and cross-site navigation.

The exact failing Semgrep image/rules/baseline reproduced 36 findings before
the fix and zero afterward. Verification also passed 116 backend tests, live SSE
commit/rollback, four gateway checks and the frontend browser regression. CI now
runs tenant SQL, migration, budget and demo regressions against disposable
PostgreSQL alongside the callback tests.
