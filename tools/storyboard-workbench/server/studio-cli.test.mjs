import assert from 'node:assert/strict';
import test from 'node:test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir, mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { invokeStudioCli, recoverContextHints } from './studio-cli.mjs';
import { createStudioReader } from './studio-library.mjs';
import { digest } from './storage.mjs';
import { requestedStudioRoute } from './studio-routes.mjs';

const codeRoot = fileURLToPath(new URL('..', import.meta.url));
const testBase = path.join(codeRoot, '.local', 'studio-tests');
const result = (patch = {}) => ({ reply: '已准备本轮候选，请选择。', status: 'waiting_user', episode: null, session_summary: '等待用户选择', rules_read: [], rules_applied: [], memory_update: [], decisions: [], artifacts: [], file_changes: [], next_actions: [], warnings: [], context_requests: [], ...patch });

async function fixture(t) {
  await mkdir(testBase, { recursive: true });
  const root = await mkdtemp(path.join(testBase, 'cli-context-'));
  t.after(async () => { assert.ok(path.resolve(root).startsWith(path.resolve(testBase) + path.sep)); await rm(root, { recursive: true, force: true }); });
  const put = async (rel, content) => { const file = path.join(root, rel); await mkdir(path.dirname(file), { recursive: true }); await writeFile(file, content, 'utf8'); };
  const instruction = '# 受控规则\n' + '这里是一段足够长的私有测试资料，用于验证后台提供完整内容和禁止公开原文。'.repeat(30);
  await put('CLAUDE.md', instruction);
  await put('对标原稿/1-当前原稿.md', '甲：本轮选定的原稿。');
  await put('对标原稿/2-其他原稿.md', '不属于本轮输入。');
  await put('爆款结构库/流量-当前卡.md', '# 当前结构卡\n必须保留的资料标记：card-nonce-1789');
  await put('stories/2-当前故事/选题.md', '# 当前故事的选题');
  await put('stories/暂时归档/1-旧故事/二创稿.md', '# 归档稿件的对照资料');
  const schema = JSON.parse(await readFile(path.join(codeRoot, 'codex-studio-schema.json'), 'utf8'));
  return { root, instruction, schema };
}

test('explicit tier choice in the user message overrides the benchmark picker without interpreting questions or quoted text', () => {
  const session = { skillId: 'xuanti', routeId: 'benchmark-tier-1' };
  for (const message of ['按本轮选定的对标资料，先给我选题候选。2档', '二档', '这次，改用第二档。', '选题候选\n２档']) assert.equal(requestedStudioRoute(session, message), 'benchmark-tier-2');
  for (const message of ['二档是什么意思？', '不要用二档', '原稿说“二档”', '先比较一档和二档', '2档？']) assert.equal(requestedStudioRoute(session, message), 'benchmark-tier-1');
  assert.equal(requestedStudioRoute({ skillId: 'erchuang', routeId: 'draft-review' }, '2档'), 'draft-review');
});

test('a selected benchmark and a direct Chinese tier request correct legacy original-topic sessions', () => {
  const original = { skillId: 'xuanti', routeId: 'original-commerce' };
  for (const selection of [{ source: { id: 'imported-source' } }, { reference: { path: '对标原稿/44-样例.md' } }]) {
    const session = { ...original, ...selection };
    for (const message of ['对标选题法 二档', '对标选题二档', '按对标选题法第二档。', '二档', '二档，现代家庭，产品用甘草金桔。', '对标选题法，二档，先给我候选']) assert.equal(requestedStudioRoute(session, message), 'benchmark-tier-2');
    assert.equal(requestedStudioRoute(session, '给我选题候选'), 'benchmark-tier-1');
    for (const message of ['不要按对标选题法二档', '“对标选题法 二档”', '原稿说“现代家庭，二档，先给候选”', '对标选题法二档是什么意思？']) assert.equal(requestedStudioRoute(session, message), 'benchmark-tier-1');
  }
  assert.equal(requestedStudioRoute(original, '对标选题法 二档'), 'benchmark-tier-2');
  for (const message of ['二档是什么意思？', '不要用对标选题法二档', '先比较一档和二档']) assert.equal(requestedStudioRoute(original, message), original.routeId);
});

test('CLI topic rounds receive real source text and can request selected structure data through the backend', async (t) => {
  const f = await fixture(t); let rounds = 0;
  const preflight = { skillId: 'xuanti', routeId: 'benchmark-tier-2', episode: null, reference: { path: '对标原稿/1-当前原稿.md' }, manifests: [{ path: 'CLAUDE.md', exists: true, hash: digest(f.instruction) }, { path: '对标原稿/1-当前原稿.md', exists: true, hash: digest('甲：本轮选定的原稿。') }] };
  const invoked = await invokeStudioCli({ projectRoot: f.root, runtimeDirectory: path.join(f.root, 'runtime'), preflight, session: { id: 'test-session', skillId: 'xuanti' }, provider: { adapter: 'codex_cli', model: 'chosen-model' }, prompt: '二档，先给候选，不落盘。', schema: f.schema,
    runTurn: async ({ prompt, model, schemaPath }) => {
      rounds += 1;
      assert.equal(model, 'chosen-model'); assert.ok(prompt.includes(f.instruction)); assert.ok(prompt.includes('甲：本轮选定的原稿。'));
      assert.ok(JSON.parse(await readFile(schemaPath, 'utf8')).properties.context_requests);
      if (rounds === 2) assert.ok(prompt.includes('card-nonce-1789'));
      return { text: JSON.stringify(rounds === 1 ? result({ status: 'needs_context', context_requests: [{ operation: 'read', path: '爆款结构库/流量-当前卡.md' }] }) : result({ rules_read: ['CLAUDE.md', '对标原稿/1-当前原稿.md', '爆款结构库/流量-当前卡.md'] })), output: 'private execution record', actualModel: model, usage: { usageAvailable: true, inputTokens: 10, outputTokens: 5 } };
    },
  });
  assert.equal(rounds, 2); assert.equal(invoked.mediatedFiles, true);
  assert.equal(invoked.reader.delivered.size, 3); assert.equal(invoked.usage.inputTokens, 20);
  assert.deepEqual(invoked.parsed.file_changes, []); assert.equal(invoked.parsed.status, 'waiting_user');
});

