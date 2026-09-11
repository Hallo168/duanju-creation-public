import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

export function repairUnitExcerpt(markdown, unitNumber) {
  const headings = [...markdown.matchAll(/^##[ \t]+([^\r\n]+)/gm)];
  const units = headings
    .map((heading, index) => ({
      heading,
      end: headings[index + 1]?.index ?? markdown.length,
      number: Number(heading[1].match(/^单元(\d+)(?=[（(\s]|$)/)?.[1]),
    }))
    .filter((item) => Number.isInteger(item.number));
  const index = units.findIndex((item) => item.number === unitNumber);
  if (index < 0) throw new Error(`修复依据中没有找到单元${unitNumber}`);
  return [
    markdown.slice(0, units[0].heading.index),
    ...units.slice(Math.max(0, index - 1), index + 2)
      .map((item) => markdown.slice(item.heading.index, item.end)),
  ].join('\n');
}

export async function loadRepairSources(projectRoot, rules, unitNumber) {
  const sources = await Promise.all(rules.map(async (rule) => {
    const filename = path.resolve(projectRoot, rule.path);
    const relative = path.relative(projectRoot, filename);
    if (relative.startsWith('..') || path.isAbsolute(relative)) {
      throw new Error('修复依据路径超出项目范围。');
    }
    const content = await readFile(filename, 'utf8');
    const excerpted = rule.readMode === '目标单元及前后单元';
    return {
      path: rule.path,
      sha256: createHash('sha256').update(content).digest('hex'),
      scope: excerpted ? '文件头、目标单元及相邻单元原文' : '全文',
      content: excerpted ? repairUnitExcerpt(content, unitNumber) : content,
    };
  }));
  return {
    manifests: sources.map(({ content: _content, ...manifest }) => manifest),
    text: sources.map((source) =>
      `===== SOURCE: ${source.path} =====\n范围：${source.scope}\n源文件 SHA-256：${source.sha256}\n${source.content}\n===== END SOURCE: ${source.path} =====`,
    ).join('\n\n'),
  };
}
