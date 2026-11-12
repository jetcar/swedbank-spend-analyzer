import { readFileSync } from 'node:fs';
import { afterEach, expect, test, vi } from 'vitest';
import { messages, setLanguage, t, formatMoney, formatDate, formatPercent, transactionCount } from '../app/static/i18n.js';

afterEach(() => { setLanguage('ru'); vi.unstubAllGlobals(); });

test('all interface keys have English and Estonian translations with matching parameters', () => {
  const app = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
  const html = ['index.html', 'login.html'].map(name => readFileSync(new URL(`../app/static/${name}`, import.meta.url), 'utf8')).join('\n');
  const keys = [...app.matchAll(/tr\('([^']+)'/g), ...html.matchAll(/data-i18n(?:-title|-placeholder|-aria-label)?="([^"]+)"/g)].map(match => match[1]);
  for (const key of keys) expect(messages, key).toHaveProperty(key);
  const parameters = text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
  for (const [key, translations] of Object.entries(messages)) {
    expect(translations).toHaveLength(2);
    for (const translation of translations) {
      expect(translation.trim().length, key).toBeGreaterThan(0);
      expect(parameters(translation), key).toEqual(parameters(key));
    }
  }
});

test('switches interface messages while leaving unknown user content untouched', () => {
  setLanguage('en');
  expect(t('Бюджет')).toBe('Budget');
  expect(t('Осталось {amount}', { amount: 'EUR 20' })).toBe('EUR 20 remaining');
  expect(t('Моя категория')).toBe('Моя категория');
  setLanguage('et');
  expect(t('Бюджет')).toBe('Eelarve');
  expect(t('Не удалось загрузить данные')).toBe('Andmete laadimine ebaõnnestus');
});

test('formats dates, money, percentages and counts in the selected locale', () => {
  setLanguage('en');
  expect(formatDate('2026-11-05T12:00:00Z')).toBe('05/11/2026');
  expect(formatMoney(1234.56)).toContain('1,234.56');
  expect(formatPercent(82.5)).toBe('82.5%');
  expect(transactionCount(1)).toBe('1 transaction');
  expect(transactionCount(2)).toBe('2 transactions');
  setLanguage('et');
  expect(formatMoney(1234.56)).toContain('234,56');
  expect(transactionCount(1)).toBe('1 tehing');
  expect(transactionCount(2)).toBe('2 tehingut');
  setLanguage('ru');
  expect(transactionCount(1)).toBe('1 операция');
  expect(transactionCount(2)).toBe('2 операции');
  expect(transactionCount(5)).toBe('5 операций');
});

test('preserves cents when formatting exact balance strings', () => {
  setLanguage('en');
  expect(formatMoney('9999999999999999.99')).toContain('9,999,999,999,999,999.99');
  expect(formatMoney('-12.34')).toMatch(/^-.*12\.34$/);
});

test('restores saved language and falls back safely for unsupported values', async () => {
  const storage = new Map();
  vi.stubGlobal('localStorage', { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) });
  setLanguage('et');
  vi.resetModules();
  const restored = await import('../app/static/i18n.js');
  expect(restored.getLanguage()).toBe('et');
  restored.setLanguage('fr');
  expect(restored.getLanguage()).toBe('ru');
});

test('works when browser storage is unavailable', async () => {
  vi.stubGlobal('localStorage', { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } });
  vi.resetModules();
  const isolated = await import('../app/static/i18n.js');
  expect(isolated.getLanguage()).toBe('ru');
  isolated.setLanguage('en');
  expect(isolated.t('Сохранить')).toBe('Save');
});
