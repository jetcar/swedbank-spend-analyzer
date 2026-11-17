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
- The entered recurring amount and period survive reloads and month copying.
- Existing categories behave exactly as monthly budgets.
