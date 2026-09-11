import { readdirSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';

const isFile = (filename) => {
  try {
    return statSync(filename).isFile();
  } catch {
    return false;
  }
};

// Resolve at invocation time: desktop updates can replace the version directory.
export function resolveCodexExecutable({
  env = process.env,
  platform = process.platform,
  userHome = homedir(),
} = {}) {
  if (platform !== 'win32') return 'codex';
  const envValue = (name) =>
    Object.entries(env).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1];
  for (const entry of (envValue('PATH') || '').split(';')) {
    const directory = entry.trim().replace(/^"(.*)"$/, '$1');
    if (!directory) continue;
    const executable = path.join(directory, 'codex.exe');
    if (isFile(executable)) return executable;
  }

  const localAppData = envValue('LOCALAPPDATA') || path.join(userHome, 'AppData', 'Local');
  const binRoot = path.join(localAppData, 'OpenAI', 'Codex', 'bin');
  try {
    const candidates = readdirSync(binRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => path.join(binRoot, entry.name, 'codex.exe'))
      .filter(isFile)
      .map((executable) => ({ executable, modified: statSync(executable).mtimeMs }))
      .sort((left, right) => right.modified - left.modified);
    if (candidates.length) return candidates[0].executable;
  } catch {
    // Preserve the normal spawn error when Codex is not installed.
  }
  return 'codex';
}

export function codexRepairError(stderr) {
  if (/spawn\s+[^\r\n]*codex(?:\.exe)?\s+ENOENT/i.test(stderr)) {
    return '工作台未找到 Codex 程序，请安装或打开 Codex 后重新启动后台。原提示词未改变。';
  }
  if (/not logged in|please (?:log|sign) in|unauthorized|401 Unauthorized/i.test(stderr)) {
    return 'Codex 登录已失效，请在 Codex 中重新登录后重试。原提示词未改变。';
  }
  if (/AI 修复超时/.test(stderr)) {
    return 'AI 修复等待超时，请稍后重试。原提示词未改变。';
  }
  return 'Codex 未能返回修复结果，请查看下方错误详情。原提示词未改变。';
}
