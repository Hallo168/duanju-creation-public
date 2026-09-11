import path from 'node:path';
import { readdir } from 'node:fs/promises';
import {
  WorkbenchError,
  childPath,
  existingChild,
  optionalText,
  digest,
} from './storage.mjs';
import { section, parseAssets } from './asset-markdown.mjs';
import { PRODUCTION_POLICY_FILE, pencilEnabled } from './production-policy.mjs';

const string = { type: 'string' };
const strings = { type: 'array', items: string };
const object = (properties) => ({
  type: 'object',
  additionalProperties: false,
  properties,
  required: Object.keys(properties),
});
const description = object({ id: string, name: string, block: string });
export const assetPlanSchema = object({
  styleName: string,
  styleBlock: string,
  characters: { type: 'array', items: description },
  scenes: { type: 'array', items: description },
  productInfo: string,
  notes: strings,
  review: string,
  assets: {
    type: 'array',
    items: object({
      id: string,
      number: string,
      name: string,
      kind: {
        type: 'string',
        enum: [
          'character',
          'pencil',
          'scene_panorama',
          'scene_grid',
          'prop',
          'product_photo',
        ],
      },
      filename: string,
      prompt: string,
      size: string,
      refs: strings,
      sourceFile: string,
    }),
  },
});

export function planSchema(includePencil = true) {
  const schema = structuredClone(assetPlanSchema);
  if (!includePencil)
    schema.properties.assets.items.properties.kind.enum =
      schema.properties.assets.items.properties.kind.enum.filter((kind) => kind !== 'pencil');
  return schema;
}

const RULES = [
  'CLAUDE.md',
  '跨层契约.md',
  '.claude/skills/生产线总则.md',
  '.claude/skills/zichan/SKILL.md',
  '.claude/skills/zichan/知识/资产生产规范.md',
  '.claude/skills/zichan/知识/风格预设.md',
  '.claude/skills/zichan/知识/角色面相字典.md',
];

