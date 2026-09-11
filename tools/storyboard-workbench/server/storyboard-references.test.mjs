import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, realpath, rm, symlink } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { createStoryboardReferences } from './storyboard-references.mjs';
import { createMediaTools } from './media.mjs';
import { runProcess } from './model-providers.mjs';

const directory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const episode = '91-图片测试';

async function fixture(t, options = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'storyboard-reference-test-'));
  t.after(async () => {
    assert.equal(path.dirname(root), os.tmpdir());
    assert.ok(path.basename(root).startsWith('storyboard-reference-test-'));
    await rm(root, { recursive: true, force: true });
  });
  const story = path.join(root, 'stories', episode);
  const assets = path.join(story, '资产图');
  await mkdir(assets, { recursive: true });
  await mkdir(path.join(root, '产品图', '牙膏'), { recursive: true });
  const service = createStoryboardReferences({ projectRoot: root, directory, cacheRoot: path.join(root, 'cache'), ...options });
  const put = async (name, bytes = 'test image') => {
    const file = path.join(assets, name);
    await writeFile(file, bytes);
    return file;
  };
  const describe = (line, number = 1) => service.describe(episode, [{ number, section: `## 单元${number}（测试）\n视频挂载：${line}\n` }]);
  return { root, story, assets, service, put, describe };
}

test('reference images preserve upload order, repeats, and exact clothing/version', async (t) => {
  const f = await fixture(t);
  await f.put('R1-周明-旧衣-人物设定图-v3.png');
  await f.put('R1-周明-会议-人物设定图-v3.png');
  await f.put('S1-会议室-四宫格.png');
  const result = (await f.describe('第1个上传 S1-会议室-四宫格.png / 第2个上传 R1C 周明·写实双格v3（R1-周明-会议-人物设定图-v3.png） / 第3个上传 S1-会议室-四宫格.png'))[1];
  assert.deepEqual(result.items.map((item) => item.number), [1, 2, 3]);
  assert.ok(result.items.every((item) => item.status === 'ready'));
  assert.equal(result.items[1].filename, 'R1-周明-会议-人物设定图-v3.png');
  assert.equal(result.items[1].assetId, 'R1C');
  assert.match(result.items[1].detail, /会议.*v3/);
  assert.equal(result.items[0].thumbnailUrl, result.items[2].thumbnailUrl);
});

test('semicolon and Markdown mounts keep full paths and use the real product image', async (t) => {
  const f = await fixture(t);
  const person = await f.put('R1-周明-人物设定图.png');
  const product = path.join(f.root, '产品图', '牙膏', '实拍 包装.png');
  await writeFile(product, 'real product fixture');
  const line = `第1个上传 [P1 实拍](<${product.split(path.sep).join('/')}>)；第2个上传 [R1 周明](资产图/${path.basename(person)})`;
  const items = (await f.describe(line))[1].items;
  assert.equal(items.length, 2);
  assert.ok(items.every((item) => item.status === 'ready'));
  assert.equal(new URL(items[0].imageUrl, 'http://localhost').searchParams.get('source'), 'product');
  assert.equal(items[1].filename, path.basename(person));
  const semicolons = (await f.describe('第1个上传 R1-周明-人物设定图.png；第2个上传 R1-周明-人物设定图.png'))[1].items;
  assert.equal(semicolons.length, 2);
  assert.ok(semicolons.every((item) => item.status === 'ready'));
});

test('ambiguous, missing, and incorrect upload labels remain visible without substituting images', async (t) => {
  const f = await fixture(t);
  await f.put('R1-周明-旧衣-人物设定图-v3.png');
  await f.put('R1-周明-会议-人物设定图-v3.png');
  const items = (await f.describe('第1个上传 R1 人物设定图 / 第2个上传 R9 不存在 / 第4个上传 R1-周明-会议-人物设定图-v3.png'))[1].items;
  assert.deepEqual(items.map((item) => item.status), ['ambiguous', 'missing', 'ambiguous']);
  assert.ok(items.every((item) => !item.imageUrl));
  const absent = await f.service.describe(episode, [{ number: 9, section: '## 单元9（没有挂载）\n' }]);
  assert.match(absent[9].error, /未找到视频挂载/);
  assert.equal(absent[9].items.length, 0);
});

