import assert from 'node:assert/strict';
import test from 'node:test';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { mkdir, mkdtemp, writeFile, readFile, rm, readdir } from 'node:fs/promises';
import { createModelService } from './model-providers.mjs';
import { createStudioLibrary, createStudioReader, decodeDocument } from './studio-library.mjs';
import { resolveStudioSelection, invokeStudioHttp, studioWriteAllowed, applyStudioChanges } from './studio-execution.mjs';
import { digest } from './storage.mjs';

const codeRoot = fileURLToPath(new URL('..', import.meta.url));
const testBase = path.join(codeRoot, '.local', 'studio-tests');
async function fixture(t) {
  await mkdir(testBase, { recursive: true });
  const root = await mkdtemp(path.join(testBase, 'run-'));
  const directory = path.join(root, 'tools', 'storyboard-workbench');
  await mkdir(directory, { recursive: true });
  t.after(async () => {
    const resolved = path.resolve(root);
    assert.ok(resolved.startsWith(path.resolve(testBase) + path.sep));
    await rm(resolved, { recursive: true, force: true });
  });
  const put = async (file, content) => { await mkdir(path.dirname(path.join(root, file)), { recursive: true }); await writeFile(path.join(root, file), content); };
  return { root, directory, put };
}
const apiProvider = { id: 'sample-api', label: '测试接口', adapter: 'openai_compatible_chat', enabled: true, model: 'default-a', baseUrl: 'https://example.test/v1/chat/completions', endpoint: '/chat/completions', modelsEndpoint: '/models', apiKey: 'test-only-secret', structuredOutputs: false };
const result = (patch = {}) => ({ reply: '本轮已完成。', status: 'completed', episode: null, session_summary: '完成本轮。', rules_read: [], rules_applied: [], memory_update: [], decisions: [], artifacts: [], file_changes: [], next_actions: [], warnings: [], context_requests: [], ...patch });

test('Key only stays on the server; unsaved model discovery and per-session selection use the right credentials', async (t) => {
  const f = await fixture(t); const requests = [];
  const models = createModelService({ projectRoot: f.root, directory: f.directory, legacyFile: path.join(f.root, 'missing.json'), fetcher: async (url, options) => { requests.push({ url, key: options.headers.Authorization }); return new Response(JSON.stringify({ data: [{ id: 'chosen-b' }, { id: 'default-a' }, { id: 'chosen-b' }] })); } });
  const preview = await models.previewModels(apiProvider);
  assert.deepEqual(preview.models, ['chosen-b', 'default-a']);
  assert.equal((await models.list()).providers.length, 0);
  const saved = await models.save({ provider: apiProvider });
  assert.equal(JSON.stringify(saved).includes(apiProvider.apiKey), false);
  assert.equal(saved.providers[0].baseUrl, 'https://example.test/v1');
  assert.equal(saved.providers[0].secretStored, true);
  const first = await resolveStudioSelection(models, { providerId: apiProvider.id, model: 'chosen-b' });
  assert.equal(first.provider.model, 'chosen-b');
  assert.equal((await models.resolve(apiProvider.id)).model, 'default-a');
  await models.save({ provider: { ...saved.providers[0], apiKey: '' } });
  assert.equal((await models.resolve(apiProvider.id)).apiKey, apiProvider.apiKey);
  await models.previewModels({ ...saved.providers[0], apiKey: 'replacement-test-key' });
  assert.equal(requests.at(-1).key, 'Bearer replacement-test-key');
  assert.equal(requests[0].url, 'https://example.test/v1/models');
  await models.save({ provider: { ...saved.providers[0], apiKey: 'changed-test-key' } });
  assert.notEqual((await resolveStudioSelection(models, { providerId: apiProvider.id, model: 'chosen-b' })).fingerprint, first.fingerprint);
});

