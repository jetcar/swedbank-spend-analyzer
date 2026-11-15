import { escapeHtml } from './expense-domain.js';

export function categoryShortName(name) {
  return Array.from(String(name || '').trim()).slice(0, 2).join('').toLocaleUpperCase();
}

export function categoryPickerHtml(categories, selectedId, { allowEmpty = false, label = 'Categories', emptyLabel = 'Uncategorized' } = {}) {
  const choices = allowEmpty ? [{ id: null, icon: '—', name: emptyLabel }, ...categories] : categories;
  return `<div class="category-picker" role="radiogroup" aria-label="${escapeHtml(label)}">${choices.map(category => {
    const value = category.id == null ? '' : String(category.id);
    const selected = value === String(selectedId ?? '');
    const shortName = category.id == null ? '—' : categoryShortName(category.name);
    return `<button type="button" class="category-choice${selected ? ' selected' : ''}" role="radio" aria-checked="${selected}" tabindex="${selected ? '0' : '-1'}" aria-label="${escapeHtml(category.name)}" title="${escapeHtml(category.name)}" data-category-choice="${escapeHtml(value)}"><span>${escapeHtml(category.icon)}</span><b>${escapeHtml(shortName)}</b></button>`;
  }).join('')}</div>`;
}

export function bindCategoryPicker(root, onSelect) {
  root.addEventListener('click', event => {
    if (root.dataset.dragged === 'true') { event.preventDefault(); event.stopPropagation(); }
  }, true);
  const buttons = [...root.querySelectorAll('[data-category-choice]')];
  function select(button) {
    buttons.forEach(choice => {
      const selected = choice === button;
      choice.classList.toggle('selected', selected);
      choice.setAttribute('aria-checked', String(selected));
      choice.tabIndex = selected ? 0 : -1;
    });
    onSelect(button.dataset.categoryChoice);
  }
  buttons.forEach((button, index) => {
    button.onclick = () => select(button);
    button.onkeydown = event => {
      const direction = {ArrowLeft:-1, ArrowUp:-1, ArrowRight:1, ArrowDown:1}[event.key];
      let next = direction == null ? null : (index + direction + buttons.length) % buttons.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = buttons.length - 1;
      if (next == null) return;
      event.preventDefault(); select(buttons[next]); buttons[next].focus();
    };
  });
  let startX = 0, startScroll = 0, pointerId = null, pressedButton = null;
  root.onpointerdown = event => {
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    pointerId = event.pointerId; startX = event.clientX; startScroll = root.scrollLeft;
    pressedButton = event.target.closest('[data-category-choice]');
    root.dataset.dragged = 'false'; root.classList.add('dragging'); root.setPointerCapture(pointerId);
  };
  root.onpointermove = event => {
    if (event.pointerId !== pointerId) return;
    const distance = event.clientX - startX;
    if (Math.abs(distance) > 12) root.dataset.dragged = 'true';
    root.scrollLeft = startScroll - distance;
  };
  root.onpointerup = root.onpointercancel = event => {
    if (event.pointerId !== pointerId) return;
    if (event.type === 'pointerup' && root.dataset.dragged !== 'true' && pressedButton) select(pressedButton);
    pointerId = null; root.classList.remove('dragging');
    pressedButton = null;
    setTimeout(() => { root.dataset.dragged = 'false'; }, 0);
  };
}
