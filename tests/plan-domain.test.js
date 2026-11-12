import { expect, test } from 'vitest';
import { formatPlanMoney, eligiblePlanTransactions, planTransactionCurrency } from '../app/static/plan-domain.js';
import { renderForecast } from '../app/static/plan-view.js';

test('forecast formatting keeps cents beyond Number precision in each locale', () => {
  for (const locale of ['en-GB', 'ru-RU', 'et-EE']) {
    const formatted = formatPlanMoney('9007199254740993.17', 'EUR', locale);
    expect(formatted.replace(/[^0-9]/g, '')).toBe('900719925474099317');
    expect(formatPlanMoney('-0.01', 'EUR', locale)).toContain('−');
  }
  expect(formatPlanMoney(null)).toBe('—');
});

test('link candidates respect tracked account, month, direction, currency, exclusion and reservation', () => {
  const item = { id: 1, month: '2026-11', direction: 'DBIT', currency: 'EUR' };
  const transaction = { id: 1, account_id: 3, date: '2026-11-03', direction: 'DBIT', currency: 'EUR', amount_exact: '12.50' };
  const transactions = [transaction, ...[
    { account_id: 4 }, { date: '2026-10-03' }, { direction: 'CRDT' }, { currency: 'USD' },
    { excluded_from_statistics: true }, { amount_exact: '0.00' }, {},
  ].map((change, index) => ({ ...transaction, id: index + 2, ...change }))];
  const candidates = eligiblePlanTransactions({ transactions, accounts: [{ id: 3, monitored: true }, { id: 4, monitored: false }],
    items: [{ id: 1, transaction_ids: [1] }, { id: 2, transaction_ids: [8] }] }, item);
  expect(candidates.map(row => row.id)).toEqual([1]);
});

test('forecast hides current balances for other months and preserves large current totals', () => {
  const totals = [{ currency: 'EUR', balance: '9007199254740993.17', closing_balance: '9007199254740993.17', expected_income: '0', expected_expense: '0' }];
  expect(renderForecast({ is_current_month: false, totals })).not.toContain('forecast-value');
  const current = renderForecast({ is_current_month: true, totals });
  expect(current.replace(/[^0-9]/g, '')).toContain('900719925474099317');
  expect(renderForecast({ is_current_month: true, totals: [{ ...totals[0], closing_balance: '-0.01' }] })).toContain('forecast-value debit');
});

test('null transaction currency inherits account currency for linking and displaying amounts', () => {
  const accounts = [{ id: 1, monitored: true, currency: 'USD' }];
  const transaction = { id: 2, account_id: 1, currency: null, direction: 'CRDT', amount_exact: '20.00', date: '2026-11-01' };
  expect(planTransactionCurrency(transaction, accounts)).toBe('USD');
  expect(planTransactionCurrency({ ...transaction, currency: 'EUR' }, accounts)).toBe('EUR');
  expect(eligiblePlanTransactions({ transactions: [transaction], accounts, items: [] }, { month: '2026-11', direction: 'CRDT', currency: 'USD' })).toEqual([transaction]);
  expect(formatPlanMoney(transaction.amount_exact, planTransactionCurrency(transaction, accounts), 'en-GB')).toContain('USD');
});

test('authoritative planning month wins over transaction display date, including null month', () => {
  const accounts = [{ id: 1, monitored: true, currency: 'EUR' }];
  const base = { account_id: 1, currency: 'EUR', direction: 'DBIT', amount_exact: '20.00' };
  const transactions = [
    { ...base, id: 1, planning_month: '2026-11', date: '2026-12-01T00:30:00+02:00' },
    { ...base, id: 2, planning_month: '2026-10', date: '2026-11-01T00:30:00+02:00' },
    { ...base, id: 3, planning_month: null, date: '2026-11-05' },
    { ...base, id: 4, date: '2026-11-05' },
  ];
  expect(eligiblePlanTransactions({ transactions, accounts, items: [] }, { month: '2026-11', direction: 'DBIT', currency: 'EUR' }).map(row => row.id)).toEqual([1, 4]);
});
