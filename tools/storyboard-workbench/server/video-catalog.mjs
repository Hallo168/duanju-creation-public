export const DEFAULT_VIDEO_PROVIDER = 'guagu_video';
export const VIDEO_PROVIDER_IDS = ['guagu_video', 'canvas_video', '12ai_video'];

const optionalNumber = (value) =>
  value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
    ? Number(value) : null;

export function videoModels(providerId, rows) {
  let models = (Array.isArray(rows) ? rows : []).flatMap((row) => {
    if (!row || typeof row !== 'object') return [];
    const id = String(row.model ?? row.id ?? '').trim();
    if (!id) return [];
    const endpointTypes = Array.isArray(row.supported_endpoint_types)
      ? row.supported_endpoint_types.join(', ') : String(row.supported_endpoint_types ?? '');
    return [{
      id,
      name: String(row.name ?? row.id ?? row.model ?? id).trim(),
      description: String(row.description ?? '').trim(),
      resolution: String(row.resolution ?? '').trim(),
      type: String(row.type ?? endpointTypes).trim(),
      unitCost: optionalNumber(row.credit_cost),
      producible: optionalNumber(row.producible),
    }];
  });
  if (providerId === 'guagu_video') {
    models = models.filter((model) => model.type === 'image_to_video' && model.resolution);
  }
  if (providerId === 'canvas_video') {
    models = models
      .filter((model) => /seedance|video|veo|kling|wan|hailuo|sora|luma|runway/i.test(`${model.id} ${model.name}`))
      .map((model) => ({ ...model, resolution: model.resolution || '720p', type: model.type || 'video' }));
  }
  if (providerId === '12ai_video') {
    models = models.filter((model) => /openai-video|video/i.test(model.type))
      .map((model) => ({ ...model, resolution: model.resolution || '480p 默认', type: model.type || 'openai-video' }));
  }
  const seen = new Set();
  return models.filter((model) => {
    if (seen.has(model.id)) return false;
    seen.add(model.id);
    return true;
  });
}

async function withDeadline(work, timeoutMs) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(work),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('渠道连接超时，请稍后刷新。')), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export function createVideoCatalog({ metadata, readProvider, timeoutMs = 12_000 }) {
  const pending = new Map();
  const load = (id) => {
    if (pending.has(id)) return pending.get(id);
    const info = metadata[id];
    if (!info || !VIDEO_PROVIDER_IDS.includes(id)) throw new Error('没有找到所选视频渠道。');
    const request = (async () => {
      let models = [];
      let error = '';
      try {
        const live = await withDeadline(() => readProvider(id), timeoutMs);
        error = typeof live?.error === 'string' ? live.error : '';
        models = videoModels(id, live?.models);
      } catch (failure) {
        error = failure instanceof Error ? failure.message : '读取渠道失败，请稍后刷新。';
      }
      return {
        id, label: info.label, tier: info.tier,
        selectable: Boolean(info.selectable && !error && models.length),
        available: !error && models.length > 0,
        error, disabledReason: info.disabledReason ?? '', note: info.note,
        defaultResolution: info.defaultResolution, referenceMode: info.referenceMode,
        submissionWarning: info.submissionWarning, models,
      };
    })();
    pending.set(id, request);
    void request.finally(() => { if (pending.get(id) === request) pending.delete(id); });
    return request;
  };
  return (ids = VIDEO_PROVIDER_IDS) => Promise.all(ids.map(load));
}
