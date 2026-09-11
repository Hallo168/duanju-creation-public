import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  readdir,
  rm,
} from 'node:fs/promises';
import { createAssetService } from './assets.mjs';
import { resolveCodexExecutable, codexRepairError } from './codex-cli.mjs';
import { loadRepairSources } from './repair-sources.mjs';
import { createVideoCatalog, videoModels, VIDEO_PROVIDER_IDS } from './video-catalog.mjs';
import {
  createModelService,
  invokeText,
  runProcess,
} from './model-providers.mjs';
import {
  parseAssets,
  replacePrompt,
  replaceAssetFilename,
  dependencyOrder,
} from './asset-markdown.mjs';
import { renderPlan, parsePlan, planPrompt, planSchema } from './asset-plan.mjs';
import {
  digest,
  writeJson,
  replaceFiles,
  recoverTransactions,
  childPath,
} from './storage.mjs';
import {
  createMediaTools,
  generateImage,
  ImageRequestError,
  PENCIL_PROMPT,
} from './media.mjs';

const directory = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
);
const fixtureRoot = path.join(directory, '.local', 'test-fixtures');

test('Guagu 实时目录保留 720p、4K、1080p 与 480p，不把未知单价当成零', () => {
  const rows = [
    { model: 'seedance28', resolution: '720p', type: 'image_to_video', credit_cost: 2.7 },
    { model: 'seedance54', resolution: '4k', type: 'image_to_video', credit_cost: 0.21 },
    { model: 'seedance52', resolution: '1080p', type: 'image_to_video', credit_cost: null },
    { model: 'seedance50', resolution: '480p', type: 'image_to_video' },
    { model: 'text-only', resolution: '720p', type: 'text_to_video' },
  ];
  const models = videoModels('guagu_video', rows);
  assert.deepEqual(models.map((model) => model.id), ['seedance28', 'seedance54', 'seedance52', 'seedance50']);
  assert.equal(models[1].resolution, '4k');
  assert.equal(models[1].unitCost, 0.21);
  assert.equal(models[2].unitCost, null);
  assert.equal(models[3].unitCost, null);
});

test('渠道并行读取、合并重复请求，慢渠道有超时且不丢失 Guagu 结果', async () => {
  const calls = [];
  let releaseGuagu;
  const guagu = new Promise((resolve) => { releaseGuagu = resolve; });
  const metadata = Object.fromEntries(VIDEO_PROVIDER_IDS.map((id) => [id, { label: id, selectable: true }]));
  const catalog = createVideoCatalog({
    metadata,
    timeoutMs: 60,
    readProvider: (id) => {
      calls.push(id);
      if (id === 'guagu_video') return guagu;
      if (id === 'canvas_video') throw new Error('测试连接失败');
      return new Promise(() => {});
    },
  });
  const first = catalog();
  const duplicate = catalog(['guagu_video']);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ['guagu_video', 'canvas_video', '12ai_video']);
  releaseGuagu({ models: [{ model: 'seedance54', resolution: '4k', type: 'image_to_video' }] });
  const duplicateResult = await duplicate;
  assert.equal(duplicateResult[0].models[0].id, 'seedance54');
  const results = await first;
  assert.equal(results[0].selectable, true);
  assert.match(results[1].error, /测试连接失败/);
  assert.match(results[2].error, /超时/);
});

test('Codex 未配置 PATH 时可发现桌面安装，并跳过不含程序的新版本目录', async (t) => {
  await mkdir(fixtureRoot, { recursive: true });
  const root = await mkdtemp(path.join(fixtureRoot, 'codex-'));
  t.after(async () => {
    if (path.dirname(root) !== fixtureRoot) throw new Error('Unsafe fixture cleanup');
    await rm(root, { recursive: true, force: true });
  });
  const bin = path.join(root, 'AppData', 'Local', 'OpenAI', 'Codex', 'bin');
  await mkdir(path.join(bin, 'installed'), { recursive: true });
  const executable = path.join(bin, 'installed', 'codex.exe');
  await writeFile(executable, 'fixture');
  await mkdir(path.join(bin, 'incomplete-new-version'));
  await writeFile(path.join(bin, 'incomplete-new-version', 'rg.exe'), 'fixture');
  assert.equal(resolveCodexExecutable({ env: {}, platform: 'win32', userHome: root }), executable);

  const custom = path.join(root, 'custom');
  await mkdir(custom);
  await writeFile(path.join(custom, 'codex.exe'), 'fixture');
  assert.equal(resolveCodexExecutable({
    env: { Path: `"${custom}"` }, platform: 'win32', userHome: root,
  }), path.join(custom, 'codex.exe'));
  assert.match(codexRepairError('spawn codex ENOENT'), /未找到 Codex/);
  assert.match(codexRepairError(`spawn ${executable} ENOENT`), /未找到 Codex/);
});

