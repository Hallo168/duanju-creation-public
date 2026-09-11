import path from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { invokeStudioContext, parseStudioJson } from './studio-execution.mjs';
import { runCodexTurn } from './studio-chat.mjs';

export function recoverContextHints(job, session, message, manifests) {
  if (job?.status !== 'failed' || job.skillId !== 'xuanti' || job.routeId !== session.routeId || job.providerId !== session.providerId) return [];
  if ([...(session.messages ?? [])].reverse().find((item) => item.role === 'user')?.content !== message) return [];
  const previousRules = session.lastPreflight?.rules;
  if (!previousRules?.length || previousRules.length !== manifests.length || manifests.some((item) => !previousRules.some((old) => old.path === item.path && old.hash === item.hash))) return [];
  const hints = new Map();
  for (const line of String(job.output ?? '').split(/\r?\n/)) {
    try {
      const event = JSON.parse(line);
      if (event.type !== 'item.completed' || event.item?.type !== 'agent_message') continue;
      const result = parseStudioJson(event.item.text);
      if (result.status !== 'needs_context' || !Array.isArray(result.context_requests)) continue;
      for (const request of result.context_requests) {
        if (['read', 'list'].includes(request.operation) && typeof request.path === 'string') hints.set(`${request.operation}:${request.path}`, { operation: request.operation, path: request.path });
      }
    } catch { /* Only completed, valid structured responses may supply optional read hints. */ }
  }
  return [...hints.values()].slice(0, 64);
}

export async function invokeStudioCli({ runtimeDirectory, runTurn = runCodexTurn, onProgress = () => {}, ...options }) {
  await mkdir(runtimeDirectory, { recursive: true });
  const schemaPath = path.join(runtimeDirectory, 'context-schema.json');
  const privateRounds = [];
  try {
    const result = await invokeStudioContext({ ...options, onProgress, invokeRound: async ({ messages, schema, round, reviewing, sourceCount }) => {
      if (!round) await writeFile(schemaPath, `${JSON.stringify(schema, null, 2)}\n`, 'utf8');
      const prompt = `本轮通过工作台后台提供资料和保存产物。不要调用本机命令、文件工具、浏览器或其他外部工具。命令文档中的 Read/List/Write 分别通过 context_requests/read、context_requests/list、file_changes 完成。必须完整遵循实际提供的业务流程；资料不足就申请补充，不能跳过或猜测。\n\n${messages.map((item) => `[${item.role}]\n${item.content}`).join('\n\n')}`;
      const result = await runTurn({ prompt, model: options.provider.model, schemaPath, timeoutMs: 20 * 60_000,
        onProgress: (progress) => onProgress({ ...progress, phase: reviewing ? 'Codex 正在独立审稿' : round ? 'Codex 正在结合补充资料继续处理' : 'Codex 正在阅读资料并处理本轮要求', requests: round + 1, sourceCount }),
      });
      privateRounds.push(result.output);
      return { output_text: result.text, ...(result.actualModel ? { model: result.actualModel } : {}), ...(result.usage?.usageAvailable ? { usage: { input_tokens: result.usage.inputTokens, output_tokens: result.usage.outputTokens } } : {}) };
    } });
    return { ...result, output: privateRounds.join('\n\n'), mediatedFiles: true };
  } catch (error) {
    error.privateOutput = [...privateRounds, error.privateOutput].filter(Boolean).join('\n\n');
    throw error;
  }
}
