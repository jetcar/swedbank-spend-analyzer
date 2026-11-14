import { buildCategoryQuery } from './expense-domain.js';
import { loadMonthTransactions } from './finance-domain.js';

export function createDashboardState(today = new Date()) {
  return {
    accounts: [], categories: [], transactions: [], chartTransactions: [], plan: null, syncStatus: { last_success_at: null },
    selectedMonth: `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`,
  };
}

export function createDashboardLoader({ state, fetchJson, getLanguage, onLoaded }) {
  let version = 0;
  return async function load() {
    const requestedVersion = ++version;
    const month = state.selectedMonth;
    const language = getLanguage();
    let result;
    try {
      // Category loading initializes a new month; let it finish before the plan
      // reads the same month so concurrent requests cannot copy it twice.
      const categories = fetchJson(buildCategoryQuery('/api/categories', month, language));
      result = await Promise.all([
        fetchJson('/api/accounts'),
        categories,
        loadMonthTransactions(fetchJson, month),
        fetchJson('/api/transactions?limit=1000&days=30'),
        fetchJson('/api/sync'),
        categories.then(() => fetchJson(buildCategoryQuery('/api/plan', month, language))),
      ]);
    } catch (error) {
      if (requestedVersion !== version) return false;
      throw error;
    }
    if (requestedVersion !== version) return false;
    [state.accounts, state.categories, state.transactions, state.chartTransactions, state.syncStatus, state.plan] = result;
    onLoaded();
    return true;
  };
}