test('修复直接携带规范全文和目标相邻单元，保留原文并拒绝缺失依据', async (t) => {
  await mkdir(fixtureRoot, { recursive: true });
  const root = await mkdtemp(path.join(fixtureRoot, 'repair-'));
  t.after(async () => {
    if (path.dirname(root) !== fixtureRoot) throw new Error('Unsafe fixture cleanup');
    await rm(root, { recursive: true, force: true });
  });
  const policy = '完整规则\r\n中文原文不能被摘要代替\r\n';
  const storyboard = '# 本集\r\n映射表\r\n' + [1, 2, 3, 4, 10].map((number) =>
    `## 单元${number}（15s）\r\n台词${number}：“逐字保留！”\r\n`,
  ).join('');
  await writeFile(path.join(root, 'rules.md'), policy, 'utf8');
  await writeFile(path.join(root, 'storyboard.md'), storyboard, 'utf8');
  const rules = [
    { path: 'rules.md', readMode: '全文' },
    { path: 'storyboard.md', readMode: '目标单元及前后单元' },
  ];
  const sources = await loadRepairSources(root, rules, 3);
  assert.ok(sources.text.includes(policy));
  assert.ok(sources.text.includes('映射表\r\n'));
  for (const number of [2, 3, 4]) assert.ok(sources.text.includes(`台词${number}：“逐字保留！”`));
  assert.ok(!sources.text.includes('## 单元1（'));
  assert.ok(!sources.text.includes('## 单元10（'));
  assert.equal(sources.manifests.length, 2);
  assert.match(sources.manifests[0].sha256, /^[a-f0-9]{64}$/);
  await assert.rejects(loadRepairSources(root, rules, 9), /没有找到单元9/);
  await assert.rejects(loadRepairSources(root, [{ path: 'missing.md' }], 3), /ENOENT/);
  await assert.rejects(loadRepairSources(root, [{ path: '../outside.md' }], 3), /超出项目范围/);
});

const episode = '01-测试故事';
const person = {
  id: 'R1A',
  number: '图片1',
  name: '测试人物',
  kind: 'character',
  filename: 'R1-人物-日常-人物设定图.png',
  prompt: '最初的提示词',
  size: '1024x1536',
  refs: [],
  sourceFile: '',
};
const scene = {
  id: 'S1底图',
  number: '—',
  name: '测试场景全景',
  kind: 'scene_panorama',
  filename: 'S1-客厅-全景.png',
  prompt: '完整全景提示词',
  size: '1536x1024',
  refs: [],
  sourceFile: '',
};
const grid = {
  id: 'S1',
  number: '图片2',
  name: '客厅四宫格',
  kind: 'scene_grid',
  filename: 'S1-客厅-四宫格.png',
  prompt: '',
  size: '1536x1024',
  refs: ['S1底图'],
  sourceFile: '',
};
const pencil = {
  id: 'R1B',
  number: '图片3',
  name: '测试人物彩铅',
  kind: 'pencil',
  filename: 'R1-人物-日常-彩铅参考图.png',
  prompt: PENCIL_PROMPT,
  size: '1024x1536',
  refs: ['R1A'],
  sourceFile: '',
};
const product = {
  id: 'P1',
  number: '图片4',
  name: '实拍产品',
  kind: 'product_photo',
  filename: 'P1-实拍产品.png',
  prompt: '',
  size: '',
  refs: [],
  sourceFile: '',
};
const planOf = (assets) => ({
  styleName: '测试风格',
  styleBlock: '测试风格正文',
  characters: [{ id: 'R1', name: '测试人物', block: '测试人物描述' }],
  scenes: [{ id: 'S1', name: '客厅', block: '客厅描述' }],
  productInfo: '',
  notes: ['测试专用，不接生产接口。'],
  review: '独立自查已执行',
  assets,
});

