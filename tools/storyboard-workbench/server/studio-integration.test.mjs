import assert from 'node:assert/strict';
import test from 'node:test';
import path from 'node:path';
import http from 'node:http';
import { connect } from 'node:net';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { mkdir, mkdtemp, cp, copyFile, readFile, writeFile, rm } from 'node:fs/promises';
import { sendStudioMessage } from '../app/pipeline/send-message.ts';

const codeRoot = fileURLToPath(new URL('..', import.meta.url));
const testBase = path.join(codeRoot, '.local', 'studio-tests');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const parsedResult = (patch = {}) => ({ reply: '当前任务完成。', status: 'completed', episode: null, session_summary: '已完成本次任务。', rules_read: [], rules_applied: [], memory_update: [], decisions: [], artifacts: [], file_changes: [], next_actions: [], warnings: [], context_requests: [], ...patch });

test('studio API: configure → import → switch model → read dependencies → atomically ingest → reuse in topic generation', { timeout: 30000 }, async (t) => {
  await mkdir(testBase, { recursive: true });
  const root = await mkdtemp(path.join(testBase, 'integration-'));
  const directory = path.join(root, 'tools', 'storyboard-workbench');
  const resources = {};
  t.after(async () => {
    const { child, mock, proxy, sockets } = resources;
    if (child && child.exitCode === null) { child.kill(); await once(child, 'exit').catch(() => {}); }
    for (const socket of sockets ?? []) socket.destroy();
    if (proxy) await new Promise((resolve) => proxy.close(resolve));
    if (mock) await new Promise((resolve) => { mock.closeAllConnections(); mock.close(resolve); });
    const resolved = path.resolve(root);
    assert.ok(resolved.startsWith(path.resolve(testBase) + path.sep));
    await rm(resolved, { recursive: true, force: true });
  });
  await mkdir(directory, { recursive: true });
  await cp(path.join(codeRoot, 'server'), path.join(directory, 'server'), { recursive: true, filter: (file) => !file.endsWith('.test.mjs') });
  for (const file of ['local-server.mjs', 'codex-studio-schema.json', 'codex-repair-schema.json', 'codex-pipeline-schema.json']) await copyFile(path.join(codeRoot, file), path.join(directory, file));
  const put = async (file, text) => { await mkdir(path.dirname(path.join(root, file)), { recursive: true }); await writeFile(path.join(root, file), text, 'utf8'); };
  for (const folder of ['stories', '对标原稿', '爆款结构库', '带货框架库', '产品图']) await mkdir(path.join(root, folder), { recursive: true });
  const serverSource = await readFile(path.join(directory, 'local-server.mjs'), 'utf8');
  const sourceFiles = new Set(['CLAUDE.md', '跨层契约.md', '爆款结构库.md', '带货框架库.md', '规则变更日志.md']);
  for (const match of serverSource.matchAll(/['"]((?:knowledge|\.claude)\/[^'"\n$]+\.md)['"]/g)) sourceFiles.add(match[1]);
  for (const file of sourceFiles) await put(file, '# 独立测试资料\n这份资料仅供隔离联调使用。\n');
  await put('爆款结构库/框架-伪装测试局.md', '# 受控方法定义\n');

  const originalText = '甲：今天回家吃饭。\n乙：好，我这就回来。\n';
  const secret = 'integration-only-api-secret';
  const modelCalls = [];
  const allCalls = [];
  let simulateStaleSource = false;
  let reviewScenario = null;
  let draftCalls = 0;
  const draftText = '## 正文\n甲：这份草稿已经生成，请继续审稿。\nDRAFT_PRESERVED_EXACTLY';
  const reviewedText = '## 正文\n甲：这份草稿已按意见修订。\n## 审稿报告\n测试审稿完成。';
  const mock = http.createServer(async (request, response) => {
    try {
      allCalls.push({ url: request.url, authorization: request.headers.authorization });
      if (request.url === '/v1/models') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ data: [{ id: 'chosen-b' }, { id: 'chosen-c' }] })); return; }
      assert.equal(request.url, '/v1/chat/completions');
      const chunks = []; for await (const chunk of request) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      modelCalls.push(payload);
      const prompt = payload.messages.map((item) => item.content).join('\n');
      const paths = [...new Set([...prompt.matchAll(/===== SOURCE: (.+?) =====/g)].map((match) => match[1]))];
      const needsMore = modelCalls.length === 1;
      let result;
      if (reviewScenario) {
        const reviewing = prompt.includes('REVIEW_PHASE_MARKER');
        if (!reviewing) {
          draftCalls++;
          result = parsedResult({ reply: '不应作为结果显示的虚假落盘声明。', episode: '2-审稿恢复', rules_read: paths, file_changes: [{ path: 'stories/2-审稿恢复/二创稿.md', content: draftText }] });
        } else {
          assert.ok(prompt.includes('DRAFT_PRESERVED_EXACTLY'), '继续审稿必须携带原草稿');
          if (reviewScenario === 'transport') { response.statusCode = 503; response.end('temporary interruption'); return; }
          const content = reviewScenario === 'plain'
            ? '审稿意见已经返回，但这次没有按约定格式提交完整修订稿。'
            : `审稿完成，结果如下：\n${JSON.stringify(parsedResult({ episode: '2-审稿恢复', rules_read: paths, file_changes: [{ path: 'stories/2-审稿恢复/二创稿.md', content: reviewedText }] }))}\n请查看。`;
          response.setHeader('Content-Type', 'application/json');
          response.end(JSON.stringify({ model: payload.model, choices: [{ message: { content } }], usage: { prompt_tokens: 25, completion_tokens: 10 } }));
          return;
        }
      } else if (needsMore) result = parsedResult({ status: 'needs_context', context_requests: [{ operation: 'read', path: '带货框架库.md' }, { operation: 'list', path: '对标原稿' }] });
      else if (modelCalls.length === 2) {
        assert.ok(payload.messages.at(-1).content.includes('带货框架库.md'));
        assert.ok(prompt.includes(originalText));
        result = parsedResult({ reply: '已保存原稿、结构卡和索引。', rules_read: paths, rules_applied: ['遵循本次入库范围。'], file_changes: [
          { path: '对标原稿/1-测试.md', content: originalText },
          { path: '爆款结构库/流量-测试.md', content: '# 测试结构卡\n来源：[原稿](../对标原稿/1-测试.md)\n' },
          { path: '爆款结构库.md', content: '# 索引\n[测试结构卡](爆款结构库/流量-测试.md)\n' },
        ] });
      } else {
        assert.ok(prompt.includes('测试结构卡'));
        result = parsedResult({ status: 'waiting_user', reply: '已按选中的结构资料准备本轮候选，请选择方向。', rules_read: paths });
        if (simulateStaleSource) {
          await put('knowledge/账号档案.md', '生成中用户更新了账号资料。');
          result = parsedResult({ reply: '这是生成后必须保留下来的五个选题候选。', rules_read: paths, session_summary: '不应记入会话的旧结论', decisions: ['不应确认的旧结论'], file_changes: [{ path: 'stories/1-未定案/选题.md', content: '不应写入的旧版定案' }] });
        }
      }
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({ model: payload.model, choices: [{ message: { content: JSON.stringify(result) } }], usage: { prompt_tokens: 25, completion_tokens: 10 } }));
    } catch (error) { response.statusCode = 500; response.end(JSON.stringify({ error: error.message })); }
  });
  resources.mock = mock;
  await new Promise((resolve) => mock.listen(0, '127.0.0.1', resolve));
  const upstream = `http://127.0.0.1:${mock.address().port}`;
  const proxyTargets = [];
  const sockets = resources.sockets = new Set();
  const proxy = resources.proxy = http.createServer();
  proxy.on('connection', (socket) => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  proxy.on('connect', (request, client, head) => {
    proxyTargets.push(request.url);
    if (request.url !== 'model-catalog.invalid:80') { client.end('HTTP/1.1 502 Bad Gateway\r\n\r\n'); return; }
    const target = connect(mock.address().port, '127.0.0.1', () => {
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) target.write(head);
      client.pipe(target); target.pipe(client);
    });
    sockets.add(target); target.on('close', () => sockets.delete(target));
    target.on('error', () => client.destroy()); client.on('error', () => target.destroy());
  });
  await new Promise((resolve) => proxy.listen(0, '127.0.0.1', resolve));
  const proxyUrl = `http://127.0.0.1:${proxy.address().port}`;
  const child = spawn(process.execPath, ['local-server.mjs'], { cwd: directory, env: { ...process.env, STORYBOARD_API_PORT: '0', HTTP_PROXY: proxyUrl, HTTPS_PROXY: proxyUrl, http_proxy: proxyUrl, https_proxy: proxyUrl, NO_PROXY: 'localhost,127.0.0.1,::1', no_proxy: 'localhost,127.0.0.1,::1' }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  resources.child = child;
  const base = await new Promise((resolve, reject) => {
    let stdout = '', stderr = '';
    const timer = setTimeout(() => reject(new Error(`API 未启动：${stderr}`)), 10000);
    child.stdout.on('data', (bytes) => { stdout += bytes.toString(); const match = stdout.match(/listening on (http:\/\/127\.0\.0\.1:\d+)/); if (match) { clearTimeout(timer); resolve(match[1]); } });
    child.stderr.on('data', (bytes) => { stderr += bytes.toString(); });
    child.once('exit', (code) => { clearTimeout(timer); reject(new Error(`API 启动退出 ${code}: ${stderr}`)); });
  });
  const call = async (route, body) => {
    const response = await fetch(base + route, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const json = await response.json(); return { status: response.status, body: json };
  };
  const saved = await call('/api/studio/providers', { provider: { id: 'fixture-api', label: '隔离测试 API', adapter: 'openai_compatible_chat', enabled: true, baseUrl: upstream + '/v1/chat/completions', endpoint: '/chat/completions', model: 'default-a', apiKey: secret, structuredOutputs: false } });
  assert.equal(saved.status, 200); assert.equal(JSON.stringify(saved.body).includes(secret), false);
  const models = await call('/api/workbench/models?providerId=fixture-api'); assert.deepEqual(models.body.models, ['chosen-b', 'chosen-c']);
  assert.deepEqual(proxyTargets, [], '本机接口不应经过代理');
  const proxiedModels = await call('/api/workbench/models', { provider: { id: 'proxy-preview', label: '代理模型目录', adapter: 'openai_compatible_chat', enabled: true, baseUrl: 'http://model-catalog.invalid/v1', apiKey: secret } });
  assert.equal(proxiedModels.status, 200, JSON.stringify(proxiedModels.body));
  assert.deepEqual(proxiedModels.body.models, ['chosen-b', 'chosen-c']);
  assert.deepEqual(proxyTargets, ['model-catalog.invalid:80']);
  const created = await call('/api/studio/sessions', { skillId: 'duibiao', routeId: 'full-ingest', providerId: 'fixture-api', model: 'chosen-b' });
  assert.equal(created.status, 201); const sessionId = created.body.session.id;
  const orphanSessionId = '11111111-1111-4111-8111-111111111111';
  const orphanJobId = '22222222-2222-4222-8222-222222222222';
  const stateRoot = '工具/运行垃圾/tasks/workbench-studio';
  await put(`${stateRoot}/sessions/${orphanSessionId}.json`, JSON.stringify({ ...created.body.session, id: orphanSessionId, status: 'running', lastJobId: orphanJobId }));
  await put(`${stateRoot}/jobs/studio-job-${orphanJobId}.json`, JSON.stringify({ id: orphanJobId, sessionId: orphanSessionId, skillId: 'duibiao', routeId: 'full-ingest', status: 'running', providerId: 'fixture-api', createdAt: new Date().toISOString() }));
  const recovered = await call(`/api/studio/sessions/${orphanSessionId}`);
  assert.equal(recovered.body.session.status, 'needs_attention');
  assert.ok(recovered.body.session.messages.at(-1).content.includes('没有自动重发'));
  assert.equal(modelCalls.length, 0);
  const imported = await call('/api/studio/source', { sessionId, filename: '测试.TXT', base64: Buffer.from(originalText).toString('base64') });
  assert.equal(imported.status, 200); assert.equal(imported.body.source.text, originalText);
  const firstCheck = await call('/api/studio/preflight', { sessionId, message: '完整拆解本轮原稿并入库。' });
  assert.equal(firstCheck.status, 200); assert.equal(firstCheck.body.provider.model, 'chosen-b'); assert.equal(firstCheck.body.requiredRules.length, 0); assert.ok(firstCheck.body.ruleCount > 0);
  await call(`/api/studio/sessions/${sessionId}`, { model: 'chosen-c' });
  const stale = await call('/api/studio/run', { token: firstCheck.body.token, confirmationKey: firstCheck.body.confirmationKey, confirmed: true });
  assert.equal(stale.status, 409); assert.equal(modelCalls.length, 0);
  const checked = await call('/api/studio/preflight', { sessionId, message: '完整拆解本轮原稿并入库。' });
  assert.equal(checked.body.provider.model, 'chosen-c');
  const submission = { token: checked.body.token, confirmationKey: checked.body.confirmationKey, confirmed: true };
  const responses = await Promise.all([call('/api/studio/run', submission), call('/api/studio/run', submission)]);
  assert.equal(responses.filter((response) => response.status === 202).length, 1);
  assert.ok(responses.some((response) => [409, 410, 429].includes(response.status)));
  const started = responses.find((response) => response.status === 202);
  const waitJob = async (id) => {
    let job;
    for (let attempt = 0; attempt < 100; attempt += 1) { job = await call(`/api/studio/jobs/${id}`); if (job.body.status !== 'running') return job.body; await sleep(40); }
    throw new Error('任务未完成');
  };
  const finished = await waitJob(started.body.id);
  assert.equal(finished.status, 'succeeded', JSON.stringify(finished));
  assert.equal(finished.model, 'chosen-c'); assert.equal(finished.output, ''); assert.deepEqual(finished.result.fileChanges, []);
  assert.equal(modelCalls.length, 2); assert.ok(modelCalls.every((payload) => payload.model === 'chosen-c'));
  assert.equal(await readFile(path.join(root, '对标原稿/1-测试.md'), 'utf8'), originalText);
  const history = await call(`/api/studio/sessions/${sessionId}`);
  assert.equal(history.body.session.messages.at(-1).receipt.model, 'chosen-c');
  assert.deepEqual(history.body.session.messages.at(-1).receipt.rulesApplied, []);
  const library = await call('/api/studio/library');
  assert.ok(library.body.groups.find((group) => group.id === 'structures').entries.some((entry) => entry.name === '流量-测试'));
  assert.equal((await call('/api/studio/library/document?path=' + encodeURIComponent('knowledge/1-核心规则.md'))).status, 403);
  const next = await call('/api/studio/sessions', { skillId: 'xuanti', routeId: 'benchmark-tier-1', providerId: 'fixture-api', model: 'chosen-b', referencePath: '爆款结构库/流量-测试.md' });
  assert.equal(next.status, 201); assert.equal(next.body.session.reference.path, '爆款结构库/流量-测试.md');
  const nextCheck = await call('/api/studio/preflight', { sessionId: next.body.session.id, message: '用选中的对标准备选题。2档' });
  assert.equal(nextCheck.body.route.id, 'benchmark-tier-2');
  assert.equal((await call('/api/studio/sessions/' + next.body.session.id)).body.session.routeId, 'benchmark-tier-2');
  assert.equal(nextCheck.status, 200, JSON.stringify(nextCheck.body));
  const directJob = await sendStudioMessage(async (route, options) => {
    const result = await call(route, options?.body ? JSON.parse(options.body) : undefined);
    assert.ok(result.status >= 200 && result.status < 300, JSON.stringify(result.body));
    return result.body;
  }, { sessionId: next.body.session.id, message: '用选中的对标准备选题。2档', model: 'chosen-b' });
  const nextRun = { status: 202, body: directJob };
  assert.equal(nextRun.status, 202);
  assert.equal((await waitJob(nextRun.body.id)).status, 'succeeded');
  assert.equal(modelCalls.at(-1).model, 'chosen-b');
  assert.ok(allCalls.every((entry) => entry.authorization === `Bearer ${secret}`));

  await call(`/api/studio/sessions/${next.body.session.id}`, { routeId: 'original-commerce' });
  const corrected = await call('/api/studio/preflight', { sessionId: next.body.session.id, message: '对标选题法 二档' });
  assert.equal(corrected.body.route.id, 'benchmark-tier-2');
  simulateStaleSource = true;
  const callsBefore = modelCalls.length;
  const staleRun = await call('/api/studio/run', { token: corrected.body.token, confirmationKey: corrected.body.confirmationKey, confirmed: true });
  assert.equal(staleRun.status, 202);
  const preserved = await waitJob(staleRun.body.id);
  assert.equal(preserved.status, 'needs_attention');
  assert.equal(preserved.result.reply, '这是生成后必须保留下来的五个选题候选。');
  assert.deepEqual(preserved.result.fileChanges, []);
  assert.deepEqual(preserved.result.artifacts, []);
  assert.deepEqual(preserved.written, []);
  assert.ok(preserved.result.warnings[0].includes('资料有更新'));
  const preservedSession = (await call(`/api/studio/sessions/${next.body.session.id}`)).body.session;
  assert.equal(preservedSession.messages.at(-1).content, preserved.result.reply);
  assert.equal(preservedSession.messages.at(-1).receipt.usage.requests, 1);
  assert.equal(preservedSession.decisions.includes('不应确认的旧结论'), false);
  assert.notEqual(preservedSession.summary, '不应记入会话的旧结论');
  const savedJob = JSON.parse(await readFile(path.join(root, stateRoot, 'jobs', `studio-job-${preserved.id}.json`), 'utf8'));
  assert.equal(savedJob.pendingResult.reply, preserved.result.reply);
  await assert.rejects(readFile(path.join(root, 'stories/1-未定案/选题.md')), /ENOENT/);
  assert.equal(modelCalls.length, callsBefore + 1, '必须由用户决定重试');

  await put('stories/2-审稿恢复/选题.md', '---\n集型: 流量\n---\n## 故事梗概\n测试定案。');
  await put('knowledge/5-审稿检查清单.md', 'REVIEW_PHASE_MARKER');
  const scriptSession = (await call('/api/studio/sessions', { skillId: 'erchuang', routeId: 'draft-review', episode: '2-审稿恢复', providerId: 'fixture-api', model: 'chosen-b' })).body.session;
  assert.ok(scriptSession);
  const submitReview = async (message) => {
    const check = await call('/api/studio/preflight', { sessionId: scriptSession.id, message });
    assert.equal(check.status, 200, JSON.stringify(check.body));
    const run = await call('/api/studio/run', { token: check.body.token, confirmationKey: check.body.confirmationKey, confirmed: true });
    assert.equal(run.status, 202, JSON.stringify(run.body));
    return waitJob(run.body.id);
  };
  reviewScenario = 'plain';
  const firstDraft = await submitReview('/erchuang');
  assert.equal(firstDraft.status, 'needs_attention');
  assert.equal(firstDraft.result.reply, draftText);
  assert.equal(firstDraft.result.resumeAction, 'review');
  assert.equal(firstDraft.result.reply.includes('虚假落盘声明'), false);
  const privateDraftJob = JSON.parse(await readFile(path.join(root, stateRoot, 'jobs', `studio-job-${firstDraft.id}.json`), 'utf8'));
  assert.equal(privateDraftJob.responseRecords.length, 2);
  assert.equal(firstDraft.responseRecords, undefined);
  const rawReview = JSON.parse(await readFile(path.join(root, stateRoot, 'jobs', privateDraftJob.responseRecords[1]), 'utf8'));
  assert.equal(rawReview.phase, 'review');
  assert.ok(rawReview.text.includes('审稿意见已经返回'));
  const scriptHistory = (await call('/api/studio/sessions/' + scriptSession.id)).body.session;
  assert.equal(scriptHistory.messages.at(-1).content, draftText);
  assert.equal(scriptHistory.messages.at(-1).receipt.resumeAction, 'review');
  await assert.rejects(readFile(path.join(root, 'stories/2-审稿恢复/二创稿.md')), /ENOENT/);
  reviewScenario = 'transport';
  const secondDraft = await submitReview('继续审稿');
  assert.equal(secondDraft.status, 'needs_attention');
  assert.equal(secondDraft.result.reply, draftText);
  assert.equal(secondDraft.result.resumeAction, 'review');
  assert.equal(draftCalls, 1, '连续两次审稿失败也不能丢失草稿后重写');
  reviewScenario = 'wrapped';
  const finalDraft = await submitReview('继续审稿');
  assert.equal(finalDraft.status, 'succeeded');
  assert.equal(await readFile(path.join(root, 'stories/2-审稿恢复/二创稿.md'), 'utf8'), reviewedText);
  assert.equal(draftCalls, 1);
  const finalRecord = JSON.parse(await readFile(path.join(root, stateRoot, 'jobs', `studio-job-${finalDraft.id}.json`), 'utf8'));
  assert.equal(finalRecord.pendingDraft, null);
});
