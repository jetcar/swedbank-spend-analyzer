import { expect, test, vi } from 'vitest';
import { createDashboardLoader, createDashboardState } from '../app/static/dashboard-state.js';
import { ApiError, createJsonClient } from '../app/static/api-client.js';

function pending() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test.each(['resolve', 'reject'])('an old load cannot overwrite or report errors after a newer month (%s)', async outcome => {
  const state = createDashboardState(new Date(2026, 10, 1));
  const oldCategories = pending();
  const fresh = [{ id: 9 }];
  const onLoaded = vi.fn();
  const fetchJson = vi.fn(url => url.includes('categories') && url.includes('2026-11')
    ? oldCategories.promise : Promise.resolve(url.includes('categories') ? fresh : []));
  const load = createDashboardLoader({ state, fetchJson, getLanguage: () => 'en', onLoaded });
  const older = load();
  state.selectedMonth = '2026-12';
  expect(await load()).toBe(true);
  oldCategories[outcome](outcome === 'reject' ? new Error('old failure') : [{ id: 1 }]);
  expect(await older).toBe(false);
  expect(state.categories).toBe(fresh);
  expect(onLoaded).toHaveBeenCalledOnce();
});

test('a failed current load keeps the previous complete snapshot', async () => {
  const state = createDashboardState();
  const previous = state.accounts = [{ id: 1 }];
  const onLoaded = vi.fn();
  const load = createDashboardLoader({ state, getLanguage: () => 'ru', onLoaded,
    fetchJson: url => url.includes('categories') ? Promise.reject(new Error('offline')) : Promise.resolve([{ id: 2 }]),
  });
  await expect(load()).rejects.toThrow('offline');
  expect(state.accounts).toBe(previous);
  expect(onLoaded).not.toHaveBeenCalled();
});

test('plan is loaded for the selected month and language as part of the complete snapshot', async () => {
  const state = createDashboardState(new Date(2026, 10, 1));
  const plan = { month: '2026-11', is_current_month: true, totals: [], categories: [], items: [] };
  const fetchJson = vi.fn(url => Promise.resolve(url.startsWith('/api/plan?') ? plan : []));
  const load = createDashboardLoader({ state, fetchJson, getLanguage: () => 'et', onLoaded: vi.fn() });
  await load();
  expect(fetchJson).toHaveBeenCalledWith('/api/plan?month=2026-11&language=et');
  expect(state.plan).toBe(plan);
});

test('plan waits for category month initialization to avoid duplicate category copies', async () => {
  const categories = pending();
  const state = createDashboardState(new Date(2026, 10, 1));
  const fetchJson = vi.fn(url => url.startsWith('/api/categories?') ? categories.promise : Promise.resolve([]));
  const load = createDashboardLoader({ state, fetchJson, getLanguage: () => 'ru', onLoaded: vi.fn() });
  const loading = load();
  expect(fetchJson.mock.calls.some(([url]) => url.startsWith('/api/plan?'))).toBe(false);
  categories.resolve([]);
  await loading;
  expect(fetchJson).toHaveBeenCalledWith('/api/plan?month=2026-11&language=ru');
});

test('the API client preserves server status and reports validation errors once', async () => {
  const onError = vi.fn();
  const client = createJsonClient({ onError, fetchImpl: vi.fn().mockResolvedValue({
    ok: false, status: 409, json: async () => ({ detail: 'Category already exists' }),
  }) });
  await expect(client('/api/categories')).rejects.toMatchObject({ name: 'ApiError', status: 409, message: 'Category already exists' });
  expect(onError).toHaveBeenCalledOnce();
  expect(onError.mock.calls[0][0]).toBeInstanceOf(ApiError);
});

test('the API client handles proxy HTML errors and successful responses', async () => {
  const fetchImpl = vi.fn().mockResolvedValueOnce({ ok: false, status: 502, json: async () => { throw new Error('HTML'); } })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ saved: true }) });
  const onSuccess = vi.fn();
  const client = createJsonClient({ fetchImpl, onSuccess });
  await expect(client('/api/accounts')).rejects.toThrow('HTTP 502');
  await expect(client('/api/accounts', { method: 'PATCH' })).resolves.toEqual({ saved: true });
  expect(onSuccess).toHaveBeenCalledOnce();
});
