import test from 'node:test';
import assert from 'node:assert/strict';
import { parseStudioJson, studioVisibleText, pendingScriptDraft } from './studio-results.mjs';

const result = { reply: '甲："回家了。"\n```markdown\n场景 {门口}\n```', status: 'completed', file_changes: [{ path: 'stories/1-测试/二创稿.md', content: '完整草稿' }], context_requests: [] };

test('compatible replies may contain a JSON fence, explanatory prose or a JSON-encoded string without changing any text', () => {
  const json = JSON.stringify(result);
  for (const value of [result, json, `\uFEFF${json}`, `已经完成，结果如下：\n${json}\n请查看。`, `说明\n\`\`\`JSON\n${json}\n\`\`\``, JSON.stringify(json)]) assert.deepEqual(parseStudioJson(value), result);
});

test('ambiguous, incomplete or invalid executable results never get guessed or repaired', () => {
  for (const value of [`${JSON.stringify(result)}\n${JSON.stringify(result)}`, JSON.stringify(result).slice(0,-5), JSON.stringify([result]), JSON.stringify({ ...result, file_changes: 'not-an-array' }), JSON.stringify({ ...result, context_requests: [{ operation: 'delete', path: 'a.md' }] }), '这次只是普通回复。']) assert.throws(() => parseStudioJson(value), { code: 'STUDIO_RESULT_FORMAT' });
});

test('prose and a complete reply field can be displayed without exposing malformed action fields or hidden reasoning', () => {
  assert.equal(studioVisibleText('这段开场需要更直接。'), '这段开场需要更直接。');
  assert.equal(studioVisibleText('<think>内部推理</think>实际回复'), '实际回复');
  assert.equal(studioVisibleText('{"reply":"已生成的候选", "file_changes": [INCOMPLETE'), '已生成的候选');
  assert.equal(studioVisibleText('{"rules_read":["private.md"],"file_changes": ['), '');
  assert.equal(studioVisibleText('<!DOCTYPE html><html>gateway error</html>'), '');
});

test('recovered drafts must belong to the selected episode and have exactly one matching script', () => {
  assert.equal(pendingScriptDraft({ ...result, episode: '1-测试' }, '1-测试').content, '完整草稿');
  assert.equal(pendingScriptDraft(result, '2-别的故事'), null);
  assert.equal(pendingScriptDraft({ ...result, episode: '2-别的故事' }, '1-测试'), null);
  assert.equal(pendingScriptDraft({ ...result, file_changes: [...result.file_changes, ...result.file_changes] }, '1-测试'), null);
});