async function fixture(t, assets = [person]) {
  await mkdir(fixtureRoot, { recursive: true });
  const root = await mkdtemp(path.join(fixtureRoot, 'case-'));
  t.after(async () => {
    if (path.dirname(root) !== fixtureRoot)
      throw new Error('Unsafe fixture cleanup');
    await rm(root, { recursive: true, force: true });
  });
  const story = path.join(root, 'stories', episode);
  await mkdir(story, { recursive: true });
  const output = renderPlan(episode, planOf(assets));
  await writeFile(path.join(story, '资产清单.md'), output.manifest);
  await writeFile(path.join(story, '资产提示词.md'), output.prompts);
  await writeFile(
    path.join(story, '二创稿.md'),
    '## 角色表\n| 姓名 | 身份 |\n| 测试人物 | 主角 |\n## 场景表\n| 场景 |\n| 客厅 |\n',
  );
  const imageCalls = [];
  const textCalls = [];
  const localCalls = [];
  const providers = {
    'assets.image': {
      id: 'image-provider',
      label: '测试生图',
      adapter: 'openai_images',
      model: 'exact-image-v1',
      baseUrl: 'http://localhost',
      endpoint: '/images/generations',
    },
    'assets.plan': {
      id: 'text-provider',
      label: '测试文字',
      adapter: 'openai_compatible_chat',
      model: 'exact-text-v2',
    },
    'project-flow': {
      id: 'flow',
      label: 'Flow',
      adapter: 'flow_image',
      model: 'gemini-3.1-flash-image',
    },
  };
  const fakeMedia = {
    verify: async (file) => {
      assert.ok((await readFile(file)).length);
      return { width: 1024, height: 1536, format: 'PNG' };
    },
    normalize: async (file, output) => {
      await mkdir(path.dirname(output), { recursive: true });
      await writeFile(output, await readFile(file), { flag: 'wx' });
      return { width: 1024, height: 1536, format: 'PNG' };
    },
    grid: async (file, output) => {
      localCalls.push({ file, output });
      await writeFile(output, Buffer.from('test-grid'), { flag: 'wx' });
      return { width: 1536, height: 1024, format: 'PNG' };
    },
  };
  const options = {
    projectRoot: root,
    directory,
    media: fakeMedia,
    models: { resolve: async (id) => structuredClone(providers[id]) },
    imageCheck: async () => {},
    imageCall: async (provider, item) => {
      imageCalls.push({ provider, item });
      return Buffer.from('test-image');
    },
    textCall: async (provider, prompt) => {
      textCalls.push({ provider, prompt });
      return JSON.stringify(planOf(assets));
    },
  };
  return {
    root,
    story,
    options,
    imageCalls,
    textCalls,
    localCalls,
    service: createAssetService(options),
  };
}
async function finished(service) {
  for (let count = 0; count < 1000; count += 1) {
    const state = await service.state(episode);
    if (!state.jobs.some((job) => job.status === 'running')) return state;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error('Test job timed out');
}

test('旧文件兼容：保留无彩铅例外，不自动增添资产', async (t) => {
  const f = await fixture(t, [person, scene, grid, product]);
  const original = await readFile(path.join(f.story, '资产清单.md'), 'utf8');
  await writeFile(
    path.join(f.story, '资产清单.md'),
    original.replace(
      'assets_ready: false',
      'assets_ready: true\n彩铅参考图: 本集不产',
    ),
  );
  const state = await f.service.state(episode);
  assert.equal(state.assets.length, 4);
  assert.ok(!state.assets.some((asset) => asset.kind === 'pencil'));
  assert.match(state.notes, /本集不产/);
  assert.equal(
    state.assets.find((asset) => asset.id === 'P1').kind,
    'product_photo',
  );
  assert.equal(f.imageCalls.length, 0);
});

test('彩铅暂停后旧任务不能提交，缺图不阻挡写实资产完成，开关可恢复', async (t) => {
  const f = await fixture(t, [person, pencil, scene, grid]);
  const policyFile = path.join(f.root, '工具', '生产开关.json');
  await writeJson(policyFile, { colored_pencil_enabled: false });
  const checks = [];
  const service = createAssetService({
    ...f.options,
    imageCheck: async (provider) => checks.push(provider.adapter),
  });
  const initial = await service.state(episode);
  const paused = initial.assets.find((asset) => asset.kind === 'pencil');
  assert.equal(paused.required, false);
  assert.match(initial.notes, /彩铅已暂时停用/);
  await assert.rejects(service.startImages({
    episode, hashes: initial.hashes, keys: [paused.key],
  }), /彩铅已暂时停用/);
  assert.equal(checks.length, 0);
  assert.equal(f.imageCalls.length, 0);
  await service.startImages({
    episode,
    hashes: initial.hashes,
    keys: initial.assets.filter((asset) => asset.required).map((asset) => asset.key),
  });
  const result = await finished(service);
  assert.equal(result.assetsReady, true);
  assert.equal(f.imageCalls.length, 2);
  assert.equal(f.localCalls.length, 1);
  assert.deepEqual(checks, ['openai_images']);
  assert.equal(result.assets.find((asset) => asset.kind === 'pencil').exists, false);
  assert.ok((await readFile(path.join(f.story, '资产清单.md'), 'utf8')).includes(pencil.filename));
  await writeJson(policyFile, { colored_pencil_enabled: true });
  const restored = await service.state(episode);
  assert.equal(restored.assets.find((asset) => asset.kind === 'pencil').required, true);
  assert.equal(f.imageCalls.length, 2);
});

test('批次运行中暂停彩铅会跳过尚未提交的派生项', async (t) => {
  const f = await fixture(t, [person, pencil]);
  let calls = 0;
  const service = createAssetService({
    ...f.options,
    imageCall: async (provider) => {
      calls += 1;
      assert.equal(provider.adapter, 'openai_images');
      await writeJson(path.join(f.root, '工具', '生产开关.json'), { colored_pencil_enabled: false });
      return Buffer.from('realistic-result');
    },
  });
  const before = await service.state(episode);
  await service.startImages({ episode, hashes: before.hashes, keys: before.assets.map((asset) => asset.key) });
  const after = await finished(service);
  assert.equal(calls, 1);
  assert.equal(after.assetsReady, true);
  assert.equal(after.jobs[0].items.find((item) => item.id === pencil.id).state, 'blocked');
});

test('暂停状态传入方案提示词和输出契约，拒绝模型返回彩铅或伪装类型', () => {
  const schema = planSchema(false);
  assert.ok(!schema.properties.assets.items.properties.kind.enum.includes('pencil'));
  assert.ok(planSchema(true).properties.assets.items.properties.kind.enum.includes('pencil'));
  const prompt = planPrompt({ pencilEnabled: false, products: [], files: [] }, '', null);
  assert.match(prompt, /只出写实人物图/);
  for (const item of [pencil, { ...pencil, kind: 'character' }])
    assert.throws(() => parsePlan(JSON.stringify(planOf([person, item])), false), /彩铅已暂时停用/);
  assert.equal(parsePlan(JSON.stringify(planOf([person, scene, grid])), false).assets.length, 3);
});

test('暂停彩铅时 Flow 显示停用，解析模型不读取其凭据或连接服务', async (t) => {
  const f = await fixture(t);
  await writeJson(path.join(f.root, '工具', '生产开关.json'), { colored_pencil_enabled: false });
  await writeJson(path.join(f.root, '工具', 'api配置.json'), {
    providers: { flow2api_banana2: { base_url: 'http://mock', settings_path: 'must-not-read.toml' } },
  });
  const service = createModelService({ projectRoot: f.root, directory: f.root, legacyFile: path.join(f.root, 'legacy.json') });
  const flow = (await service.list()).providers.find((provider) => provider.id === 'project-flow');
  assert.equal(flow.enabled, false);
  assert.match(flow.label, /暂时停用/);
  await assert.rejects(service.resolve('project-flow'), /未启用/);
});

test('批量脚本在彩铅暂停时跳过专用通道和文件名任务，不创建输出目录', async (t) => {
  const f = await fixture(t);
  const script = path.resolve(directory, '../../工具/gen_grids_multi.py');
  // Copy only the executor and nonsecret policy to isolate this from the live switch.
  const executor = path.join(f.root, 'gen_grids_multi.py');
  await writeFile(executor, await readFile(script));
  await writeJson(path.join(f.root, '生产开关.json'), { colored_pencil_enabled: false });
  const output = path.join(f.root, 'should-not-exist');
  const tasks = path.join(f.root, 'tasks.json');
  await writeJson(tasks, {
    output_dir: output,
    tasks: [
      { id: 'dedicated', file: 'anything.png', provider: 'flow2api_banana2' },
      { id: 'named', file: 'R1-彩铅参考图.png' },
    ],
  });
  const result = await runProcess('python', [executor, tasks]);
  assert.match(result, /no image requests/);
  assert.match(result, /dedicated: SKIP/);
  assert.match(result, /named: SKIP/);
  await assert.rejects(readdir(output), { code: 'ENOENT' });
});

test('提示词精确修改、冲突阻止与旧图保留', async (t) => {
  const f = await fixture(t, [person, scene]);
  const initial = await f.service.state(episode);
  const selected = initial.assets[0];
  const before = await readFile(path.join(f.story, '资产提示词.md'), 'utf8');
  await f.service.savePrompt({
    episode,
    key: selected.key,
    hashes: initial.hashes,
    prompt: '仅修改人物提示词',
  });
  const state = await f.service.state(episode);
  assert.equal(state.assets[0].prompt, '仅修改人物提示词');
  assert.equal(state.assets[1].prompt, scene.prompt);
  assert.equal(state.assetsReady, false);
  await assert.rejects(
    f.service.savePrompt({
      episode,
      key: selected.key,
      hashes: initial.hashes,
      prompt: '过期编辑',
    }),
    /已更新/,
  );
  const backups = await readdir(path.join(f.story, '工作台记录', '文件备份'));
  assert.ok(backups.length);
  assert.equal(
    await readFile(
      path.join(
        f.story,
        '工作台记录',
        '文件备份',
        backups[0],
        '1-资产提示词.md',
      ),
      'utf8',
    ),
    before,
  );
});

test('版本化文件名保持稳定 ID，派生依赖跟随新基础图', () => {
  const files = renderPlan(episode, planOf([person, pencil, scene, grid]));
  const original = parseAssets(files.manifest, files.prompts);
  const next = replaceAssetFilename(
    files.manifest,
    files.prompts,
    person.filename,
    person.filename.replace('.png', '-wb-test-v2.png'),
    '已生成',
  );
  const parsed = parseAssets(next.manifest, next.prompts);
  assert.equal(parsed[0].key, original[0].key);
  assert.deepEqual(parsed[1].refs, [original[0].key]);
  assert.equal(parsed[2].size, '1536x1024');
  assert.throws(() => dependencyOrder(parsed, [parsed[1].key]), /缺少参考图/);
  assert.throws(
    () =>
      dependencyOrder(
        [
          { key: 'a', refs: ['b'] },
          { key: 'b', refs: ['a'] },
        ],
        ['a', 'b'],
      ),
    /循环/,
  );
});

test('指定单图执行只请求一次，新图保存而旧图不覆盖', async (t) => {
  const f = await fixture(t, [person, scene]);
  await mkdir(path.join(f.story, '资产图'));
  await writeFile(path.join(f.story, '资产图', person.filename), 'old-image');
  const before = await f.service.state(episode);
  await f.service.startImages({
    episode,
    hashes: before.hashes,
    keys: [before.assets[0].key],
  });
  const result = await finished(f.service);
  assert.equal(f.imageCalls.length, 1);
  assert.equal(f.imageCalls[0].provider.model, 'exact-image-v1');
  assert.equal(f.imageCalls[0].item.prompt, person.prompt);
  assert.equal(result.jobs[0].status, 'completed');
  assert.equal(
    await readFile(path.join(f.story, '资产图', person.filename), 'utf8'),
    'old-image',
  );
  assert.notEqual(result.assets[0].filename, person.filename);
  assert.equal(result.assets[1].exists, false);
});

test('基础图到彩铅与四宫格按依赖执行，真实产品缺失不生图顶替', async (t) => {
  const f = await fixture(t, [grid, pencil, person, scene, product]);
  const before = await f.service.state(episode);
  await f.service.startImages({
    episode,
    hashes: before.hashes,
    keys: before.assets.map((asset) => asset.key),
  });
  const result = await finished(f.service);
  assert.equal(f.imageCalls.length, 3);
  assert.equal(f.localCalls.length, 1);
  const pencilCall = f.imageCalls.find(
    (call) => call.provider.adapter === 'flow_image',
  );
  assert.equal(pencilCall.item.refs.length, 1);
  assert.match(pencilCall.item.refs[0], /-ref-0\.png/);
  assert.equal(await readFile(pencilCall.item.refs[0], 'utf8'), 'test-image');
  assert.equal(result.assetsReady, false);
  assert.equal(
    result.jobs[0].items.find((item) => item.id === 'P1').state,
    'blocked',
  );
  assert.ok(
    result.assets
      .filter((item) => item.kind !== 'product_photo')
      .every((item) => item.exists && !item.outdated),
  );
});

test('刷新后恢复执行记录，结果未知阻止重投，需有核实说明', async (t) => {
  const f = await fixture(t);
  let calls = 0;
  const service = createAssetService({
    ...f.options,
    imageCall: async () => {
      calls += 1;
      throw new ImageRequestError('结果未知');
    },
  });
  const before = await service.state(episode);
  await service.startImages({
    episode,
    hashes: before.hashes,
    keys: [before.assets[0].key],
  });
  const result = await finished(service);
  assert.equal(result.jobs[0].items[0].state, 'unknown_outcome');
  const restarted = createAssetService(f.options);
  const restored = await restarted.state(episode);
  await assert.rejects(
    restarted.startImages({
      episode,
      hashes: restored.hashes,
      keys: [before.assets[0].key],
    }),
    /还未处理/,
  );
  await assert.rejects(
    restarted.recover({
      episode,
      jobId: restored.jobs[0].id,
      key: before.assets[0].key,
      action: 'resolve',
      note: '',
    }),
    /填写/,
  );
  await restarted.recover({
    episode,
    jobId: restored.jobs[0].id,
    key: before.assets[0].key,
    action: 'resolve',
    note: '已在测试平台核实：未生成、未扣费。',
  });
  assert.equal(calls, 1);
  assert.equal(
    (await restarted.state(episode)).jobs[0].items[0].resolved.note.includes(
      '未扣费',
    ),
    true,
  );
});

test('生图期间外部修改保留结果；恢复只采用原图，不重新生成', async (t) => {
  const f = await fixture(t);
  const service = createAssetService({
    ...f.options,
    imageCall: async () => {
      await writeFile(
        path.join(f.story, '资产清单.md'),
        (await readFile(path.join(f.story, '资产清单.md'), 'utf8')) +
          '\n外部新批注\n',
      );
      return Buffer.from('result-before-conflict');
    },
  });
  const initial = await service.state(episode);
  await service.startImages({
    episode,
    hashes: initial.hashes,
    keys: [initial.assets[0].key],
  });
  const result = await finished(service);
  assert.equal(result.jobs[0].items[0].state, 'conflict');
  assert.ok(result.jobs[0].items[0].result);
  assert.match(
    await readFile(path.join(f.story, '资产清单.md'), 'utf8'),
    /外部新批注/,
  );
  await service.recover({
    episode,
    hashes: result.hashes,
    jobId: result.jobs[0].id,
    key: initial.assets[0].key,
    action: 'recover',
  });
  const recovered = await finished(service);
  assert.equal(recovered.jobs[0].items[0].state, 'completed');
  assert.equal(recovered.assetsReady, true);
});

test('服务中断时的已提交项恢复为结果未知，不自动运行', async (t) => {
  const f = await fixture(t);
  const initial = await f.service.state(episode);
  const id = '11111111-1111-4111-8111-111111111111';
  await writeJson(path.join(f.story, '工作台记录', '资产', id, 'run.json'), {
    id,
    episode,
    status: 'running',
    createdAt: new Date().toISOString(),
    items: [{ ...initial.assets[0], state: 'submitting' }],
    models: [],
  });
  const state = await f.service.state(episode);
  assert.equal(state.jobs[0].status, 'interrupted');
  assert.equal(state.jobs[0].items[0].state, 'unknown_outcome');
  assert.equal(f.imageCalls.length, 0);
});

test('文字方案执行两轮同模型，按文件契约保存后独立停止', async (t) => {
  const f = await fixture(t);
  const rules = [
    'CLAUDE.md',
    '跨层契约.md',
    '.claude/skills/生产线总则.md',
    '.claude/skills/zichan/SKILL.md',
    '.claude/skills/zichan/知识/资产生产规范.md',
    '.claude/skills/zichan/知识/风格预设.md',
    '.claude/skills/zichan/知识/角色面相字典.md',
  ];
  for (const rule of rules) {
    await mkdir(path.dirname(path.join(f.root, rule)), { recursive: true });
    await writeFile(path.join(f.root, rule), `测试规范 ${rule}`);
  }
  const before = await f.service.state(episode);
  await f.service.startPlan({
    episode,
    hashes: before.hashes,
    notes: '只调整这份测试方案',
  });
  const after = await finished(f.service);
  assert.equal(after.jobs[0].status, 'completed');
  assert.equal(f.textCalls.length, 2);
  assert.equal(f.textCalls[0].provider.model, f.textCalls[1].provider.model);
  assert.match(f.textCalls[1].prompt, /独立自查轮/);
  assert.equal(f.imageCalls.length, 0);
  assert.equal(after.assets[0].prompt, person.prompt);
});

test('步骤模型独立覆盖、旧设置导入、密钥响应脱敏', async (t) => {
  const f = await fixture(t);
  const legacyFile = path.join(f.root, 'legacy.json');
  const service = createModelService({
    projectRoot: f.root,
    directory: f.root,
    legacyFile,
    legacyDefaults: [
      {
        id: 'codex-cli',
        label: '本机 Agent',
        adapter: 'codex_cli',
        enabled: true,
        model: 'cli-model',
      },
    ],
  });
  const initial = await service.list();
  assert.equal(initial.providers[0].model, 'cli-model');
  await service.save({
    provider: {
      id: 'custom-api',
      label: '自定义文字',
      adapter: 'openai_compatible_chat',
      baseUrl: 'http://localhost:18000/v1',
      enabled: true,
      model: 'base-model',
      apiKey: 'secret-test-key',
    },
  });
  const listed = await service.list();
  assert.ok(!JSON.stringify(listed).includes('secret-test-key'));
  assert.equal(listed.providers[1].secretStored, true);
  await service.save({
    overrides: {
      'assets.plan': { providerId: 'custom-api', model: 'chosen-model-exact' },
    },
  });
  assert.equal(
    (await service.resolve('assets.plan')).model,
    'chosen-model-exact',
  );
  assert.equal((await service.resolve('custom-api')).model, 'base-model');
  await service.save({ provider: { ...listed.providers[1], apiKey: '' } });
  assert.equal((await service.resolve('custom-api')).apiKey, 'secret-test-key');
  for (const candidate of [
    { baseUrl: 'https://poloai.example/v1' },
    { baseUrl: 'https://api.wuyinkeji.example/v1' },
    { label: '无音', baseUrl: 'https://image.example/v1' },
  ]) {
    const provider = { ...listed.providers[1], adapter: 'openai_images', ...candidate };
    await assert.rejects(service.save({ provider }), /停用/);
    await assert.rejects(service.previewModels(provider), /停用/);
  }
  await assert.rejects(
    service.save({ revision: initial.revision, overrides: {} }),
    /已更新/,
  );
});

test('PoloAI 文字接口支持目录、保存与调用，资产生图限制不外溢', async (t) => {
  const f = await fixture(t);
  const calls = [];
  const options = {
    projectRoot: f.root,
    directory: f.root,
    legacyFile: path.join(f.root, 'legacy.json'),
    fetcher: async (url, request) => {
      calls.push({ url, request });
      return Response.json({ data: [{ id: 'chosen-text-model' }] });
    },
  };
  const service = createModelService(options);
  const initial = await service.list();
  for (const adapter of ['openai_compatible_chat', 'openai_responses', 'custom_webhook']) {
    const candidate = {
      id: `poloai-${adapter}`,
      label: 'PoLoAI 文字模型',
      adapter,
      baseUrl: 'https://poloai.example/v1',
      enabled: true,
      model: 'chosen-text-model',
      apiKey: 'poloai-test-secret',
    };
    assert.deepEqual((await service.previewModels(candidate)).models, ['chosen-text-model']);
    assert.equal(calls.at(-1).url, 'https://poloai.example/v1/models');
    assert.equal(calls.at(-1).request.headers.Authorization, `Bearer ${candidate.apiKey}`);
    const saved = await service.save({ provider: candidate });
    assert.ok(!JSON.stringify(saved).includes(candidate.apiKey));
    assert.deepEqual(saved.defaults, initial.defaults);
    const reloaded = createModelService(options);
    const visible = (await reloaded.legacyList()).find((item) => item.id === candidate.id);
    assert.equal(visible.configured, true);
    assert.equal(visible.secretStored, true);
    assert.deepEqual((await reloaded.models(candidate.id)).models, ['chosen-text-model']);
    const provider = await reloaded.resolve(candidate.id);
    const reply = await invokeText(provider, '测试文字请求', {}, {
      fetcher: async (url, request) => {
        assert.equal(url, `${candidate.baseUrl}${provider.endpoint}`);
        assert.equal(request.headers.Authorization, `Bearer ${candidate.apiKey}`);
        assert.equal(JSON.parse(request.body).model, candidate.model);
        return Response.json({ output_text: '文字调用完成' });
      },
    });
    assert.equal(reply, '文字调用完成');
    const imageCandidate = { ...candidate, adapter: 'openai_images' };
    const count = calls.length;
    await assert.rejects(service.save({ provider: imageCandidate }), /资产生图/);
    await assert.rejects(service.previewModels(imageCandidate), /资产生图/);
    assert.equal(calls.length, count);
    assert.equal((await reloaded.resolve(candidate.id)).adapter, adapter);
  }
});

test('图片 HTTP 适配确实携带指定模型和有序参考图，断线不重试', async (t) => {
  const f = await fixture(t);
  const reference = path.join(f.root, 'reference.png');
  await writeFile(reference, 'reference-bytes');
  const calls = [];
  const provider = {
    id: 'image',
    adapter: 'openai_images',
    model: 'chosen-image-model',
    baseUrl: 'http://mock/v1',
    endpoint: '/images/generations',
    editEndpoint: '/images/edits',
    apiKey: 'test-secret',
  };
  const bytes = await generateImage(
    provider,
    { refs: [reference], prompt: '当前提示词', size: '1024x1536' },
    {
      fetcher: async (url, options) => {
        calls.push({ url, options });
        return new Response(
          JSON.stringify({
            data: [{ b64_json: Buffer.from('success').toString('base64') }],
          }),
        );
      },
    },
  );
  assert.equal(bytes.toString(), 'success');
  assert.equal(calls[0].url, 'http://mock/v1/images/edits');
  assert.equal(calls[0].options.body.get('model'), provider.model);
  assert.equal(calls[0].options.body.get('image[]').name, 'reference.png');
  let attempts = 0;
  await assert.rejects(
    generateImage(
      provider,
      { refs: [], prompt: '当前提示词', size: '1024x1536' },
      {
        fetcher: async () => {
          attempts += 1;
          throw new Error('connection reset');
        },
      },
    ),
    (error) => error.outcome === 'unknown_outcome',
  );
  assert.equal(attempts, 1);
});

test('文字 API 将完整指定模型和规范内容传入请求', async () => {
  let sent;
  const value = await invokeText(
    {
      adapter: 'openai_compatible_chat',
      baseUrl: 'http://mock/v1',
      endpoint: '/chat/completions',
      model: 'chosen-text-model',
    },
    '当前规范与本集材料',
    {},
    {
      fetcher: async (_url, options) => {
        sent = JSON.parse(options.body);
        return new Response(
          JSON.stringify({
            choices: [{ message: { content: '{"ok":true}' } }],
          }),
        );
      },
    },
  );
  assert.equal(sent.model, 'chosen-text-model');
  assert.equal(sent.messages[0].content, '当前规范与本集材料');
  assert.equal(value, '{"ok":true}');
});

test('中断的双文件保存可回滚；外部更新不被回滚覆盖', async (t) => {
  const f = await fixture(t);
  const backup = path.join(f.root, 'backups');
  const transaction = path.join(backup, 'test');
  await mkdir(transaction, { recursive: true });
  const fileA = path.join(f.root, 'a.md');
  const fileB = path.join(f.root, 'b.md');
  await writeFile(fileA, 'after-a');
  await writeFile(fileB, 'before-b');
  await writeFile(path.join(transaction, 'a.bak'), 'before-a');
  const entries = [
    {
      path: fileA,
      existed: true,
      backup: path.join(transaction, 'a.bak'),
      before: digest('before-a'),
      after: digest('after-a'),
    },
    {
      path: fileB,
      existed: true,
      backup: path.join(transaction, 'b.bak'),
      before: digest('before-b'),
      after: digest('after-b'),
    },
  ];
  await writeJson(path.join(transaction, 'transaction.json'), {
    status: 'prepared',
    entries,
  });
  await recoverTransactions(backup);
  assert.equal(await readFile(fileA, 'utf8'), 'before-a');
  await writeFile(fileA, 'external edit');
  await writeJson(path.join(transaction, 'transaction.json'), {
    status: 'prepared',
    entries,
  });
  await assert.rejects(recoverTransactions(backup), /又有修改/);
  assert.equal(await readFile(fileA, 'utf8'), 'external edit');
  await assert.rejects(
    replaceFiles(
      [{ path: fileA, content: 'new', expectedHash: digest('old') }],
      backup,
      'conflict',
    ),
    /已在其他地方更新/,
  );
});

test('技术工具真实检查容器、完整解码、尺寸及四宫格派生', async (t) => {
  const f = await fixture(t);
  const source = path.join(f.root, 'source.png');
  await runProcess('python', [
    '-c',
    'from PIL import Image; import sys; Image.new("RGB",(512,256),(80,90,100)).save(sys.argv[1])',
    source,
  ]);
  const media = createMediaTools(directory, f.root);
  assert.equal((await media.verify(source)).width, 512);
  const normalized = path.join(f.root, 'normalized.png');
  assert.equal((await media.normalize(source, normalized)).format, 'PNG');
  await assert.rejects(
    media.normalize(source, path.join(f.root, 'wrong-ratio.png'), [9, 16]),
    /dimensions/,
  );
  const gridOutput = path.join(f.root, 'grid.png');
  const result = await media.grid(source, gridOutput);
  assert.equal(result.width, 1536);
  assert.equal(result.height, 1024);
  const broken = path.join(f.root, 'broken.png');
  await writeFile(broken, (await readFile(source)).subarray(0, 70));
  await assert.rejects(media.verify(broken));
});

test('结构校验只检查契约和路径，禁止真实产品被改成生图任务', () => {
  assert.throws(() => childPath(directory, '../outside'), /项目/);
  assert.throws(() => childPath(directory, 'C:\\outside'), /路径/);
  assert.throws(
    () =>
      parsePlan(
        JSON.stringify(planOf([{ ...product, prompt: '生成产品包装' }])),
      ),
    /只能使用图库实拍/,
  );
  const files = renderPlan(episode, planOf([person]));
  assert.throws(
    () => replacePrompt(files.prompts, person.filename, '```\n不完整'),
    /代码块/,
  );
});

test('回填只改编号表的正确状态列，保留额外备注与旧分镜挂载', () => {
  const manifest = `---\nassets_ready: false\n---\n## 编号分配表\n| 默认编号 | 资产ID | 内容 | 建议文件名 | 来源 | 状态 | 备注 |\n|---|---|---|---|---|---|---|\n| 图片1 | R1A | 人物 | ${person.filename} | 文生图 | 待生成 | 用户原备注 |\n## 每单元挂载建议\n| 单元 | 文件 | 说明 |\n| 1 | ${person.filename} | 旧分镜保持原图 |\n`;
  const next = replaceAssetFilename(
    manifest,
    `建议文件名：\x60${person.filename}\x60`,
    person.filename,
    'R1-新版本.png',
    '已生成',
  );
  assert.match(
    next.manifest,
    /R1-新版本.png \| 文生图 \| 已生成 \| 用户原备注/,
  );
  assert.ok(
    next.manifest.includes(`| 1 | ${person.filename} | 旧分镜保持原图 |`),
  );
  assert.ok(next.prompts.includes('R1-新版本.png'));
});

test('未执行的新方案即使已有同名图片，也明确标为待重做', async (t) => {
  const f = await fixture(t);
  await mkdir(path.join(f.story, '资产图'));
  await writeFile(
    path.join(f.story, '资产图', person.filename),
    'old-unmatched-image',
  );
  const state = await f.service.state(episode);
  assert.equal(state.assets[0].exists, true);
  assert.equal(state.assets[0].outdated, true);
  assert.equal(state.assetsReady, false);
});

test('参考图在请求中被外部修改时，提交快照与记录一致且不自动采用结果', async (t) => {
  const f = await fixture(t, [person, pencil]);
  let state = await f.service.state(episode);
  await f.service.startImages({
    episode,
    hashes: state.hashes,
    keys: [state.assets[0].key],
  });
  state = await finished(f.service);
  const baseFile = path.join(f.story, '资产图', state.assets[0].filename);
  const service = createAssetService({
    ...f.options,
    imageCall: async (_provider, item) => {
      assert.equal(await readFile(item.refs[0], 'utf8'), 'test-image');
      await writeFile(baseFile, 'external-reference-edit');
      assert.equal(await readFile(item.refs[0], 'utf8'), 'test-image');
      return Buffer.from('generated-pencil');
    },
  });
  await service.startImages({
    episode,
    hashes: state.hashes,
    keys: [state.assets[1].key],
  });
  const result = await finished(service);
  assert.equal(result.jobs[0].items[0].state, 'conflict');
  assert.equal(await readFile(baseFile, 'utf8'), 'external-reference-edit');
  assert.equal(result.assets[1].exists, false);
});

test('Flow 只收到固定模型、提示词和一张参考图', async (t) => {
  const f = await fixture(t);
  const reference = path.join(f.root, 'realistic.png');
  await writeFile(reference, 'realistic-bytes');
  const provider = {
    adapter: 'flow_image',
    model: 'gemini-3.1-flash-image',
    baseUrl: 'http://mock',
    endpoint: '/models/gemini-3.1-flash-image:generateContent',
    apiKey: 'flow-test',
  };
  let sent;
  await generateImage(
    provider,
    { refs: [reference], prompt: '不应使用的自定义彩铅词', size: '9999x9999' },
    {
      fetcher: async (_url, options) => {
        sent = JSON.parse(options.body);
        return new Response(
          JSON.stringify({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      inlineData: {
                        data: Buffer.from('flow-output').toString('base64'),
                      },
                    },
                  ],
                },
              },
            ],
          }),
        );
      },
    },
  );
  assert.equal(sent.contents[0].parts.length, 2);
  assert.equal(sent.contents[0].parts[1].text, PENCIL_PROMPT);
  assert.equal(sent.generationConfig.imageConfig.aspectRatio, '9:16');
  assert.equal(sent.generationConfig.imageConfig.imageSize, '1K');
});

