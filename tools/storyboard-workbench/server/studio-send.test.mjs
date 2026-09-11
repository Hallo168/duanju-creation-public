import test from 'node:test';
import assert from 'node:assert/strict';
import { sendStudioMessage } from '../app/pipeline/send-message.ts';

test('各创作路线发送后直接启动，不再等待核对或额外勾选', async () => {
  for (const skill of ['chat', 'xuanti', 'erchuang', 'zichan', 'fenjing', 'houqi']) {
    const job = { id: `job-${skill}`, skillId: skill, status: 'running' };
    let starts = 0;
    const result = await sendStudioMessage(async (route, options) => {
      const body = JSON.parse(options.body);
      if (route === '/api/studio/preflight') {
        assert.equal(body.message, '执行本轮要求');
        return { token: skill, confirmationKey: `scope-${skill}`, skill: { id: skill }, provider: { model: 'chosen-model' }, creditRisk: true, externalWriteRisk: true };
      }
      assert.equal(route, '/api/studio/run');
      assert.equal(body.token, skill);
      assert.equal(body.confirmationKey, `scope-${skill}`);
      assert.ok(body.confirmed && body.creditConfirmed && body.externalWriteConfirmed);
      starts++;
      return job;
    }, { sessionId: 'current-session', message: '执行本轮要求', model: 'chosen-model' });
    assert.deepEqual(result, job);
    assert.equal(starts, 1);
  }
});

test('发送时模型不一致不会启动，也不会自动重试', async () => {
  let calls = 0;
  await assert.rejects(sendStudioMessage(async () => {
    calls++;
    return { provider: { model: 'different-model' } };
  }, { sessionId: 'current-session', message: '本轮要求', model: 'chosen-model' }), /模型与本轮选择不一致/);
  assert.equal(calls, 1);
});

test('发送结果未知时保留错误，不重投任务', async () => {
  let starts = 0;
  await assert.rejects(sendStudioMessage(async (route) => {
    if (route === '/api/studio/preflight') return { token: 'one-use', confirmationKey: 'scope', provider: { model: 'chosen-model' } };
    starts++;
    throw new Error('connection interrupted');
  }, { sessionId: 'current-session', message: '本轮要求', model: 'chosen-model' }), /connection interrupted/);
  assert.equal(starts, 1);
});
