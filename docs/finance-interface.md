# Finance interface

The application has Overview, Transactions, Categories and Accounts views. The
selected month applies to the overview, transaction filters and category budgets.
Balances remain current across all dates. Light and graphite themes persist in
`localStorage` under `finance-theme` and are shared with the Passi sign-in page.
Language selection remains Russian, English or Estonian.

The Sync button shows the date and time of the last successful synchronization
in the selected language and browser's local timezone. `GET /api/sync` returns
`{"last_success_at": "2026-11-09T12:03:00+00:00"}` for the authenticated owner,
or null before the first success. Manual, scheduled and initial connection syncs
persist this timestamp in the existing `control.bank_sync_states` table. Failed
syncs retain the previous success time. Completion sends a change notification
even when no new operations were imported, so open views refresh the timestamp.

Overview charts show daily income and spending, not historical account balances.
Balance and transaction totals are summed in integer cents and grouped by currency;
unknown currencies use `XXX`. No exchange-rate conversion is implied. Category
budgets retain the existing application's EUR convention and aggregation behavior.
A negative category budget is an income target.

Each transaction has an “Include in statistics” switch in its detail dialog.
Turn it off for account transfers or repayments that should not count as earnings
or spending. Excluded transactions remain visible with a label and still affect
account balances. Monthly totals, cash-flow charts, category budgets and the
summary/daily-summary/daily-breakdown APIs omit them, including all split
allocations. Turning the switch back on restores their contribution.

`PATCH /api/transactions/{id}/statistics` accepts
`{"excluded_from_statistics": true}` (or `false` to restore) and returns the ID
and saved flag. The flag is a required JSON boolean; invalid payloads return 422,
and missing or other users' transactions return 404. Transaction and expense
lists expose `excluded_from_statistics` without hiding excluded rows. The flag
defaults to false for existing and new transactions, is preserved during bank
sync, and does not alter categories or split allocations. Existing tenant schemas
receive the column through the usual idempotent schema upgrade.

`GET /api/transactions` adds `account_id`, `category_id`, `splits` and the exact
decimal string `amount_exact`. Existing `amount` and default date ordering remain.
The UI requests `page_by_id=true`, then passes the final ID as `before_id` until
all pages of the month are loaded. It sorts by the bank date before rendering.
This keeps totals independent of the 1,000-row response limit and prevents new
inserts from shifting subsequent pages. Concurrent edits/deletions can be reflected
on the next live refresh; this is not a database snapshot. Only 100 filtered rows
are rendered initially; Show more renders the next 100.

## Verification

`npm run build` bundles the dashboard and sign-in resources into `frontend-dist/`.
JS entry points, shared chunks, CSS and the favicon use content hashes in their
filenames. HTML references are rewritten from the build manifest; `index.html`
and `login.html` keep stable names. The theme script stays synchronous and external
so it applies before paint without weakening the CSP.

Docker builds these files in a separate Node stage and copies only the output to
the Python runtime. Rebuild the image after changing frontend sources. Running
Python directly uses `frontend-dist/` when present, otherwise uncached source
files for development. The production browser regression runs against the bundle.

HTML is always served with `Cache-Control: no-store`, including `/`, static HTML
paths and the sign-in page. Conditional HTML requests receive fresh responses.
Only successful fingerprinted asset responses use
`Cache-Control: public, max-age=31536000, immutable`; missing files and unversioned
responses are not stored. Both nginx gateways preserve this distinction without
duplicating cache headers or dropping the existing security headers.

Build references: [esbuild entry hashes](https://esbuild.github.io/api/#entry-names),
[shared chunks](https://esbuild.github.io/api/#splitting), and
[nginx response headers](https://nginx.org/en/docs/http/ngx_http_headers_module.html).

```sh
npm ci
npm run build
npm test
npx playwright install --with-deps chromium
npm run test:browser
```

The browser regression starts an ephemeral local static server and intercepts all
API requests with synthetic fixtures. It checks both themes, desktop/tablet/phone
layouts, long bank text, category assignment, splits, income budgets, balance
save failure/retry, translations, empty states and sign-in defaults. It never
contacts a bank or an existing application server. On Windows an installed Edge
can be used with `PLAYWRIGHT_CHANNEL=msedge`.

`tests/test_transaction_statistics.py` checks exclusion/restoration, split totals,
unchanged balances, validation, owner scope, bank reimport and schema upgrades
against a disposable PostgreSQL database. The browser regression also checks the
switch, failed-save recovery, persisted labels, monthly totals and charts.

`tests/test_transaction_pagination.py` runs against an isolated PostgreSQL database
and checks tenant/monitor/month scope, exact amounts, allocation metadata, cursor
pagination and the original default ordering. The existing live-event and network
integration tests require their documented running Docker stack.
