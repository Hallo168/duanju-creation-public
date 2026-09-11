import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { mkdir, realpath, rename, unlink } from 'node:fs/promises';
import { createMediaTools } from './media.mjs';
import { runProcess } from './model-providers.mjs';
import { digest, existingChild, fileInfo, storyPath, WorkbenchError } from './storage.mjs';

const IMAGE_EXTENSION = /\.(png|jpe?g|webp)$/i;
const revision = (info) => info ? `${info.modifiedAt}-${info.size}` : '';

function within(root, file) {
  const relative = path.relative(root, file);
  return relative && !relative.startsWith('..') && !path.isAbsolute(relative)
    ? relative.split(path.sep).join('/') : null;
}

// A small queue prevents a long episode from spawning one Python process per image.
function queue(limit) {
  let active = 0;
  const waiting = [];
  return async (operation) => {
    if (active >= limit) await new Promise((resolve) => waiting.push(resolve));
    else active += 1;
    try { return await operation(); }
    finally {
      const next = waiting.shift();
      if (next) next();
      else active -= 1;
    }
  };
}

export function createStoryboardReferences({ projectRoot, directory, cacheRoot, run = runProcess, media = createMediaTools(directory, projectRoot) }) {
  const productRoot = path.join(projectRoot, '产品图');
  const script = path.join(directory, 'server', 'storyboard-references.py');
  const pending = new Map();
  const thumbnailQueue = queue(2);
  const describeQueue = queue(2);

  async function describe(episode, sections) {
    const story = await storyPath(projectRoot, episode);
    const actualProductRoot = await realpath(productRoot).catch((error) => {
      if (error.code === 'ENOENT') return productRoot;
      throw error;
    });
    const result = JSON.parse(await describeQueue(() => run('python', [script], {
      cwd: projectRoot,
      input: JSON.stringify({ story, units: sections.map(({ number, section }) => ({ number, section })) }),
      timeoutMs: 30_000,
    })));
    const files = new Map();
    async function imageLocation(file) {
      if (!files.has(file)) files.set(file, (async () => {
        const storyRelative = within(story, file);
        const productRelative = within(actualProductRoot, file);
        const relative = storyRelative || productRelative;
        if (!relative || !IMAGE_EXTENSION.test(relative)) throw new Error('图片不在本集或产品图库中。');
        const root = storyRelative ? story : actualProductRoot;
        const checked = await existingChild(root, relative);
        const info = await fileInfo(checked);
        if (!info?.size) throw new Error('图片已移动或为空，请刷新后核对。');
        const params = new URLSearchParams({ episode, source: storyRelative ? 'story' : 'product', file: relative, v: revision(info) });
        const imageUrl = `/api/storyboard/reference-image?${params}`;
        return { imageUrl, thumbnailUrl: `${imageUrl}&size=thumbnail` };
      })());
      return files.get(file);
    }
    return Object.fromEntries(await Promise.all(result.units.map(async (unit) => [unit.number, {
      error: unit.error,
      items: await Promise.all(unit.items.map(async ({ path: file, ...item }) => {
        if (!file || item.status !== 'ready') return item;
        try { return { ...item, ...await imageLocation(file) }; }
        catch (error) { return { ...item, status: 'missing', message: error.message }; }
      })),
    }])));
  }

  async function thumbnail(file, info) {
    const key = digest(`storyboard-thumb-v1:${file}:${revision(info)}`);
    const output = path.join(cacheRoot, `${key}.webp`);
    if ((await fileInfo(output))?.size) return output;
    if (!pending.has(key)) {
      const job = thumbnailQueue(async () => {
        await mkdir(cacheRoot, { recursive: true });
        if ((await fileInfo(output))?.size) return output;
        const temporary = path.join(cacheRoot, `${key}-${randomUUID()}.webp`);
        try {
          await media.thumbnail(file, temporary);
          if (!(await fileInfo(temporary))?.size) throw new Error('缩略图未生成，请刷新后重试。');
          if (revision(await fileInfo(file)) !== revision(info)) throw new WorkbenchError('图片已更新，请刷新后查看。', 409);
          await rename(temporary, output);
          return output;
        } finally {
          await unlink(temporary).catch((error) => { if (error.code !== 'ENOENT') throw error; });
        }
      }).finally(() => pending.delete(key));
      pending.set(key, job);
    }
    return pending.get(key);
  }

  async function image(url, headers = {}) {
    const story = await storyPath(projectRoot, url.searchParams.get('episode'));
    const source = url.searchParams.get('source');
    if (!['story', 'product'].includes(source)) throw new WorkbenchError('图片来源无效。');
    const relative = url.searchParams.get('file');
    if (!relative || !IMAGE_EXTENSION.test(relative)) throw new WorkbenchError('图片格式不受支持。');
    const file = await existingChild(source === 'story' ? story : productRoot, relative);
    const info = await fileInfo(file);
    if (!info?.size) throw new WorkbenchError('图片不存在或为空。', 404);
    if (url.searchParams.get('v') !== revision(info)) throw new WorkbenchError('图片已更新，请刷新后查看。', 409);
    const size = url.searchParams.get('size');
    if (size && size !== 'thumbnail') throw new WorkbenchError('图片尺寸选项无效。');
    const etag = `"${digest(`${file}:${revision(info)}:${size || 'original'}:v1`)}"`;
    const responseHeaders = {
      'Cache-Control': 'private, max-age=86400, immutable',
      'X-Content-Type-Options': 'nosniff',
      ETag: etag,
      'Content-Type': size === 'thumbnail' || /\.webp$/i.test(file) ? 'image/webp' : /\.jpe?g$/i.test(file) ? 'image/jpeg' : 'image/png',
    };
    if (headers['if-none-match'] === etag) return { status: 304, headers: responseHeaders };
    const output = size === 'thumbnail' ? await thumbnail(file, info) : file;
    return { status: 200, file: output, headers: { ...responseHeaders, 'Content-Length': (await fileInfo(output)).size } };
  }

  return { describe, image };
}
