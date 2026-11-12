import { t as tr, getLocale, formatMoney as money, formatDate as date } from './i18n.js';
import { escapeHtml } from './expense-domain.js';
import { currencyTotals } from './finance-domain.js';
import { icon } from './icons.js';
import { renderBudgetStatus } from './budget-status.js';
import { $, empty, maskAccount } from './dashboard-ui.js';

function totalsMarkup(rows, amount) {
  return currencyTotals(rows, amount).map(total => `<span>${escapeHtml(money(total.amount, total.currency))}</span>`).join('') || '—';
}

export function createOverviewView({ state, renderRow, bindRows }) {
  function renderOverview() {
    const included = state.transactions.filter(row => !row.excluded_from_statistics);
    const tracked = state.accounts.filter(account => account.monitored);
    const total = totalsMarkup(tracked, account => account.balance);
    $('#total-balance').innerHTML = total;
    $('#accounts-total').innerHTML = total;
    $('#earned').innerHTML = totalsMarkup(included.filter(row => row.direction === 'CRDT'));
    $('#spent').innerHTML = totalsMarkup(included.filter(row => row.direction === 'DBIT'));
    $('#account-summary').innerHTML = tracked.slice(0,3).map(account => `<div class="compact-account"><span class="account-icon">${icon('bank')}</span><div class="account-info"><strong>${escapeHtml(account.name || tr('Счёт {id}', {id:account.id}))}</strong><span class="muted">${escapeHtml(maskAccount(account))}</span></div><b>${escapeHtml(money(account.balance, account.currency || 'XXX'))}</b></div>`).join('') || empty(tr('Выберите, что отслеживать'));
    $('#budget-summary').innerHTML = state.categories.filter(category => category.enabled).slice(0,5).map(category => `<div class="budget-row"><span class="budget-icon">${escapeHtml(category.icon)}</span><div class="budget-row-main"><strong>${escapeHtml(category.name)}</strong>${renderBudgetStatus(category)}</div></div>`).join('') || empty(tr('В этом месяце категорий нет'));
    $('#recent-transactions').innerHTML = state.transactions.slice(0,5).map(renderRow).join('') || empty(tr('Нет операций за этот месяц'));
    bindRows($('#recent-transactions'));
    const currency = $('#chart-currency').value;
    const currencies = [...new Set(included.map(row => row.currency || 'XXX'))];
    $('#chart-currency').innerHTML = currencies.map(item => `<option>${escapeHtml(item)}</option>`).join('');
    if (currencies.includes(currency)) $('#chart-currency').value = currency;
    renderChart();
  }

  function renderChart() {
    const currency = $('#chart-currency').value;
    const rows = state.transactions.filter(row => !row.excluded_from_statistics && (row.currency || 'XXX') === currency);
    if (!rows.length) { $('#cashflow').innerHTML = empty(tr('Нет операций за этот месяц')); return; }
    const [year, month] = state.selectedMonth.split('-').map(Number);
    const days = Array.from({length:new Date(year,month,0).getDate()}, (_,index) => ({day:index+1,income:0,expense:0}));
    for (const row of rows) {
      if (!row.date?.startsWith(state.selectedMonth)) continue;
      const day = days[Number(row.date.slice(8,10))-1];
      if (day && ['CRDT','DBIT'].includes(row.direction)) day[row.direction === 'CRDT' ? 'income' : 'expense'] += Math.abs(Number(row.amount_exact ?? row.amount) || 0);
    }
    const max = Math.max(1,...days.flatMap(day => [day.income,day.expense]));
    const step = 460/days.length;
    const grid = [0,.5,1].map(ratio => `<line class="chart-grid" x1="40" x2="510" y1="${180-ratio*145}" y2="${180-ratio*145}"/><text class="chart-axis" x="35" y="${184-ratio*145}" text-anchor="end">${escapeHtml(new Intl.NumberFormat(getLocale(),{notation:'compact',maximumFractionDigits:1}).format(max*ratio))}</text>`).join('');
    const bars = days.map((day,index) => `<g><title>${escapeHtml(date(`${state.selectedMonth}-${String(day.day).padStart(2,'0')}`))}: ${escapeHtml(tr('Доходы'))} ${escapeHtml(money(day.income,currency))}; ${escapeHtml(tr('Расходы'))} ${escapeHtml(money(day.expense,currency))}</title><rect class="chart-income" x="${45+index*step}" y="${180-day.income/max*145}" width="${step*.32}" height="${day.income/max*145}" rx="2"/><rect class="chart-expense" x="${45+index*step+step*.35}" y="${180-day.expense/max*145}" width="${step*.32}" height="${day.expense/max*145}" rx="2"/>${index%5 === 0 || index === days.length-1 ? `<text class="chart-axis" x="${47+index*step}" y="204" text-anchor="middle">${day.day}</text>` : ''}</g>`).join('');
    $('#cashflow').innerHTML = `<svg class="cashflow-svg" viewBox="0 0 520 215" role="img" aria-label="${escapeHtml(tr('Доходы и расходы по дням выбранного месяца'))}">${grid}${bars}</svg>`;
  }

  return { render: renderOverview, bind() { $('#chart-currency').onchange = renderChart; } };
}
