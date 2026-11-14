import test from 'node:test';
import assert from 'node:assert/strict';
import {newestFirst} from '../src/webui/frontend/core/sort.js';

test('sort timestamps before paging, including timezone offsets and undated records', () => {
  const rows = [
    {id: 'old', created_at: '2026-11-14T10:00:00+08:00'},
    {id: 'missing'},
    {id: 'latest', created_at: '2026-11-14T03:00:00Z'},
    {id: 'fallback', created_at: 'invalid', updated_at: '2026-11-14T02:30:00Z'},
    {id: 'equal', created_at: '2026-11-14T02:00:00Z'},
  ];
  assert.deepEqual(newestFirst(rows).map(row => row.id), ['latest', 'fallback', 'old', 'equal', 'missing']);
  assert.equal(rows[0].id, 'old');
  const many = Array.from({length: 23}, (_,i) => ({created_at: new Date(Date.UTC(2026, 0, i+1)).toISOString(), i}));
  const sorted = newestFirst(many);
  assert.deepEqual(sorted.slice(0,10).map(row=>row.i), [22,21,20,19,18,17,16,15,14,13]);
  assert.deepEqual(sorted.slice(20).map(row=>row.i), [2,1,0]);
});

test('task creation determines experiment order, with fallback for historical records', () => {
  const rows = [
    {id:'old-task', task:{created_at:'2026-11-01T00:00:00Z'}, updated_at:'2026-11-15T00:00:00Z'},
    {id:'new-task', task:{created_at:'2026-11-14T00:00:00Z'}},
    {id:'historical', updated_at:'2026-11-13T00:00:00Z'},
  ];
  assert.deepEqual(newestFirst(rows,['task.created_at','updated_at']).map(row=>row.id), ['new-task','historical','old-task']);
});
