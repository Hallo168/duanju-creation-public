import { WorkbenchError } from './storage.mjs';

const statuses = new Set(['waiting_user', 'completed', 'blocked', 'needs_context']);
const isResult = (value) => value && !Array.isArray(value) && typeof value === 'object'
  && typeof value.reply === 'string' && statuses.has(value.status)
  && (value.file_changes === undefined || (Array.isArray(value.file_changes) && value.file_changes.every((item) => item && typeof item.path === 'string' && typeof item.content === 'string')))
  && (value.context_requests === undefined || (Array.isArray(value.context_requests) && value.context_requests.every((item) => item && ['read', 'list'].includes(item.operation) && typeof item.path === 'string')));

const formatError = () => Object.assign(new WorkbenchError('回复格式未能识别，尚未完成保存。', 502), { code: 'STUDIO_RESULT_FORMAT' });

function decode(value) {
  for (let depth = 0; depth < 3 && typeof value === 'string'; depth++) value = JSON.parse(value);
  return value;
}

// Parse syntax only. Never invent missing fields, fix an incomplete write, or
// choose between multiple competing results. Braces inside strings are data.
export function parseStudioJson(value) {
  try {
    const parsed = decode(value);
    if (isResult(parsed)) return parsed;
    throw formatError();
  } catch (error) {
    if (error.code === 'STUDIO_RESULT_FORMAT') throw error;
    // Try a result surrounded by ordinary explanatory text below.
  }
  if (typeof value !== 'string') throw formatError();
  const text = value.replace(/^\uFEFF/u, '').trim();
  const candidates = [];
  let start = -1, depth = 0, quoted = false, escaped = false;
  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (start < 0) { if (character === '{') { start = index; depth = 1; } continue; }
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') quoted = false;
      continue;
    }
    if (character === '"') quoted = true;
    else if (character === '{') depth++;
    else if (character === '}' && --depth === 0) {
      try {
        const candidate = decode(text.slice(start, index + 1));
        if (isResult(candidate)) candidates.push(candidate);
      } catch { /* Leave malformed content unchanged. */ }
      start = -1;
    }
  }
  if (candidates.length !== 1) throw formatError();
  return candidates[0];
}

// Only recover reader-facing prose, never a malformed command envelope or its
// internal receipts. The caller still checks credentials and instruction copies.
export function studioVisibleText(value) {
  const text = String(value ?? '').replace(/<(think|analysis)>[\s\S]*?<\/\1>/giu, '').trim();
  const reply = text.match(/^(?:```(?:json)?\s*)?\{\s*"reply"\s*:\s*("(?:[^"\\]|\\.)*")/iu)?.[1];
  if (reply) { try { return JSON.parse(reply).trim(); } catch { return ''; } }
  if (/"(?:reply|status|file_changes|rules_read|context_requests)"\s*:/u.test(text) || /^\s*[[{]/u.test(text) || /^\s*<(?:!doctype|html)/iu.test(text)) return '';
  return text;
}

export function pendingScriptDraft(result, episode) {
  if (!episode || result?.episode && result.episode !== episode || !Array.isArray(result?.file_changes)) return null;
  const target = `stories/${episode}/二创稿.md`;
  const drafts = result.file_changes.filter((item) => typeof item?.path === 'string' && item.path.replaceAll('\\', '/') === target && typeof item.content === 'string' && item.content.trim());
  return drafts?.length === 1 ? { path: target, content: drafts[0].content } : null;
}

export function unfinishedStudioResult(reply, episode, warning, resumeAction = null) {
  return { reply, status: 'blocked', episode: episode ?? null, sessionSummary: '', decisions: [], memoryUpdate: [], artifacts: [], fileChanges: [], rulesRead: [], rulesApplied: [], warnings: [warning], nextActions: [resumeAction === 'review' ? '继续审稿' : '重试'], resumeAction };
}
