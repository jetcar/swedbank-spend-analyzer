import { cents } from './finance-domain.js';

// Format integer and fractional parts separately so even very large balances
// never pass through Number (Intl string support differs between browsers).
export function formatPlanMoney(value, currency = 'EUR', locale = 'ru-RU') {
  if (value == null) return '—';
  const amount = cents(value);
  const absolute = amount < 0n ? -amount : amount;
  const options = { style: 'currency', currency: currency || 'XXX', currencyDisplay: 'code', minimumFractionDigits: 2, maximumFractionDigits: 2 };
  const formatter = new Intl.NumberFormat(locale, options);
  const parts = formatter.formatToParts(absolute / 100n);
  const result = parts.map(part => part.type === 'fraction' ? String(absolute % 100n).padStart(2, '0') : part.value).join('');
  return amount < 0n ? `−${result}` : result;
}

export function eligiblePlanTransactions({ transactions, accounts, items }, item) {
  const tracked = new Set(accounts.filter(account => account.monitored).map(account => account.id));
  const reserved = new Set(items.filter(row => row.id !== item.id).flatMap(row => row.transaction_ids));
  return transactions.filter(row => tracked.has(row.account_id) && !row.excluded_from_statistics
    && (Object.hasOwn(row, 'planning_month') ? row.planning_month === item.month : row.date?.startsWith(item.month))
    && row.direction === item.direction && planTransactionCurrency(row, accounts) === item.currency
    && cents(row.amount_exact ?? row.amount) > 0n && !reserved.has(row.id));
}

export function planTransactionCurrency(transaction, accounts) {
  return transaction.currency ?? accounts.find(account => account.id === transaction.account_id)?.currency;
}