test('TXT / MD imports retain original bytes, support Chinese encodings, and bind versions to their conversation', async (t) => {
  const f = await fixture(t); const library = createStudioLibrary({ projectRoot: f.root, directory: f.directory }); const sessionId = randomUUID();
  const bytes = Buffer.concat([Buffer.from([255, 254]), Buffer.from('母亲：回家吃饭。\r\n', 'utf16le')]);
  const source = await library.saveSource({ sessionId, filename: '原稿.TXT', base64: bytes.toString('base64') });
  assert.equal(source.text, '母亲：回家吃饭。\r\n');
  assert.equal(source.encoding, 'UTF-16 LE');
  const sourceFolder = path.dirname(path.dirname(path.join(f.root, source.path)));
  assert.deepEqual(await readFile(path.join(sourceFolder, 'original.txt')), bytes);
  const edited = await library.saveSource({ sessionId, sourceId: source.id, expectedHash: source.hash, title: '修改后的标题', text: '# 新输入\n母亲：回来吧。\n' });
  assert.notEqual(edited.hash, source.hash);
  assert.equal(await readFile(path.join(f.root, source.path), 'utf8'), source.text);
  await assert.rejects(library.saveSource({ sessionId, sourceId: source.id, expectedHash: source.hash, text: 'stale' }), /已更新/);
  await assert.rejects(library.getSource(source.id, randomUUID()), /本会话/);
  await assert.rejects(library.saveSource({ sessionId, filename: '../file.md', base64: 'eA==' }), /文件名/);
  assert.equal(decodeDocument(Buffer.from([196, 227, 186, 195])).text, '你好');
  assert.equal(decodeDocument(Buffer.concat([Buffer.from([254, 255]), Buffer.from('中文', 'utf16le').swap16()])).text, '中文');
  assert.throws(() => decodeDocument(Buffer.from([0, 1, 2])), /纯文本/);
});

test('library groups preserve existing categories; private definitions and traversal cannot be previewed', async (t) => {
  const f = await fixture(t);
  await f.put('对标原稿/1-样例.md', '# 原稿'); await f.put('爆款结构库.md', '# 索引');
  await f.put('爆款结构库/流量-样例.md', '# 数据卡'); await f.put('爆款结构库/框架-定义.md', '# 私有方法');
  await f.put('knowledge/手法库.md', '# 混合规范与经验'); await f.put('产品图/样品/产品规格.md', '# 产品事实');
  const library = createStudioLibrary({ projectRoot: f.root, directory: f.directory });
  const { groups } = await library.catalog();
  assert.equal(groups.length, 8);
  assert.equal(groups.find((group) => group.id === 'structures').entries.find((item) => item.name === '流量-样例').reusable, true);
  assert.equal((await library.document('对标原稿/1-样例.md')).content, '# 原稿');
  await assert.rejects(library.document('爆款结构库/框架-定义.md'), /后台规范/);
  await assert.rejects(library.document('knowledge/手法库.md'), /后台规范/);
  await assert.rejects(library.document('../outside.md'), /路径/);
  await assert.rejects(library.reference('knowledge/手法库.md'), /原稿或结构卡/);
});

test('HTTP execution can request real dependent files and keeps the chosen model across rounds', async (t) => {
  const f = await fixture(t); await f.put('CLAUDE.md', '# 测试规范'); await f.put('带货框架库/样例.md', '本次真实依赖内容');
  const baseSchema = JSON.parse(await readFile(path.join(codeRoot, 'codex-studio-schema.json'), 'utf8'));
  const preflight = { skillId: 'duibiao', episode: null, manifests: [{ path: 'CLAUDE.md', exists: true, required: true, hash: digest('# 测试规范') }] };
  const payloads = [];
  const invoked = await invokeStudioHttp({ provider: { ...apiProvider, baseUrl: 'https://example.test/v1', model: 'chosen-b' }, prompt: '只完成当前拆解。', schema: baseSchema, projectRoot: f.root, preflight, session: { id: randomUUID(), skillId: 'duibiao' }, fetcher: async (_url, options) => {
    const body = JSON.parse(options.body); payloads.push(body);
    const reply = payloads.length === 1 ? result({ status: 'needs_context', context_requests: [{ operation: 'read', path: '带货框架库/样例.md' }] }) : result({ rules_read: ['CLAUDE.md', '带货框架库/样例.md'] });
    return new Response(JSON.stringify({ model: 'chosen-b', choices: [{ message: { content: JSON.stringify(reply) } }], usage: { prompt_tokens: 10, completion_tokens: 5 } }));
  } });
  assert.equal(payloads.length, 2); assert.ok(payloads.every((body) => body.model === 'chosen-b'));
  assert.ok(payloads[1].messages.at(-1).content.includes('本次真实依赖内容'));
  assert.equal(invoked.reader.observed.get('带货框架库/样例.md'), digest('本次真实依赖内容'));
  assert.equal(invoked.usage.inputTokens, 20);
  await assert.rejects(invoked.reader.read('工具/api配置.json'), /允许读取/);
  await f.put('带货框架库/样例.md', '外部更新'); await assert.rejects(invoked.reader.assertFresh(), /已经更新/);
});

