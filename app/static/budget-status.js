import { t as tr, formatMoney as money, formatPercent } from './i18n.js';
import { categoryBudgetState, escapeHtml } from './expense-domain.js';

export function renderBudgetStatus(category) {
  const state = categoryBudgetState(category);
  const income = Number(category.monthly_budget ?? category.budget) < 0;
  const remaining = state.remaining == null ? null : Number(state.remaining);
  const positive = value => String(value).replace(/^-/, '');
  const label = income
    ? remaining > 0 ? tr('Осталось заработать {amount}', { amount: money(state.remaining) })
      : remaining === 0 ? tr('План дохода выполнен') : tr('Сверх плана {amount}', { amount: money(positive(state.remaining)) })
    : remaining === null ? tr('Без лимита') : remaining < 0
    ? tr('Перерасход {amount}', { amount: money(positive(state.remaining)) }) : remaining === 0
      ? tr('Бюджет исчерпан') : tr('Осталось {amount}', { amount: money(state.remaining) });
  return `<span class="budget-status budget-${state.status}" data-budget-status="${escapeHtml(category.id)}">
    <span class="budget-amount">${income ? tr('Получено {earned} из {budget}', { earned: money(category.earned || 0), budget: money(positive(state.budget)) }) : state.percent === null ? money(state.spent) : tr('{spent} из {budget}', { spent: money(state.spent), budget: money(state.budget) })}</span>
    ${state.percent === null ? '' : `<span class="budget-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.min(100, Math.max(0, state.percent))}" aria-label="${income ? tr('Выполнение плана дохода') : tr('Использование бюджета')}" aria-valuetext="${income ? tr('{percent} плана дохода выполнено', { percent: formatPercent(state.percent) }) : tr('{percent} бюджета использовано', { percent: formatPercent(state.percent) })}"><span class="budget-progress-fill" style="width: ${Math.min(100, Math.max(0, state.percent))}%"></span></span>`}
    <span class="budget-caption">${label}${state.percent === null ? '' : ` · ${formatPercent(state.percent)}`}</span>
  </span>`;
}
