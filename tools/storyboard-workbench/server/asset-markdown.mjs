import path from 'node:path';
import { WorkbenchError, digest } from './storage.mjs';

const clean = (value = '') => value.replace(/`/g, '').trim();
const cells = (line) =>
  line
    .trim()
    .replace(/^\||\|$/g, '')
    .split(/(?<!\\)\|/)
    .map(clean);

export function section(markdown, title) {
  const lines = markdown.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim() === `## ${title}`);
  if (start < 0) return '';
  let end = start + 1;
  while (end < lines.length && !/^##\s/.test(lines[end])) end += 1;
  return lines.slice(start + 1, end).join('\n');
}

export function metadata(markdown, key) {
  const frontmatter = markdown.match(/^---\r?\n([\s\S]*?)\r?\n---/u)?.[1] ?? '';
  return (
    frontmatter
      .split(/\r?\n/)
      .find((line) => line.startsWith(`${key}:`))
      ?.slice(key.length + 1)
      .trim() ?? ''
  );
}

export function setReady(markdown, ready) {
  if (!/^---\r?\n/.test(markdown))
    throw new WorkbenchError('资产清单缺少文件头，不能写入放行标记。', 409);
  return markdown.replace(/^---\r?\n([\s\S]*?)\r?\n---/u, (full, header) => {
    const next = /^assets_ready:/m.test(header)
      ? header.replace(/^assets_ready:.*$/m, `assets_ready: ${ready}`)
      : `${header}\nassets_ready: ${ready}`;
    return `---\n${next}\n---`;
  });
}

export function promptBlocks(markdown) {
  const result = [];
  const fences =
    /^(?<fence>`{3,})(?<language>[^\r\n]*)\r?\n(?<body>[\s\S]*?)^\k<fence>[ \t]*\r?$/gm;
  let previousEnd = 0;
  let currentSize = '';
  for (const match of markdown.matchAll(fences)) {
    const prefix = markdown.slice(previousEnd, match.index);
    previousEnd = match.index + match[0].length;
    if (
      /bash|powershell|python|json|javascript|shell|cmd/i.test(
        match.groups.language,
      )
    )
      continue;
    const fileMatches = [
      ...prefix.matchAll(
        /(?:建议文件名|保存文件名|文件名)[：:]\s*`?([^`\r\n]+\.(?:png|jpe?g|webp))`?/gi,
      ),
    ];
    let filename = fileMatches.at(-1)?.[1]?.trim();
    if (!filename) {
      const heading =
        [...prefix.matchAll(/^#{2,4}\s+(.+)$/gm)].at(-1)?.[1] ?? '';
      filename = heading.match(/`([^`]+\.(?:png|jpe?g|webp))`/i)?.[1];
    }
    if (
      !filename ||
      filename !== path.basename(filename) ||
      /[\\/]/.test(filename)
    )
      continue;
    const declaredSize = [...prefix.matchAll(/\b(\d{3,4})[x×](\d{3,4})\b/g)].at(
      -1,
    );
    if (declaredSize) currentSize = `${declaredSize[1]}x${declaredSize[2]}`;
    const start = match.index + match[0].indexOf('\n') + 1;
    const raw = match.groups.body;
    const prompt = raw.replace(/\r?\n$/, '');
    result.push({
      filename,
      prompt,
      start,
      end: start + prompt.length,
      context: prefix.slice(-2400),
      size: currentSize,
    });
  }
  return result;
}

export function replacePrompt(markdown, filename, prompt) {
  if (
    typeof prompt !== 'string' ||
    !prompt.trim() ||
    prompt.length > 60000 ||
    /^```/m.test(prompt)
  )
    throw new WorkbenchError('提示词不能为空，且不能包含独立代码块标记。');
  const matches = promptBlocks(markdown).filter(
    (item) => item.filename === filename,
  );
  if (matches.length !== 1)
    throw new WorkbenchError(
      '没有找到唯一对应的提示词，请先检查资产提示词文件。',
      409,
    );
  const item = matches[0];
  return (
    markdown.slice(0, item.start) + prompt.trim() + markdown.slice(item.end)
  );
}

export function kindOf(filename, source = '', label = '') {
  if (/^C\d|^封面[-－]/i.test(filename)) return 'cover';
  if (/实拍|产品图库|复制自.*产品图/.test(source)) return 'product_photo';
  if (/彩铅|素描/.test(filename)) return 'pencil';
  if (/^R\d/i.test(filename)) return 'character';
  if (/四宫格/.test(filename)) return 'scene_grid';
  if (/全景/.test(filename)) return 'scene_panorama';
  if (/^S\d/i.test(filename)) return 'scene';
  if (/产品实拍/.test(label)) return 'product_photo';
  return 'prop';
}

export function parseAssets(manifest, prompts) {
  const body = section(manifest, '编号分配表');
  const rows = body.split(/\r?\n/).filter((line) => /^\s*\|/.test(line));
  if (!rows.length) return [];
  const header = cells(rows[0]);
  const indexes = {
    number: header.findIndex((s) => /默认编号|上传编号|图片编号/.test(s)),
    id: header.findIndex((s) => /资产\s*ID/i.test(s)),
    name: header.findIndex((s) => /内容|名称/.test(s)),
    filename: header.findIndex((s) => /文件名/.test(s)),
    source: header.findIndex((s) => /来源/.test(s)),
    status: header.findIndex((s) => /状态/.test(s)),
  };
  if (indexes.filename < 0) return [];
  const blocks = promptBlocks(prompts);
  const result = [];
  for (const line of rows.slice(1)) {
    if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) continue;
    const values = cells(line);
    const filename = values[indexes.filename]?.match(
      /([^\s`]+\.(?:png|jpe?g|webp))/i,
    )?.[1];
    if (!filename || /[\\/]|\.\./.test(filename)) continue;
    const id = values[indexes.id] || filename;
    const source = values[indexes.source] ?? '';
    const name = values[indexes.name] || filename;
    const candidates = blocks.filter((item) => item.filename === filename);
    const block = candidates.length === 1 ? candidates[0] : null;
    const kind = kindOf(filename, source, name);
    const key = digest(id).slice(0, 20);
    const copyPath = source.match(
      /(?:复制自\s*)?(产品图\/[^）)]+?\.(?:png|jpe?g|webp))/i,
    )?.[1];
    result.push({
      key,
      id,
      name,
      filename,
      number: values[indexes.number] ?? '—',
      source,
      declaredStatus: values[indexes.status] ?? '',
      kind,
      prompt: block?.prompt ?? '',
      promptEditable:
        Boolean(block) &&
        !['pencil', 'scene_grid', 'product_photo'].includes(kind),
      refs: [],
      size: block?.size || '',
      sourceFile: copyPath ?? '',
      required:
        kind !== 'cover' &&
        !/作废|停用|不使用|非必需/.test(values[indexes.status] ?? ''),
    });
  }
  for (const entry of result) {
    if (result.filter((item) => item.id === entry.id).length > 1)
      entry.key = digest(
        `${entry.id}\n${entry.filename.replace(/-wb-[^.]+(?=\.)/, '')}`,
      ).slice(0, 20);
  }
  for (const entry of result) {
    if (entry.kind === 'pencil') {
      const expected = entry.filename
        .replace(/彩铅参考图|素描参考图/, '人物设定图')
        .replace(/-wb-[^.]+(?=\.)/, '');
      const match = result.find(
        (item) => item.filename.replace(/-wb-[^.]+(?=\.)/, '') === expected,
      );
      if (match) entry.refs = [match.key];
    } else if (entry.kind === 'scene_grid') {
      const baseId = entry.id.match(/^S\d+/i)?.[0];
      const matches = result.filter(
        (item) =>
          item.kind === 'scene_panorama' &&
          item.id.match(/^S\d+/i)?.[0] === baseId,
      );
      const exact = matches.find(
        (item) => item.filename === entry.filename.replace('四宫格', '全景'),
      );
      if (exact || matches.length === 1)
        entry.refs = [(exact ?? matches[0]).key];
    }
    if (/ref[:：\s]/i.test(entry.source)) {
      const refIds = [
        ...entry.source.matchAll(
          /(?:ref[:：\s]+|[,，]\s*)([RSP]\d+[A-Z]?\d*(?:底图)?)/gi,
        ),
      ].map((match) => match[1]);
      const refs = refIds.map((id) => {
        const matches = result.filter((item) => item.id === id);
        return matches.length === 1 ? matches[0] : null;
      });
      if (refs.length && refs.every((ref) => ref && ref.key !== entry.key))
        entry.refs = refs.map((ref) => ref.key);
      else entry.refs = [];
    }
    entry.dependencyIssue =
      (['pencil', 'scene_grid'].includes(entry.kind) ||
        /图生图|变体|ref\s/i.test(entry.source)) &&
      !entry.refs.length
        ? '没有找到明确的参考图，请在资产清单的来源列填写 ref 资产ID。'
        : '';
  }
  return result;
}

export function replaceAssetFilename(
  manifest,
  prompts,
  oldFile,
  nextFile,
  status,
) {
  let inTable = false;
  let fileColumn = -1;
  let statusColumn = -1;
  const lines = manifest.split(/\r?\n/).map((line) => {
    if (/^##\s/.test(line)) {
      inTable = line.trim() === '## 编号分配表';
      fileColumn = -1;
      statusColumn = -1;
    }
    if (!inTable || !line.trim().startsWith('|')) return line;
    const fields = cells(line);
    if (fileColumn < 0) {
      fileColumn = fields.findIndex((cell) => /文件名/.test(cell));
      statusColumn = fields.findIndex((cell) => /状态/.test(cell));
      return line;
    }
    if (fields[fileColumn] !== oldFile) return line;
    fields[fileColumn] = nextFile;
    if (status && statusColumn >= 0) fields[statusColumn] = status;
    return `| ${fields.join(' | ')} |`;
  });
  // Exact filenames are replaced; mount descriptions and other prompt text remain intact.
  return {
    manifest: lines.join('\n'),
    prompts: prompts.replace(
      new RegExp(
        `(?<![\\p{L}\\p{N}_-])${oldFile.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\p{L}\\p{N}_-])`,
        'gu',
      ),
      () => nextFile,
    ),
  };
}

export function dependencyOrder(assets, selectedKeys) {
  const selected = new Set(selectedKeys);
  const byKey = new Map(assets.map((item) => [item.key, item]));
  if (byKey.size !== assets.length)
    throw new WorkbenchError('资产清单含重复条目，请先修正编号分配表。', 409);
  const visiting = new Set();
  const visited = new Set();
  const ordered = [];
  const walk = (key) => {
    if (visited.has(key)) return;
    if (visiting.has(key))
      throw new WorkbenchError('资产参考图存在循环依赖，请先修改方案。');
    const entry = byKey.get(key);
    if (!entry) throw new WorkbenchError('所选资产已经变化，请刷新。', 409);
    visiting.add(key);
    for (const ref of entry.refs ?? []) {
      if (!byKey.has(ref))
        throw new WorkbenchError(`${entry.name}的参考图不在清单中。`);
      if (selected.has(ref)) walk(ref);
      else if (!byKey.get(ref).exists)
        throw new WorkbenchError(
          `${entry.name}缺少参考图，请一并选中对应的基础资产。`,
        );
    }
    visiting.delete(key);
    visited.add(key);
    ordered.push(entry);
  };
  for (const key of selected) walk(key);
  return ordered;
}
