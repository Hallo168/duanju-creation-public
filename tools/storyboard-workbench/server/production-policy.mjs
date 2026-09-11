import path from 'node:path';
import { optionalJson, WorkbenchError } from './storage.mjs';

export const PRODUCTION_POLICY_FILE = '工具/生产开关.json';
export const PENCIL_PAUSED_MESSAGE =
  '彩铅已暂时停用：只生成写实人物图，彩铅缺图不影响资产完成。';

export async function pencilEnabled(projectRoot) {
  const policy = await optionalJson(
    path.join(projectRoot, PRODUCTION_POLICY_FILE),
    { colored_pencil_enabled: true },
  );
  if (typeof policy.colored_pencil_enabled !== 'boolean')
    throw new WorkbenchError('生产开关中的彩铅设置无效，请检查后继续。', 409);
  return policy.colored_pencil_enabled;
}
