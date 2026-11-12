import { t as tr, getLocale, formatDate } from './i18n.js';
import { escapeHtml as esc } from './expense-domain.js';
import { cents } from './finance-domain.js';
import { formatPlanMoney, eligiblePlanTransactions, planTransactionCurrency } from './plan-domain.js';
import { $, empty, runAction, bindDialogDismiss, transactionTitle } from './dashboard-ui.js';
import { icon } from './icons.js';

const money = (value, currency) => esc(formatPlanMoney(value, currency, getLocale()));
const statusOptions = [['auto', 'Автоматически'], ['open', 'Ждать остаток'], ['closed', 'Завершено']];
const modeOptions = [['budget', 'Бюджет'], ['payment', 'Платёж']];
const options = (values, selected) => values.map(([value, label]) => `<option value="${value}" ${value === selected ? 'selected' : ''}>${esc(tr(label))}</option>`).join('');
const field = (label, control) => `<label class="field"><span>${esc(tr(label))}</span>${control}</label>`;
const statusSelect = (value, attributes = '') => `<select ${attributes}>${options(statusOptions, value)}</select>`;

export function renderForecast(plan, compact = false) {
  if (!plan) return '';
  if (!plan.is_current_month) return `<p class="muted plan-note">${esc(tr('Прогноз доступен для текущего месяца. Здесь можно редактировать план и смотреть факт.'))}</p>`;
  if (!plan.totals.length) return empty(tr('Добавьте отслеживаемый счёт или план, чтобы увидеть прогноз'));
  return `<div class="forecast-grid ${compact ? 'forecast-compact' : ''}">${plan.totals.map(total => `<article class="forecast-currency">
    <div class="forecast-heading"><span>${esc(tr('На конец месяца'))}</span><span class="badge">${esc(total.currency || 'XXX')}</span></div>
    <strong class="forecast-value ${cents(total.closing_balance) < 0n ? 'debit' : ''}">${money(total.closing_balance, total.currency)}</strong>
    <dl class="forecast-breakdown"><div><dt>${esc(tr('Баланс сейчас'))}</dt><dd>${money(total.balance, total.currency)}</dd></div><div><dt>${esc(tr('Ещё доходы'))}</dt><dd class="credit">+ ${money(total.expected_income, total.currency)}</dd></div><div><dt>${esc(tr('Ещё расходы'))}</dt><dd>− ${money(total.expected_expense, total.currency)}</dd></div></dl>
  </article>`).join('')}</div>`;
}

