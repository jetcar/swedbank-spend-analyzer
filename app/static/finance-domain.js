// Keep exact decimal amounts through aggregation; currencies are never combined.
export function cents(value) {
  const match = String(value ?? '0').match(/^(-?)(\d+)(?:\.(\d{1,2}))?$/);
  if (!match) return 0n;
  return (match[1] ? -1n : 1n) * (BigInt(match[2]) * 100n + BigInt((match[3] || '').padEnd(2, '0')));
}
export function decimal(value) {
  const absolute = value < 0n ? -value : value;
  return `${value < 0n ? '-' : ''}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`;
}
export function currencyTotals(rows, amount = row => row.amount_exact ?? row.amount) {
  const totals = new Map();
  for (const row of rows) {
    const currency = row.currency || 'XXX';
    totals.set(currency, (totals.get(currency) || 0n) + cents(amount(row)));
  }
  return [...totals].map(([currency, total]) => ({ currency, amount: decimal(total) }));
}
export function filterTransactions(rows, { search = '', account = '', category = '', direction = '' } = {}) {
  const normalizedSearch = search.toLocaleLowerCase();
  return rows.filter(row => (!search || [row.counterparty_name, row.description]
    .filter(Boolean).join(' ').toLocaleLowerCase().includes(normalizedSearch))
    && (!account || row.account_id === Number(account))
    && (!direction || row.direction === direction)
    && (!category || (category === 'none' ? row.category_id == null && !row.splits?.length
      : row.category_id === Number(category) || row.splits?.some(part => part.category_id === Number(category)))));
}
function categoryAllocation(row, amount) {
  const category = row.category_details;
  return {
    key: category ? `${category.icon || ''}|${category.name}` : 'uncategorized',
    name: category?.name || '',
    icon: category?.icon || '',
    amount,
  };
}
function addCategory(target, allocation) {
  const existing = target.get(allocation.key);
  if (existing) existing.amount += allocation.amount;
  else target.set(allocation.key, allocation);
}
export function buildCashflowDays(rows, range, now = new Date()) {
  const length = Math.max(1, Math.min(30, Number(range) || 30));
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const days = Array.from({ length }, (_, index) => {
    const value = new Date(end);
    value.setUTCDate(value.getUTCDate() - (length - index - 1));
    return { date: value.toISOString().slice(0, 10), income: 0, expense: 0, incomeCategories: [], expenseCategories: [] };
  });
  const byDate = new Map(days.map(day => [day.date, day]));
  const categoryTotals = new Map(days.map(day => [day.date, { income: new Map(), expense: new Map() }]));
  for (const row of rows) {
    const day = byDate.get(row.date?.slice(0, 10));
    if (day && ['CRDT', 'DBIT'].includes(row.direction)) {
      const direction = row.direction === 'CRDT' ? 'income' : 'expense';
      const amount = Math.abs(Number(row.amount_exact ?? row.amount) || 0);
      day[direction] += amount;
      const totals = categoryTotals.get(day.date)[direction];
      if (row.splits?.length) {
        for (const split of row.splits) {
          const category = split.category;
          addCategory(totals, {
            key: category ? `${category.icon || ''}|${category.name}` : 'uncategorized',
            name: category?.name || '', icon: category?.icon || '',
            amount: Math.abs(Number(split.amount) || 0),
          });
        }
      } else addCategory(totals, categoryAllocation(row, amount));
    }
  }
  for (const day of days) {
    const totals = categoryTotals.get(day.date);
    day.incomeCategories = [...totals.income.values()].sort((a, b) => b.amount - a.amount);
    day.expenseCategories = [...totals.expense.values()].sort((a, b) => b.amount - a.amount);
  }
  return days;
}
export async function loadMonthTransactions(fetchJson, month) {
  return loadTransactionPages(fetchJson, `month=${encodeURIComponent(month)}`);
}
export async function loadRecentTransactions(fetchJson, days = 30) {
  return loadTransactionPages(fetchJson, `days=${encodeURIComponent(days)}`);
}
async function loadTransactionPages(fetchJson, query) {
  const rows = [];
  let before = '';
  for (;;) {
    const page = await fetchJson(`/api/transactions?limit=1000&page_by_id=true&${query}${before}`);
    rows.push(...page);
    if (page.length < 1000) break;
    const lastId = page.at(-1).id;
    if (before === `&before_id=${lastId}`) throw new Error('Transaction pagination did not advance');
    before = `&before_id=${lastId}`;
  }
  return rows.sort((a, b) => (b.date || '').localeCompare(a.date || '') || b.id - a.id);
}
