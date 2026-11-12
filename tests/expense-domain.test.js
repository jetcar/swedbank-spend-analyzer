import { describe, expect, test, vi } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import {
  buildCategoryTabs,
  buildCategoryQuery,
  buildDailyExpenseIndex,
  buildMonthQuery,
  calculateSplitTotal,
  categoryIcons,
  categoryBudgetState,
  decrementCategoryTotals,
  escapeHtml,
  filterAndSortExpenses,
  getMovePlan,
  isValidSplit,
  refreshCategoryViews,
  rebalanceSplitRows,
  transactionDateLabel,
} from '../app/static/expense-domain.js';

describe('frontend helpers', () => {
  test('refreshes category status, expense lists, and daily groups as one operation', async () => {
    const callbacks = Object.fromEntries([
      'loadCategories', 'loadExpenses', 'loadDays',
      'renderCategoryManager', 'renderCategories', 'renderExpenses',
    ].map(name => [name, vi.fn().mockResolvedValue()]));

    await refreshCategoryViews(callbacks);

    for (const callback of Object.values(callbacks)) expect(callback).toHaveBeenCalledOnce();
    for (const renderName of ['renderCategoryManager', 'renderCategories', 'renderExpenses']) {
      const renderOrder = callbacks[renderName].mock.invocationCallOrder[0];
      for (const loadName of ['loadCategories', 'loadExpenses', 'loadDays']) {
        expect(callbacks[loadName].mock.invocationCallOrder[0]).toBeLessThan(renderOrder);
      }
    }
  });

  test('every category assignment uses the shared full-view refresh path', () => {
    const source = readFileSync(new URL('../app/static/transactions-view.js', import.meta.url), 'utf8');
    const assignment = source.slice(
      source.indexOf('function openTransaction'),
      source.indexOf('function bind()'),
    );

    expect(assignment).toContain('await reload()');
    expect(assignment).not.toContain('sourceDay');
    expect(assignment.match(/\/api\/expenses\/\$\{id\}\/category/g)).toHaveLength(1);
  });

  test.each([
    [0, 100, 'within', 100, 0],
    [79.99, 100, 'within', 20.01, 80],
    [80, 100, 'near', 20, 80],
    [100, 100, 'reached', 0, 100],
    [125.50, 100, 'over', -25.5, 125.5],
    [42, 0, 'unlimited', null, null],
  ])('budget %s / %s has status %s', (spent, budget, status, remaining, percent) => {
    expect(categoryBudgetState({ spent, budget })).toEqual({ spent, budget, status, remaining, percent });
  });

  test('provides 30 unique category icons', () => {
    expect(categoryIcons).toHaveLength(30);
    expect(new Set(categoryIcons).size).toBe(30);
  });

  test.each([
    [0, 100, 0, 'income-pending'],
    [79.99, 20.01, 80, 'income-pending'],
    [100, 0, 100, 'income-reached'],
    [125.5, -25.5, 125.5, 'income-reached'],
  ])('tracks income %s against a negative budget', (earned, remaining, percent, status) => {
    expect(categoryBudgetState({ budget: -100, spent: 50, earned }))
      .toEqual({ budget: -100, spent: 50, remaining, percent, status });
  });

  test('a negative budget without income starts at zero progress', () => {
    expect(categoryBudgetState({ budget: -100, spent: 50 }))
      .toMatchObject({ remaining: 100, percent: 0, status: 'income-pending' });
  });

  test('builds a month query without losing existing parameters', () => {
    expect(buildMonthQuery('/api/expenses', '2026-11')).toBe('/api/expenses?month=2026-11');
    expect(buildMonthQuery('/api/expenses?limit=10', '2026-11')).toBe('/api/expenses?limit=10&month=2026-11');
  });

  test('includes the selected language when loading categories', () => {
    expect(buildCategoryQuery('/api/categories', '2026-11', 'et'))
      .toBe('/api/categories?month=2026-11&language=et');
  });

  test('labels the source of the displayed transaction date', () => {
    expect(transactionDateLabel('booking')).toBe('Дата бронирования');
    expect(transactionDateLabel('transaction')).toBe('Дата операции');
    expect(transactionDateLabel('value')).toBe('Дата валютирования');
    expect(transactionDateLabel(null)).toBe('Дата недоступна');
  });

  test('uses one transaction date renderer in every transaction list', () => {
    const source = readFileSync(new URL('../app/static/transactions-view.js', import.meta.url), 'utf8');

    const row = source.slice(source.indexOf('function transactionRow'), source.indexOf('function bindTransactionRows'));
    expect(row).toContain('renderTransactionDate(transaction)');
  });

  test('shows category creation inside category management', () => {
    const html = readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
    const managementStart = html.indexOf('<section data-page="categories"');
    const managementEnd = html.indexOf('</section>', managementStart);
    const form = html.indexOf('<form id="category-form"', managementStart);

    expect(managementStart).toBeGreaterThan(-1);
    expect(form).toBeGreaterThan(managementStart);
    expect(form).toBeLessThan(managementEnd);
  });

  test('escapes untrusted bank and category text for HTML templates', () => {
    expect(escapeHtml(`<img src=x onerror="alert(1)">'&`))
      .toBe('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&#39;&amp;');
  });

  test('does not directly interpolate reported API fields into HTML sinks', () => {
    const directory = new URL('../app/static/', import.meta.url);
    const source = readdirSync(directory).filter(name => name.endsWith('.js'))
      .map(name => readFileSync(new URL(name, directory), 'utf8')).join('\n');
    for (const unsafe of [
      'data-day-row="${day.date}"',
      'data-expense-id="${transaction.id}"',
      'data-account-id="${account.id}"',
      'data-assign-day="${item.id}"',
      'title="${item.name}"',
      '${transaction.description ||',
      '${expense.description ||',
      '${account.iban ||',
      '${category.icon}',
    ]) expect(source).not.toContain(unsafe);
  });

  test('shows only enabled categories in tabs', () => {
    expect(buildCategoryTabs([
      { id: 1, name: 'Еда', icon: '🍽️', enabled: true },
      { id: 2, name: 'Скрыто', icon: '🧾', enabled: false },
    ], 1)).toEqual([
      { id: null, name: 'Общий список', icon: '📋' },
      { id: 1, name: 'Еда', icon: '🍽️', active: true },
    ]);
  });

  test('filters by category, description, and counterparty and sorts newest first', () => {
    const expenses = [
      { id: 1, category_id: 4, description: 'старый магазин', date: '2026-01-01' },
      { id: 2, category_id: 4, description: 'Новый магазин', counterparty_name: 'Transport Company', date: '2026-11-05' },
      { id: 3, category_id: 5, description: 'Новый магазин', date: '2026-11-06' },
    ];
    expect(filterAndSortExpenses(expenses, 4, 'МАГАЗИН').map(expense => expense.id)).toEqual([2, 1]);
    expect(filterAndSortExpenses(expenses, 4, 'transport').map(expense => expense.id)).toEqual([2]);
  });

  test('calculates and validates split totals in cents', () => {
    const rows = [{ amount: 10.1 }, { amount: 9.9 }];
    expect(calculateSplitTotal(rows)).toBe(20);
    expect(isValidSplit(rows, 20)).toBe(true);
    expect(isValidSplit([{ amount: 20 }], 20)).toBe(false);
    expect(isValidSplit([{ amount: 20 }, { amount: 0 }], 20)).toBe(false);
    expect(isValidSplit([{ amount: 10.1 }, { amount: 9.89 }], 20)).toBe(false);
  });

  test('automatically puts the remainder into the next split row', () => {
    expect(rebalanceSplitRows([{ amount: 10 }, { amount: 10 }], 0, 7.25, 20)).toEqual({
      rows: [{ amount: 7.25 }, { amount: 12.75 }],
      balancedIndex: 1,
    });
    expect(rebalanceSplitRows([{ amount: 10 }, { amount: 5 }, { amount: 5 }], 1, 8.4, 20)).toEqual({
      rows: [{ amount: 10 }, { amount: 8.4 }, { amount: 1.6 }],
      balancedIndex: 2,
    });
  });
});

