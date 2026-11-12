# Monthly plan and forecast

## Objective and accepted scope
Categories are the recurring monthly plan, copied forward with their budget. Bank transactions populate actuals. Add one-off expenses and expected income for one month, and show the expected closing balance without counting completed cash flows twice. User approved this behavior in the conversation; implementation and additive schema changes are authorized.

## Contract
- Category adds `forecast_mode`: `budget` (default) or `payment`; `payment_status`: `auto` (default), `open`, `closed`; `currency`: uppercase three letters, default EUR. Negative existing budgets remain expected recurring income. Copy currency and mode to the next month, reset status to auto. Existing budgets are preserved.
- Budget remainder is max(abs(plan) - matching actual, 0). Payment auto closes on any matching nonzero actual; explicit open preserves the unpaid difference for partial payments, closed means nothing else expected. Original planned amount is never overwritten.
- One-off items are additional to category plans, with month, name, direction DBIT/CRDT, amount (positive decimal), currency, payment_status and transaction_ids. Multiple whole transactions can fulfill an item; a transaction may fulfill at most one item. Link/unlink is explicit. Item actuals update automatically from links. Auto closes a one-off on any matching actual; open supports installments. No duplicate imported transactions or bank mutations.
- A linked whole transaction is reserved for its one-off item and does not also consume a category's recurring allowance in the forecast. UI labels these as additional to category plans. Expenses already covered by a category stay in that category without a separate one-off.
- When bank synchronization merges duplicate transactions, transfer the duplicate's plan link to the survivor if it has no link. An existing survivor link takes precedence, so one logical cash movement never fulfills two items.
- Validate all links against owner, month, direction, currency, tracked account, positive amount and exclusion flag. Excluded/untracked/moved or deleted transactions cease fulfilling plans on refresh. Dates use the project's booking/value-date convention. Split transactions may link as a whole; their category allocations are then omitted from category forecast actuals.
- Forecast aggregates all eligible transactions (no pagination cap), category allocations (including splits exactly once), and only tracked account balances. Currency groups never mix. Unknown currency is separate.
- Read the forecast from one consistent database snapshot so concurrent synchronization or linking cannot mix actuals, balances and links. Initialize an absent month before opening the read snapshot.
- Transaction responses expose `planning_month` from the stored effective timestamp in UTC; the picker uses it rather than the localized display date. A missing transaction currency falls back to the account currency. Invalid existing links remain visible and require explicit removal.
- Current-month closing balance = current tracked balance + unreceived income - unspent planned expenses. Actual cash movements are already in balance. Exclusion from statistics does not remove cash from balance. Show forecast only for the current month; other months still support plan editing/actuals and have an explanatory message, not a misleading current-balance forecast.
- APIs: GET `/api/plan?month=YYYY-MM&language=ru` -> `{month,is_current_month,totals:[{currency,balance,expected_income,expected_expense,closing_balance}],categories:[{id,name,icon,budget,currency,forecast_mode,payment_status,enabled,actual,remaining}],items:[{id,name,month,direction,amount,currency,payment_status,transaction_ids,actual,remaining}]}`. All new monetary outputs are exact decimal strings. `closing_balance` is null outside current month.
- POST `/api/plan/items`: `{name,month,direction,amount,currency,payment_status?,transaction_ids?}`. PATCH `/api/plan/items/{id}` accepts these except month; DELETE removes an item and releases links. All writes return item or `{id,deleted:true}`; GET is authoritative after a write. Category settings use existing PATCH `/api/categories/{id}`.
- Preserve existing category write response shape for existing clients/tests; GET categories can expose new settings. UI reads accurate forecast numbers from GET plan.

## Implementation
Backend owns models, additive tenant upgrades, validated services, thin routers, and API tests. Frontend adds Plan navigation, forecast summary on Overview, an editable plan table, category mode/currency/status controls and one-off CRUD/link UI; follow existing RU/EN/ET translations and responsive styling. No new dependencies or changes to external spreadsheets.

## Validation and structure
Python services in app/services, HTTP adapters in app/routers, browser modules in app/static. Keep imports acyclic, money as Decimal in backend, escaped labels in HTML. Tests in tests use unittest and Vitest; browser regression uses synthetic fixtures. Verify plan/actual differences, partial/closed payments, split allocations, one-off duplicate-link rejection, multi-currency, month reset, disabled categories, tenant separation and errors.

Commands: `rtk python -m unittest discover -s tests`, `rtk npm test`, `rtk npm run build`, `rtk npm run test:browser`, `rtk graphify update .`.

Always validate ownership and date/currency boundaries. Never modify real bank data, credentials or deploy without a separate request. No external service or new dependency is required.

## Release and rollback
Tenant initialization adds the category columns and plan tables lazily on first
access after restarting the application. Existing budgets and assignments stay
intact. Reverting application code can leave the additive tables/columns in place;
do not drop saved plans during rollback. Build the frontend before restarting so
the asset manifest and files belong to the same build.