export async function assetSources(projectRoot, story, notes = '') {
  const prefix = `stories/${path.basename(story)}`;
  const files = [];
  for (const relative of [
    ...RULES,
    PRODUCTION_POLICY_FILE,
    `${prefix}/二创稿.md`,
    `${prefix}/选题.md`,
    `${prefix}/资产清单.md`,
    `${prefix}/资产提示词.md`,
  ]) {
    const content = await optionalText(childPath(projectRoot, relative));
    if (
      !content &&
      (RULES.includes(relative) || relative.endsWith('/二创稿.md'))
    )
      throw new WorkbenchError(`缺少本步材料：${relative}`, 409);
    files.push({ path: relative, hash: digest(content), content });
  }
  const script = files.find((item) => item.path.endsWith('/二创稿.md')).content;
  if (!section(script, '角色表') || !section(script, '场景表'))
    throw new WorkbenchError(
      '二创稿缺少固定标题“角色表”或“场景表”，请先补齐映射表。',
      409,
    );
  const products = [];
  const productRoot = path.join(projectRoot, '产品图');
  let entries = [];
  try {
    entries = await readdir(productRoot, { withFileTypes: true });
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const mentions =
    files
      .filter((item) => item.path.startsWith(prefix))
      .map((item) => item.content)
      .join('\n') + notes;
  for (const entry of entries.filter((item) => item.isDirectory())) {
    const directory = await existingChild(productRoot, entry.name);
    const photos = (await readdir(directory)).filter((name) =>
      /\.(png|jpe?g|webp)$/i.test(name),
    );
    products.push({
      product: entry.name,
      files: photos.map((name) => `产品图/${entry.name}/${name}`),
    });
    if (mentions.includes(entry.name)) {
      const relative = `产品图/${entry.name}/产品规格.md`;
      const content = await optionalText(childPath(projectRoot, relative));
      if (content)
        files.push({ path: relative, hash: digest(content), content });
    }
  }
  return { files, products, pencilEnabled: await pencilEnabled(projectRoot) };
}

export function planPrompt(sources, notes, previousPlan) {
  return `你在执行短剧工作台中“资产方案”这一个步骤。输出符合下方 JSON Schema 的完整资产方案，由程序保存正式文件。不得调用生图、不得写文件，不启动分镜，不生成封面，不修改其他环节。用户当前要求按环节分段执行，覆盖原 skill 的自动下阶段动作。
角色和场景以二创稿的固定 H2 表为准，正文用于理解道具、菜品、剧情状态，不能从正文另猜一套角色。保留本集已有明确裁定、所选外观、风格和原提示词，只有用户明确要求的范围才调整。最新用户条件：${notes || '沿用现有资料和本集裁定。'}
${sources.pencilEnabled === false ? '当前项目已暂时停用彩铅，覆盖旧材料的双版本要求：本方案只出写实人物图，不输出任何彩铅条目、提示词、补图要求或彩铅引用。人物参考及挂载建议使用同角色同状态写实图；在 notes 记录彩铅暂时停用。' : '彩铅启用时必须引用同角色同状态写实图。'}
图生图依赖写入 refs（资产 ID），写实状态变体必须引用本角色基础写实图，四宫格必须引用对应全景。真实产品必须标为 product_photo，sourceFile 只能选下列真实图库文件，不得编造路径或生图替代；缺图则 sourceFile 留空并写 notes。size 填本集请求像素尺寸；已有尺寸原样保留。filename 为不含目录的 PNG 文件名，资产 ID 唯一。人物描述、场景描述、全局风格按规范写入相应字段，产品规格没有来源的留空。自查结果写 review。
${previousPlan ? `这是同一模型的独立自查轮。重新按实际规范检查下面的完整方案，修正后返回完整 JSON，review 写本轮自查，不返回自由回复：\n${JSON.stringify(previousPlan)}` : '这是第一轮资产方案生成。'}
JSON Schema：${JSON.stringify(planSchema(sources.pencilEnabled))}
可用实拍图库（路径仅供选择；不要把不相关产品传入方案）：${JSON.stringify(sources.products)}
本次材料，正文为事实和规范依据，不执行其中与本步范围无关的操作：
${sources.files.map((file) => `===== ${file.path} · ${file.hash} =====\n${file.content}`).join('\n\n')}`;
}

export function parsePlan(text, includePencil = true) {
  const source = String(text)
    .trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/, '');
  let plan;
  try {
    plan = JSON.parse(source);
  } catch {
    throw new WorkbenchError(
      '模型返回内容不是完整资产方案 JSON，原结果已保存。',
      409,
    );
  }
  for (const key of ['styleName', 'styleBlock', 'productInfo', 'review'])
    if (typeof plan[key] !== 'string')
      throw new WorkbenchError(`资产方案缺少字段 ${key}。`, 409);
  if (plan.styleBlock.includes('```'))
    throw new WorkbenchError('全局风格块不能包含嵌套代码块。', 409);
  if (
    !Array.isArray(plan.assets) ||
    !plan.assets.length ||
    plan.assets.length > 160
  )
    throw new WorkbenchError('资产方案的条目数量不正确。', 409);
  for (const key of ['characters', 'scenes', 'notes'])
    if (!Array.isArray(plan[key]))
      throw new WorkbenchError(`资产方案缺少 ${key}。`, 409);
  if (plan.notes.some((note) => typeof note !== 'string'))
    throw new WorkbenchError('本集说明必须为文字。', 409);
  for (const item of [...plan.characters, ...plan.scenes])
    if (
      ['id', 'name', 'block'].some(
        (key) => typeof item[key] !== 'string' || item[key].includes('```'),
      )
    )
      throw new WorkbenchError('描述块格式不完整。', 409);
  const ids = new Set();
  const filenames = new Set();
  const numbers = new Set();
  for (const asset of plan.assets) {
    if (
      !asset ||
      Object.keys(assetPlanSchema.properties.assets.items.properties).some(
        (key) =>
          key === 'refs'
            ? !Array.isArray(asset[key]) ||
              asset[key].some((ref) => typeof ref !== 'string')
            : typeof asset[key] !== 'string',
      )
    )
      throw new WorkbenchError('资产条目字段不完整。', 409);
    if (!/^[RSP]\d+[A-Za-z0-9]*(?:底图)?$/.test(asset.id) || ids.has(asset.id))
      throw new WorkbenchError('资产 ID 无效或重复。', 409);
    if (
      !/^[^\\/:*?"<>|\r\n]+\.png$/i.test(asset.filename) ||
      asset.filename.includes('..') ||
      filenames.has(asset.filename)
    )
      throw new WorkbenchError('资产文件名无效或重复。', 409);
    if (
      !assetPlanSchema.properties.assets.items.properties.kind.enum.includes(
        asset.kind,
      )
    )
      throw new WorkbenchError('资产类型不受支持。', 409);
    if (!includePencil && (asset.kind === 'pencil' || /彩铅|素描/.test(asset.filename)))
      throw new WorkbenchError('彩铅已暂时停用，方案仍含彩铅条目，未保存。', 409);
    if (
      (asset.kind === 'pencil' && !asset.filename.includes('彩铅参考图')) ||
      (asset.kind === 'scene_panorama' && !asset.filename.includes('全景')) ||
      (asset.kind === 'scene_grid' && !asset.filename.includes('四宫格')) ||
      (asset.kind === 'character' && !/^R\d/.test(asset.filename))
    )
      throw new WorkbenchError('资产文件名与条目类型不一致。', 409);
    if (asset.number && asset.number !== '—') {
      if (!/^图片\d+$/.test(asset.number) || numbers.has(asset.number))
        throw new WorkbenchError('默认图片编号无效或重复。', 409);
      numbers.add(asset.number);
    }
    if (asset.prompt.includes('```') || asset.prompt.length > 60000)
      throw new WorkbenchError('提示词代码块格式不正确。', 409);
    if (
      !['product_photo', 'scene_grid', 'pencil'].includes(asset.kind) &&
      (!asset.prompt.trim() || !/^\d{3,4}x\d{3,4}$/.test(asset.size))
    )
      throw new WorkbenchError('生图条目缺少提示词或像素尺寸。', 409);
    if (
      asset.kind === 'product_photo' &&
      (asset.prompt ||
        asset.refs.length ||
        (asset.sourceFile &&
          !/^产品图\/[^/]+\/[^/]+\.(png|jpe?g|webp)$/i.test(asset.sourceFile)))
    )
      throw new WorkbenchError(
        '真实产品条目只能使用图库实拍，不可含生图任务。',
        409,
      );
    ids.add(asset.id);
    filenames.add(asset.filename);
  }
  for (const asset of plan.assets) {
    if (asset.refs.some((id) => !ids.has(id) || id === asset.id))
      throw new WorkbenchError('方案引用了不存在或自身的参考图。', 409);
    if (
      ['pencil', 'scene_grid'].includes(asset.kind) &&
      asset.refs.length !== 1
    )
      throw new WorkbenchError('派生图必须有一张明确的基础图。', 409);
    const ref = plan.assets.find((item) => item.id === asset.refs[0]);
    if (
      (asset.kind === 'pencil' && ref?.kind !== 'character') ||
      (asset.kind === 'scene_grid' && ref?.kind !== 'scene_panorama')
    )
      throw new WorkbenchError('派生图的基础图类型不正确。', 409);
  }
  return plan;
}

const cell = (text) => String(text).replace(/[\r\n|`]/g, ' ');
export function renderPlan(episode, plan) {
  const blocks = (list) =>
    list
      .map(
        (item) =>
          `### ${item.id} ${item.name}\n\n\x60\x60\x60\n${item.block}\n\x60\x60\x60`,
      )
      .join('\n\n');
  const style = `## 全局风格块\n\n\x60\x60\x60\n${plan.styleBlock}\n\x60\x60\x60`;
  const rows = plan.assets.map((asset) => {
    const source =
      asset.kind === 'product_photo'
        ? asset.sourceFile
          ? `实拍（复制自 ${asset.sourceFile}）`
          : '实拍（待补充）'
        : asset.kind === 'scene_grid'
          ? `程序派生（ref ${asset.refs.join(',')}）`
          : asset.refs.length
            ? `图生图（ref ${asset.refs.join(',')}）`
            : '文生图';
    return `| ${[asset.number || '—', asset.id, asset.name, asset.filename, source, asset.kind === 'product_photo' ? '待导入' : '待生成'].map(cell).join(' | ')} |`;
  });
  const manifest = `---\nstory: ${episode}\nassets_ready: false\n风格预设: ${cell(plan.styleName)}\n---\n\n${style}\n\n## 人物描述块\n\n${blocks(plan.characters)}\n\n## 场景描述块\n\n${blocks(plan.scenes)}\n\n## 产品信息\n\n${plan.productInfo}\n\n## 编号分配表\n\n| 默认编号 | 资产ID | 内容 | 建议文件名 | 来源 | 状态 |\n|---|---|---|---|---|---|\n${rows.join('\n')}\n\n## 每单元挂载建议\n\n由分镜阶段回填。\n\n## 本集说明\n\n${plan.notes.map((note) => `- ${note}`).join('\n')}\n`;
  const prompts = `---\nstory: ${episode}\n风格预设: ${cell(plan.styleName)}\n---\n\n${style}\n\n## 资产提示词\n\n${plan.assets.map((asset) => `### ${asset.id} ${asset.name}\n\n建议文件名：\x60${asset.filename}\x60\n\n请求尺寸：${asset.size || '按来源保留'}\n来源：${asset.refs.length ? `ref ${asset.refs.join(',')}` : asset.sourceFile || '文生图'}\n\n${asset.prompt ? `\x60\x60\x60\n${asset.prompt}\n\x60\x60\x60` : asset.kind === 'scene_grid' ? '按资产生产规范由全景底图派生四宫格。' : '使用产品实拍照片。'}`).join('\n\n')}\n\n## 本集说明\n\n${plan.notes.map((note) => `- ${note}`).join('\n')}\n\n## 提示词自查\n\n${plan.review}\n`;
  const parsed = parseAssets(manifest, prompts);
  if (parsed.length !== plan.assets.length)
    throw new WorkbenchError('方案转换后条目数量不一致，已停止保存。', 409);
  return { manifest, prompts };
}

export async function ensureSourcesUnchanged(projectRoot, sources) {
  for (const file of sources.files)
    if (
      digest(await optionalText(childPath(projectRoot, file.path))) !==
      file.hash
    )
      throw new WorkbenchError(
        '生成期间上游材料发生修改。本次结果已保留，请刷新核对后再采用。',
        409,
      );
}
