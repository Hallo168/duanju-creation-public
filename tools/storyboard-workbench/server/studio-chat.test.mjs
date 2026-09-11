import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';
import { chatPrompt, cliChatError, invokeCodexChat, readCliEvents } from './studio-chat.mjs';

test('chat keeps conversation context and includes the new message once, without internal receipts', () => {
  const session = { messages: [{ role: 'user', content: '记住青山', receipt: { rulesRead: ['private-rule.md'] } }, { role: 'assistant', content: '记住了' }, { role: 'user', content: '刚才的词是什么？' }], pinnedMemory: [{ content: '中文交流' }] };
  const prompt = chatPrompt(session, '刚才的词是什么？');
  assert.equal(prompt.split('刚才的词是什么？').length, 2);
  assert.ok(prompt.includes('记住青山'));
  assert.ok(prompt.includes('中文交流'));
  assert.ok(!prompt.includes('private-rule.md'));
});

test('Codex JSON events yield only the final answer and actual usage', () => {
  const result = readCliEvents([
    JSON.stringify({ type: 'item.completed', item: { type: 'reasoning', text: 'internal reasoning' } }),
    JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: '你好，继续聊。' } }),
    JSON.stringify({ type: 'turn.completed', usage: { input_tokens: 123, cached_input_tokens: 50, output_tokens: 20 } }),
  ].join('\n'));
  assert.equal(result.reply, '你好，继续聊。');
  assert.equal(result.completed, true);
  assert.equal(result.usage.inputTokens, 123);
  assert.equal(result.usage.outputTokens, 20);
});

function fakeCli(lines, code = 0, stderr = '') {
  return (_executable, args, options) => {
    assert.equal(options.windowsHide, true);
    assert.ok(args.includes('read-only'));
    assert.ok(!args.includes('--ignore-user-config'));
    assert.ok(!args.includes('--ignore-rules'));
    const child = new EventEmitter();
    child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.stdin = new EventEmitter();
    child.stdin.end = () => queueMicrotask(() => {
      const bytes = Buffer.from(lines.join('\n') + '\n');
      // Deliberately split multibyte Chinese characters across stdout events.
      for (let i = 0; i < bytes.length; i += 2) child.stdout.emit('data', bytes.subarray(i, i + 2));
      child.stderr.emit('data', Buffer.from(stderr));
      child.emit('close', code);
    });
    child.kill = () => child.emit('close', -1);
    return child;
  };
}

test('chat adapter handles UTF-8 stream chunks and returns a displayable result without write actions', async () => {
  const result = await invokeCodexChat({ session: { messages: [] }, message: '你好', provider: { model: 'chosen-model' }, spawnImpl: fakeCli([
    JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: '你好，可以正常对话。' } }),
    JSON.stringify({ type: 'turn.completed', usage: { input_tokens: 10, output_tokens: 8 } }),
  ]) });
  const parsed = JSON.parse(result.text);
  assert.equal(parsed.reply, '你好，可以正常对话。');
  assert.deepEqual(parsed.file_changes, []);
  assert.deepEqual(parsed.rules_read, []);
  assert.equal(result.actualModel, 'chosen-model');
});

test('incomplete and failed turns are not treated as successful or retried', async () => {
  let calls = 0;
  const spawn = fakeCli([JSON.stringify({ type: 'turn.failed', error: { message: '401 Unauthorized: private authentication detail' } })]);
  await assert.rejects(invokeCodexChat({ session: {}, message: '你好', provider: {}, spawnImpl: (...args) => { calls += 1; return spawn(...args); } }), (error) => {
    assert.ok(error.message.includes('登录已失效'));
    assert.ok(!error.message.includes('private authentication'));
    return true;
  });
  assert.equal(calls, 1);
  await assert.rejects(invokeCodexChat({ session: {}, message: '你好', provider: {}, spawnImpl: fakeCli([JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: '未完成内容' } })]) }), /没有返回完整回复/);
  assert.ok(cliChatError('request timeout').includes('不会自动重发'));
});

test('interrupted streams are reported as a connection failure, while a recovered stream may still succeed', async () => {
  const warning = JSON.stringify({ type: 'error', message: 'Reconnecting... stream disconnected before completion: error decoding response body' });
  await assert.rejects(invokeCodexChat({ session: {}, message: '你好', provider: {}, spawnImpl: fakeCli([warning]) }), /网络连接中断/);
  const recovered = await invokeCodexChat({ session: {}, message: '你好', provider: {}, spawnImpl: fakeCli([warning, JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: '已恢复连接。' } }), JSON.stringify({ type: 'turn.completed', usage: { input_tokens: 1, output_tokens: 1 } })]) });
  assert.equal(JSON.parse(recovered.text).reply, '已恢复连接。');
});
