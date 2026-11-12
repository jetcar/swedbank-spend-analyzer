import { getLanguage, setLanguage, translatePage } from './i18n.js';
import './demo-controls.js';

const language = document.getElementById('language');
language.value = getLanguage();
language.addEventListener('change', () => {
  setLanguage(language.value);
  translatePage();
});
document.getElementById('return-url').value = new URLSearchParams(location.search).get('return_url') || '/';
translatePage();
