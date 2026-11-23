import { t as tr, getLocale, formatMoney as money, formatDate as date } from './i18n.js';
import { escapeHtml } from './expense-domain.js';
import { buildCashflowDays, currencyTotals } from './finance-domain.js';
import { icon } from './icons.js';
import { renderBudgetStatus } from './budget-status.js';
import { $, empty, maskAccount } from './dashboard-ui.js';

const CHART_RANGE_STORAGE_KEY = 'finance.chartRange';

function totalsMarkup(rows, amount) {
  return currencyTotals(rows, amount).map(total => `<span>${escapeHtml(money(total.amount, total.currency))}</span>`).join('') || '—';
}

const chartPalettes = {
  income: ['#397f5d', '#4f9473', '#67aa8a', '#2f746d', '#568d48', '#76a45b'],
  expense: ['#bd4f38', '#d16b42', '#a94c63', '#d4893f', '#965d8c', '#b86d58'],
};
function categoryColor(key, direction) {
  if (key === 'uncategorized') return '#8a918c';
  const hash = [...String(key)].reduce((value, character) => ((value * 31) + character.charCodeAt(0)) >>> 0, 0);
  const palette = chartPalettes[direction];
  return palette[hash % palette.length];
}
function categoryLabel(item) {
  return `${item.icon ? `${item.icon} ` : ''}${item.name || tr('Без категории')}`;
}
function stackedRects(day, direction, x, width, max, index, currency) {
  let offset = 0;
  const categories = day[`${direction}Categories`];
  if (!categories.length) return `<rect class="chart-${direction}" x="${x}" y="180" width="${width}" height="0" aria-hidden="true"/>`;
  return categories.map(category => {
    const height = category.amount / max * 145;
    offset += height;
    const label = `${date(day.date)}, ${categoryLabel(category)}, ${money(category.amount, currency)}`;
    return `<rect class="chart-segment chart-${direction}" data-chart-day="${index}" data-chart-direction="${direction}" tabindex="0" role="img" aria-label="${escapeHtml(label)}" x="${x}" y="${180-offset}" width="${width}" height="${height}" rx="2" style="--segment-color:${categoryColor(category.key, direction)}"/>`;
  }).join('');
}

