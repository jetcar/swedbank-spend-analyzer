// The server invalidates on every connection, recovering missed offline changes.
export function startLiveRefresh({ reload, canRefresh = () => true, onError = () => {}, onExpired = () => {}, EventSourceClass = EventSource }) {
  const source = new EventSourceClass('/api/events');
  let timer;
  let pending = false;
  let running = false;
  let stopped = false;
  function schedule(delay = 300) {
    if (!stopped && !timer) timer = setTimeout(flush, delay);
  }
  async function flush() {
    timer = undefined;
    if (stopped || !pending || running) return;
    if (!canRefresh()) { schedule(); return; }
    pending = false;
    running = true;
    try {
      await reload();
    } catch (error) {
      if (!stopped) {
        pending = true;
        onError(error);
      }
    } finally {
      running = false;
      if (pending) schedule(1000);
    }
  }
  source.addEventListener('changed', () => { pending = true; schedule(); });
  function stop() {
    if (stopped) return;
    stopped = true;
    clearTimeout(timer);
    source.close();
  }
  source.addEventListener('expired', () => {
    if (stopped) return;
    stop();
    onExpired();
  });
  return stop;
}
