import test from 'node:test';
import assert from 'node:assert/strict';
import {createPageCache, fetchApi, prefetchApi, invalidateRequests} from '../src/webui/frontend/core/page-cache.js';

globalThis.window = new EventTarget();

test('mounted pages expire by type and explicit invalidation wins', () => {
  let time = 0;
  const cache = createPageCache(() => time);
  assert.equal(cache.needsRefresh('market'), true);
  cache.mark('market'); cache.mark('data');
  time = 29_999;
  assert.equal(cache.needsRefresh('market'), false);
  assert.equal(cache.needsRefresh('data'), false);
  time = 30_000;
  assert.equal(cache.needsRefresh('data'), true);
  cache.invalidate('market');
  assert.equal(cache.needsRefresh('market'), true);
  cache.mark('market');
  time += 300_000;
  assert.equal(cache.needsRefresh('market'), true);
});

test('simultaneous reads share one request with independent response bodies', async () => {
  invalidateRequests();
  let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json({items:[1]}); };
  const [a,b] = await Promise.all([fetchApi('/api/test'), fetchApi('/api/test')]);
  assert.deepEqual(await a.json(), await b.json());
  assert.equal(calls, 1);
});

test('prefetched response is consumed once and normal refresh gets new data', async () => {
  invalidateRequests();
  let calls = 0;
  globalThis.fetch = async () => Response.json({version:++calls});
  await prefetchApi('/api/read');
  await prefetchApi('/api/read');
  assert.equal((await (await fetchApi('/api/read')).json()).version, 1);
  assert.equal((await (await fetchApi('/api/read')).json()).version, 2);
});

test('failed preload is retried when opening the page', async () => {
  invalidateRequests();
  let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? new Response('', {status:500}) : Response.json({ok:true});
  await prefetchApi('/api/retry');
  assert.equal((await fetchApi('/api/retry')).ok, true);
  assert.equal(calls, 2);
});

test('confirmation previews keep cached pages; committed writes invalidate them', async () => {
  invalidateRequests();
  let changed = 0;
  const listener = () => changed++;
  window.addEventListener('quantlens:changed', listener);
  globalThis.fetch = async () => Response.json({requires_confirm:true});
  await fetchApi('/api/write', {method:'POST'});
  assert.equal(changed, 0);
  globalThis.fetch = async () => Response.json({ok:true});
  await fetchApi('/api/write', {method:'POST'});
  assert.equal(changed, 1);
  window.removeEventListener('quantlens:changed', listener);
});

test('preload finishing after a mutation cannot repopulate stale cache', async () => {
  invalidateRequests();
  let finish;
  let calls = 0;
  globalThis.fetch = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const preload = prefetchApi('/api/race');
  invalidateRequests();
  finish(Response.json({old:true}));
  await preload;
  globalThis.fetch = async () => { calls++; return Response.json({old:false}); };
  assert.equal((await (await fetchApi('/api/race')).json()).old, false);
  assert.equal(calls, 2);
});

test('task completion invalidates views once, even if it finishes before first poll', async () => {
  invalidateRequests();
  let changed = 0;
  const listener = () => changed++;
  window.addEventListener('quantlens:changed', listener);
  globalThis.fetch = async (path, options) => Response.json(options?.method === 'POST'
    ? {task_id:'cache-test'} : {task_id:'cache-test', status:'completed'});
  await fetchApi('/api/start', {method:'POST'});
  await fetchApi('/api/tasks/cache-test');
  await fetchApi('/api/tasks/cache-test');
  assert.equal(changed, 2);
  window.removeEventListener('quantlens:changed', listener);
});