test('an explicitly missing version never falls back to the sole older file', async (t) => {
  const f = await fixture(t);
  await f.put('R1-周明-会议-人物设定图-v3.png');
  const item = (await f.describe('第1个上传 R1-周明-会议-人物设定图-v4.png'))[1].items[0];
  assert.equal(item.status, 'missing');
  assert.equal(item.imageUrl, undefined);
});

test('legacy asset aliases before exact filenames do not turn valid images into missing slots', async (t) => {
  const f = await fixture(t);
  await f.put('R1-姚春枝-日常-人物设定图.png');
  await f.put('S1-饺子铺前店-四宫格.png');
  const items = (await f.describe('第1个上传 R1A R1-姚春枝-日常-人物设定图.png / 第2个上传 S1 S1-饺子铺前店-四宫格.png'))[1].items;
  assert.deepEqual(items.map((item) => item.status), ['ready', 'ready']);
  assert.deepEqual(items.map((item) => item.assetId), ['R1A', 'S1']);
});

test('media access rejects traversal, outside links, stale versions, and unsupported formats', async (t) => {
  const f = await fixture(t);
  const file = await f.put('R1-周明-人物设定图.png');
  const outside = path.join(f.root, 'outside.png');
  await writeFile(outside, 'not in the image library');
  const outsideItem = (await f.describe(`第1个上传 [R1 外部](<${outside.split(path.sep).join('/')}>)`))[1].items[0];
  assert.equal(outsideItem.status, 'missing');
  assert.equal(outsideItem.imageUrl, undefined);
  const item = (await f.describe('第1个上传 R1-周明-人物设定图.png'))[1].items[0];
  const url = new URL(item.imageUrl, 'http://localhost');
  const served = await f.service.image(url);
  assert.equal(served.file, await realpath(file));
  assert.equal(served.headers['Content-Type'], 'image/png');
  assert.equal((await f.service.image(url, { 'if-none-match': served.headers.ETag })).status, 304);
  const traversal = new URL(url);
  traversal.searchParams.set('file', '../../outside.png');
  await assert.rejects(f.service.image(traversal), /文件必须位于/);
  const unsupported = new URL(url);
  unsupported.searchParams.set('file', '资产图/secret.json');
  await assert.rejects(f.service.image(unsupported), /格式不受支持/);
  await writeFile(file, 'a changed image with a different length');
  await assert.rejects(f.service.image(url), /图片已更新/);
  const updated = (await f.describe('第1个上传 R1-周明-人物设定图.png'))[1].items[0];
  assert.notEqual(updated.thumbnailUrl, item.thumbnailUrl);
});

test('a junction cannot escape the permitted image root', async (t) => {
  const f = await fixture(t);
  const outside = path.join(f.root, 'elsewhere');
  await mkdir(outside);
  await writeFile(path.join(outside, 'R1-外部.png'), 'test');
  await symlink(outside, path.join(f.assets, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
  const item = (await f.describe('第1个上传 [R1 外部](资产图/linked/R1-外部.png)'))[1].items[0];
  assert.equal(item.status, 'missing');
});

test('concurrent thumbnail requests share a cache and original pixels remain untouched', async (t) => {
  let calls = 0;
  const media = createMediaTools(directory, directory);
  const f = await fixture(t, { media: { thumbnail: async (...args) => { calls += 1; return media.thumbnail(...args); } } });
  const root = f.root;
  const file = path.join(f.assets, 'R1-周明-人物设定图.png');
  await runProcess('python', ['-c', 'from PIL import Image; import sys; im=Image.new("RGB",(1024,1536),"red"); im.putpixel((0,0),(0,255,0)); im.save(sys.argv[1])', file], { cwd: root });
  const before = await readFile(file);
  const item = (await f.describe(path.basename(file)))[1].items[0];
  const url = new URL(item.thumbnailUrl, 'http://localhost');
  const [one, two] = await Promise.all([f.service.image(url), f.service.image(url)]);
  assert.equal(calls, 1);
  assert.equal(one.file, two.file);
  const dimensions = JSON.parse(await runProcess('python', ['-c', 'from PIL import Image; import json,sys; im=Image.open(sys.argv[1]); im.load(); print(json.dumps({"size":im.size,"format":im.format}))', one.file], { cwd: root }));
  assert.deepEqual(dimensions.size, [320, 480]);
  assert.equal(dimensions.format, 'WEBP');
  assert.deepEqual(await readFile(file), before);
  await f.service.image(url);
  assert.equal(calls, 1);
});
