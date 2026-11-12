# Implementation Plan: Monthly Categories

## Monthly plan forecast — 2026-11-11

Approved contract: `SPEC-month-forecast.md`. Categories remain recurring plans;
one-off items represent additional expenses and expected income.

1. Add category settings and one-off records, validated tenant-scoped API and exact forecast calculations.
2. Add the Plan view, Overview forecast, payment controls and one-off transaction linking.
3. Verify accounting boundaries and isolation with backend tests, frontend regression and desktop/mobile browser checks.
4. Independently review the implementation, resolve findings and update Graphify.

No production deployment or real financial-data changes are included.

## Finance redesign — 2026-11-09

Implement the approved light and graphite mockups in the existing vanilla frontend.
Keep the existing authenticated API, tenant isolation, balances, monthly budgets,
category assignment, split editing, language selection and live updates.

1. Shared color tokens, persisted theme, accessible navigation, responsive shell and login.
2. Real-data overview, monthly operation list/day grouping and category/account management.
3. Verify both themes at desktop and phone widths; exercise mutations, empty/error states,
   language switching and theme persistence; run frontend and targeted API regression tests.
4. Update graphify, commit, push and create a PR against current master.

The overview chart shows actual daily income/spending, not a fabricated balance history.
Monthly totals must not depend on the operation-list limit. Currencies stay separate.
The original workspace's unrelated changes are preserved in place.

Acceptance: four working views plus login; no horizontal page overflow at 375px;
keyboard-visible focus and labelled controls; negative budgets remain income targets;
no production demo data or new bank requests are introduced for preview.

## Task List

1. Модель и миграция месячных категорий.
2. API месяца, копирования, бюджета, активности и удаления.
3. UI менеджмента категорий и выбор месяца.
4. Подключение выбранного месяца к спискам и назначениям.
5. Автоматическая проверка сценариев.

## Risks

- Существующие категории глобальные: миграция должна разнести назначения по месяцам до включения уникальности.
- Удаление не должно затронуть операции соседнего месяца.
- Полный перерендёринг не должен возвращать скачки прокрутки в уже исправленном сценарии.

## Checkpoints

- После миграции: приложение запускается и существующие операции доступны.
- После API: CRUD месячной категории работает без ручного SQL.
- Перед завершением: автоматический API/UI smoke test проходит.

## Backend architecture — 2026-11-09

Goal: app/main.py only composes FastAPI, middleware, routers and lifecycle.
Keep routes, request/response schemas, SQL, transaction boundaries, isolation and
bank consent semantics unchanged. Preserve unrelated working-tree edits.

1. Extract request models, date helpers and database/schema infrastructure. Verify demo and date tests.
2. Extract bank client, import/scheduling services and startup orchestration. Verify sync/security tests.
3. Separate finance services from HTTP routers; move pages and event streaming to routers.
4. Migrate internal imports and test patches to their owning modules. Compare OpenAPI before/after;
   run all Python tests against disposable PostgreSQL, plus frontend tests.
5. Document dependency boundaries, add architecture regression checks, update graphify.

Dependencies flow from main -> routers/services -> database/schema/models and bank client.
No application module imports main. Services never import routers. Shared state has one owner.
Risks: mocks patched at the wrong module, import cycles, changed FastAPI defaults, demo reaching
persistent storage. Existing security/integration tests and API-schema comparison cover these.

## Remaining backend/frontend review — 2026-11-09

Review all application modules and static assets for dependency boundaries, stale callers,
request lifecycle and failure handling. Preserve explicit decisions, including ADR-003's
OIDC callback behavior, existing HTTP routes and the current visual design.

1. Reproduce/fix tenant routing in fixture scripts and validate OIDC token failure handling.
2. Extract a DOM-free frontend request client and versioned dashboard loader; test failures/races.
3. Split dashboard feature views/editors from composition, with explicit state/callback inputs.
4. Handle demo SSE expiry and mutation errors; exercise browser regressions and import boundaries.
5. Run isolated PostgreSQL/backend tests, frontend unit/browser checks, document findings and update graph.