test('topic reader excludes other original scripts and cannot scan the original inventory', async (t) => {
  const f = await fixture(t);
  const reader = createStudioReader({ projectRoot: f.root, skillId: 'xuanti', referencePath: '对标原稿/1-当前原稿.md' });
  assert.ok((await reader.read('对标原稿/1-当前原稿.md')).content);
  await assert.rejects(reader.read('对标原稿/2-其他原稿.md'), /范围/);
  await assert.rejects(reader.list('对标原稿'), /不扫描原稿库存/);
  assert.ok((await reader.read('stories/2-当前故事/选题.md')).content);
  assert.ok((await reader.read('stories/暂时归档/1-旧故事/二创稿.md')).content);
  assert.equal((await reader.list('stories/暂时归档')).entries.length, 1);
  await assert.rejects(reader.read('stories/2-当前故事/认证配置.json'), /范围/);
  await assert.rejects(reader.list('归档'), /范围/);
});

test('a missing allowed file is reported to the model so it can locate the archived version', async (t) => {
  const f = await fixture(t); let rounds = 0;
  const invoked = await invokeStudioCli({ projectRoot: f.root, runtimeDirectory: path.join(f.root, 'runtime'), preflight: { skillId: 'xuanti', manifests: [] }, session: { id: 'test' }, provider: { adapter: 'codex_cli' }, prompt: '检查旧故事后再给候选。', schema: f.schema, runTurn: async ({ prompt }) => {
    rounds += 1;
    if (rounds === 2) { assert.ok(prompt.includes('not_found')); assert.ok(prompt.includes('stories/暂时归档/1-旧故事')); }
    return { text: JSON.stringify(rounds === 1 ? result({ status: 'needs_context', context_requests: [{ operation: 'read', path: 'stories/1-旧故事/选题.md' }, { operation: 'list', path: 'stories/暂时归档' }] }) : result()), output: 'private' };
  } });
  assert.equal(rounds, 2); assert.equal(invoked.parsed.status, 'waiting_user');
});

test('only a matching failed attempt can contribute read hints, and source bodies are fetched again', async (t) => {
  const f = await fixture(t);
  const manifests = [{ path: 'CLAUDE.md', hash: digest(f.instruction), exists: true }];
  const session = { skillId: 'xuanti', routeId: 'benchmark-tier-2', providerId: 'codex-cli', messages: [{ role: 'user', content: '先给候选。2档' }], lastPreflight: { rules: manifests } };
  const job = { skillId: 'xuanti', routeId: session.routeId, providerId: session.providerId, status: 'failed', output: JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: JSON.stringify(result({ status: 'needs_context', context_requests: [{ operation: 'read', path: '爆款结构库/流量-当前卡.md' }] })) } }) };
  const hints = recoverContextHints(job, session, '先给候选。2档', manifests);
  assert.equal(hints.length, 1);
  assert.deepEqual(recoverContextHints({ ...job, status: 'running' }, session, '先给候选。2档', manifests), []);
  assert.deepEqual(recoverContextHints(job, session, '换一个任务', manifests), []);
  assert.deepEqual(recoverContextHints(job, session, '先给候选。2档', [{ ...manifests[0], hash: 'changed' }]), []);
  await invokeStudioCli({ projectRoot: f.root, runtimeDirectory: path.join(f.root, 'runtime'), preflight: { skillId: 'xuanti', manifests, contextHints: hints }, session, provider: { adapter: 'codex_cli' }, prompt: '先给候选。2档', schema: f.schema, runTurn: async ({ prompt }) => { assert.ok(prompt.includes('card-nonce-1789')); return { text: JSON.stringify(result()), output: 'private' }; } });
});

test('CLI context transport does not release copied private instruction text', async (t) => {
  const f = await fixture(t);
  await assert.rejects(invokeStudioCli({ projectRoot: f.root, runtimeDirectory: path.join(f.root, 'runtime'), preflight: { skillId: 'xuanti', manifests: [{ path: 'CLAUDE.md', exists: true, hash: digest(f.instruction) }] }, session: { id: 'test' }, provider: { adapter: 'codex_cli' }, prompt: '只讨论本轮作品。', schema: f.schema, runTurn: async () => ({ text: JSON.stringify(result({ reply: f.instruction })), output: 'private', usage: null }) }), /内部规范原文/);
});