describe('daily expense domain', () => {
  test('indexes split parts as one full expense', () => {
    const { partsById, expensesById } = buildDailyExpenseIndex([{ categories: [
      { id: 1, transactions: [{ id: 42, amount: 12.25, split: true }] },
      { id: 5, transactions: [{ id: 42, amount: 7.75, split: true }] },
    ] }]);

    expect(partsById[42]).toHaveLength(2);
    expect(expensesById[42]).toMatchObject({ amount: 20, category_id: null });
  });

  test('keeps an uncategorized expense uncategorized', () => {
    const { expensesById } = buildDailyExpenseIndex([{ categories: [
      { id: null, transactions: [{ id: 7, amount: 4.2, split: false }] },
    ] }]);

    expect(expensesById[7]).toMatchObject({ amount: 4.2, category_id: null });
  });

  test('moving one split part plans the complete expense', () => {
    const plan = getMovePlan({
      parts: [
        { category_id: 1, amount: 12.25, split: true },
        { category_id: 5, amount: 7.75, split: true },
      ],
      expense: { amount: 20 },
      sourceCategoryId: 1,
    });

    expect(plan.isSplit).toBe(true);
    expect(plan.partsToMove).toHaveLength(2);
    expect(plan.movedAmount).toBe(20);
  });

  test('non-split expense keeps its original amount', () => {
    const plan = getMovePlan({ parts: [], expense: { amount: 8.5 }, sourceCategoryId: 5 });

    expect(plan.isSplit).toBe(false);
    expect(plan.movedAmount).toBe(8.5);
    expect(plan.partsToMove[0].category_id).toBe(5);
  });

  test('category totals never become negative', () => {
    expect(decrementCategoryTotals({ count: 1, total: 4, removedCount: 2, removedAmount: 10 }))
      .toEqual({ count: 0, total: 0 });
  });

  test('decrements category totals by all returned split parts', () => {
    expect(decrementCategoryTotals({ count: 3, total: 20, removedCount: 2, removedAmount: 20 }))
      .toEqual({ count: 1, total: 0 });
  });
});
