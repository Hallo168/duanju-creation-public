import { randomUUID, createHash } from 'node:crypto';
import {
  mkdir,
  readFile,
  writeFile,
  rename,
  copyFile,
  realpath,
  stat,
  unlink,
  readdir,
} from 'node:fs/promises';
import path from 'node:path';

export class WorkbenchError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

export const digest = (value) =>
  createHash('sha256').update(value).digest('hex');
export const stamp = () =>
  `${new Date().toISOString().replace(/[:.]/g, '-')}-${randomUUID().slice(0, 8)}`;

export async function optionalText(file) {
  try {
    return await readFile(file, 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return '';
    throw error;
  }
}

export async function optionalJson(file, fallback) {
  const text = await optionalText(file);
  if (!text) return fallback;
  try {
    return JSON.parse(text);
  } catch {
    throw new WorkbenchError(
      `保存的数据无法读取：${path.basename(file)}。请保留原文件后检查。`,
      409,
    );
  }
}

export async function atomicWrite(file, value, expectedHash) {
  await mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${randomUUID()}.tmp`;
  await writeFile(temporary, value, {
    encoding: typeof value === 'string' ? 'utf8' : undefined,
    flag: 'wx',
  });
  for (let attempt = 0; ; attempt += 1) {
    try {
      if (
        expectedHash !== undefined &&
        digest(await optionalText(file)) !== expectedHash
      )
        throw new WorkbenchError(
          '文件在写入前发生更新，本次内容已保留为临时文件。',
          409,
        );
      await rename(temporary, file);
      break;
    } catch (error) {
      // Windows virus scanners and file watchers briefly hold freshly written files.
      // Only repeat the same local atomic rename; the destination is never deleted.
      if (!['EPERM', 'EACCES', 'EBUSY'].includes(error.code) || attempt >= 5)
        throw error;
      await new Promise((resolve) => setTimeout(resolve, 25 * 2 ** attempt));
    }
  }
}

export const writeJson = (file, value) =>
  atomicWrite(file, `${JSON.stringify(value, null, 2)}\n`);

export function childPath(root, relative) {
  if (
    typeof relative !== 'string' ||
    !relative ||
    relative.includes('\0') ||
    /[\\:]/.test(relative) ||
    path.isAbsolute(relative)
  ) {
    throw new WorkbenchError('文件路径无效。');
  }
  const resolved = path.resolve(root, relative);
  const relation = path.relative(path.resolve(root), resolved);
  if (!relation || relation.startsWith('..') || path.isAbsolute(relation))
    throw new WorkbenchError('文件必须位于本次项目中。');
  return resolved;
}

export async function existingChild(root, relative) {
  const target = childPath(root, relative);
  const [realRoot, realTarget] = await Promise.all([
    realpath(root),
    realpath(target),
  ]);
  const relation = path.relative(realRoot, realTarget);
  if (!relation || relation.startsWith('..') || path.isAbsolute(relation))
    throw new WorkbenchError('文件指向了项目以外的位置。');
  return realTarget;
}

export function episodeName(value) {
  if (
    typeof value !== 'string' ||
    !/^\d+[-－][^\\/:*?"<>|]+$/.test(value) ||
    value.includes('..')
  )
    throw new WorkbenchError('请选择有效的故事。');
  return value;
}

export async function storyPath(projectRoot, episode) {
  return existingChild(path.join(projectRoot, 'stories'), episodeName(episode));
}

export async function fileInfo(file) {
  try {
    const value = await stat(file);
    return value.isFile()
      ? { size: value.size, modifiedAt: value.mtimeMs }
      : null;
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

export function createLocks() {
  const active = new Set();
  return async (key, operation) => {
    if (active.has(key))
      throw new WorkbenchError('这个故事正在保存或执行任务，请稍后再试。', 409);
    active.add(key);
    try {
      return await operation();
    } finally {
      active.delete(key);
    }
  };
}

// All expected hashes are checked before writing. A journal and backups remain
// available if the process is interrupted between the two canonical files.
export async function replaceFiles(changes, backupRoot, label) {
  const id = stamp();
  const directory = path.join(backupRoot, id);
  await mkdir(directory, { recursive: true });
  const entries = [];
  for (let index = 0; index < changes.length; index += 1) {
    const change = changes[index];
    const original = await optionalText(change.path);
    if (
      change.expectedHash !== undefined &&
      digest(original) !== change.expectedHash
    ) {
      throw new WorkbenchError(
        '文件已在其他地方更新。请刷新后再保存；本次结果已保留。',
        409,
      );
    }
    const existed = Boolean(await fileInfo(change.path));
    const backup = path.join(
      directory,
      `${index}-${path.basename(change.path)}`,
    );
    if (existed) await copyFile(change.path, backup);
    entries.push({ ...change, original, existed, backup });
  }
  const journal = path.join(directory, 'transaction.json');
  const publicEntries = entries.map(
    ({ path: file, backup, existed, original, content }) => ({
      path: file,
      backup,
      existed,
      before: digest(original),
      after: digest(content),
    }),
  );
  await writeJson(journal, {
    status: 'prepared',
    label,
    entries: publicEntries,
  });
  const written = [];
  try {
    for (const entry of entries) {
      // Check again immediately before the replacement to catch external editors.
      if (digest(await optionalText(entry.path)) !== digest(entry.original))
        throw new WorkbenchError('保存期间文件发生变化，已停止写入。', 409);
      await atomicWrite(entry.path, entry.content, digest(entry.original));
      written.push(entry);
    }
    await writeJson(journal, {
      status: 'completed',
      label,
      entries: publicEntries,
    });
  } catch (error) {
    for (const entry of written.reverse()) {
      if (digest(await optionalText(entry.path)) === digest(entry.content)) {
        if (entry.existed)
          await atomicWrite(entry.path, entry.original, digest(entry.content));
        else await unlink(entry.path);
      }
    }
    await writeJson(journal, {
      status: 'rolled_back',
      label,
      error: error.message,
      entries: publicEntries,
    });
    throw error;
  }
  return { id, directory };
}

// Interrupted multi-file saves are resolved before accepting another mutation.
// Never replace a file an external editor changed after this transaction.
export async function recoverTransactions(backupRoot) {
  let directories;
  try {
    directories = await readdir(backupRoot, { withFileTypes: true });
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  for (const directory of directories.filter((item) => item.isDirectory())) {
    const journal = path.join(backupRoot, directory.name, 'transaction.json');
    const record = await optionalJson(journal, null);
    if (!record || !['prepared', 'conflict'].includes(record.status)) continue;
    const hashes = await Promise.all(
      record.entries.map(async (entry) =>
        digest(await optionalText(entry.path)),
      ),
    );
    if (hashes.every((hash, index) => hash === record.entries[index].after)) {
      await writeJson(journal, { ...record, status: 'completed' });
      continue;
    }
    if (
      hashes.some(
        (hash, index) =>
          ![record.entries[index].before, record.entries[index].after].includes(
            hash,
          ),
      )
    ) {
      await writeJson(journal, { ...record, status: 'conflict' });
      throw new WorkbenchError(
        `上次保存中断后文件又有修改，请先核对备份：${journal}`,
        409,
      );
    }
    for (let index = 0; index < record.entries.length; index += 1) {
      const entry = record.entries[index];
      if (hashes[index] !== entry.after) continue;
      if (entry.existed)
        await atomicWrite(entry.path, await readFile(entry.backup));
      else await unlink(entry.path);
    }
    await writeJson(journal, { ...record, status: 'rolled_back' });
  }
}
