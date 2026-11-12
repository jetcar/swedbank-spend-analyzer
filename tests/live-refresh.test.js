import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { startLiveRefresh } from '../app/static/live-refresh.js';

let source;
let stop;
class Events {
  constructor() { source = this; }
  addEventListener(name, callback) { this[name] = callback; }
  close = vi.fn();
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => { stop?.(); vi.useRealTimers(); });
function start(options) {
  stop = startLiveRefresh({ EventSourceClass: Events, ...options });
}

test('coalesces a burst of MCP updates into one reload', async () => {
  const reload = vi.fn().mockResolvedValue();
  start({ reload });
  for (let i = 0; i < 150; i++) source.changed();
  await vi.advanceTimersByTimeAsync(300);
  expect(reload).toHaveBeenCalledTimes(1);
});

test('a change during a request triggers another reload without overlap', async () => {
  let finish;
  const reload = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue();
  start({ reload });
  source.changed();
  await vi.advanceTimersByTimeAsync(300);
  source.changed();
  await vi.advanceTimersByTimeAsync(300);
  expect(reload).toHaveBeenCalledTimes(1);
  finish();
  await vi.advanceTimersByTimeAsync(1000);
  expect(reload).toHaveBeenCalledTimes(2);
});

test('retries failed refreshes without needing another signal', async () => {
  const reload = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue();
  const onError = vi.fn();
  start({ reload, onError });
  source.changed();
  await vi.advanceTimersByTimeAsync(1300);
  expect(reload).toHaveBeenCalledTimes(2);
  expect(onError).toHaveBeenCalledTimes(1);
});

test('defers updates while editing and refreshes after reconnection', async () => {
  let editing = true;
  const reload = vi.fn().mockResolvedValue();
  start({ reload, canRefresh: () => !editing });
  source.changed();
  await vi.advanceTimersByTimeAsync(600);
  expect(reload).not.toHaveBeenCalled();
  editing = false;
  await vi.advanceTimersByTimeAsync(300);
  source.changed(); // initial server event on a reconnected stream
  await vi.advanceTimersByTimeAsync(300);
  expect(reload).toHaveBeenCalledTimes(2);
});

test('expired demo stops pending refreshes and notifies once', async () => {
  const reload = vi.fn(), onExpired = vi.fn();
  start({ reload, onExpired });
  source.changed();
  source.expired();
  source.expired();
  await vi.advanceTimersByTimeAsync(5000);
  expect(reload).not.toHaveBeenCalled();
  expect(source.close).toHaveBeenCalledOnce();
  expect(onExpired).toHaveBeenCalledOnce();
});

test('stopping an in-flight failed refresh does not report or retry it', async () => {
  let reject;
  const reload = vi.fn(() => new Promise((_resolve, no) => { reject = no; }));
  const onError = vi.fn();
  start({ reload, onError });
  source.changed();
  await vi.advanceTimersByTimeAsync(300);
  stop();
  reject(new Error('offline'));
  await vi.advanceTimersByTimeAsync(5000);
  expect(onError).not.toHaveBeenCalled();
  expect(reload).toHaveBeenCalledOnce();
});
