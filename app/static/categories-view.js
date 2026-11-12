import { t as tr } from './i18n.js';
import { categoryIcons, escapeHtml } from './expense-domain.js';
import { icon } from './icons.js';
import { $, runAction } from './dashboard-ui.js';
import { renderBudgetStatus } from './budget-status.js';

export function createCategoriesView({ state, fetchJson, reload }) {
  function openIconPicker(input) {
    const picker = document.querySelector('#icon-picker');
    picker.innerHTML = categoryIcons.map(icon => `<button type="button" data-picker-icon="${icon}">${icon}</button>`).join('');
    const rect = input.getBoundingClientRect();
    picker.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 280))}px`;
    picker.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - 260)}px`;
    picker.hidden = false;
    picker.querySelectorAll('[data-picker-icon]').forEach(button => button.onclick = () => {
      input.value = button.dataset.pickerIcon;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      picker.hidden = true;
    });
  }

  function renderCategoryManager() {
    document.querySelector('#category-manager').innerHTML = state.categories.length ? state.categories.map(category => `
      <div class="expense" data-manager-category="${escapeHtml(category.id)}">
        <input class="category-icon-input" value="${escapeHtml(category.icon)}" maxlength="4" readonly title="${tr('Выбрать иконку')}" data-category-icon="${escapeHtml(category.id)}">
        <div class="expense-main"><input class="category-name-input" value="${escapeHtml(category.name)}" maxlength="100" required aria-label="${tr('Название категории')}: ${escapeHtml(category.name)}" title="${tr('Enter — сохранить, Escape — отменить')}" data-category-name="${escapeHtml(category.id)}"><div class="expense-meta">${category.enabled ? tr('Включена') : tr('Выключена')}</div></div>
        <div class="category-metrics">
          <div class="category-metric"><span>${category.budget < 0 ? tr('Доходы за месяц') : tr('Расходы за месяц')}</span>${renderBudgetStatus(category)}</div>
          <div class="category-metric"><span>${category.budget < 0 ? tr('Ожидаемый доход') : tr('Бюджет')}</span><input type="number" step="0.01" value="${escapeHtml(category.budget || '')}" placeholder="∞" title="${tr('Отрицательный бюджет — ожидаемый доход')}" data-category-budget="${escapeHtml(category.id)}"></div>
        </div>
        <label class="switch"><input type="checkbox" ${category.enabled ? 'checked' : ''} data-category-enabled="${escapeHtml(category.id)}"> ${tr('Вкл.')}</label>
        <button class="icon-button" title="${tr('Удалить категорию')}" data-category-delete="${escapeHtml(category.id)}">${icon('trash')}</button>
      </div>`).join('') : `<div class="muted">${tr('В этом месяце категорий нет')}</div>`;
    function saveSetting(input, id, settings) {
      return runAction(input, async () => {
        try { await updateCategory(id, settings); }
        catch (error) {
          renderCategoryManager(); improveControls();
          if (settings.name !== undefined) {
            const alert = document.createElement('p');
            alert.setAttribute('role', 'alert');
            alert.dataset.error = '';
            alert.className = 'debit expense-meta';
            alert.textContent = tr('Не удалось переименовать категорию. Проверьте, что имя не занято, и попробуйте ещё раз.');
            document.querySelector(`[data-manager-category="${id}"] .expense-main`)?.append(alert);
          }
          throw error;
        }
        await reload();
      });
    }
    document.querySelectorAll('[data-category-name]').forEach(input => {
      const id = Number(input.dataset.categoryName);
      const category = state.categories.find(row => row.id === id);
      input.oninput = () => input.setCustomValidity('');
      input.onchange = () => {
        const name = input.value.trim();
        input.value = name;
        input.setCustomValidity(name ? '' : tr('Введите название категории'));
        if (!input.reportValidity() || name === category.name) return;
        return saveSetting(input, id, { name });
      };
      input.onkeydown = event => {
        if (event.key === 'Escape') {
          input.value = category.name;
          input.setCustomValidity('');
        }
        if (event.key === 'Enter' || event.key === 'Escape') {
          event.preventDefault();
          input.blur();
        }
      };
    });
    document.querySelectorAll('[data-category-budget]').forEach(input => input.onchange = () => saveSetting(input, Number(input.dataset.categoryBudget), { budget: Number(input.value) }));
    document.querySelectorAll('[data-category-enabled]').forEach(input => input.onchange = () => saveSetting(input, Number(input.dataset.categoryEnabled), { enabled: input.checked }));
    document.querySelectorAll('[data-category-icon]').forEach(input => input.onchange = () => {
      if (input.value.trim()) saveSetting(input, Number(input.dataset.categoryIcon), { icon: input.value.trim() });
    });
    document.querySelectorAll('[data-category-icon]').forEach(input => input.onclick = () => openIconPicker(input));
    const newCategoryIcon = document.querySelector('#category-form input[name="icon"]');
    if (newCategoryIcon) newCategoryIcon.onclick = () => openIconPicker(newCategoryIcon);
    document.querySelectorAll('[data-category-delete]').forEach(button => button.onclick = () => {
      if (!confirm(tr('Удалить категорию за этот месяц? Операции станут без категории.'))) return;
      return runAction(button, async () => {
        await fetchJson(`/api/categories/${button.dataset.categoryDelete}`, { method: 'DELETE' });
        await reload();
      });
    });
  }

  async function updateCategory(id, settings) {
    const updated = await fetchJson(`/api/categories/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) });
    const category = state.categories.find(row => row.id === id);
    if (category) Object.assign(category, settings, updated);
  }

  function improveControls() {
    document.querySelectorAll('[data-category-budget]').forEach(input => input.setAttribute('aria-label',`${tr('Бюджет')}: ${state.categories.find(row => row.id === Number(input.dataset.categoryBudget))?.name}`));
    document.querySelectorAll('[data-category-icon]').forEach(input => { input.setAttribute('aria-label',tr('Выбрать иконку')); input.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();openIconPicker(input);} }; });
    document.querySelectorAll('[data-category-enabled]').forEach(input => input.setAttribute('aria-label',`${tr('Включена')}: ${state.categories.find(row => row.id === Number(input.dataset.categoryEnabled))?.name}`));
  }

  function bind() {
    $('#category-form').onsubmit = event => runAction(event.submitter, async () => {
      const form = event.target;
      await fetchJson('/api/categories', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: form.elements.name.value, icon: form.elements.icon.value, month: state.selectedMonth, budget: Number(form.elements.budget.value || 0) }) });
      form.reset(); await reload();
    }, event);
    $('#category-form input[name="icon"]').onkeydown = event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openIconPicker(event.target); }
    };
    document.addEventListener('click', event => { if (!event.target.closest('#icon-picker') && !event.target.closest('.category-icon-input')) $('#icon-picker').hidden = true; });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') $('#icon-picker').hidden = true; });
  }
  return { bind, render() { renderCategoryManager(); improveControls(); $('#category-count').textContent = state.categories.length; } };
}
