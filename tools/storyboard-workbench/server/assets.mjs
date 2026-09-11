import path from 'node:path';
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { createReadStream } from 'node:fs';
import {
  WorkbenchError,
  digest,
  stamp,
  optionalText,
  optionalJson,
  writeJson,
  childPath,
  existingChild,
  storyPath,
  fileInfo,
  createLocks,
  replaceFiles,
  recoverTransactions,
} from './storage.mjs';
import {
  parseAssets,
  section,
  metadata,
  replacePrompt,
  setReady,
  replaceAssetFilename,
  dependencyOrder,
} from './asset-markdown.mjs';
import { modelReceipt, invokeText } from './model-providers.mjs';
import {
  planSchema,
  assetSources,
  planPrompt,
  parsePlan,
  renderPlan,
  ensureSourcesUnchanged,
} from './asset-plan.mjs';
import {
  createMediaTools,
  preflightImage,
  generateImage,
  PENCIL_PROMPT,
  limitedBytes,
} from './media.mjs';
import { pencilEnabled, PENCIL_PAUSED_MESSAGE } from './production-policy.mjs';

const runDir = (story) => path.join(story, '工作台记录', '资产');
const backupDir = (story) => path.join(story, '工作台记录', '文件备份');
const docs = async (story) => {
  const [manifest, prompts, script] = await Promise.all(
    ['资产清单.md', '资产提示词.md', '二创稿.md'].map((name) =>
      optionalText(path.join(story, name)),
    ),
  );
  return {
    manifest,
    prompts,
    script,
    hashes: {
      manifest: digest(manifest),
      prompts: digest(prompts),
      script: digest(script),
    },
  };
};
const sameHashes = (a, b) =>
  ['manifest', 'prompts', 'script'].every((key) => a[key] === b[key]);
const safeId = (id) => {
  if (!/^[a-f0-9-]{36}$/i.test(id))
    throw new WorkbenchError('执行记录 ID 无效。');
  return id;
};
const stale = (asset) =>
  /提示词已修改|参考图已更新|待重做|待生成|待导入/.test(asset.declaredStatus);
const versionName = (filename) =>
  `${filename.replace(/\.[^.]+$/, '').replace(/-wb-[^.]+$/, '')}-wb-${stamp()}.png`;
const expectedSize = (asset) =>
  asset.kind === 'product_photo'
    ? null
    : asset.kind === 'pencil'
      ? [9, 16]
      : asset.size
        ? asset.size.split('x').map(Number)
        : null;

function publicRun(job) {
  return {
    id: job.id,
    episode: job.episode,
    kind: job.kind,
    status: job.status,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    message: job.message,
    phase: job.phase,
    models: job.models,
    review: job.review,
    recordPath: job.recordPath,
    items: (job.items || []).map((item) => ({
      key: item.key,
      id: item.id,
      name: item.name,
      state: item.state,
      message: item.message,
      result: item.result,
      resolved: item.resolved,
      recoverable:
        [
          'received',
          'verifying',
          'download_failed',
          'conflict',
          'technical_failed',
          'unknown_outcome',
        ].includes(item.state) &&
        Boolean(
          item.responseFile ||
          item.rawFile ||
          item.result ||
          (item.kind === 'scene_grid' && item.inputs?.refs?.length),
        ),
    })),
  };
}

