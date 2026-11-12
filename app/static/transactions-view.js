import { t as tr, formatMoney as money, formatDate as date, transactionCount } from './i18n.js';
import { escapeHtml, transactionDateLabel } from './expense-domain.js';
import { filterTransactions } from './finance-domain.js';
import { icon } from './icons.js';
import { $, empty, maskAccount, renderTransactionDate, runAction, transactionTitle, transactionDescription, bindDialogDismiss } from './dashboard-ui.js';

export function createTransactionsView({ state, fetchJson, reload, openSplit }) {
  let byDay = false, visibleLimit = 100;

  function categoryFor(transaction) { return state.categories.find(category => category.id === transaction.category_id); }

  function categoryLabel(transaction) {
    if (transaction.splits?.length) return transaction.splits.map(part => state.categories.find(category => category.id === part.category_id)?.name || tr('Без категории')).join(' · ');
    return categoryFor(transaction)?.name || tr('Без категории');
  }

  function transactionRow(transaction) {
    const credit = transaction.direction === 'CRDT';
    const description = transactionDescription(transaction);
    return `<button class="transaction-row" data-transaction="${escapeHtml(transaction.id)}"><span class="transaction-icon">${escapeHtml(categoryFor(transaction)?.icon || (credit ? '↙' : '↗'))}</span><span class="transaction-main"><span class="transaction-title">${escapeHtml(transactionTitle(transaction))}</span><span class="transaction-meta">${description ? `${escapeHtml(description)} · ` : ''}${renderTransactionDate(transaction)} · ${escapeHtml(categoryLabel(transaction))}${transaction.excluded_from_statistics ? ` · <span class="statistics-excluded">${tr('Не учитывается в статистике')}</span>` : ''}</span></span><span class="transaction-amount ${credit ? 'credit' : 'debit'}">${credit ? '+' : '−'}${escapeHtml(money(transaction.amount_exact ?? transaction.amount, transaction.currency || 'XXX'))}</span></button>`;
  }

  function bindTransactionRows(root) {
    root.querySelectorAll('[data-transaction]').forEach(button => button.onclick = () => openTransaction(Number(button.dataset.transaction)));
  }

  function renderFilters() {
    const account = $('#account-filter').value, category = $('#category-filter').value;
    $('#account-filter').innerHTML = `<option value="">${tr('Все счета')}</option>` + state.accounts.filter(row => row.monitored).map(row => `<option value="${row.id}">${escapeHtml(row.name || maskAccount(row))}</option>`).join('');
    $('#category-filter').innerHTML = `<option value="">${tr('Все категории')}</option><option value="none">${tr('Без категории')}</option>` + state.categories.map(row => `<option value="${row.id}">${escapeHtml(row.icon)} ${escapeHtml(row.name)}</option>`).join('');
    if ([...$('#account-filter').options].some(option => option.value === account)) $('#account-filter').value = account;
    if ([...$('#category-filter').options].some(option => option.value === category)) $('#category-filter').value = category;
  }

  function renderTransactions() {
    const rows = filterTransactions(state.transactions,{search:$('#transaction-search').value.trim(),account:$('#account-filter').value,category:$('#category-filter').value,direction:$('#direction-filter').value});
    $('#transaction-count').textContent = transactionCount(rows.length);
    let lastDay;
    $('#transactions').innerHTML = rows.slice(0,visibleLimit).map(row => {
      const day = row.date?.slice(0,10) || '';
      const heading = byDay && day !== lastDay ? `<h3 class="day-heading">${escapeHtml(day ? date(day) : tr('Дата недоступна'))}</h3>` : '';
      lastDay = day;
      return heading + transactionRow(row);
    }).join('') || empty(tr('Нет операций за этот месяц'));
    $('#load-more').hidden = rows.length <= visibleLimit;
    bindTransactionRows($('#transactions'));
  }

  function openTransaction(id) {
    const transaction = state.transactions.find(row => row.id === id);
    if (!transaction) return;
    const account = state.accounts.find(row => row.id === transaction.account_id);
    const available = state.categories.filter(row => row.enabled || row.id === transaction.category_id);
    $('#transaction-detail').innerHTML = `<div class="detail-amount ${transaction.direction === 'CRDT' ? 'credit' : 'debit'}">${transaction.direction === 'CRDT' ? '+' : '−'}${escapeHtml(money(transaction.amount_exact ?? transaction.amount,transaction.currency || 'XXX'))}</div><p class="detail-description">${escapeHtml(transaction.description || transaction.counterparty_name || tr('Без описания'))}</p><div class="detail-data">${transaction.counterparty_name ? `<p><span class="muted">${tr('Контрагент')}</span><b>${escapeHtml(transaction.counterparty_name)}</b></p>` : ''}<p><span class="muted">${tr(transactionDateLabel(transaction.date_kind))}</span><b>${escapeHtml(date(transaction.date))}</b></p><p><span class="muted">${tr('Счета')}</span><b>${escapeHtml(account?.name || '')} ${escapeHtml(account ? maskAccount(account) : '')}</b></p><p><span class="muted">${tr('Категории')}</span><b>${escapeHtml(categoryLabel(transaction))}</b></p>${(transaction.splits || []).map(part => `<p><span>${escapeHtml(state.categories.find(row => row.id === part.category_id)?.name || tr('Без категории'))}</span><b>${escapeHtml(money(part.amount,transaction.currency || 'XXX'))}</b></p>`).join('')}</div><form id="assign-form" class="detail-actions"><label class="field"><span>${tr('Категории')}</span><select name="category"><option value="">${tr('Без категории')}</option>${available.map(row => `<option value="${row.id}" ${row.id === transaction.category_id ? 'selected' : ''}>${escapeHtml(row.icon)} ${escapeHtml(row.name)}</option>`).join('')}</select></label>${transaction.splits?.length ? `<p class="muted form-hint">${tr('Выбор категории заменит разбиение операции')}</p>` : ''}<button type="submit">${tr('Сохранить')}</button><button type="button" class="secondary" id="detail-split" ${state.categories.filter(row => row.enabled).length < 2 ? 'disabled' : ''}>${icon('split')}${tr('Разделить трату')}</button></form>`;
    $('#assign-form').insertAdjacentHTML('beforebegin', `<div class="statistics-setting"><label class="switch"><input type="checkbox" id="detail-statistics" aria-describedby="statistics-hint" ${transaction.excluded_from_statistics ? '' : 'checked'}><span>${tr('Учитывать в статистике')}</span></label><p class="muted form-hint" id="statistics-hint">${tr('Отключите для переводов между своими счетами и возвратов. Баланс счёта не изменится.')}</p></div>`);
    $('#detail-statistics').onchange = event => runAction(event.currentTarget, async () => {
      const control = event.target;
      const excluded = !control.checked;
      try {
        await fetchJson(`/api/transactions/${id}/statistics`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({excluded_from_statistics:excluded})});
      } catch (error) {
        control.checked = !transaction.excluded_from_statistics;
        throw error;
      }
      transaction.excluded_from_statistics = excluded;
      await reload();
    });
    $('#assign-form').onsubmit = event => runAction(event.submitter, async () => {
      await fetchJson(`/api/expenses/${id}/category`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({category_id:event.target.elements.category.value ? Number(event.target.elements.category.value) : null})});
      $('#transaction-dialog').close(); await reload();
    },event);
    $('#detail-split').onclick = () => openSplit(id);
    $('#transaction-dialog').showModal();
  }

  function bind() {
    bindDialogDismiss($('#transaction-dialog'));
    for (const id of ['transaction-search', 'account-filter', 'category-filter', 'direction-filter']) $(`#${id}`).addEventListener('input', () => { visibleLimit = 100; renderTransactions(); });
    $('#load-more').onclick = () => { visibleLimit += 100; renderTransactions(); };
    for (const mode of ['transactions', 'days']) $(`#tab-${mode}`).onclick = () => {
      byDay = mode === 'days';
      for (const name of ['transactions', 'days']) { $(`#tab-${name}`).classList.toggle('active', mode === name); $(`#tab-${name}`).setAttribute('aria-pressed', String(mode === name)); }
      renderTransactions();
    };
    $('[data-close-detail]').onclick = () => $('#transaction-dialog').close();
  }
  return { bind, render() { renderFilters(); renderTransactions(); }, resetLimit() { visibleLimit = 100; }, renderRow: transactionRow, bindRows: bindTransactionRows };
}
