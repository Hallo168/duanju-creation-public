import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile, readdir, stat } from 'node:fs/promises';
import { WorkbenchError, digest, childPath, existingChild, optionalJson, writeJson, createLocks } from './storage.mjs';

export const DOCUMENT_LIMIT = 2 * 1024 * 1024;
const idPattern = /^[a-f0-9-]{36}$/i;
const cleanRelative = (value) => {
  if (typeof value !== 'string' || !value || value.includes('\0') || value.includes(':')) throw new WorkbenchError('资料路径无效。');
  const rel = value.replaceAll('\\', '/').replace(/^\.\//, '');
  if (rel.startsWith('/') || rel.split('/').some((part) => !part || part === '.' || part === '..')) throw new WorkbenchError('资料路径无效。');
  return rel;
};
export const normalizeStudioPath = cleanRelative;

export function decodeDocument(bytes) {
  if (!bytes.length) throw new WorkbenchError('文件是空的。');
  if (bytes.length > DOCUMENT_LIMIT) throw new WorkbenchError('每份原稿最多 2 MB，文件没有被截断。', 413);
  let text, encoding = 'UTF-8';
  try {
    if (bytes[0] === 255 && bytes[1] === 254) {
      encoding = 'UTF-16 LE'; text = new TextDecoder('utf-16le', { fatal: true }).decode(bytes);
    } else if (bytes[0] === 254 && bytes[1] === 255) {
      encoding = 'UTF-16 BE'; text = new TextDecoder('utf-16be', { fatal: true }).decode(bytes);
    } else {
      try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
      catch { encoding = 'GB18030 / GBK'; text = new TextDecoder('gb18030', { fatal: true }).decode(bytes); }
    }
  } catch { throw new WorkbenchError('无法读取文件编码，请另存为 UTF-8 后导入。'); }
  if (!text.trim() || Array.from(text).some((character) => character.charCodeAt(0) < 32 && !['\t', '\n', '\r'].includes(character))) throw new WorkbenchError('请导入包含台词的纯文本 TXT 或 MD 文件。');
  return { text, encoding };
}

export const LIBRARY_CATEGORIES = [
  { id: 'originals', label: '对标原稿', directory: '对标原稿', purpose: '保留来源全文；指定对标时调用。' },
  { id: 'structures', label: '剧情结构', directory: '爆款结构库', index: '爆款结构库.md', purpose: '机制查重、选题引用与使用记录。' },
  { id: 'commerce', label: '带货框架', directory: '带货框架库', index: '带货框架库.md', purpose: '选题指定框架，成稿读取对应框架。' },
  { id: 'openings', label: '开头库', files: ['knowledge/爆款开头库.md'], purpose: '选题的开场阶段按原流程调用。' },
  { id: 'phrases', label: '转化话术', files: ['knowledge/转化话术库.md'], purpose: '带货成稿按功能和说话人调用。' },
  { id: 'techniques', label: '手法库', files: ['knowledge/手法库.md'], purpose: '成稿调用；新手法候选在对话中确认。' },
  { id: 'products', label: '产品资料', products: true, purpose: '产品事实与专属素材的来源。' },
  { id: 'experience', label: '账号与经验', files: ['knowledge/账号档案.md', 'knowledge/偏好库.md', 'knowledge/审稿修改经验.md'], purpose: '选题、成稿、审稿按阶段读取。' },
];

async function safeMarkdownFiles(root, folder) {
  let directory;
  try { directory = await existingChild(root, folder); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  const entries = await readdir(directory, { withFileTypes: true });
  return entries.filter((entry) => entry.isFile() && entry.name.endsWith('.md')).map((entry) => `${folder}/${entry.name}`);
}

export function createStudioLibrary({ projectRoot, directory }) {
  const sourceRoot = path.join(directory, '.local', 'studio-sources');
  const lock = createLocks();
  const sourcePath = (id) => {
    if (!idPattern.test(id ?? '')) throw new WorkbenchError('原稿编号无效。');
    return childPath(sourceRoot, id);
  };
  const getSource = async (id, sessionId) => {
    const folder = sourcePath(id);
    const metadata = await optionalJson(path.join(folder, 'source.json'), null);
    if (!metadata || metadata.sessionId !== sessionId) throw new WorkbenchError('本会话没有这份原稿。', 404);
    const rel = `${id}/versions/${metadata.hash}.md`;
    const text = await readFile(await existingChild(sourceRoot, rel), 'utf8');
    if (digest(text) !== metadata.hash) throw new WorkbenchError('原稿版本校验失败，请重新导入。', 409);
    return { ...metadata, text, path: path.relative(projectRoot, path.join(folder, 'versions', `${metadata.hash}.md`)).replaceAll('\\', '/') };
  };
  const saveSource = async (body) => lock(`source:${body.sessionId}`, async () => {
    if (!idPattern.test(body.sessionId ?? '')) throw new WorkbenchError('请先打开一个工作会话。');
    if (body.sourceId) {
      const previous = await getSource(body.sourceId, body.sessionId);
      if (body.expectedHash !== previous.hash) throw new WorkbenchError('原稿已更新，请刷新后再保存。', 409);
      if (typeof body.text !== 'string' || !body.text.trim() || Buffer.byteLength(body.text) > DOCUMENT_LIMIT) throw new WorkbenchError('原稿为空或超过 2 MB，未保存修改。');
      const text = body.text;
      const metadata = { ...previous, title: String(body.title ?? previous.title).trim().slice(0, 160), hash: digest(text), updatedAt: new Date().toISOString(), edited: true };
      delete metadata.text; delete metadata.path;
      const folder = sourcePath(metadata.id);
      await writeFile(path.join(folder, 'versions', `${metadata.hash}.md`), text, 'utf8');
      await writeJson(path.join(folder, 'source.json'), metadata);
      return getSource(metadata.id, body.sessionId);
    }
    if (typeof body.filename !== 'string' || body.filename.includes('\0') || /[\\/:]/.test(body.filename) || !/\.(txt|md)$/i.test(body.filename) || body.filename.length > 200) throw new WorkbenchError('仅支持文件名有效的 TXT 或 MD 文件。');
    if (typeof body.base64 !== 'string' || body.base64.length > Math.ceil(DOCUMENT_LIMIT / 3) * 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(body.base64)) throw new WorkbenchError('文件内容无效或超过 2 MB。', 413);
    const bytes = Buffer.from(body.base64, 'base64');
    const decoded = decodeDocument(bytes);
    const metadata = { id: randomUUID(), sessionId: body.sessionId, filename: body.filename, title: body.filename.replace(/\.(txt|md)$/i, ''), size: bytes.length, encoding: decoded.encoding, originalHash: digest(bytes), hash: digest(decoded.text), edited: false, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    const folder = sourcePath(metadata.id);
    await mkdir(path.join(folder, 'versions'), { recursive: true });
    await writeFile(path.join(folder, `original${path.extname(body.filename).toLowerCase()}`), bytes, { flag: 'wx' });
    await writeFile(path.join(folder, 'versions', `${metadata.hash}.md`), decoded.text, { encoding: 'utf8', flag: 'wx' });
    await writeJson(path.join(folder, 'source.json'), metadata);
    return getSource(metadata.id, body.sessionId);
  });
  const catalog = async () => {
    const groups = [];
    for (const category of LIBRARY_CATEGORIES) {
      const files = category.files ? [...category.files] : [];
      if (category.directory) files.push(...await safeMarkdownFiles(projectRoot, category.directory));
      if (category.index) files.unshift(category.index);
      if (category.products) {
        let products = [];
        try { products = await readdir(await existingChild(projectRoot, '产品图'), { withFileTypes: true }); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
        for (const product of products.filter((item) => item.isDirectory())) files.push(`产品图/${product.name}/产品规格.md`);
      }
      const entries = [];
      for (const file of files) {
        let info;
        try { info = await stat(await existingChild(projectRoot, file)); }
        catch (error) { if (error.code === 'ENOENT') continue; throw error; }
        const internal = file.startsWith('knowledge/') || /\/框架-/.test(file) || file === category.index;
        entries.push({ path: file, name: category.products ? file.split('/')[1] : path.basename(file, '.md'), size: info.size, modifiedAt: info.mtime.toISOString(), readable: !internal, reusable: category.id === 'originals' || (category.id === 'structures' && !internal) });
      }
      groups.push({ id: category.id, label: category.label, purpose: category.purpose, entries: entries.sort((a, b) => b.modifiedAt.localeCompare(a.modifiedAt) || a.name.localeCompare(b.name, 'zh-CN')), count: entries.length });
    }
    return { groups };
  };
  const document = async (requested) => {
    const rel = cleanRelative(requested);
    const { groups } = await catalog();
    const entry = groups.flatMap((group) => group.entries).find((item) => item.path === rel);
    if (!entry?.readable) throw new WorkbenchError('此内容包含后台规范，不提供原文查看。', 403);
    if (entry.size > DOCUMENT_LIMIT) throw new WorkbenchError('文件超过预览限制，未截断内容。', 413);
    const content = await readFile(await existingChild(projectRoot, rel), 'utf8');
    return { ...entry, content, hash: digest(content) };
  };
  const reference = async (requested) => {
    const rel = cleanRelative(requested);
    const { groups } = await catalog();
    const entry = groups.flatMap((group) => group.entries).find((item) => item.path === rel);
    if (!entry?.reusable) throw new WorkbenchError('请选择自己的对标原稿或结构卡。');
    return { ...entry, hash: digest(await readFile(await existingChild(projectRoot, rel), 'utf8')) };
  };
  return { saveSource, getSource, catalog, document, reference };
}

export function isPrivateInstructionPath(file) {
  return file === 'CLAUDE.md' || file === '跨层契约.md' || file.startsWith('.claude/') || /^knowledge\/(?:\d+-|俗套黑名单|选题黑名单)/.test(file) || /\/框架-/.test(file);
}

// Resolve only text assets belonging to the approved workflow, never arbitrary
// project files, credentials, source code, external paths, or external URLs.
export function createStudioReader({ projectRoot, skillId, episode, sourcePath, referencePath, initialPaths = [] }) {
  const delivered = new Map();
  const observed = new Map();
  const allowed = (file) => {
    if (skillId === 'xuanti' && file.startsWith('对标原稿/') && ![sourcePath, referencePath].includes(file)) return false;
    if (skillId === 'xuanti' && /^stories\/(?:(?:归档|暂时归档)\/)?\d{1,4}[-－][^/]+\/(?:选题|二创稿|原稿)\.md$/u.test(file)) return true;
    if ([sourcePath, referencePath, ...initialPaths].filter(Boolean).includes(file)) return true;
    if (referencePath && /^对标原稿\/[^/]+\.md$/u.test(file)) return true;
    if (['CLAUDE.md', '跨层契约.md', '爆款结构库.md', '带货框架库.md', '规则变更日志.md', '洗稿提示词.md'].includes(file)) return true;
    if (/^(knowledge\/[^/]+|\.claude\/commands\/(duibiao|xuanti|erchuang|fupan|xigao)|\.claude\/skills\/(?:生产线总则|(?:zichan|fenjing|xiufu|houqi|fengmian)\/(?:SKILL|知识\/[^/]+))|爆款结构库\/[^/]+|带货框架库\/[^/]+|产品图\/[^/]+\/产品规格)\.md$/u.test(file)) return true;
    if (episode && file.startsWith(`stories/${episode}/`) && file.endsWith('.md')) return true;
    return skillId === 'fupan' && /^stories\/\d+[-－][^/]+\/数据\.md$/u.test(file);
  };
  const read = async (requested) => {
    const rel = cleanRelative(requested);
    if (!allowed(rel)) throw new WorkbenchError('该资料不在本次允许读取的范围内。', 403);
    const file = await existingChild(projectRoot, rel);
    if (!(await stat(file)).isFile()) throw new WorkbenchError('所选资料不是文本文件。');
    const text = await readFile(file, 'utf8');
    if (Buffer.byteLength(text) > DOCUMENT_LIMIT) throw new WorkbenchError('单份资料超过上下文限制，未截断。', 413);
    const previous = delivered.get(rel);
    if (previous && previous.hash !== digest(text)) throw new WorkbenchError('读取期间资料发生变化，请重新运行。', 409);
    const item = { path: rel, content: text, hash: digest(text), private: isPrivateInstructionPath(rel) };
    delivered.set(rel, item); observed.set(rel, item.hash);
    return item;
  };
  const list = async (requested) => {
    const rel = cleanRelative(requested);
    if (skillId === 'xuanti' && rel === '对标原稿') throw new WorkbenchError('选题只读取本轮明确选定的对标原稿，不扫描原稿库存。', 403);
    const topicStoryDirectory = skillId === 'xuanti' && /^stories\/(?:归档|暂时归档|(?:(?:归档|暂时归档)\/)?\d{1,4}[-－][^/]+)$/u.test(rel);
    if (!topicStoryDirectory && !['stories', '对标原稿', '爆款结构库', '带货框架库', '产品图', 'knowledge'].includes(rel)) throw new WorkbenchError('该目录不在本次允许列出的范围内。', 403);
    const folder = await existingChild(projectRoot, rel);
    const entries = (await readdir(folder, { withFileTypes: true })).filter((item) => !item.isSymbolicLink() && (item.isDirectory() || item.name.endsWith('.md'))).map((item) => ({ path: `${rel}/${item.name}`, kind: item.isDirectory() ? 'directory' : 'file' })).sort((a, b) => a.path.localeCompare(b.path, 'zh-CN', { numeric: true }));
    if (entries.length > 5000) throw new WorkbenchError('目录过大，请缩小本次资料范围。');
    return { directory: rel, entries };
  };
  const assertFresh = async () => {
    const changedPaths = [];
    for (const [file, expected] of observed) {
      try {
        const actual = digest(await readFile(await existingChild(projectRoot, file), 'utf8'));
        if (actual !== expected) changedPaths.push(file);
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        changedPaths.push(file);
      }
    }
    if (changedPaths.length) throw Object.assign(new WorkbenchError('本轮使用的资料已经更新，未写入旧结果。', 409), { code: 'STUDIO_SOURCES_CHANGED', changedPaths });
  };
  return { read, list, delivered, observed, assertFresh, allowed };
}
