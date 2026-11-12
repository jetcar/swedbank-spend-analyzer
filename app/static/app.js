import { t as tr, getLanguage, setLanguage, translatePage } from './i18n.js';
import { mountIcons } from './icons.js';
import { startLiveRefresh } from './live-refresh.js';
import { createJsonClient } from './api-client.js';
import { createDashboardState, createDashboardLoader } from './dashboard-state.js';
import { $, setStatus, addClientLog, handleLoadError, runAction, renderSyncStatus } from './dashboard-ui.js';
import { createCategoriesView } from './categories-view.js';
import { createSplitEditor } from './split-editor.js';
import { createAccountsView } from './accounts-view.js';
import { createTransactionsView } from './transactions-view.js';
import { createOverviewView } from './overview-view.js';
import { createPlanView } from './plan-view.js';

const state = createDashboardState();
const fetchJson = createJsonClient({
  onSuccess: (url, options) => addClientLog('OK', `${options.method || 'GET'} ${url}`),
  onError: (error, url, options) => addClientLog(tr('ОШИБКА'), `${options.method || 'GET'} ${url}`, error.message),
});
const load = createDashboardLoader({ state, fetchJson, getLanguage, onLoaded() {
  renderSyncStatus(state.syncStatus.last_success_at);
  accounts.render(); categories.render(); transactions.render(); overview.render(); plan.render();
  $('#category-month').value = state.selectedMonth;
  setStatus(() => '');
} });
const categories = createCategoriesView({ state, fetchJson, reload: load });
const split = createSplitEditor({ state, fetchJson, reload: load });
const transactions = createTransactionsView({ state, fetchJson, reload: load, openSplit: split.open });
const overview = createOverviewView({ state, renderRow: transactions.renderRow, bindRows: transactions.bindRows });
const accounts = createAccountsView({ state, fetchJson, reload: load, onBalanceChange: overview.render });
const plan = createPlanView({ state, fetchJson, reload: load });

function switchView() {
  const view = ['overview','operations','categories','accounts','plan'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
  const titles = {overview:tr('Обзор'),operations:tr('Операции'),categories:tr('Категории'),accounts:tr('Счета'),plan:tr('План месяца')};
  const subtitles = {overview:tr('Всё важное о ваших финансах'),operations:tr('Назначение и разбор операций'),categories:tr('Бюджеты и активность'),accounts:tr('Выберите, что отслеживать'),plan:tr('План, факт и ожидаемый остаток')};
  document.querySelectorAll('[data-page]').forEach(section => {section.hidden = section.dataset.page !== view;});
  document.querySelectorAll('[data-view]').forEach(link => { if (link.dataset.view === view) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); });
  $('#view-title').textContent = titles[view]; $('#view-subtitle').textContent = subtitles[view];
  document.title = `${titles[view]} · Finance`;
  $('#category-month').hidden = view === 'accounts';
}

translatePage(); mountIcons(); switchView();
for (const view of [categories, split, transactions, overview, plan]) view.bind();
$('#language').value = getLanguage(); $('#category-month').value = state.selectedMonth;
window.addEventListener('hashchange', () => { switchView(); window.scrollTo(0, 0); });
$('#language').onchange = event => runAction(null, async () => { setLanguage(event.target.value); translatePage(); switchView(); await load(); });
document.querySelectorAll('[data-connect]').forEach(button => button.onclick = () => runAction(button, async () => {
  const status = await fetchJson('/auth/status');
  if (!status.demo) { location.assign('/connect'); return; }
  setStatus(() => tr('Подключение sandbox…'));
  const result = await fetchJson('/api/demo/connect', { method: 'POST' });
  location.assign(result.url);
}));
$('#sync').onclick = event => runAction(event.currentTarget, async () => { setStatus(() => tr('Синхронизация…')); await fetchJson('/api/sync', { method: 'POST' }); await load(); setStatus(() => tr('Данные обновлены')); });
$('#retry-load').onclick = event => runAction(event.currentTarget, load);
$('#category-month').onchange = event => runAction(null, async () => { state.selectedMonth = event.target.value || state.selectedMonth; transactions.resetLimit(); await load(); });

let stopLiveRefresh;
function connectLiveRefresh() {
  stopLiveRefresh?.();
  stopLiveRefresh = startLiveRefresh({
    reload: load,
    canRefresh: () => !document.querySelector('dialog[open]') && !accounts.isEditing() && !document.activeElement?.matches('input,select,textarea'),
    onError: handleLoadError,
    onExpired: () => location.assign('/auth/demo'),
  });
}
window.addEventListener('pagehide', () => stopLiveRefresh?.());
window.addEventListener('pageshow', event => { if (event.persisted) { connectLiveRefresh(); load().catch(handleLoadError); } });
setStatus(() => tr('Загрузка…'));
load().then(() => {
  const connection = new URLSearchParams(location.search);
  if (connection.get('bank_connected') === '1') { setStatus(() => tr('Swedbank подключён · счетов: {count}', { count: Number(connection.get('accounts') || 0) })); history.replaceState({}, '', location.pathname + location.hash); }
}).catch(handleLoadError).finally(connectLiveRefresh);
