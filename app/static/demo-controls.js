import { t } from './i18n.js';

// Fetch keeps the same-origin context under the gateway's no-referrer policy;
// a native POST navigation can send Origin: null and be rejected by the gateway.
document.querySelectorAll('form[action^="/auth/demo/"]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    let error = form.querySelector('[role="alert"]');
    if (error) error.remove();
    try {
      const response = await fetch(form.getAttribute('action'), { method: 'POST' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      location.assign('/');
    } catch {
      error = document.createElement('p');
      error.setAttribute('role', 'alert');
      error.textContent = t('Не удалось открыть демо. Попробуйте ещё раз.');
      form.appendChild(error);
      button.disabled = false;
    }
  });
});