export function createPlanView({ state, fetchJson, reload }) {
  let editingId = null;
  let editingMonth = null;

  async function write(url, method, body) {
    await fetchJson(url, { method, headers: { 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
    await reload();
  }

  function categoryRow(category) {
    const id = esc(category.id);
    return `<article class="plan-row ${category.enabled ? '' : 'plan-disabled'}" data-plan-category="${id}">
      <div class="plan-row-heading"><strong>${esc(category.icon)} ${esc(category.name)}</strong><span class="badge">${esc(tr(cents(category.budget) < 0n ? 'Доходы' : 'Расходы'))}${category.enabled ? '' : ` · ${esc(tr('Выключена'))}`}</span></div>
      <div class="plan-category-fields">${field('План месяца', `<input type="number" step="0.01" value="${esc(category.budget)}" data-plan-setting="budget" aria-label="${esc(tr('План месяца'))}: ${esc(category.name)}">`)}
      ${field('Валюта', `<input value="${esc(category.currency)}" maxlength="3" minlength="3" pattern="[A-Za-z]{3}" required data-plan-setting="currency" aria-label="${esc(tr('Валюта'))}: ${esc(category.name)}">`)}
      ${field('Тип расчёта', `<select data-plan-setting="forecast_mode" aria-label="${esc(tr('Тип расчёта'))}: ${esc(category.name)}">${options(modeOptions, category.forecast_mode)}</select>`)}
      ${field('Статус платежа', statusSelect(category.payment_status, `data-plan-setting="payment_status" ${category.forecast_mode === 'budget' ? 'disabled' : ''} aria-label="${esc(tr('Статус платежа'))}: ${esc(category.name)}"`))}</div>
      <div class="plan-actuals"><span>${esc(tr('Факт'))}<b>${money(category.actual, category.currency)}</b></span><span>${esc(tr('Ещё ожидается'))}<b>${money(category.remaining, category.currency)}</b></span></div>
    </article>`;
  }

  function itemRow(item) {
    return `<article class="plan-row"><div class="plan-row-heading"><strong>${esc(item.name)}</strong><span class="badge">${esc(tr(item.direction === 'CRDT' ? 'Доходы' : 'Расходы'))}</span></div>
      <div class="plan-actuals"><span>${esc(tr('План месяца'))}<b>${money(item.amount, item.currency)}</b></span><span>${esc(tr('Факт'))}<b>${money(item.actual, item.currency)}</b></span><span>${esc(tr('Ещё ожидается'))}<b>${money(item.remaining, item.currency)}</b></span></div>
      <div class="plan-item-footer"><span class="muted">${esc(tr(statusOptions.find(([value]) => value === item.payment_status)?.[1] || 'Автоматически'))} · ${esc(tr('Связано операций: {count}', { count: item.transaction_ids.length }))}</span><div class="plan-item-actions"><button class="secondary" data-edit-plan="${esc(item.id)}">${esc(tr('Изменить'))}</button><button class="icon-button" data-delete-plan="${esc(item.id)}" aria-label="${esc(tr('Удалить'))}: ${esc(item.name)}">${icon('trash')}</button></div></div></article>`;
  }

  function render() {
    const plan = state.plan;
    if (!plan) return;
    $('#plan-forecast').innerHTML = renderForecast(plan);
    $('#overview-forecast').innerHTML = renderForecast(plan, true);
    $('#plan-categories').innerHTML = plan.categories.map(categoryRow).join('') || empty(tr('В этом месяце категорий нет'));
    $('#plan-items').innerHTML = plan.items.map(itemRow).join('') || empty(tr('Разовых расходов и доходов пока нет'));
    $('#plan-categories').querySelectorAll('[data-plan-setting]').forEach(input => {
      input.onchange = () => {
        if (input.dataset.planSetting === 'currency') input.value = input.value.trim().toUpperCase();
        if (!input.reportValidity()) return;
        const id = input.closest('[data-plan-category]').dataset.planCategory;
        return runAction(input, async () => {
          try { await write(`/api/categories/${id}`, 'PATCH', { [input.dataset.planSetting]: input.value || '0' }); }
          catch (error) { render(); throw error; }
        });
      };
    });
    $('#plan-items').querySelectorAll('[data-edit-plan]').forEach(button => button.onclick = () => openEditor(state.plan.items.find(item => item.id === Number(button.dataset.editPlan))));
    $('#plan-items').querySelectorAll('[data-delete-plan]').forEach(button => button.onclick = () => {
      if (confirm(tr('Удалить разовый план? Связанные операции сохранятся.'))) return runAction(button, () => write(`/api/plan/items/${button.dataset.deletePlan}`, 'DELETE'));
    });
  }

  function renderTransactionChoices(selectedIds) {
    const form = $('#plan-item-form');
    const selected = new Set(selectedIds);
    const candidates = eligiblePlanTransactions({ ...state, items: state.plan.items }, {
      id: editingId, month: editingMonth, direction: form.elements.direction.value, currency: form.elements.currency.value.trim().toUpperCase(),
    });
    const candidateIds = new Set(candidates.map(row => row.id));
    const unavailable = [...selected].filter(id => !candidateIds.has(id));
    const staleMarkup = unavailable.map(id => {
      const row = state.transactions.find(transaction => transaction.id === id);
      return `<label class="plan-transaction-choice"><input type="checkbox" name="transaction_ids" value="${esc(id)}" data-unavailable-link checked><span><strong>${esc(row ? transactionTitle(row) : tr('Операция #{id}', { id }))}</strong><small class="debit">${esc(tr('Операция больше не подходит этому плану. Снимите отметку, чтобы убрать связь и сохранить.'))}</small></span></label>`;
    }).join('');
    const candidateMarkup = candidates.map(row => `<label class="plan-transaction-choice"><input type="checkbox" name="transaction_ids" value="${esc(row.id)}" ${selected.has(row.id) ? 'checked' : ''}><span><strong>${esc(transactionTitle(row))}</strong><small class="muted">${esc(formatDate(row.date))}</small></span><b>${money(row.amount_exact ?? row.amount, planTransactionCurrency(row, state.accounts))}</b></label>`).join('');
    $('#plan-transaction-choices').innerHTML = staleMarkup + candidateMarkup || `<p class="muted plan-note">${esc(tr('Подходящих операций нет. Их можно связать позже.'))}</p>`;
    $('#plan-transaction-choices').querySelectorAll('[data-unavailable-link]').forEach(input => {
      const validate = () => input.setCustomValidity(input.checked ? tr('Операция больше не подходит этому плану. Снимите отметку, чтобы убрать связь и сохранить.') : '');
      input.onchange = validate;
      validate();
    });
  }

  function openEditor(item = null) {
    editingId = item?.id ?? null;
    editingMonth = state.selectedMonth;
    $('#plan-item-title').textContent = tr(item ? 'Изменить разовый план' : 'Разовый расход или доход');
    $('#plan-item-form').innerHTML = `<p class="plan-note muted">${esc(tr('Дополнительно к категориям. Не добавляйте сюда расходы, уже включённые в бюджет категории.'))}</p>
      ${field('Название', `<input name="name" required maxlength="100" value="${esc(item?.name || '')}" autofocus>`)}
      <div class="plan-form-grid">${field('Направление', `<select name="direction">${options([['DBIT', 'Расходы'], ['CRDT', 'Доходы']], item?.direction || 'DBIT')}</select>`)}
      ${field('Сумма', `<input name="amount" type="number" required min="0.01" step="0.01" value="${esc(item?.amount || '')}" placeholder="0.00">`)}
      ${field('Валюта', `<input name="currency" value="${esc(item?.currency || 'EUR')}" required minlength="3" maxlength="3" pattern="[A-Za-z]{3}">`)}
      ${field('Статус платежа', statusSelect(item?.payment_status || 'auto', 'name="payment_status"'))}</div>
      <p class="plan-note muted">${esc(tr('Автоматически — завершить после первой операции. Ждать остаток — учитывать частичную оплату. Завершено — больше ничего не ожидается.'))}</p>
      <fieldset class="plan-links"><legend>${esc(tr('Связанные операции'))}</legend><p class="plan-note muted">${esc(tr('Выберите операции целиком. Они учтутся здесь, а не в остатке бюджета категории. Снимите отметку, чтобы убрать связь.'))}</p><div id="plan-transaction-choices"></div></fieldset>
      <div class="modal-actions"><button type="button" class="secondary" id="plan-item-cancel">${esc(tr('Отмена'))}</button><button type="submit">${esc(tr('Сохранить'))}</button></div>`;
    renderTransactionChoices(item?.transaction_ids || []);
    const form = $('#plan-item-form');
    for (const name of ['direction', 'currency']) form.elements[name].onchange = () => {
      const selected = [...form.querySelectorAll('[name="transaction_ids"]:checked')].map(input => Number(input.value));
      form.elements.currency.value = form.elements.currency.value.trim().toUpperCase();
      renderTransactionChoices(selected);
    };
    $('#plan-item-cancel').onclick = () => $('#plan-item-dialog').close();
    $('#plan-item-dialog').querySelectorAll('[data-error]').forEach(alert => alert.remove());
    $('#plan-item-dialog').showModal();
  }

  function bind() {
    $('#add-plan-item').onclick = () => openEditor();
    $('#plan-item-close').onclick = () => $('#plan-item-dialog').close();
    bindDialogDismiss($('#plan-item-dialog'));
    $('#plan-item-form').onsubmit = event => runAction(event.submitter, async () => {
      const form = event.target;
      const body = { name: form.elements.name.value.trim(), direction: form.elements.direction.value,
        amount: form.elements.amount.value, currency: form.elements.currency.value.trim().toUpperCase(), payment_status: form.elements.payment_status.value,
        transaction_ids: [...form.querySelectorAll('[name="transaction_ids"]:checked')].map(input => Number(input.value)),
      };
      if (editingId == null) body.month = editingMonth;
      const saved = await fetchJson(editingId == null ? '/api/plan/items' : `/api/plan/items/${editingId}`, {
        method: editingId == null ? 'POST' : 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      // The write has succeeded even if refreshing fails. Retrying must update
      // this item rather than creating the same expected expense a second time.
      editingId = saved.id;
      await reload();
      $('#plan-item-dialog').close();
    }, event);
  }
  return { render, bind };
}
