// Apply before paint on both the dashboard and the sign-in page.
(() => {
  let saved;
  try { saved = localStorage.getItem('finance-theme'); } catch { /* Storage is optional. */ }
  const apply = theme => {
    document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-pressed', String(theme === 'dark'));
    });
  };
  apply(saved);
  document.addEventListener('DOMContentLoaded', () => apply(document.documentElement.dataset.theme));
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    apply(next);
    try { localStorage.setItem('finance-theme', next); } catch { /* Keep the current session usable. */ }
  });
})();