export function createAssetService({
  projectRoot,
  directory,
  models,
  media = createMediaTools(directory, projectRoot),
  textCall = invokeText,
  imageCall = generateImage,
  imageCheck = preflightImage,
  fetcher = fetch,
}) {
  const locks = createLocks();
  const running = new Map();
  const persist = async (story, job) => {
    job.updatedAt = new Date().toISOString();
    await writeJson(path.join(runDir(story), job.id, 'run.json'), job);
  };
  const runs = async (story) => {
    let entries = [];
    try {
      entries = await readdir(runDir(story), { withFileTypes: true });
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const result = [];
    for (const entry of entries.filter(
      (item) => item.isDirectory() && /^[a-f0-9-]{36}$/i.test(item.name),
    )) {
      let job =
        running.get(entry.name) ||
        (await optionalJson(
          path.join(runDir(story), entry.name, 'run.json'),
          null,
        ));
      if (!job) continue;
      if (job.status === 'running' && !running.has(job.id)) {
        job = {
          ...job,
          status: 'interrupted',
          message:
            '服务曾中断，已恢复执行记录。请核对已返回的结果；不会自动重新提交。',
        };
        for (const item of job.items || [])
          if (item.state === 'submitting') item.state = 'unknown_outcome';
          else if (item.state === 'queued') item.state = 'interrupted';
        await persist(story, job);
      }
      // Terminal status is visible only after the final write has released the
      // running guard; otherwise the UI can offer recovery a moment too early.
      result.push(running.has(job.id) ? { ...job, status: 'running' } : job);
    }
    return result.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  };
  const guard = async (story) => {
    if (
      [...running.values()].some((job) => job.episode === path.basename(story))
    )
      throw new WorkbenchError(
        '本集资产任务正在执行，请等待完成后再修改。',
        409,
      );
    for (const name of ['工作台记录', '资产图']) {
      await mkdir(path.join(story, name), { recursive: true });
      await existingChild(story, name);
    }
    await recoverTransactions(backupDir(story));
  };
  const assetFiles = async (story, source) => {
    const assets = parseAssets(source.manifest, source.prompts);
    const includePencil = await pencilEnabled(projectRoot);
    for (const asset of assets) {
      if (!includePencil && asset.kind === 'pencil') {
        asset.required = false;
        asset.declaredStatus = '彩铅暂时停用';
      }
      const file = childPath(path.join(story, '资产图'), asset.filename);
      const info = await fileInfo(file);
      if (info) await existingChild(story, `资产图/${asset.filename}`);
      asset.exists = Boolean(info);
      asset.bytes = info?.size || 0;
      asset.modifiedAt = info?.modifiedAt || null;
      asset.outdated = stale(asset);
      asset.imageUrl = asset.exists
        ? `/api/workbench/assets/media?episode=${encodeURIComponent(path.basename(story))}&key=${asset.key}&v=${asset.modifiedAt}`
        : '';
      if (!asset.size)
        asset.size =
          asset.kind === 'character'
            ? '1024x1536'
            : asset.kind === 'scene_panorama'
              ? '1536x1024'
              : asset.kind === 'scene_grid'
                ? '1536x1024'
                : '1024x1024';
    }
    return assets;
  };
  const state = async (episode) => {
    const story = await storyPath(projectRoot, episode);
    const source = await docs(story);
    return {
      episode,
      hashes: source.hashes,
      assetsReady: /^true(?:\s+#.*)?$/.test(
        metadata(source.manifest, 'assets_ready'),
      ),
      hasScript: Boolean(source.script),
      hasPlan: Boolean(source.manifest),
      characters: section(source.script, '角色表'),
      scenes: section(source.script, '场景表'),
      notes: [
        (await pencilEnabled(projectRoot)) ? '' : PENCIL_PAUSED_MESSAGE,
        metadata(source.manifest, '彩铅参考图'),
        section(source.manifest, '本集说明').trim(),
      ]
        .filter(Boolean)
        .join('\n'),
      assets: await assetFiles(story, source),
      jobs: (await runs(story)).map(publicRun),
    };
  };
  const saveDocs = async (story, before, next, label) => {
    await replaceFiles(
      [
        {
          path: path.join(story, '资产清单.md'),
          content: next.manifest,
          expectedHash: before.hashes.manifest,
        },
        {
          path: path.join(story, '资产提示词.md'),
          content: next.prompts,
          expectedHash: before.hashes.prompts,
        },
      ],
      backupDir(story),
      label,
    );
    return {
      ...next,
      script: before.script,
      hashes: {
        manifest: digest(next.manifest),
        prompts: digest(next.prompts),
        script: before.hashes.script,
      },
    };
  };
  const markDependents = (manifest, prompts, assets, key) => {
    let next = { manifest, prompts };
    const changed = new Set([key]);
    for (let pass = 0; pass < assets.length; pass += 1)
      for (const asset of assets) {
        if (
          changed.has(asset.key) ||
          !asset.refs.some((ref) => changed.has(ref))
        )
          continue;
        next = replaceAssetFilename(
          next.manifest,
          next.prompts,
          asset.filename,
          asset.filename,
          '参考图已更新·待重做',
        );
        changed.add(asset.key);
      }
    return next;
  };
  const savePrompt = (body) =>
    locks(body.episode, async () => {
      const story = await storyPath(projectRoot, body.episode);
      await guard(story);
      const source = await docs(story);
      if (!sameHashes(source.hashes, body.hashes || {}))
        throw new WorkbenchError(
          '本集文件已更新，请刷新后再保存。你的编辑仍留在页面。',
          409,
        );
      const assets = await assetFiles(story, source);
      const asset = assets.find((item) => item.key === body.key);
      if (!asset?.promptEditable)
        throw new WorkbenchError('这条资产没有可直接编辑的生成提示词。');
      if (asset.prompt === body.prompt?.trim()) return state(body.episode);
      let next = replaceAssetFilename(
        source.manifest,
        replacePrompt(source.prompts, asset.filename, body.prompt),
        asset.filename,
        asset.filename,
        '提示词已修改·待重做',
      );
      next = markDependents(next.manifest, next.prompts, assets, asset.key);
      await saveDocs(
        story,
        source,
        { ...next, manifest: setReady(next.manifest, false) },
        `编辑 ${asset.id} 提示词`,
      );
      return state(body.episode);
    });
  const freshJob = async (story, kind, source, extra = {}) => {
    const id = randomUUID();
    const job = {
      id,
      episode: path.basename(story),
      kind,
      status: 'running',
      phase: '准备材料',
      createdAt: new Date().toISOString(),
      cursor: source.hashes,
      items: [],
      models: [],
      ...extra,
    };
    job.recordPath = path
      .relative(projectRoot, path.join(runDir(story), id))
      .replaceAll('\\', '/');
    await mkdir(path.join(runDir(story), id), { recursive: true });
    await writeJson(path.join(runDir(story), id, 'inputs.json'), source);
    await persist(story, job);
    running.set(id, job);
    return job;
  };
  const background = (story, job, operation) => {
    void (async () => {
      try {
        await operation();
      } catch (error) {
        job.status = error.status === 409 ? 'conflict' : 'failed';
        job.message = error.message;
        for (const item of job.items)
          if (item.state === 'queued') {
            item.state = 'blocked';
            item.message = '本次已停止，该项尚未提交。';
          }
      } finally {
        try {
          await persist(story, job);
        } finally {
          running.delete(job.id);
        }
      }
    })().catch((error) => {
      process.stderr.write(
        `Asset job persistence failed: ${error.code || error.name}\n`,
      );
    });
    return publicRun(job);
  };
  const startPlan = (body) =>
    locks(body.episode, async () => {
      const story = await storyPath(projectRoot, body.episode);
      await guard(story);
      const source = await docs(story);
      if (!sameHashes(source.hashes, body.hashes || {}))
        throw new WorkbenchError('上游材料已更新，请刷新后开始。', 409);
      const notes = String(body.notes || '').slice(0, 12000);
      if (source.manifest && !notes.trim())
        throw new WorkbenchError('已有资产方案。请先写明本次要调整的范围。');
      const provider = await models.resolve('assets.plan');
      const materials = await assetSources(projectRoot, story, notes);
      const job = await freshJob(story, 'plan', source, {
        models: [modelReceipt(provider)],
      });
      return background(story, job, async () => {
        const folder = path.join(runDir(story), job.id);
        const schemaPath = path.join(folder, 'schema.json');
        const schema = planSchema(materials.pencilEnabled);
        await writeJson(schemaPath, schema);
        await writeJson(path.join(folder, 'sources.json'), materials);
        let plan;
        for (let round = 0; round < 2; round += 1) {
          job.phase = round ? '独立自查' : '生成资产方案';
          await persist(story, job);
          const prompt = planPrompt(materials, notes, round ? plan : null);
          await writeFile(
            path.join(folder, `prompt-${round + 1}.txt`),
            prompt,
            'utf8',
          );
          const output = await textCall(provider, prompt, schema, {
            projectRoot,
            schemaPath,
            fetcher,
          });
          await writeFile(
            path.join(folder, `response-${round + 1}.txt`),
            String(output),
            'utf8',
          );
          plan = parsePlan(output, materials.pencilEnabled);
        }
        for (const asset of plan.assets)
          if (asset.kind === 'pencil') asset.prompt = PENCIL_PROMPT;
        const next = renderPlan(body.episode, plan);
        dependencyOrder(
          parseAssets(next.manifest, next.prompts),
          parseAssets(next.manifest, next.prompts).map((item) => item.key),
        );
        await writeJson(path.join(folder, 'plan.json'), plan);
        await writeFile(
          path.join(folder, '资产清单.md'),
          next.manifest,
          'utf8',
        );
        await writeFile(
          path.join(folder, '资产提示词.md'),
          next.prompts,
          'utf8',
        );
        await ensureSourcesUnchanged(projectRoot, materials);
        // Keep completed image rows and storyboard mount advice when the exact prompt is unchanged.
        const oldAssets = await assetFiles(story, source);
        const plannedAssets = parseAssets(next.manifest, next.prompts);
        const references = (asset, list) =>
          asset.refs
            .map((key) => list.find((item) => item.key === key)?.filename)
            .join('\n');
        for (const asset of plannedAssets) {
          const old = oldAssets.find(
            (item) =>
              item.filename === asset.filename &&
              item.prompt === asset.prompt &&
              item.kind === asset.kind &&
              references(item, oldAssets) === references(asset, plannedAssets),
          );
          if (old?.exists)
            Object.assign(
              next,
              replaceAssetFilename(
                next.manifest,
                next.prompts,
                asset.filename,
                asset.filename,
                old.declaredStatus,
              ),
            );
        }
        const retainedAssets = oldAssets.filter(
          (asset) =>
            !asset.required &&
            !plannedAssets.some((item) => item.id === asset.id),
        );
        if (retainedAssets.length) {
          const cell = (value) => String(value).replace(/[\r\n|`]/g, ' ');
          const rows = retainedAssets
            .map(
              (asset) =>
                '| ' +
                [
                  asset.number,
                  asset.id,
                  asset.name,
                  asset.filename,
                  asset.source,
                  asset.declaredStatus,
                ]
                  .map(cell)
                  .join(' | ') +
                ' |',
            )
            .join('\n');
          next.manifest = next.manifest.replace(
            '## 每单元挂载建议',
            rows + '\n\n## 每单元挂载建议',
          );
          const promptEntries = retainedAssets
            .filter((asset) => asset.prompt)
            .map(
              (asset) =>
                '### ' +
                asset.id +
                ' ' +
                asset.name +
                '\n\n建议文件名：`' +
                asset.filename +
                '`\n\n```\n' +
                asset.prompt +
                '\n```',
            )
            .join('\n\n');
          if (promptEntries)
            next.prompts += '\n## 既有其他资产\n\n' + promptEntries + '\n';
        }
        const mounts = section(source.manifest, '每单元挂载建议');
        if (mounts)
          next.manifest = next.manifest.replace(
            '由分镜阶段回填。',
            mounts.trim(),
          );
        for (const name of ['manifest', 'prompts']) {
          const originalHeader =
            source[name].match(/^---\r?\n([\s\S]*?)\r?\n---/)?.[1] || '';
          const retained = originalHeader
            .split(/\r?\n/)
            .filter(
              (line) => line && !/^(story|assets_ready|风格预设):/.test(line),
            );
          if (retained.length)
            next[name] = next[name].replace(
              /^---\n/,
              `---\n${retained.join('\n')}\n`,
            );
        }
        job.cursor = (
          await saveDocs(story, source, next, '资产方案及独立自查')
        ).hashes;
        await checkReady(story, job);
        job.review = plan.review;
        job.status = 'completed';
        job.phase = '资产方案已保存';
        job.message = '已完成方案与独立自查，可以编辑提示词或开始生成图片。';
      });
    });
  const adopt = async (story, job, item) => {
    const source = await docs(story);
    if (!sameHashes(source.hashes, job.cursor))
      throw new WorkbenchError(
        '执行期间文件被修改。新图已保留，未覆盖当前清单。',
        409,
      );
    const assets = await assetFiles(story, source);
    const asset = assets.find((entry) => entry.key === item.key);
    if (!asset)
      throw new WorkbenchError('当前清单没有原资产，新图已保留。', 409);
    for (const ref of item.inputs?.refs || []) {
      const currentRef = assets.find(
        (entry) =>
          asset.refs.includes(entry.key) && entry.filename === ref.file,
      );
      if (
        !currentRef ||
        digest(
          await readFile(
            await existingChild(story, `资产图/${currentRef.filename}`),
          ),
        ) !== ref.hash
      )
        throw new WorkbenchError(
          '执行期间参考图文件被修改。新图已保留，未自动采用。',
          409,
        );
    }
    let next = replaceAssetFilename(
      source.manifest,
      source.prompts,
      asset.filename,
      item.result.filename,
      '已生成·技术核验通过',
    );
    next = markDependents(next.manifest, next.prompts, assets, asset.key);
    const updated = await saveDocs(
      story,
      source,
      { ...next, manifest: setReady(next.manifest, false) },
      `采用 ${item.id} 新版本`,
    );
    job.cursor = updated.hashes;
    item.state = 'completed';
    item.message = '已保存新版本';
    await persist(story, job);
  };
  const checkReady = async (story, job) => {
    const source = await docs(story);
    if (!sameHashes(source.hashes, job.cursor))
      throw new WorkbenchError('文件已经更新，未回填放行标记。', 409);
    const assets = (await assetFiles(story, source)).filter(
      (item) => item.required,
    );
    let ready = assets.length > 0;
    const checks = [];
    for (const asset of assets) {
      if (!asset.exists || asset.outdated) {
        ready = false;
        checks.push({
          id: asset.id,
          ok: false,
          reason: asset.exists ? '待重做' : '缺图',
        });
        continue;
      }
      try {
        checks.push({
          id: asset.id,
          ok: true,
          ...(await media.verify(
            await existingChild(story, `资产图/${asset.filename}`),
          )),
        });
      } catch {
        ready = false;
        checks.push({ id: asset.id, ok: false, reason: '技术核验未通过' });
      }
    }
    await writeJson(
      path.join(runDir(story), job.id, 'technical-checks.json'),
      checks,
    );
    const latest = await docs(story);
    if (!sameHashes(latest.hashes, job.cursor))
      throw new WorkbenchError('核验期间文件更新，未写入放行标记。', 409);
    let checkedManifest = latest.manifest;
    for (const check of checks.filter(
      (item) => item.reason === '技术核验未通过',
    )) {
      const asset = assets.find((item) => item.id === check.id);
      checkedManifest = replaceAssetFilename(
        checkedManifest,
        latest.prompts,
        asset.filename,
        asset.filename,
        '技术核验未通过·待重做',
      ).manifest;
    }
    const updated = await saveDocs(
      story,
      latest,
      { manifest: setReady(checkedManifest, ready), prompts: latest.prompts },
      '资产技术放行',
    );
    job.cursor = updated.hashes;
    return ready;
  };
  const startImages = (body) =>
    locks(body.episode, async () => {
      const story = await storyPath(projectRoot, body.episode);
      await guard(story);
      const source = await docs(story);
      if (!sameHashes(source.hashes, body.hashes || {}))
        throw new WorkbenchError('资产文件已变化，请刷新后开始。', 409);
      const assets = await assetFiles(story, source);
      if (!assets.length)
        throw new WorkbenchError('先生成或导入本集资产方案。');
      if (
        !Array.isArray(body.keys) ||
        !body.keys.length ||
        body.keys.length > 160
      )
        throw new WorkbenchError('请选择本次要生成的资产。');
      if (!(await pencilEnabled(projectRoot)) && assets.some(
        (asset) => body.keys.includes(asset.key) && asset.kind === 'pencil',
      ))
        throw new WorkbenchError(PENCIL_PAUSED_MESSAGE, 409);
      const selected = dependencyOrder(assets, body.keys);
      if (selected.some((item) => !item.required || item.kind === 'cover'))
        throw new WorkbenchError('本轮资产入口不处理封面或已停用资产。');
      const ambiguous = (await runs(story))
        .flatMap((job) => job.items || [])
        .find(
          (item) =>
            body.keys.includes(item.key) &&
            !item.resolved &&
            [
              'unknown_outcome',
              'download_failed',
              'received',
              'verifying',
              'conflict',
              'technical_failed',
            ].includes(item.state),
        );
      if (ambiguous)
        throw new WorkbenchError(
          `${ambiguous.id} 的上次结果还未处理，请先恢复结果或填写核实说明。`,
          409,
        );
      for (const asset of selected) {
        if (asset.dependencyIssue)
          throw new WorkbenchError(
            `${asset.id}：${asset.dependencyIssue}`,
            409,
          );
        if (
          !['product_photo', 'scene_grid', 'pencil'].includes(asset.kind) &&
          !asset.prompt
        )
          throw new WorkbenchError(`${asset.id} 没有唯一对应提示词。`, 409);
      }
      const chosen = new Map();
      if (
        selected.some(
          (asset) =>
            !['product_photo', 'scene_grid', 'pencil'].includes(asset.kind),
        )
      )
        chosen.set('image', await models.resolve('assets.image'));
      if (selected.some((asset) => asset.kind === 'pencil'))
        chosen.set('pencil', await models.resolve('project-flow'));
      for (const provider of chosen.values())
        await imageCheck(provider, fetcher);
      const job = await freshJob(story, 'images', source, {
        items: selected.map((asset) => ({
          ...asset,
          sourcePromptHash: digest(asset.prompt),
          prompt: asset.kind === 'pencil' ? PENCIL_PROMPT : asset.prompt,
          state: 'queued',
        })),
        models: [...chosen.values()].map(modelReceipt),
      });
      return background(story, job, async () => {
        const folder = path.join(runDir(story), job.id);
        for (const item of job.items) {
          if (item.kind === 'pencil' && !(await pencilEnabled(projectRoot))) {
            item.state = 'blocked';
            item.message = PENCIL_PAUSED_MESSAGE;
            await persist(story, job);
            continue;
          }
          if (!sameHashes((await docs(story)).hashes, job.cursor))
            throw new WorkbenchError(
              '执行期间上游文件发生修改，已停止后续提交。',
              409,
            );
          if (
            item.refs.some(
              (key) =>
                job.items.find((entry) => entry.key === key)?.state &&
                job.items.find((entry) => entry.key === key).state !==
                  'completed',
            )
          ) {
            item.state = 'blocked';
            item.message = '基础图未完成';
            await persist(story, job);
            continue;
          }
          job.phase = `处理 ${item.id} ${item.name}`;
          const current = await assetFiles(story, await docs(story));
          const refs = await Promise.all(
            item.refs.map(async (key) => {
              const ref = current.find((entry) => entry.key === key);
              if (!ref?.exists || ref.outdated)
                throw new WorkbenchError(
                  `${item.id} 的参考图缺失或待重做。`,
                  409,
                );
              return existingChild(story, `资产图/${ref.filename}`);
            }),
          );
          item.inputs = { promptHash: digest(item.prompt), refs: [] };
          const snapshotRefs = [];
          for (let index = 0; index < refs.length; index += 1) {
            const bytes = await readFile(refs[index]);
            const snapshotFile = `${item.key}-ref-${index}${path.extname(refs[index])}`;
            await writeFile(path.join(folder, snapshotFile), bytes, {
              flag: 'wx',
            });
            await media.verify(path.join(folder, snapshotFile));
            item.inputs.refs.push({
              file: path.basename(refs[index]),
              hash: digest(bytes),
              snapshotFile,
            });
            snapshotRefs.push(path.join(folder, snapshotFile));
          }
          item.outputFilename = versionName(item.filename);
          const output = childPath(
            path.join(story, '资产图'),
            item.outputFilename,
          );
          await mkdir(path.dirname(output), { recursive: true });
          try {
            let technical;
            if (item.kind === 'product_photo') {
              if (!item.sourceFile) {
                item.state = 'blocked';
                item.message = '请导入产品实拍照片';
                await persist(story, job);
                continue;
              }
              const photo = await existingChild(
                path.join(projectRoot, '产品图'),
                item.sourceFile.replace(/^产品图\//, ''),
              );
              item.rawFile = `${item.key}-product-source${path.extname(photo)}`;
              await writeFile(
                path.join(folder, item.rawFile),
                await readFile(photo),
                { flag: 'wx' },
              );
              item.state = 'verifying';
              await persist(story, job);
              technical = await media.normalize(
                path.join(folder, item.rawFile),
                output,
              );
            } else if (item.kind === 'scene_grid') {
              item.state = 'verifying';
              await persist(story, job);
              technical = await media.grid(snapshotRefs[0], output);
            } else {
              const provider = chosen.get(
                item.kind === 'pencil' ? 'pencil' : 'image',
              );
              item.model = modelReceipt(provider);
              item.state = 'submitting';
              await persist(story, job);
              const bytes = await imageCall(
                provider,
                { prompt: item.prompt, refs: snapshotRefs, size: item.size },
                {
                  fetcher,
                  onResponse: async (raw) => {
                    item.responseFile = `${item.key}-response.json`;
                    await writeFile(path.join(folder, item.responseFile), raw);
                    await persist(story, job);
                  },
                },
              );
              item.rawFile = `${item.key}-raw-image`;
              await writeFile(path.join(folder, item.rawFile), bytes, {
                flag: 'wx',
              });
              item.state = 'received';
              await persist(story, job);
              item.state = 'verifying';
              await persist(story, job);
              technical = await media.normalize(
                path.join(folder, item.rawFile),
                output,
                expectedSize(item),
              );
            }
            item.result = { filename: item.outputFilename, ...technical };
            await persist(story, job);
            await adopt(story, job, item);
          } catch (error) {
            item.state =
              error.status === 409
                ? 'conflict'
                : error.outcome ||
                  (item.state === 'submitting'
                    ? 'unknown_outcome'
                    : item.state === 'verifying'
                      ? 'technical_failed'
                      : 'failed');
            item.message = error.message;
            await persist(story, job);
            if (['unknown_outcome', 'conflict'].includes(item.state)) {
              job.message = error.message;
              break;
            }
          }
        }
        for (const item of job.items)
          if (item.state === 'queued') {
            item.state = 'blocked';
            item.message = '本次已停止，该项尚未提交。';
          }
        job.phase = '核对本集文件';
        await persist(story, job);
        const ready = await checkReady(story, job);
        const complete = job.items.every((item) => item.state === 'completed');
        job.status = complete ? 'completed' : 'needs_attention';
        job.phase = complete ? '本次范围已完成' : '有待处理条目';
        job.message = complete
          ? ready
            ? '本次生成完成，全套资产技术核验通过，可以进入分镜。'
            : '本次范围已完成；本集还有缺图或待重做条目。'
          : '已保留成功结果；请处理未完成条目。';
      });
    });
  const importPhoto = (body) =>
    locks(body.episode, async () => {
      const story = await storyPath(projectRoot, body.episode);
      await guard(story);
      const source = await docs(story);
      if (!sameHashes(source.hashes, body.hashes || {}))
        throw new WorkbenchError('文件已更新，请刷新后导入。', 409);
      const asset = (await assetFiles(story, source)).find(
        (item) => item.key === body.key,
      );
      if (asset?.kind !== 'product_photo')
        throw new WorkbenchError('这个导入入口仅用于产品实拍图。');
      if (
        typeof body.data !== 'string' ||
        body.data.length > 28_000_000 ||
        !/^[A-Za-z0-9+/]+={0,2}$/.test(body.data)
      )
        throw new WorkbenchError('照片内容无效或超过 20 MB。');
      const job = await freshJob(story, 'import', source, {
        items: [{ ...asset, state: 'verifying' }],
      });
      return background(story, job, async () => {
        const item = job.items[0];
        item.rawFile = `${item.key}-raw-image`;
        await writeFile(
          path.join(runDir(story), job.id, item.rawFile),
          Buffer.from(body.data, 'base64'),
          { flag: 'wx' },
        );
        item.outputFilename = versionName(asset.filename);
        const technical = await media.normalize(
          path.join(runDir(story), job.id, item.rawFile),
          childPath(path.join(story, '资产图'), item.outputFilename),
        );
        item.result = { filename: item.outputFilename, ...technical };
        await adopt(story, job, item);
        await checkReady(story, job);
        job.status = 'completed';
        job.phase = '实拍图已导入';
        job.message = '已保存实拍图新版本，并核对本集资产。';
      });
    });
  const recover = (body) =>
    locks(body.episode, async () => {
      const story = await storyPath(projectRoot, body.episode);
      await guard(story);
      const job = (await runs(story)).find(
        (item) => item.id === safeId(body.jobId),
      );
      const item = job?.items.find((entry) => entry.key === body.key);
      if (!item || item.state === 'completed')
        throw new WorkbenchError('没有可处理的执行结果。');
      if (body.action === 'resolve') {
        if (typeof body.note !== 'string' || body.note.trim().length < 4)
          throw new WorkbenchError('请填写核对原平台结果后的处理说明。');
        item.resolved = {
          at: new Date().toISOString(),
          note: body.note.trim().slice(0, 2000),
        };
        await persist(story, job);
        return publicRun(job);
      }
      const source = await docs(story);
      if (!sameHashes(source.hashes, body.hashes || {}))
        throw new WorkbenchError('文件已更新，请刷新后恢复。', 409);
      const current = (await assetFiles(story, source)).find(
        (entry) => entry.key === item.key,
      );
      if (
        !current ||
        digest(current.prompt) !==
          (item.sourcePromptHash || digest(item.prompt))
      )
        throw new WorkbenchError(
          '当前提示词已改变。原图保留在执行记录中，请核对后手动处理。',
          409,
        );
      for (const ref of item.inputs?.refs || []) {
        const file = current.refs
          .map((key) =>
            parseAssets(source.manifest, source.prompts).find(
              (entry) => entry.key === key,
            ),
          )
          .find((entry) => entry?.filename === ref.file);
        if (
          !file ||
          digest(
            await readFile(
              await existingChild(story, `资产图/${file.filename}`),
            ),
          ) !== ref.hash
        )
          throw new WorkbenchError(
            '当前参考图已改变，不能自动采用旧输入的结果。',
            409,
          );
      }
      job.cursor = source.hashes;
      job.status = 'running';
      running.set(job.id, job);
      await persist(story, job);
      return background(story, job, async () => {
        const folder = path.join(runDir(story), job.id);
        let output = childPath(
          path.join(story, '资产图'),
          item.outputFilename || versionName(item.filename),
        );
        let technical;
        if (await fileInfo(output)) {
          try {
            technical = await media.verify(output, expectedSize(item));
          } catch {
            output = childPath(
              path.join(story, '资产图'),
              versionName(item.filename),
            );
          }
        }
        if (
          !technical &&
          item.kind === 'scene_grid' &&
          item.inputs?.refs?.[0]?.snapshotFile
        ) {
          technical = await media.grid(
            childPath(folder, item.inputs.refs[0].snapshotFile),
            output,
          );
        }
        if (!technical) {
          let bytes;
          if (item.rawFile && (await fileInfo(path.join(folder, item.rawFile))))
            bytes = await readFile(path.join(folder, item.rawFile));
          else if (item.responseFile) {
            const response = await optionalJson(
              path.join(folder, item.responseFile),
              {},
            );
            const inline = response.candidates
              ?.flatMap((entry) => entry.content?.parts || [])
              .find((part) => part.inlineData?.data || part.inline_data?.data);
            const base64 =
              response.data?.[0]?.b64_json ||
              inline?.inlineData?.data ||
              inline?.inline_data?.data;
            if (base64) bytes = Buffer.from(base64, 'base64');
            else if (response.data?.[0]?.url) {
              const url = new URL(response.data[0].url);
              if (
                !['http:', 'https:'].includes(url.protocol) ||
                url.username ||
                url.password
              )
                throw new WorkbenchError('下载地址无效。');
              const downloaded = await fetcher(url, {
                signal: AbortSignal.timeout(120_000),
              });
              if (!downloaded.ok)
                throw new WorkbenchError(
                  `恢复下载返回 HTTP ${downloaded.status}。`,
                );
              bytes = await limitedBytes(downloaded, 65_000_000);
            }
          }
          if (!bytes)
            throw new WorkbenchError(
              '记录中没有可恢复的图片，需要先核实原平台结果。',
              409,
            );
          item.rawFile = `${item.key}-recovered-${stamp()}`;
          await writeFile(path.join(folder, item.rawFile), bytes, {
            flag: 'wx',
          });
          technical = await media.normalize(
            path.join(folder, item.rawFile),
            output,
            expectedSize(item),
          );
        }
        item.result = { filename: path.basename(output), ...technical };
        await adopt(story, job, item);
        await checkReady(story, job);
        job.status = job.items.every((entry) => entry.state === 'completed')
          ? 'completed'
          : 'needs_attention';
        job.phase = '原结果已恢复';
        job.message = '已恢复原结果，没有重新提交生成。';
      });
    });
  const verifyExisting = (body) =>
    locks(body.episode, async () => {
      const story = await storyPath(projectRoot, body.episode);
      await guard(story);
      const source = await docs(story);
      if (!source.manifest || !sameHashes(source.hashes, body.hashes || {}))
        throw new WorkbenchError(
          '资产文件已变化或尚未建立，请刷新后核对。',
          409,
        );
      const job = await freshJob(story, 'verify', source);
      return background(story, job, async () => {
        const ready = await checkReady(story, job);
        job.status = 'completed';
        job.phase = '已有文件核对完成';
        job.message = ready
          ? '现有资产技术核验通过，可以进入分镜。'
          : '核对已完成，本集还有缺图或待重做条目。';
      });
    });
  const serveImage = async (response, url) => {
    const story = await storyPath(projectRoot, url.searchParams.get('episode'));
    const asset = parseAssets((await docs(story)).manifest, '').find(
      (item) => item.key === url.searchParams.get('key'),
    );
    if (!asset) throw new WorkbenchError('图片不在本集当前清单中。', 404);
    const file = await existingChild(story, `资产图/${asset.filename}`);
    response.writeHead(200, {
      'Content-Type': /\.jpe?g$/i.test(file)
        ? 'image/jpeg'
        : /\.webp$/i.test(file)
          ? 'image/webp'
          : 'image/png',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    });
    createReadStream(file)
      .on('error', () => response.destroy())
      .pipe(response);
  };
  return {
    activeCount: () => running.size,
    state,
    savePrompt,
    startPlan,
    startImages,
    importPhoto,
    verifyExisting,
    recover,
    serveImage,
  };
}
