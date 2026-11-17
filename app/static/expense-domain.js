export const formatMoney = (amount, currency = 'EUR') => amount == null ? '—' : `${Number(amount).toFixed(2)} ${currency}`;

export const formatDate = value => value ? new Date(value).toLocaleDateString('ru-RU') : '—';

export const transactionDateLabel = kind => ({
  booking: 'Дата бронирования',
  transaction: 'Дата операции',
  value: 'Дата валютирования',
}[kind] || 'Дата недоступна');

export const buildMonthQuery = (endpoint, month) => `${endpoint}${endpoint.includes('?') ? '&' : '?'}month=${encodeURIComponent(month)}`;

export const buildCategoryQuery = (endpoint, month, language) => `${buildMonthQuery(endpoint, month)}&language=${encodeURIComponent(language)}`;

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

export function categoryBudgetState(category) {
  const cents = value => {
    const normalized = String(value ?? 0).trim();
    const match = normalized.match(/^(-?)(\d+)(?:\.(\d+))?$/);
    if (!match) return 0n;
    const fraction = `${match[3] || ''}00`.slice(0, 2);
    return BigInt(`${match[1]}${match[2]}${fraction}`);
  };
  const spent = cents(category.spent);
  const budget = cents(category.available_budget ?? category.monthly_budget ?? category.budget);
  const monthlyBudget = cents(category.monthly_budget ?? category.budget);
  const income = monthlyBudget < 0n;
  const actual = income ? cents(category.earned) : spent;
  const target = budget < 0n ? -budget : budget;
  const limited = target > 0n || ['day', 'week'].includes(category.budget_period);
  const recurring = ['day', 'week'].includes(category.budget_period);
  const overrun = recurring && !income && spent > target ? spent - target : 0n;
  const projectedBudget = (monthlyBudget < 0n ? -monthlyBudget : monthlyBudget) + overrun;
  const percent = target > 0n ? Number((actual * 1000n + target / 2n) / target) / 10 : null;
  const amount = value => {
    const sign = value < 0n ? '-' : '';
    const absolute = value < 0n ? -value : value;
    return `${sign}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`;
  };
  return {
    spent: amount(spent),
    budget: amount(budget),
    projectedBudget: amount(projectedBudget),
    remaining: limited ? amount(target - actual) : null,
    percent,
    status: income ? (actual >= target ? 'income-reached' : 'income-pending')
      : budget === 0n ? (limited ? (spent > 0n ? 'over' : 'within') : 'unlimited')
        : spent > budget ? 'over' : spent === budget ? 'reached' : spent * 10n >= budget * 8n ? 'near' : 'within',
  };
}

export const categoryIcons = ['🍽️', '🚗', '🏠', '📱', '🧾', '🛒', '☕', '🍔', '🥦', '🚌', '⛽', '✈️', '🏥', '💊', '🎓', '💡', '🔧', '🧹', '🎁', '🎮', '🎬', '🎵', '🐾', '👕', '💻', '📚', '💰', '🏦', '🌿', '🏋️'];

export function buildCategoryTabs(categories, selectedCategory) {
  return [
    { id: null, name: 'Общий список', icon: '📋' },
    ...categories.filter(category => category.enabled).map(category => ({
      id: category.id,
      name: category.name,
      icon: category.icon,
      active: category.id === selectedCategory,
    })),
  ];
}

export function filterAndSortExpenses(expenses, selectedCategory, filter = '') {
  const normalizedFilter = filter.toLocaleLowerCase();
  return expenses
    .filter(expense => expense.category_id === selectedCategory && [expense.counterparty_name, expense.description]
      .filter(Boolean).join(' ').toLocaleLowerCase().includes(normalizedFilter))
    .sort((left, right) => {
      if (!left.date && right.date) return -1;
      if (left.date && !right.date) return 1;
      return (right.date || '').localeCompare(left.date || '');
    });
}

export function calculateSplitTotal(rows) {
  return Math.round(rows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0) * 100) / 100;
}

export function rebalanceSplitRows(rows, changedIndex, amount, expectedAmount) {
  const updated = rows.map(row => ({ ...row }));
  if (!updated[changedIndex]) return { rows: updated, balancedIndex: null };
  updated[changedIndex].amount = Math.max(0, Math.round((Number(amount) || 0) * 100)) / 100;
  if (updated.length < 2) return { rows: updated, balancedIndex: null };
  const balancedIndex = (changedIndex + 1) % updated.length;
  const fixedCents = updated.reduce((sum, row, index) => index === balancedIndex ? sum : sum + Math.round((Number(row.amount) || 0) * 100), 0);
  updated[balancedIndex].amount = Math.max(0, Math.round((Number(expectedAmount) || 0) * 100) - fixedCents) / 100;
  return { rows: updated, balancedIndex };
}

export function isValidSplit(rows, expectedAmount) {
  return rows.length >= 2 && rows.every(row => Number(row.amount) > 0)
    && calculateSplitTotal(rows) === Math.round(Number(expectedAmount || 0) * 100) / 100;
}

export function buildDailyExpenseIndex(days) {
  const partsById = {};
  const expensesById = {};
  for (const day of days) {
    for (const category of day.categories || []) {
      for (const transaction of category.transactions || []) {
        const part = { ...transaction, category_id: category.id };
        partsById[transaction.id] = [...(partsById[transaction.id] || []), part];
      }
    }
  }
  for (const [id, parts] of Object.entries(partsById)) {
    expensesById[id] = {
      ...parts[0],
      amount: parts.reduce((sum, part) => sum + Number(part.amount || 0), 0),
      category_id: parts.length > 1 ? null : parts[0].category_id,
    };
  }
  return { partsById, expensesById };
}

export function getMovePlan({ parts, expense, sourceCategoryId }) {
  const normalizedParts = parts?.length ? parts : [{
    category_id: sourceCategoryId == null || sourceCategoryId === '' ? null : Number(sourceCategoryId),
    amount: Number(expense?.amount || 0),
    split: false,
  }];
  const isSplit = normalizedParts.length > 1 || normalizedParts.some(part => part.split);
  const partsToMove = isSplit ? normalizedParts : [normalizedParts[0]];
  return {
    isSplit,
    partsToMove,
    movedAmount: partsToMove.reduce((sum, part) => sum + Number(part.amount || 0), 0),
  };
}

export function decrementCategoryTotals({ count, total, removedCount, removedAmount }) {
  return {
    count: Math.max(0, Number(count || 0) - removedCount),
    total: Math.max(0, Number(total || 0) - Number(removedAmount || 0)),
  };
}

export async function refreshCategoryViews({
  loadCategories,
  loadExpenses,
  loadDays,
  renderCategoryManager,
  renderCategories,
  renderExpenses,
}) {
  await Promise.all([loadCategories(), loadExpenses(), loadDays()]);
  renderCategoryManager();
  renderCategories();
  renderExpenses();
}
