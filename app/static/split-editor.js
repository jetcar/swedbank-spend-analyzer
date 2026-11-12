import { t as tr, formatMoney as money } from './i18n.js';
import { calculateSplitTotal, escapeHtml, isValidSplit, rebalanceSplitRows } from './expense-domain.js';
import { $, runAction, setStatus, transactionTitle, transactionDescription, bindDialogDismiss } from './dashboard-ui.js';

export function createSplitEditor({ state, fetchJson, reload }) {
  let splitExpense = null;
  let splitRows = [];

  function renderSplitRows() {
    const available = state.categories.filter(category => category.enabled || splitRows.some(row => row.category_id === category.id));
    document.querySelector('#split-rows').innerHTML = splitRows.map((row, index) => `
      <div class="split-row">
        <select aria-label="${tr('Категории')} ${index+1}" data-split-category="${index}">${available.map(category => `<option value="${escapeHtml(category.id)}" ${category.id === row.category_id ? 'selected' : ''}>${escapeHtml(category.icon)} ${escapeHtml(category.id == null ? tr('Без категории') : category.name)}</option>`).join('')}</select>
        <input type="number" aria-label="${tr('Сумма')} ${index+1}" min="0.01" step="0.01" data-split-amount="${index}" value="${escapeHtml(row.amount)}">
        ${splitRows.length > 2 ? `<button class="icon-button" aria-label="${tr('Удалить часть')}" data-split-remove="${index}">✕</button>` : ''}
      </div>`).join('');
    document.querySelectorAll('[data-split-category]').forEach(input => input.onchange = () => { splitRows[Number(input.dataset.splitCategory)].category_id = Number(input.value); });
    document.querySelectorAll('[data-split-amount]').forEach(input => input.oninput = () => {
      const changedIndex = Number(input.dataset.splitAmount);
      const result = rebalanceSplitRows(splitRows, changedIndex, input.value, splitExpense?.amount);
      splitRows = result.rows;
      if (result.balancedIndex !== null) {
        const balancedInput = document.querySelector(`[data-split-amount="${result.balancedIndex}"]`);
        if (balancedInput) balancedInput.value = Number(splitRows[result.balancedIndex].amount).toFixed(2);
      }
      renderSplitTotal();
    });
    document.querySelectorAll('[data-split-remove]').forEach(button => button.onclick = () => { splitRows.splice(Number(button.dataset.splitRemove), 1); renderSplitRows(); renderSplitTotal(); });
    renderSplitTotal();
  }

  function renderSplitTotal() {
    const total = calculateSplitTotal(splitRows);
    const expected = Number(splitExpense?.amount || 0);
    const unique = new Set(splitRows.map(row => row.category_id)).size === splitRows.length;
    const matches = isValidSplit(splitRows, expected) && unique;
    document.querySelector('#split-save').disabled = !matches;
    document.querySelector('#split-total').textContent = `${tr('Распределено: {total} из {expected}', { total: money(total, splitExpense?.currency), expected: money(expected, splitExpense?.currency) })}${matches ? '' : ` · ${unique ? tr('сумма не совпадает') : tr('Выберите разные категории')}`}`;
    document.querySelector('#split-total').className = `expense-meta ${matches ? '' : 'debit'}`;
  }

  function openSplitModal(expenseId) {
    splitExpense = state.transactions.find(expense => expense.id === expenseId);
    if (!splitExpense) return;
    if (state.categories.filter(category => category.enabled).length < 2) { setStatus(() => tr('Для разделения нужны две активные категории')); return; }
    const available = state.categories.filter(category => category.enabled);
    const existing = splitExpense.splits || [];
    splitRows = existing.length >= 2 ? existing.map(row => ({ ...row })) : [
      { category_id: available[0]?.id, amount: Math.round(Number(splitExpense.amount) / 2 * 100) / 100 },
      { category_id: available[1]?.id || available[0]?.id, amount: Math.round((Number(splitExpense.amount) - Math.round(Number(splitExpense.amount) / 2 * 100) / 100) * 100) / 100 },
    ];
    const description = transactionDescription(splitExpense);
    document.querySelector('#split-expense-label').textContent = `${transactionTitle(splitExpense)}${description ? ` · ${description}` : ''} · ${money(splitExpense.amount, splitExpense.currency)}`;
    renderSplitRows();
    document.querySelector('#transaction-dialog').close();
    document.querySelector('#split-modal').showModal();
  }

  function closeSplitModal() { document.querySelector('#split-modal').close(); splitExpense = null; }

  async function saveSplit() {
    if (!splitExpense) return;
    const total = calculateSplitTotal(splitRows);
    if (!isValidSplit(splitRows, splitExpense.amount) || new Set(splitRows.map(row => row.category_id)).size !== splitRows.length) {
      document.querySelector('#split-total').textContent = `${tr('Распределено: {total} из {expected}', { total: money(total), expected: money(splitExpense.amount, splitExpense.currency) })} · ${tr('сумма не совпадает')}`;
      document.querySelector('#split-total').className = 'expense-meta debit';
      return;
    }
    await fetchJson(`/api/expenses/${splitExpense.id}/splits`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ splits: splitRows.map(row => ({ category_id: Number(row.category_id), amount: Number(row.amount) })) }) });
    closeSplitModal(); await reload();
  }

  function bind() {
    bindDialogDismiss($('#split-modal'));
    $('#split-close').onclick = closeSplitModal; $('#split-cancel').onclick = closeSplitModal;
    $('#split-modal').addEventListener('close', () => { splitExpense = null; });
    $('#split-add').onclick = () => {
      const available = state.categories.filter(row => row.enabled);
      splitRows.push({ category_id: available.find(row => !splitRows.some(part => part.category_id === row.id))?.id || available[0]?.id, amount: 0 });
      renderSplitRows();
    };
    $('#split-save').onclick = event => runAction(event.currentTarget, saveSplit);
  }
  return { bind, open: openSplitModal };
}
