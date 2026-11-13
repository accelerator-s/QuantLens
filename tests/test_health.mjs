import test from 'node:test';
import assert from 'node:assert/strict';
import { createRestartDetector } from '../src/webui/frontend/core/health.js';

const health = id => ({server: {instance_id: id}});

test('first connection and ordinary polling do not reload the frontend', () => {
  const restarted = createRestartDetector();
  assert.equal(restarted(health('first')), false);
  assert.equal(restarted(health('first')), false);
});

test('connection failures do not erase the previous instance', () => {
  const restarted = createRestartDetector();
  restarted(health('first'));
  assert.equal(restarted(null), false);
  assert.equal(restarted(health('second')), true);
});

test('a new backend instance reloads only once', () => {
  const restarted = createRestartDetector();
  restarted(health('first'));
  assert.equal(restarted(health('second')), true);
  assert.equal(restarted(health('second')), false);
  assert.equal(restarted(health('third')), false);
});
