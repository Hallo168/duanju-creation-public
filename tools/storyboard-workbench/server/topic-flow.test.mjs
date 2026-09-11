import test from 'node:test';
import assert from 'node:assert/strict';
import { topicCandidateNumbers, topicMessageText } from '../app/pipeline/topic-flow.ts';

test('selection buttons require five actual uniquely numbered proposals', () => {
  const candidates = ['一', '二', '三', '四', '五'].map((number) => `## 【选题${number}】故事\n【故事梗概】真实候选内容`).join('\n\n');
  assert.deepEqual(topicCandidateNumbers(candidates), [1, 2, 3, 4, 5]);
  assert.deepEqual(topicCandidateNumbers('稍后会给你【选题1】至【选题5】。'), []);
  assert.deepEqual(topicCandidateNumbers('【选题1】只有一题'), []);
  assert.deepEqual(topicCandidateNumbers(candidates + '\n【选题一】重复'), []);
});

test('legacy stale error text explains that the old reply cannot be recovered', () => {
  assert.match(topicMessageText('本轮没有完成：本轮使用的资料已经更新，已阻止旧结果写入。旧版本与任务记录已保留，请核对后处理。'), /回复正文没有保存/);
  assert.equal(topicMessageText('正常选题内容'), '正常选题内容');
});
