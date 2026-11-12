# Account balances

## Behavior

Each account has a current calculated balance, independent of the selected month
and monitoring checkbox. Initially it is the sum of all stored credits minus all
stored debits. An empty account starts at zero. Users may enter a new current
balance, including zero or a negative amount, at any time.

Saving stores a per-account adjustment: requested balance minus the transaction
total at save time. Later inserts, updates, and deletions change the calculated
balance by their net effect. Repeated synchronization of the same transactions
does not change it. A newly imported historical transaction counts when imported.
Changing categories or splitting an expense must never count a transaction twice.
Matched pending card authorizations and their later booked records are one
logical transaction: the earliest date and original annotations stay on the
surviving row, while the booked status and stable bank identifier take precedence.
The `PDNG` to `BOOK` transition does not add a second balance contribution. If
matching variants were already stored as separate rows, collapsing them removes
the duplicate contribution and restores the single-transaction balance.
An identical pending card record repeated with a later provider `value_date` is
also the same logical transaction and keeps its first observed date and balance
contribution.

Amounts use decimal arithmetic. DBIT subtracts the absolute amount; CRDT adds it.
Missing amounts and unrecognized directions contribute zero. Transactions with an
explicit currency different from the account currency are excluded; a missing
currency is treated as the account currency. With no account currency, all its
transactions are included, matching the existing single-currency account model.

## Storage and API

- `accounts.balance_adjustment NUMERIC(24,2) NOT NULL DEFAULT 0` is added through
  the existing idempotent tenant-schema upgrade. Existing rows start at zero.
- `GET /api/accounts` includes `balance` as an exact two-decimal string.
- `PATCH /api/accounts/{id}/balance` accepts `{ "balance": "123.45" }` and returns
  `{ "id": 1, "balance": "123.45" }`. Values must be finite, with at most two
  fractional digits and eighteen digits total. Numeric JSON values are also accepted.
- `PATCH /api/accounts/{id}/currency` accepts an explicit three-letter uppercase
  currency code such as `{ "currency": "EUR" }`. Assigning a currency determines
  which stored transactions contribute to the calculated balance; `XXX` remains an
  unassigned bank value and does not implicitly match transactions in other currencies.
  A user-assigned currency is retained when the same bank account is reconnected.
- Reads and writes use the existing tenant session and owner checks; missing or
  inaccessible account IDs return 404. Concurrent manual edits serialize on the
  account row. Existing change notifications refresh other open views.
- Reconnecting a bank account retains its adjustment because the existing account
  row is reused. Raw bank transactions are never modified by a balance edit.

The adjustment is preferable to storing a cached running balance: it derives the
latest value directly from the transaction ledger, including corrections, without
adding update hooks or making every importer maintain a second total.

## Interface and verification

Account cards show the balance and currency with an Edit action, explicit Save
and Cancel, validation, and an inline save error. An open editor keeps its draft
during automatic refresh. All labels support Russian, English, and Estonian.

Implementation order: storage/API and PostgreSQL integration tests; then account
cards and browser verification. Source files are `app/models.py`, `app/main.py`,
and `app/static/`; integration tests are in `tests/test_account_balance.py`.

Run `rtk npm test` and, in the app container with this workspace available,
`python -m unittest discover -s /tmp/account-balance-tests -p 'test_account_balance.py'`.
Verify credits/debits, more than 1000 operations, manual resets, subsequent changes,
negative/zero/invalid values, tenant isolation, and upgrade of an existing table.
Browser checks cover edit/save/cancel/error, draft preservation, and phone layout.

Rollback: the previous application can run with the extra column present; retain
it to preserve saved adjustments. Removing the column would discard user edits.
