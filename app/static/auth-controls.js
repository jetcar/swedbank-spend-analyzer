const signOut = document.getElementById('sign-out');
signOut.addEventListener('click', () => { location.assign('/auth/logout'); });
fetch('/auth/status', { cache: 'no-store' })
  .then(response => response.ok ? response.json() : null)
  .then(status => {
    signOut.hidden = !(status?.enabled && status.authenticated);
    const demo = status?.demo === true;
    document.getElementById('demo-entry').hidden = demo;
    document.getElementById('demo-notice').hidden = !demo;
    document.body.dataset.demo = String(demo);
    if (demo) {
      document.querySelectorAll('[data-connect] [data-i18n]').forEach(label => { label.dataset.i18n = 'Подключить sandbox'; });
      translatePage();
    }
  })
  .catch(() => { /* Keep the financial dashboard usable if the status request fails. */ });
import { translatePage } from './i18n.js';
import './demo-controls.js';
