import { t as tr, getLocale, formatDate as date } from './i18n.js';
import { escapeHtml, transactionDateLabel } from './expense-domain.js';
import { icon } from './icons.js';

export const $ = selector => document.querySelector(selector);
export const empty = message => `<div class="empty-state">${icon('wallet')}<br>${escapeHtml(message)}</div>`;
export const maskAccount = account => account.iban ? `•••• ${account.iban.slice(-4)}` : account.currency || '';

export function setStatus(render) {
  document.querySelector('#status').textContent = render();
}

export function renderTransactionDate(transaction) {
  return `${escapeHtml(date(transaction?.date))} · ${escapeHtml(tr(transactionDateLabel(transaction?.date_kind)))}`;
}

export function addClientLog(level, message, details = '') {
  const log = document.querySelector('#client-logs');
  if (!log) return;
  const time = new Date().toLocaleTimeString(getLocale());
  const suffix = details ? ` — ${details}` : '';
  const line = `[${time}] ${level}: ${message}${suffix}`;
  log.textContent = log.textContent === tr('Ожидание загрузки…') ? line : `${log.textContent}\n${line}`;
  log.textContent = log.textContent.split('\n').slice(-100).join('\n');
  log.scrollTop = log.scrollHeight;
}

export function handleLoadError(error) {
  const dialog = document.querySelector('dialog[open]');
  if (dialog) { let alert = dialog.querySelector('[data-error]'); if (!alert) { alert = document.createElement('p'); alert.dataset.error = ''; alert.setAttribute('role','alert'); alert.className = 'debit'; dialog.append(alert); } alert.textContent = tr('Не удалось сохранить изменения. Попробуйте ещё раз.'); }
  setStatus(() => tr('Не удалось загрузить данные'));
  addClientLog(tr('ОШИБКА'),tr('Загрузка данных остановлена'),error.message);
}

export async function runAction(button, action, event) {
  event?.preventDefault();
  document.querySelectorAll('[data-error]').forEach(alert => alert.remove());
  if (button) button.disabled = true;
  try { await action(); } catch (error) { handleLoadError(error); } finally { if (button?.isConnected) button.disabled = false; }
}

export function transactionTitle(transaction) {
  return transaction.counterparty_name || transaction.description || tr('Без описания');
}

export function transactionDescription(transaction) {
  return transaction.counterparty_name && transaction.description ? transaction.description : '';
}

export function bindDialogDismiss(dialog) {
  const isOutside = event => {
    const bounds = dialog.getBoundingClientRect();
    return event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom);
  };
  let startedOutside = false;
  dialog.addEventListener('pointerdown', event => { startedOutside = isOutside(event); });
  dialog.addEventListener('click', event => {
    if (startedOutside && isOutside(event)) dialog.close();
    startedOutside = false;
  });
  dialog.addEventListener('close', () => { startedOutside = false; });
}

export function renderSyncStatus(lastSuccessAt) {
  const time = $('#last-sync-time');
  if (lastSuccessAt) {
    time.dateTime = lastSuccessAt;
    time.textContent = new Intl.DateTimeFormat(getLocale(), { dateStyle: 'short', timeStyle: 'short' }).format(new Date(lastSuccessAt));
  } else {
    time.removeAttribute('datetime');
    time.textContent = tr('Ещё не выполнялась');
  }
}