test('Responses uses the selected model with no stale response ID, and transport errors are never automatically retried', async (t) => {
  const f = await fixture(t); const schema = JSON.parse(await readFile(path.join(codeRoot, 'codex-studio-schema.json'), 'utf8')); let requests = 0;
  const args = { provider: { ...apiProvider, baseUrl: 'https://example.test/v1', adapter: 'openai_responses', endpoint: '/responses', model: 'chosen-c', structuredOutputs: true }, schema, prompt: '讨论本轮。', projectRoot: f.root, preflight: { skillId: 'duibiao', manifests: [] }, session: { id: randomUUID(), remoteState: { 'sample-api': { responseId: 'old-model-response' } } } };
  const done = await invokeStudioHttp({ ...args, fetcher: async (_url, options) => { requests += 1; const body = JSON.parse(options.body); assert.equal(body.model, 'chosen-c'); assert.equal(body.store, false); assert.equal(body.previous_response_id, undefined); assert.equal(body.text.format.type, 'json_schema'); return new Response(JSON.stringify({ output_text: JSON.stringify(result()) })); } });
  assert.equal(done.usage.usageAvailable, false);
  await assert.rejects(invokeStudioHttp({ ...args, fetcher: async () => { requests += 1; throw new Error('network interruption'); } }), /没有自动重发/);
  assert.equal(requests, 2);
});

test('a source changed or removed during generation preserves the actual reply and identifies the stale source', async (t) => {
  const f = await fixture(t);
  const schema = JSON.parse(await readFile(path.join(codeRoot, 'codex-studio-schema.json'), 'utf8'));
  for (const removed of [false, true]) {
    await f.put('CLAUDE.md', '原版资料');
    let calls = 0;
    await assert.rejects(invokeStudioHttp({ provider: apiProvider, schema, prompt: '给五个选题候选，不写盘。', projectRoot: f.root,
      preflight: { skillId: 'xuanti', manifests: [{ path: 'CLAUDE.md', exists: true, hash: digest('原版资料') }] }, session: { id: randomUUID() },
      fetcher: async () => {
        calls++;
        if (removed) await rm(path.join(f.root, 'CLAUDE.md'));
        else await f.put('CLAUDE.md', '用户修改后的资料');
        return new Response(JSON.stringify({ model: 'chosen-model', choices: [{ message: { content: JSON.stringify(result({ status: 'waiting_user', reply: '这里是已经生成的真实候选。' })) } }], usage: { prompt_tokens: 12, completion_tokens: 8 } }));
      },
    }), (error) => {
      assert.equal(error.code, 'STUDIO_SOURCES_CHANGED');
      assert.deepEqual(error.changedPaths, ['CLAUDE.md']);
      assert.equal(error.studioResult.reply, '这里是已经生成的真实候选。');
      assert.equal(error.studioUsage.outputTokens, 8);
      assert.equal(error.actualModel, 'chosen-model');
      return true;
    });
    assert.equal(calls, 1, '资料变化不能触发自动重试');
  }
});

