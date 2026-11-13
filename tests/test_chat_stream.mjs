import test from 'node:test';
import assert from 'node:assert/strict';
import { createThinkingStream } from '../src/webui/frontend/components/chat-stream/thinking-stream.js';
import { requestChat } from '../src/webui/frontend/core/chat-request.js';

test('think tags split at every boundary; inline/fenced code remains literal', () => {
  const fixtures = [
    ['<think>分析原因</think>**回答**', '分析原因', '**回答**'],
    ['<think>外层<think>内层</think>继续</think>回答', '外层内层继续', '回答'],
    ['`<think>代码</think>`\n```xml\n<think>代码块</think>\n```\n<think>思考</think>答案', '思考', '`<think>代码</think>`\n```xml\n<think>代码块</think>\n```\n答案'],
    ['~~~xml\n<think>代码块</think>\n~~~\n<think >思考</think >答案', '思考', '~~~xml\n<think>代码块</think>\n~~~\n答案'],
    ['普通回答，不包含思考', '', '普通回答，不包含思考'],
    ['<think>尚在思考', '尚在思考', ''],
  ];
  for (const [source, reasoning, content] of fixtures) {
    for (let size = 1; size <= source.length; size++) {
      const result = { reasoning: '', content: '' };
      const parser = createThinkingStream((channel, text) => { result[channel] += text; });
      for (let i = 0; i < source.length; i += size) parser.feed(source.slice(i, i + size));
      parser.finish();
      assert.deepEqual(result, { reasoning, content }, `chunk=${size}, ${source}`);
    }
  }
});

test('chat request delivers real UTF-8 deltas before stream completion', async () => {
  const original = globalThis.fetch;
  const encoder = new TextEncoder();
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  globalThis.fetch = async () => new Response(body);
  const events = [];
  const send = value => {
    // Deliberately split inside Chinese UTF-8 characters and JSON lines.
    const bytes = encoder.encode(JSON.stringify(value) + '\n');
    for (let i = 0; i < bytes.length; i += 2) controller.enqueue(bytes.slice(i, i + 2));
  };
  try {
    const pending = requestChat('/api/factors/chat', {}, { onDelta: event => events.push(event) });
    send({ type: 'start', model: 'test-model' });
    send({ type: 'delta', channel: 'reasoning', text: '先思考原因' });
    for (let i = 0; i < 100 && !events.length; i++) await new Promise(resolve => setImmediate(resolve));
    assert.equal(events[0]?.text, '先思考原因');
    send({ type: 'delta', channel: 'content', text: '**中文回答**' });
    send({ type: 'done' });
    assert.deepEqual(await pending, { reply: '**中文回答**', reasoning: '先思考原因', model: 'test-model' });
  } finally { globalThis.fetch = original; }
});

test('broken stream and provider errors cannot be mistaken for a completed reply', async () => {
  const original = globalThis.fetch;
  try {
    for (const [body, pattern] of [
      ['{"type":"delta","channel":"content","text":"partial"}\n', /意外断开/],
      ['{"type":"error","message":"模型中断"}\n', /模型中断/],
    ]) {
      globalThis.fetch = async () => new Response(body);
      await assert.rejects(requestChat('/api/factors/chat', {}), pattern);
    }
  } finally { globalThis.fetch = original; }
});
