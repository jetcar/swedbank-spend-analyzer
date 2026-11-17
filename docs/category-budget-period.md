# Spec: Recurring category budget periods

## Objective

Let each monthly category store its budget as a monthly, weekly, or daily amount. Existing categories remain monthly. The effective monthly target is:

- monthly: entered amount;
- weekly: entered amount × calendar days in the selected month ÷ 7;
- daily: entered amount × calendar days in the selected month.

Calculated targets are rounded to cents. Negative amounts remain income targets. Copying categories into a new month preserves the entered amount and period, then recalculates the target using that month's day count.

## Tech stack and structure

- Python, FastAPI, SQLAlchemy, PostgreSQL: `app/models.py`, `app/schema.py`, `app/schemas.py`, `app/services/`.
- Browser JavaScript: `app/static/`.
- Python and Vitest/Playwright tests: `tests/`.

## Commands

- Backend tests: `python -m unittest`
- Frontend tests: `npm test`
- Browser test: `npm run test:browser`
- Frontend build: `npm run build`

## Data and API contract

`Category.budget` stores the entered amount. `Category.budget_period` stores `month`, `week`, or `day`, defaulting to `month`. Category and plan responses expose both the entered `budget` and calculated `monthly_budget`.

For `day` and `week`, responses also expose an `available_budget` accrued through the current Europe/Tallinn calendar day. In the current month it equals the source amount multiplied by elapsed calendar days (and divided by 7 for weekly budgets). Past months accrue the full monthly target; future months accrue zero. Monthly budgets always use their full target. Spending and remaining status compare against `available_budget`, while `monthly_budget` remains the full-month projection.

The category card shows a dynamic projected month total for daily and weekly expense budgets: `monthly_budget + max(spent - available_budget, 0)`. Future daily/weekly allowances therefore remain on top of any current overspending. The projection falls when later savings absorb that overspending and stays level when new spending matches the newly accrued allowance.

The overall plan forecast uses the same dynamic target. Because transactions have already reduced the account balance, the remaining forecast is `projected month total - actual`, equivalent to `monthly_budget - min(actual, available_budget)`. This keeps all future daily/weekly allowances in the closing-balance forecast without counting current spending twice.

## Testing strategy

- Unit-test monthly conversion for 28-, 29-, 30-, and 31-day months, including weekly cent rounding and negative income targets.
- Service tests cover persistence and copying into a month with a different day count.
- Frontend and browser tests cover selecting a period and displaying the calculated monthly target.

## Boundaries

- Always preserve existing monthly behavior and currency/sign semantics.
- Never store a derived monthly value as the source amount.
- Never change transaction categorization or historical spending totals.

## Success criteria

- The category editor offers month, week, and day.
- The monthly target shown throughout the dashboard and plan uses the selected month's calendar-day count.
- Daily and weekly categories show remaining or overspending against the amount accrued through today.
- Past months use the full target and future months accrue zero; monthly categories retain existing behavior.
- The entered recurring amount and period survive reloads and month copying.
- Existing categories behave exactly as monthly budgets.