test('private instruction copies are rejected before delivery', async (t) => {
  const f = await fixture(t); const secret = Array.from({ length: 60 }, (_, i) => `内部测试句${i}仅用于机密输出校验。`).join(''); await f.put('CLAUDE.md', secret);
  const schema = JSON.parse(await readFile(path.join(codeRoot, 'codex-studio-schema.json'), 'utf8'));
  await assert.rejects(invokeStudioHttp({ provider: { ...apiProvider, baseUrl: 'https://example.test/v1' }, prompt: '讨论作品', schema, projectRoot: f.root, preflight: { skillId: 'duibiao', manifests: [{ path: 'CLAUDE.md', exists: true, hash: digest(secret) }] }, session: { id: randomUUID() }, fetcher: async () => new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(result({ reply: secret.slice(0, 400) })) } }] })) }), /内部规范原文/);
});

test('format failures preserve the raw response before parsing and expose only safe reader-facing text', async (t) => {
  const f = await fixture(t);
  const schema = JSON.parse(await readFile(path.join(codeRoot, 'codex-studio-schema.json'), 'utf8'));
  const instruction = '本次私有测试规范内容不允许作为公开回复。'.repeat(30);
  await f.put('CLAUDE.md', instruction);
  const args = { provider: apiProvider, schema, prompt: '本轮只讨论', projectRoot: f.root, preflight: { skillId: 'xuanti', manifests: [{ path: 'CLAUDE.md', exists: true, hash: digest(instruction) }] }, session: { id: randomUUID() } };
  for (const [reply, visible, finishReason] of [
    ['实际返回的普通文字回复。', '实际返回的普通文字回复。', 'stop'],
    ['{"reply":"已完成的可读句子","file_changes":[', '已完成的可读句子', 'stop'],
    [JSON.stringify(result()), result().reply, 'length'],
    [instruction, undefined, 'stop'],
    [apiProvider.apiKey, undefined, 'stop'],
  ]) {
    const records = [];
    await assert.rejects(invokeStudioHttp({ ...args, onResponse: async (record) => { records.push(record); }, fetcher: async () => new Response(JSON.stringify({ model: 'actual-model', choices: [{ finish_reason: finishReason, message: { content: reply } }], usage: { prompt_tokens: 20, completion_tokens: 10 } })) }), (error) => {
      assert.equal(error.studioReply, visible);
      if (finishReason === 'length') assert.equal(error.code, 'STUDIO_OUTPUT_INCOMPLETE');
      return true;
    });
    assert.equal(records.length, 1);
    assert.equal(records[0].usage.requests, 1);
    assert.equal(records[0].actualModel, 'actual-model');
    assert.equal(records[0].text.includes(apiProvider.apiKey), false);
    if (reply !== apiProvider.apiKey) assert.equal(records[0].text, reply);
  }
});

test('script drafting and review run separately with the same selected model, and drafts remain internal until review', async (t) => {
  const f = await fixture(t);
  await f.put('CLAUDE.md', '# 规则'); await f.put('knowledge/5-审稿检查清单.md', 'REVIEW_PHASE_ONLY');
  const schema = JSON.parse(await readFile(path.join(codeRoot, 'codex-studio-schema.json'), 'utf8'));
  const payloads = []; let preserved = null;
  const preflight = { skillId: 'erchuang', routeId: 'draft-review', episode: '1-稿件', manifests: [{ path: 'CLAUDE.md', exists: true, hash: digest('# 规则') }, { path: 'knowledge/5-审稿检查清单.md', exists: true, hash: digest('REVIEW_PHASE_ONLY') }] };
  const invoked = await invokeStudioHttp({ provider: { ...apiProvider, baseUrl: 'https://example.test/v1', model: 'chosen-c' }, schema, prompt: '完成本集文案。', projectRoot: f.root, preflight, session: { id: randomUUID() }, onDraft: async (draft) => { preserved = draft; }, fetcher: async (_url, options) => {
    const body = JSON.parse(options.body); payloads.push(body);
    if (payloads.length === 1) assert.equal(JSON.stringify(body).includes('REVIEW_PHASE_ONLY'), false);
    else { assert.ok(JSON.stringify(body).includes('REVIEW_PHASE_ONLY')); assert.ok(preserved); }
    return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(result({ episode: '1-稿件', rules_read: ['CLAUDE.md', 'knowledge/5-审稿检查清单.md'], file_changes: [{ path: 'stories/1-稿件/二创稿.md', content: payloads.length === 1 ? '第一稿' : '独立审稿后的稿件' }] })) } }] }));
  } });
  assert.equal(payloads.length, 2); assert.ok(payloads.every((body) => body.model === 'chosen-c'));
  assert.equal(invoked.parsed.file_changes[0].content, '独立审稿后的稿件');
  assert.equal(invoked.pendingDraft, null);
  await assert.rejects(readFile(path.join(f.root, 'stories/1-稿件/二创稿.md')), /ENOENT/);
});

