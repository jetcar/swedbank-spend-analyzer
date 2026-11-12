import { t as tr, formatMoney as money } from './i18n.js';
import { escapeHtml } from './expense-domain.js';
import { maskAccount, runAction } from './dashboard-ui.js';

export function createAccountsView({ state, fetchJson, reload, onBalanceChange }) {
  const accountBalanceDrafts = new Map();
  const accountCurrencyDrafts = new Map();

  function renderAccounts() {
    document.querySelector('#account-list').innerHTML = state.accounts.map(account => {
      const draft = accountBalanceDrafts.get(account.id);
      const currencyDraft = accountCurrencyDrafts.get(account.id);
      const name = account.name || tr('Счёт {id}', { id: account.id });
      return `<article class="account-card" data-account-card="${escapeHtml(account.id)}">
        <label class="account-heading switch">
          <input type="checkbox" data-account-id="${escapeHtml(account.id)}" ${account.monitored ? 'checked' : ''}>
          <span><strong>${escapeHtml(name)}</strong><br><span class="muted">${escapeHtml(maskAccount(account))} · ${escapeHtml(account.currency || '')}</span></span>
        </label>
        ${currencyDraft === undefined ? `<div class="account-currency-row"><span>${tr('Валюта')}: <strong>${escapeHtml(account.currency || '—')}</strong></span><button type="button" class="secondary" data-currency-edit="${escapeHtml(account.id)}">${tr('Изменить')}</button></div>` : `<form class="account-currency-form" data-currency-form="${escapeHtml(account.id)}">
          <label for="account-currency-${escapeHtml(account.id)}">${tr('Валюта')}</label>
          <input id="account-currency-${escapeHtml(account.id)}" name="currency" value="${escapeHtml(currencyDraft)}" required maxlength="3" pattern="[A-Za-z]{3}" title="${tr('Введите трёхбуквенный код валюты')}" autocomplete="off">
          <div class="account-currency-actions"><button type="submit">${tr('Сохранить')}</button><button type="button" class="secondary" data-currency-cancel="${escapeHtml(account.id)}">${tr('Отмена')}</button></div>
        </form>`}
        <div class="account-balance-row"><div><span class="muted">${tr('Текущий баланс')}</span><strong class="account-balance" data-account-balance="${escapeHtml(account.id)}">${escapeHtml(money(account.balance, account.currency))}</strong></div>
          ${draft ? '' : `<button type="button" class="secondary" data-balance-edit="${escapeHtml(account.id)}" aria-label="${escapeHtml(tr('Изменить баланс: {account}', { account: name }))}">${tr('Изменить')}</button>`}
        </div>
        ${draft ? `<form class="account-balance-form" data-balance-form="${escapeHtml(account.id)}">
          <label for="account-balance-${escapeHtml(account.id)}">${tr('Новый баланс')} (${escapeHtml(account.currency || 'EUR')})</label>
          <input id="account-balance-${escapeHtml(account.id)}" name="balance" type="text" inputmode="decimal" required maxlength="20" pattern="-?[0-9]+([.,][0-9]{1,2})?" title="${tr('Введите сумму с точностью до двух знаков после запятой')}" value="${escapeHtml(draft.value)}" ${draft.saving ? 'disabled' : ''}>
          <div class="account-balance-actions"><button type="submit" ${draft.saving ? 'disabled' : ''}>${draft.saving ? tr('Сохранение…') : tr('Сохранить')}</button><button type="button" class="secondary" data-balance-cancel="${escapeHtml(account.id)}" ${draft.saving ? 'disabled' : ''}>${tr('Отмена')}</button></div>
          <p class="account-balance-hint muted">${tr('Новые операции будут менять указанную сумму.')}</p>
          ${draft.error ? `<p class="account-balance-error" role="alert">${tr('Не удалось сохранить баланс. Попробуйте ещё раз.')}</p>` : ''}
        </form>` : ''}
      </article>`;
    }).join('');
    document.querySelectorAll('[data-account-id]').forEach(input => input.onchange = () => runAction(input, async () => {
      const account = state.accounts.find(row => row.id === Number(input.dataset.accountId));
      try {
        await fetchJson(`/api/accounts/${input.dataset.accountId}/monitor?monitored=${input.checked}`, { method: 'PATCH' });
      } catch (error) { input.checked = account.monitored; throw error; }
      await reload();
    }));
    document.querySelectorAll('[data-currency-edit]').forEach(button => button.onclick = () => {
      const id = Number(button.dataset.currencyEdit);
      accountCurrencyDrafts.set(id, String(state.accounts.find(account => account.id === id).currency || ''));
      renderAccounts();
      document.querySelector(`#account-currency-${id}`).select();
    });
    document.querySelectorAll('[data-currency-cancel]').forEach(button => button.onclick = () => {
      const id = Number(button.dataset.currencyCancel);
      accountCurrencyDrafts.delete(id);
      renderAccounts();
      document.querySelector(`[data-currency-edit="${id}"]`).focus();
    });
    document.querySelectorAll('[data-currency-form]').forEach(form => {
      const id = Number(form.dataset.currencyForm);
      const button = form.querySelector('button');
      form.elements.currency.oninput = event => {
        event.target.value = event.target.value.toUpperCase();
        accountCurrencyDrafts.set(id, event.target.value);
      };
      form.onsubmit = event => runAction(button, async () => {
        if (!form.reportValidity()) return;
        const currency = form.elements.currency.value.toUpperCase();
        await fetchJson(`/api/accounts/${id}/currency`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ currency }),
        });
        accountCurrencyDrafts.delete(id);
        await reload();
      }, event);
    });
    document.querySelectorAll('[data-balance-edit]').forEach(button => button.onclick = () => {
      const id = Number(button.dataset.balanceEdit);
      accountBalanceDrafts.set(id, { value: String(state.accounts.find(account => account.id === id).balance), saving: false, error: false });
      renderAccounts();
      document.querySelector(`#account-balance-${id}`).select();
    });
    document.querySelectorAll('[data-balance-cancel]').forEach(button => button.onclick = () => {
      const id = Number(button.dataset.balanceCancel);
      accountBalanceDrafts.delete(id);
      renderAccounts();
      document.querySelector(`[data-balance-edit="${id}"]`).focus();
    });
    document.querySelectorAll('[data-balance-form]').forEach(form => {
      const id = Number(form.dataset.balanceForm);
      form.elements.balance.oninput = event => { accountBalanceDrafts.get(id).value = event.target.value; };
      form.onsubmit = async event => {
        event.preventDefault();
        const draft = accountBalanceDrafts.get(id);
        if (draft.saving || !form.reportValidity()) return;
        draft.value = form.elements.balance.value;
        draft.saving = true;
        draft.error = false;
        renderAccounts();
        try {
          const result = await fetchJson(`/api/accounts/${id}/balance`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ balance: draft.value.replace(',', '.') }),
          });
          const account = state.accounts.find(item => item.id === id);
          if (account) account.balance = result.balance;
          await reload();
          accountBalanceDrafts.delete(id);
        } catch {
          draft.error = true;
        } finally {
          draft.saving = false;
          renderAccounts(); onBalanceChange();
          const target = accountBalanceDrafts.has(id) ? `#account-balance-${id}` : `[data-balance-edit="${id}"]`;
          document.querySelector(target)?.focus();
        }
      };
    });
  }

  return { render: renderAccounts, isEditing: () => accountBalanceDrafts.size > 0 || accountCurrencyDrafts.size > 0 };
}
