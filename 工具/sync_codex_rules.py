"""Publish full project rules to Codex. Default: read-only consistency check.

Run with --apply to back up changed destinations and synchronize them.
Knowledge files remain shared at the project root; workflow bodies are not summarized.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS = Path.home() / '.codex' / 'skills'
AGENT_SKILLS = ROOT / '.agents' / 'skills'
COMMANDS = ('duibiao', 'xuanti', 'erchuang', 'fenxi', 'fupan', 'xigao')
PRODUCTION = ('zichan', 'fenjing', 'xiufu', 'houqi', 'fengmian', 'daoyan-fenjing', 'dola-video-check')
MARKER = '<!-- 正式流程全文 -->\n'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_text(path):
    return path.read_text(encoding='utf-8-sig')


def split_source(text):
    match = re.match(r'\A---\n(.*?)\n---\n', text, re.S)
    if not match:
        raise ValueError('Source has no frontmatter')
    # Claude argument hints are literal bracketed prose, not YAML sequences.
    metadata_text = re.sub(r'^argument-hint: .*\n?', '', match.group(1), flags=re.M)
    meta = yaml.safe_load(metadata_text)
    if set(meta) - {'name', 'description', 'argument-hint'}:
        raise ValueError('New source metadata needs explicit review')
    hint = re.search(r'^argument-hint: (.*)$', match.group(1), re.M)
    if hint:
        meta['argument-hint'] = hint.group(1)
    return meta, text[match.end():]


def execution_notes():
    return (
        f'项目根目录固定为 `{ROOT.as_posix()}`；正文中的项目相对路径均从此解析，'
        '知识库、生产线总则和跨层契约继续读取项目内同一份文件。\n'
        'Claude 工具名 Read/Grep/Glob/Write/Edit/Agent 按语义使用 Codex 等价工具；'
        '源流程要求的干净子代理使用零上下文 worker，只给指定材料与范围，'
        '不携带主代理结论；Claude 专用模型名称不作为 Codex 模型标识。'
        '已退役的质检员不恢复，审核次数与用户关卡按正文执行。\n'
        '正文的 `/命令名` 在 Codex 中使用对应 `$技能名`；'
        '`$ARGUMENTS` 为本次用户提供的参数，缺参按正文处理。\n'
    )


def expected_files():
    constitution = read_text(ROOT / 'CLAUDE.md')
    agents = constitution.rstrip() + '\n\n## Codex 执行约定\n\n'
    agents += '上文是项目总规则全文，直接执行，无需再转读 CLAUDE.md 获取同一正文。\n'
    agents += execution_notes()
    files = [(ROOT / 'CLAUDE.md', ROOT / 'AGENTS.md', agents.encode('utf-8'))]
    for skill_root in (SKILLS, AGENT_SKILLS):
        for name in COMMANDS + PRODUCTION:
            source = ROOT / '.claude' / ('commands' if name in COMMANDS else 'skills')
            source = source / (name + '.md') if name in COMMANDS else source / name / 'SKILL.md'
            meta, body = split_source(read_text(source))
            # This source uses its physical location to explain the root. The installed
            # Codex file is elsewhere, so translate only that location-dependent sentence.
            if name == 'fenxi':
                old = '项目根目录是本文件的 `../..`，当前位置为 `.`。'
                if body.count(old) != 1:
                    raise ValueError('fenxi root sentence changed; review before publishing')
                body = body.replace(old, f'项目根目录固定为 `{ROOT.as_posix()}`。')
            header = '---\n' + yaml.safe_dump(
                {'name': name, 'description': meta['description']},
                allow_unicode=True, sort_keys=False, width=1000,
            ) + '---\n\n'
            notes = '## Codex 执行约定\n\n'
            notes += f'本文件包含完整正式流程；适用项目为 `{ROOT.as_posix()}`。'
            notes += '开工完整读取项目 AGENTS.md；本轮已完整读入且未更新时复用。\n'
            skill_notes = execution_notes()
            if name == 'daoyan-fenjing':
                skill_notes = skill_notes.replace(
                    '知识库、生产线总则和跨层契约继续读取项目内同一份文件。',
                    f'导演规则、生产线总则与跨层契约读取 .claude/skills/{name}/知识 内独立副本；输入剧情与资产读取指定故事目录。',
                )
            notes += skill_notes
            if meta.get('argument-hint'):
                notes += '\n参数提示：' + meta['argument-hint'] + '\n'
            notes += '\n'
            result = (header + notes + MARKER + body).encode('utf-8')
            files.append((source, skill_root / name / 'SKILL.md', result))
            if name in ('daoyan-fenjing', 'dola-video-check'):
                ui = source.parent / 'agents' / 'openai.yaml'
                files.append((ui, skill_root / name / 'agents' / 'openai.yaml', ui.read_bytes()))
            if name == 'dola-video-check':
                for folder in ('scripts', 'references'):
                    for resource in sorted((source.parent / folder).rglob('*')):
                        if resource.is_file() and resource.suffix in ('.py', '.md'):
                            relative = resource.relative_to(source.parent)
                            files.append((resource, skill_root / name / relative, resource.read_bytes()))
    fantui_src = SKILLS / 'video-fantui'
    if (fantui_src / 'SKILL.md').exists():
        for resource in sorted(fantui_src.rglob('*')):
            if resource.is_file() and '__pycache__' not in resource.parts and resource.suffix != '.pyc':
                relative = resource.relative_to(fantui_src)
                files.append((resource, AGENT_SKILLS / 'video-fantui' / relative, resource.read_bytes()))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    files = expected_files()
    changed = [(src, dst, data) for src, dst, data in files
               if not dst.exists() or dst.read_bytes() != data]
    if args.apply and changed:
        backup = ROOT / '备份' / ('Codex正式规则-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.mkdir(parents=True, exist_ok=False)
        entries = []
        for index, (source, target, data) in enumerate(changed):
            entry = {'source': str(source), 'target': str(target), 'existed': target.exists(),
                     'new_sha256': digest(data)}
            if target.exists():
                old = target.read_bytes()
                saved = backup / f'{index:02d}-{target.parent.name}-{target.name}'
                saved.write_bytes(old)
                if saved.read_bytes() != old:
                    raise IOError('Backup verification failed')
                entry.update(backup=str(saved), old_sha256=digest(old))
            entries.append(entry)
        # Save the rollback manifest before any destination is changed.
        (backup / 'manifest.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        for source, target, data in changed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        print('BACKUP', backup)
    failures = []
    for source, target, data in files:
        ok = target.exists() and target.read_bytes() == data
        print('OK' if ok else 'DIFF', target)
        if not ok:
            failures.append(str(target))
    # Detect a source change during publication instead of reporting stale success.
    if expected_files() != files:
        raise RuntimeError('Source rules changed during synchronization; rerun')
    print(f'{len(files) - len(failures)}/{len(files)} full rule files consistent')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