test('new cards and indexes are saved together with backups; stale versions cannot overwrite user changes', async (t) => {
  const f = await fixture(t); await f.put('爆款结构库.md', '# 旧索引');
  const reader = createStudioReader({ projectRoot: f.root, skillId: 'duibiao' }); await reader.read('爆款结构库.md');
  const preflight = { skillId: 'duibiao', episode: null, manifests: [] }; const route = { mode: 'guarded_write' }; const backupRoot = path.join(f.root, 'backups');
  await assert.rejects(applyStudioChanges({ changes: [{ path: '爆款结构库/流量-新卡.md', content: '# 新卡' }], projectRoot: f.root, backupRoot, preflight, route, reader }), /索引/);
  const written = await applyStudioChanges({ changes: [{ path: '爆款结构库/流量-新卡.md', content: '# 新卡' }, { path: '爆款结构库.md', content: '# 新索引\n[新卡](爆款结构库/流量-新卡.md)' }], projectRoot: f.root, backupRoot, preflight, route, reader });
  assert.equal(written.written.length, 2);
  const transactions = await readdir(backupRoot); const transaction = JSON.parse(await readFile(path.join(backupRoot, transactions[0], 'transaction.json'), 'utf8'));
  assert.equal(transaction.status, 'completed');
  assert.equal(await readFile(transaction.entries.find((item) => item.existed).backup, 'utf8'), '# 旧索引');
  const freshReader = createStudioReader({ projectRoot: f.root, skillId: 'duibiao' }); await freshReader.read('爆款结构库.md');
  await f.put('爆款结构库.md', '# 用户刚改过');
  await assert.rejects(applyStudioChanges({ changes: [{ path: '爆款结构库.md', content: '不应覆盖' }], projectRoot: f.root, backupRoot, preflight, route, reader: freshReader }), /已经更新/);
  assert.equal(await readFile(path.join(f.root, '爆款结构库.md'), 'utf8'), '# 用户刚改过');
});

test('original archive preservation and existing cross-library write-back scopes are enforced', async (t) => {
  const f = await fixture(t);
  assert.equal(studioWriteAllowed('erchuang', '1-故事', '爆款结构库.md'), true);
  assert.equal(studioWriteAllowed('fupan', '1-故事', '带货框架库/框架.md'), true);
  assert.equal(studioWriteAllowed('duibiao', null, 'knowledge/爆款开头库.md'), true);
  assert.equal(studioWriteAllowed('duibiao', null, '工具/api配置.json'), false);
  assert.equal(studioWriteAllowed('erchuang', '1-故事', 'stories/2-别人/二创稿.md'), false);
  const args = { projectRoot: f.root, backupRoot: path.join(f.root, 'backup'), route: { mode: 'guarded_write' }, preflight: { skillId: 'duibiao', source: { text: '不可删改的完整原稿' }, manifests: [] } };
  await assert.rejects(applyStudioChanges({ ...args, changes: [{ path: '对标原稿/1-来源.md', content: '缩写' }] }), /完整原稿/);
  await assert.rejects(applyStudioChanges({ ...args, route: { mode: 'read_only' }, changes: [{ path: '对标原稿/1-来源.md', content: '不可删改的完整原稿' }] }), /只读/);
});
