// Keep mounted views and preloaded resources for this browser session only.
export function createPageCache(now = () => Date.now()) {
  const loaded = new Map();
  const dirty = new Set();
  return {
    mark(id) { loaded.set(id, now()); dirty.delete(id); },
    invalidate(id) { if (id) dirty.add(id); else for (const key of loaded.keys()) dirty.add(key); },
    needsRefresh(id) {
      const ttl = ['system', 'data', 'admin'].includes(id) ? 30_000 : 300_000;
      return !loaded.has(id) || dirty.has(id) || now() - loaded.get(id) >= ttl;
    },
  };
}

const prefetched = new Map();
const inflight = new Map();
const tasks = new Map();
let generation = 0;

export function invalidateRequests() {
  generation += 1;
  prefetched.clear();
  inflight.clear();
}

function changed() {
  invalidateRequests();
  window.dispatchEvent(new Event('quantlens:changed'));
}

export async function fetchApi(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  if (method !== 'GET') {
    const response = await fetch(path, options);
    if (response.ok) {
      const data = await response.clone().json().catch(() => null);
      if (data?.task_id) tasks.set(data.task_id, 'running');
      if (!data?.requires_confirm) changed();
    }
    return response;
  }
  const entry = prefetched.get(path);
  prefetched.delete(path);
  if (entry && entry.expires > Date.now()) return entry.response.clone();
  if (!inflight.has(path)) {
    const promise = fetch(path, options).then(async response => {
      if (/^\/api\/(?:data\/sync\/[^/]+\/progress|factors\/chat\/research\/[^/]+\/progress|factor-evolution\/[^/]+)$/.test(path) && response.ok) {
        const data = await response.clone().json();
        const task = path.startsWith('/api/factor-evolution/') ? data.task : data;
        if (!task?.task_id) return response;
        const previous = tasks.get(task.task_id);
        tasks.set(task.task_id, task.status);
        if (tasks.size > 100) tasks.delete(tasks.keys().next().value);
        if (task.status !== 'running' && previous === 'running') changed();
      }
      return response;
    }).finally(() => { if (inflight.get(path) === promise) inflight.delete(path); });
    inflight.set(path, promise);
  }
  return (await inflight.get(path)).clone();
}

export async function prefetchApi(path) {
  if (prefetched.get(path)?.expires > Date.now()) return;
  const version = generation;
  const response = await fetchApi(path);
  if (response.ok && version === generation) {
    prefetched.set(path, {response, expires: Date.now() + 30_000});
  }
}
