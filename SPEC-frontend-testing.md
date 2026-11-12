# Spec: Modular frontend logic and tests

## Objective

Keep expense and category behavior in importable, pure JavaScript modules. Verify calculations and state transitions with Vitest; reserve browser automation for a small number of DOM smoke tests.

## Test boundaries

- Unit tests: expense indexing, split detection, moving all split parts, totals, filtering, and other state transformations.
- DOM smoke tests: only confirm that important user actions render the expected result in a real browser.
- No Playwright coverage for pure calculations or API-independent state logic.

## Commands

- Unit tests: `npm test`
- Watch mode: `npm run test:watch`

## Success Criteria

- Core daily-expense logic is importable from `app/static/expense-domain.js`.
- Unit tests run without a browser and cover split return behavior.
- The application still loads as an ES module in the browser.
- Browser checks remain narrow and do not replace unit tests.

## Interface languages

- `app/static/i18n.js` supplies Russian (default), English and Estonian interface messages.
- The header selector saves the language in `localStorage` under `spend-analyzer-language`; unavailable storage falls back to an in-memory selection.
- Switching updates static labels, dynamic views, budget states, dialogs, errors and document language without navigation. Month, category selection, filters and form drafts remain in place.
- Dates, currency amounts, percentages and transaction counts use the selected locale. Category names, account names and transaction descriptions are user data and are not translated or rewritten.
- Tests check translation coverage, interpolation, plural forms, formatting, persistence and blocked storage. Browser smoke checks cover English, Estonian, the split dialog and persistence after reload.
