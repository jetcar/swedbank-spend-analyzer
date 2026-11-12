## Monthly plan forecast — 2026-11-11
- [x] Category payment settings, one-off API and current-month closing forecast.
- [x] Plan view, Overview summary, translations and accessible mobile controls.
- [x] Accounting/isolation tests, frontend tests, browser verification and independent review.
- [x] Documentation and graph update.

Verification: 63 frontend unit/architecture tests and 15 forecast API/PostgreSQL
tests pass. Browser regression covers 30 theme/layout combinations, plan CRUD,
transaction linking and explicit unlinking, partial/closed payments, stale links,
failed writes and retry after successful creation followed by a failed refresh.
An actual isolated app/browser check confirmed 50.00 planned minus 32.83 linked
actual leaves 17.17 pending. Independent review findings were fixed, including
UTC planning-month metadata, currency fallback and a consistent database snapshot.

Follow-up: fixed notification-trigger owner escaping using explicit PostgreSQL
escape strings. The regression passes with standard_conforming_strings both on
and off, on SQLAlchemy 2.0.36 and the pinned 2.1.6. The production image builds
with pinned dependencies; live SSE commit/rollback integration passes against
the disposable application and database.

Local rollout verified: 135 backend tests pass with pinned dependencies, plus
11 Compose network/gateway checks and the live SSE test. The optional external
bank connectivity probe was skipped. Recreated only the local app service and
reloaded the gateway; health/database and the Plan UI at
http://127.0.0.1:8000/#plan are working. A pre-update PostgreSQL custom-format
dump is retained under `%LOCALAPPDATA%/bank-backups/20261111-151004/database.dump`;
the previous app image is tagged `bank-app:before-month-plan`.

PR integration is based on current master, retaining its account-currency,
balance and pending-transaction fixes. Duplicate bank transactions transfer
one-off plan links to the survivor; an existing survivor allocation wins.
All 19 merge tests and 15 forecast tests pass after this integration fix, and
63 frontend tests, the production build and 30-layout browser regression pass.

CI follow-up: the dark tablet header overflowed at 768px. Navigation now wraps
onto a separate row between 761px and 1100px. The expanded browser regression
checks header controls and 70 width/theme/view combinations, including both
sides of responsive breakpoints. It fails against the old CSS and passes with
the fix in Linux Chromium and Windows Edge; 63 frontend unit tests also pass.
The GitHub runner then stalled resolving its Ubuntu package mirror during
Playwright dependency installation. Browser CI now uses the official Playwright
1.64.1 Noble image pinned by digest, with read-only source and a disposable test
copy, matching the locally verified Linux environment and retaining every test.

- [x] Модель и миграция месячных категорий
- [x] API управления месячными категориями
- [x] UI менеджмента и выбора месяца
- [x] Месячные категории в списках и назначениях
- [x] Автоматические проверки

## Finance redesign
- [x] Shared themes, navigation and login; syntax and theme tests pass.
- [x] Overview, operations, categories and accounts use real API data; API regression tests pass.
- [x] Desktop/mobile browser checks, translations, mutations and empty/error states pass.
- [x] Graph updated, commits pushed and PR #25 created.

## Backend architecture
- [x] Extract foundational modules; preserve database and date behavior.
- [x] Extract banking/scheduling/startup; preserve consent and sync behavior.
- [x] Separate HTTP routers and finance services; preserve API schema.
- [x] Verify imports, backend regression and dependency boundaries; document and update graph.

Verification: 95 backend tests against disposable PostgreSQL, one live SSE integration
test and 45 frontend tests passed. OpenAPI matches the pre-refactor schema exactly.
Compose network-security checks require a running gateway/egress stack and were
unavailable in the isolated application test environment.

## Remaining backend/frontend review
- [x] Audit remaining backend modules and fix confirmed failures.
- [x] Separate dashboard loading, API transport and feature views.
- [x] Verify request races, errors, demo expiry, browser flows and architecture boundaries.
- [x] Record review coverage and update graph.

Verification: 102 backend tests and one live SSE integration test passed against
disposable PostgreSQL; 55 frontend unit/architecture tests and the browser
regression passed in Edge. OpenAPI is unchanged. The SSE test now uses the local
API owner, matching seed_demo even when the legacy migration owner differs.
Full Compose gateway/egress network-security checks remain outside this isolated
application test environment. Coverage: `docs/backend-frontend-review.md`.

## Pull request integration
- [x] Preserve current master features in extracted modules: sync timestamps,
  counterparty names, dialog dismissal and fingerprinted assets/cache policy.
- [x] Build the production Docker image with updated master dependencies.
- [x] Pass 112 backend tests, 56 frontend tests and browser regression after merging.
- [x] Compare all existing master OpenAPI operations and schemas; no changes.
- [x] Pass live SSE commit/rollback integration after merging master.