export function createOverviewView({ state, renderRow, bindRows, openCategory }) {
  let recentLimit = 5;
  let recentMonth = state.selectedMonth;

  function renderRecentTransactions() {
    if (recentMonth !== state.selectedMonth) {
      recentMonth = state.selectedMonth;
      recentLimit = 5;
    }
    $('#recent-transactions').innerHTML = state.transactions.slice(0, recentLimit).map(renderRow).join('') || empty(tr('Нет операций за этот месяц'));
    bindRows($('#recent-transactions'));
    $('#load-more-recent').hidden = recentLimit >= state.transactions.length;
  }

  function renderOverview() {
    const included = state.transactions.filter(row => !row.excluded_from_statistics);
    const tracked = state.accounts.filter(account => account.monitored);
    const total = totalsMarkup(tracked, account => account.balance);
    $('#total-balance').innerHTML = total;
    $('#accounts-total').innerHTML = total;
    $('#earned').innerHTML = totalsMarkup(included.filter(row => row.direction === 'CRDT'));
    $('#spent').innerHTML = totalsMarkup(included.filter(row => row.direction === 'DBIT'));
    $('#account-summary').innerHTML = tracked.slice(0,3).map(account => `<div class="compact-account"><span class="account-icon">${icon('bank')}</span><div class="account-info"><strong>${escapeHtml(account.name || tr('Счёт {id}', {id:account.id}))}</strong><span class="muted">${escapeHtml(maskAccount(account))}</span></div><b>${escapeHtml(money(account.balance, account.currency || 'XXX'))}</b></div>`).join('') || empty(tr('Выберите, что отслеживать'));
    $('#budget-summary').innerHTML = state.categories.filter(category => category.enabled).map(category => `<button type="button" class="budget-row budget-link" data-overview-category="${escapeHtml(category.id)}"><span class="budget-icon">${escapeHtml(category.icon)}</span><span class="budget-row-main"><strong>${escapeHtml(category.name)}</strong>${renderBudgetStatus(category)}</span></button>`).join('') || empty(tr('В этом месяце категорий нет'));
    document.querySelectorAll('[data-overview-category]').forEach(button => button.onclick = () => openCategory(Number(button.dataset.overviewCategory)));
    renderRecentTransactions();
    const currency = $('#chart-currency').value;
    const currencies = [...new Set(state.chartTransactions.filter(row => !row.excluded_from_statistics).map(row => row.currency || 'XXX'))];
    $('#chart-currency').innerHTML = currencies.map(item => `<option>${escapeHtml(item)}</option>`).join('');
    if (currencies.includes(currency)) $('#chart-currency').value = currency;
    renderChart();
  }

  function renderChart() {
    const currency = $('#chart-currency').value;
    const rows = state.chartTransactions.filter(row => !row.excluded_from_statistics && (row.currency || 'XXX') === currency);
    if (!rows.length) { $('#cashflow').innerHTML = empty(tr('Нет операций за выбранный период')); return; }
    const days = buildCashflowDays(rows, $('#chart-range').value);
    const max = Math.max(1,...days.flatMap(day => [day.income,day.expense]));
    const step = 460/days.length;
    const grid = [0,.5,1].map(ratio => `<line class="chart-grid" x1="40" x2="510" y1="${180-ratio*145}" y2="${180-ratio*145}"/><text class="chart-axis" x="35" y="${184-ratio*145}" text-anchor="end">${escapeHtml(new Intl.NumberFormat(getLocale(),{notation:'compact',maximumFractionDigits:1}).format(max*ratio))}</text>`).join('');
    const bars = days.map((day,index) => `<g>${stackedRects(day,'income',45+index*step,step*.32,max,index,currency)}${stackedRects(day,'expense',45+index*step+step*.35,step*.32,max,index,currency)}${index%Math.max(1,Math.ceil(days.length/6)) === 0 || index === days.length-1 ? `<text class="chart-axis" x="${47+index*step}" y="204" text-anchor="middle">${Number(day.date.slice(8,10))}</text>` : ''}</g>`).join('');
    $('#cashflow').innerHTML = `<svg class="cashflow-svg" viewBox="0 0 520 215" role="img" aria-label="${escapeHtml(tr('Доходы и расходы за выбранный период'))}">${grid}${bars}</svg><div class="chart-tooltip" role="tooltip" hidden></div>`;
    const root = $('#cashflow');
    const tooltip = root.querySelector('.chart-tooltip');
    const hideTooltip = () => { tooltip.hidden = true; };
    const showTooltip = (target, clientX, clientY) => {
      const day = days[Number(target.dataset.chartDay)];
      const direction = target.dataset.chartDirection;
      const categories = day[`${direction}Categories`];
      const total = day[direction];
      tooltip.innerHTML = `<strong>${escapeHtml(date(day.date))}</strong><div class="chart-tooltip-total"><span>${escapeHtml(tr('Всего'))}</span><b>${escapeHtml(money(total,currency))}</b></div>${categories.map(category => `<div><span><i style="--segment-color:${categoryColor(category.key,direction)}"></i>${escapeHtml(categoryLabel(category))}</span><b>${escapeHtml(money(category.amount,currency))}</b></div>`).join('')}`;
      const bounds = target.getBoundingClientRect();
      tooltip.hidden = false;
      const anchorX = clientX ?? bounds.left + bounds.width / 2;
      const anchorY = clientY ?? bounds.top;
      const margin = 8;
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      const left = Math.max(margin, Math.min(anchorX, window.innerWidth - width - margin));
      let top = anchorY + 12;
      if (top + height > window.innerHeight - margin) top = anchorY - height - 12;
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${Math.max(margin, top)}px`;
    };
    root.querySelectorAll('.chart-segment').forEach(segment => {
      segment.onmouseenter = event => showTooltip(segment, event.clientX, event.clientY);
      segment.onmousemove = event => showTooltip(segment, event.clientX, event.clientY);
      segment.onmouseleave = hideTooltip;
      segment.onfocus = () => showTooltip(segment);
      segment.onblur = hideTooltip;
    });
  }

  return { render: renderOverview, bind() {
    const range = $('#chart-range');
    try {
      const saved = localStorage.getItem(CHART_RANGE_STORAGE_KEY);
      if ([...range.options].some(option => option.value === saved)) range.value = saved;
    } catch {}
    $('#chart-currency').onchange = renderChart;
    $('#load-more-recent').onclick = () => {
      recentLimit += 5;
      renderRecentTransactions();
    };
    range.onchange = () => {
      try { localStorage.setItem(CHART_RANGE_STORAGE_KEY, range.value); } catch {}
      renderChart();
    };
  } };
}