test('已有文件可单独重新核验放行，不调用生成接口', async (t) => {
  const f = await fixture(t);
  let state = await f.service.state(episode);
  await f.service.startImages({
    episode,
    hashes: state.hashes,
    keys: [state.assets[0].key],
  });
  state = await finished(f.service);
  const manifest = path.join(f.story, '资产清单.md');
  await writeFile(
    manifest,
    (await readFile(manifest, 'utf8')).replace(
      'assets_ready: true',
      'assets_ready: false',
    ),
  );
  state = await f.service.state(episode);
  await f.service.verifyExisting({ episode, hashes: state.hashes });
  const result = await finished(f.service);
  assert.equal(result.assetsReady, true);
  assert.equal(result.jobs[0].kind, 'verify');
  assert.equal(f.imageCalls.length, 1);
});

test('残缺的下载文件保留，通过原响应恢复成新的完整版本', async (t) => {
  const f = await fixture(t);
  let first = true;
  const media = {
    ...f.options.media,
    verify: async (file) => {
      if ((await readFile(file)).toString() === 'partial-output')
        throw new Error('Incomplete file');
      return f.options.media.verify(file);
    },
    normalize: async (file, output) => {
      if (first) {
        first = false;
        await writeFile(output, 'partial-output');
        throw new Error('Interrupted normalization');
      }
      return f.options.media.normalize(file, output);
    },
  };
  const service = createAssetService({ ...f.options, media });
  let state = await service.state(episode);
  await service.startImages({
    episode,
    hashes: state.hashes,
    keys: [state.assets[0].key],
  });
  state = await finished(service);
  assert.equal(state.jobs[0].items[0].state, 'technical_failed');
  await service.recover({
    episode,
    hashes: state.hashes,
    jobId: state.jobs[0].id,
    key: state.assets[0].key,
    action: 'recover',
  });
  const result = await finished(service);
  assert.equal(result.jobs[0].items[0].state, 'completed');
  assert.equal(f.imageCalls.length, 1);
  assert.equal((await readdir(path.join(f.story, '资产图'))).length, 2);
});
