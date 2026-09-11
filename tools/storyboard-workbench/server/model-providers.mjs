import path from 'node:path';
import { spawn } from 'node:child_process';
import { resolveCodexExecutable } from './codex-cli.mjs';
import { pencilEnabled } from './production-policy.mjs';
import {
  WorkbenchError,
  createLocks,
  optionalJson,
  writeJson,
  digest,
} from './storage.mjs';

const adapters = [
  'codex_cli',
  'openai_responses',
  'openai_compatible_chat',
  'custom_webhook',
  'openai_images',
];
const capabilities = (adapter) =>
  adapter === 'openai_images' || adapter === 'flow_image'
    ? ['image']
    : adapter === 'codex_cli'
      ? ['text', 'agent']
      : ['text'];
const endpointDefaults = {
  openai_responses: '/responses',
  openai_compatible_chat: '/chat/completions',
  custom_webhook: '/',
  openai_images: '/images/generations',
};
export const STEP_DEFINITIONS = [
  {
    id: 'assets.plan',
    label: '资产方案与提示词',
    capability: 'text',
    defaultKey: 'text',
  },
  {
    id: 'assets.image',
    label: '普通资产图片',
    capability: 'image',
    defaultKey: 'image',
  },
];

export function publicModel(provider) {
  const { apiKey, ...rest } = provider;
  const keyAvailable =
    !provider.apiKeyEnv || Boolean(process.env[provider.apiKeyEnv]);
  return {
    ...rest,
    capabilities: capabilities(provider.adapter),
    secretStored: Boolean(apiKey),
    keyAvailable,
    configured:
      provider.enabled &&
      keyAvailable &&
      (provider.adapter === 'codex_cli' ||
        Boolean(provider.baseUrl && provider.model)),
  };
}

export function sanitizeModel(candidate, previous = {}) {
  if (!candidate || typeof candidate !== 'object')
    throw new WorkbenchError('请填写接口配置。');
  if (!/^[a-z0-9][a-z0-9_-]{1,39}$/i.test(candidate.id ?? ''))
    throw new WorkbenchError('接口 ID 格式不正确。');
  if (!adapters.includes(candidate.adapter))
    throw new WorkbenchError('不支持这个接口协议。');
  const clean = (value, limit = 200) =>
    String(value ?? '')
      .trim()
      .replace(/[\r\n]/g, ' ')
      .slice(0, limit);
  const provider = {
    id: candidate.id,
    label: clean(candidate.label, 80),
    adapter: candidate.adapter,
    enabled: candidate.enabled === true,
    model: clean(candidate.model),
    baseUrl: clean(candidate.baseUrl, 600).replace(/\/$/, ''),
    endpoint: clean(
      candidate.endpoint || endpointDefaults[candidate.adapter],
      200,
    ),
    editEndpoint: clean(candidate.editEndpoint || '/images/edits', 200),
    modelsEndpoint: clean(candidate.modelsEndpoint || '/models', 200),
    apiKeyEnv: clean(candidate.apiKeyEnv),
    apiKey:
      candidate.clearKey === true
        ? ''
        : typeof candidate.apiKey === 'string' && candidate.apiKey.trim()
          ? candidate.apiKey.trim()
          : previous.apiKey || '',
    structuredOutputs: candidate.structuredOutputs === true,
    description: clean(candidate.description, 300),
  };
  // A newly entered key takes precedence over a legacy environment-variable setting.
  if (typeof candidate.apiKey === 'string' && candidate.apiKey.trim())
    provider.apiKeyEnv = '';
  if (!provider.label) throw new WorkbenchError('请填写接口名称。');
  // The project ban applies to asset-image fallback, not text model providers.
  if (
    capabilities(provider.adapter).includes('image') &&
    /poloai|\bpolo\b|wuyin|无音/i.test(`${provider.id} ${provider.label} ${provider.baseUrl}`)
  )
    throw new WorkbenchError('该资产生图通道已停用并移除；请使用当前项目配置中的生图渠道。');
  if (
    provider.apiKeyEnv &&
    !/^[A-Za-z_][A-Za-z0-9_]*$/.test(provider.apiKeyEnv)
  )
    throw new WorkbenchError('密钥环境变量名格式不正确。');
  if (provider.adapter !== 'codex_cli') {
    let url;
    try {
      url = new URL(provider.baseUrl);
    } catch {
      throw new WorkbenchError('接口地址不是有效 URL。');
    }
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      throw new WorkbenchError(
        '接口地址只允许不含账号、密码和查询参数的 HTTP 地址。',
      );
    const suffix = endpointDefaults[provider.adapter];
    if (suffix && suffix !== '/' && url.pathname.replace(/\/$/, '').endsWith(suffix)) {
      url.pathname = url.pathname.replace(/\/$/, '').slice(0, -suffix.length);
      provider.baseUrl = url.toString().replace(/\/$/, '');
      provider.endpoint = suffix;
    }
    for (const key of ['endpoint', 'editEndpoint', 'modelsEndpoint']) {
      if (!/^\/(?!\/)/.test(provider[key]) || /[?#\\]|\.\./.test(provider[key]))
        throw new WorkbenchError('端点必须是以 / 开头的相对路径。');
    }
  }
  return provider;
}

export const providerUrl = (provider, endpoint = provider.endpoint) =>
  `${provider.baseUrl.replace(/\/$/, '')}${endpoint}`;
export const providerHeaders = (provider, json = true) => ({
  ...(json ? { 'Content-Type': 'application/json' } : {}),
  ...(provider.apiKey
    ? {
        [provider.adapter === 'flow_image'
          ? 'x-goog-api-key'
          : 'Authorization']:
          provider.adapter === 'flow_image'
            ? provider.apiKey
            : `Bearer ${provider.apiKey}`,
      }
    : {}),
});
export const modelReceipt = (provider) => ({
  providerId: provider.id,
  label: provider.label,
  adapter: provider.adapter,
  model: provider.model || '(CLI 当前默认模型)',
  baseUrl: provider.baseUrl,
  endpoint: provider.endpoint,
});

export function runProcess(
  command,
  args,
  { cwd, input = '', timeoutMs = 600_000 } = {},
) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        NO_COLOR: '1',
      },
    });
    const chunks = [];
    const errors = [];
    let bytes = 0;
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.stdout.on('data', (chunk) => {
      bytes += chunk.length;
      if (bytes < 12_000_000) chunks.push(chunk);
      else child.kill();
    });
    child.stderr.on('data', (chunk) => {
      if (errors.reduce((n, part) => n + part.length, 0) < 100_000)
        errors.push(chunk);
    });
    child.on('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(Buffer.concat(chunks).toString('utf8'));
      else
        reject(
          new WorkbenchError(
            `本机程序未完成（${code ?? '超时'}）：${Buffer.concat(errors).toString('utf8').slice(-1600)}`,
            502,
          ),
        );
    });
    child.stdin.on('error', () => {});
    child.stdin.end(input, 'utf8');
  });
}

export function createModelService({
  projectRoot,
  directory,
  legacyFile,
  legacyDefaults = [],
  fetcher = fetch,
}) {
  const file = path.join(directory, '.local', 'models.json');
  const lock = createLocks();
  const load = async () => {
    const saved = await optionalJson(file, null);
    if (saved) return saved;
    const legacy = await optionalJson(legacyFile, { providers: [] });
    const byId = new Map(legacyDefaults.map((item) => [item.id, item]));
    for (const provider of legacy.providers ?? [])
      byId.set(provider.id, provider);
    const providers = [];
    for (const item of byId.values()) {
      if (!item.baseUrl && item.adapter !== 'codex_cli') continue;
      providers.push(sanitizeModel(item));
    }
    return {
      version: 1,
      providers,
      defaults: {
        text: {
          providerId: providers.find((item) => item.enabled)?.id || 'codex-cli',
          model: '',
        },
        image: { providerId: 'project-chatgpt2api', model: '' },
      },
      overrides: {},
    };
  };
  const builtins = async (secretId = '') => {
    const config = await optionalJson(
      path.join(projectRoot, '工具', 'api配置.json'),
      {},
    );
    const c2a = config.providers?.chatgpt2api;
    const flow = config.providers?.flow2api_banana2;
    const result = [];
    if (c2a)
      result.push({
        id: 'project-chatgpt2api',
        label: '本机 GPTAPI',
        adapter: 'openai_images',
        enabled: !c2a.disabled,
        model: c2a.model || 'gpt-image-2',
        baseUrl: c2a.base_url,
        endpoint: '/v1/images/generations',
        editEndpoint: '/v1/images/edits',
        modelsEndpoint: '/v1/models',
        ...(secretId === 'project-chatgpt2api' ? { apiKey: c2a.api_key } : {}),
        readonly: true,
        source: '项目图片配置',
      });
    if (flow) {
      const enabled = !flow.disabled && await pencilEnabled(projectRoot);
      let apiKey = '';
      if (enabled && secretId === 'project-flow') {
        apiKey = (
          await runProcess(
            'python',
            [
              '-c',
              'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["global"]["api_key"])',
              path.resolve(projectRoot, flow.settings_path),
            ],
            { cwd: projectRoot },
          )
        ).trim();
      }
      result.push({
        id: 'project-flow',
        label: enabled ? 'Flow · 彩铅专用' : 'Flow · 彩铅暂时停用',
        adapter: 'flow_image',
        enabled,
        model: 'gemini-3.1-flash-image',
        baseUrl: flow.base_url,
        endpoint: (
          flow.endpoints?.generate || '/models/{model}:generateContent'
        ).replace('{model}', 'gemini-3.1-flash-image'),
        modelsEndpoint: flow.endpoints?.models || '/models',
        apiKey,
        readonly: true,
        source: '固定彩铅配置',
      });
    }
    return result;
  };
  const list = async () => {
    const config = await load();
    const providers = [...config.providers, ...(await builtins())];
    return {
      ...config,
      providers: providers.map(publicModel),
      steps: STEP_DEFINITIONS,
      revision: digest(JSON.stringify(config)),
    };
  };
  const save = (body) =>
    lock('models', async () => {
      const config = await load();
      if (
        body.revision !== undefined &&
        body.revision !== digest(JSON.stringify(config))
      )
        throw new WorkbenchError('模型配置已更新，请刷新后再保存。', 409);
      if (body.provider) {
        if (body.provider.id.startsWith('project-'))
          throw new WorkbenchError(
            '项目现有接口从原配置读取；可新增接口后在步骤中选择。',
          );
        const index = config.providers.findIndex(
          (item) => item.id === body.provider.id,
        );
        const provider = sanitizeModel(body.provider, config.providers[index]);
        if (index < 0) config.providers.push(provider);
        else config.providers[index] = provider;
      }
      const providers = [...config.providers, ...(await builtins())];
      const validate = (mapping, capability) => {
        if (
          !mapping ||
          typeof mapping.providerId !== 'string' ||
          typeof mapping.model !== 'string' ||
          mapping.model.length > 200
        )
          throw new WorkbenchError('模型选择格式不正确。');
        const found = providers.find((item) => item.id === mapping.providerId);
        if (
          !found ||
          !capabilities(found.adapter).includes(capability) ||
          found.id === 'project-flow'
        )
          throw new WorkbenchError('选择的接口不支持这个步骤。');
        return { providerId: mapping.providerId, model: mapping.model.trim() };
      };
      if (body.defaults)
        config.defaults = {
          text: validate(body.defaults.text, 'text'),
          image: validate(body.defaults.image, 'image'),
        };
      if (body.overrides) {
        config.overrides = {};
        for (const [id, mapping] of Object.entries(body.overrides)) {
          const step = STEP_DEFINITIONS.find((item) => item.id === id);
          if (!step) throw new WorkbenchError('没有这个可配置步骤。');
          config.overrides[id] = validate(mapping, step.capability);
        }
      }
      await writeJson(file, config);
      return list();
    });
  const resolve = async (stepOrId, allowBlank = false) => {
    const config = await load();
    const step = STEP_DEFINITIONS.find((item) => item.id === stepOrId);
    const mapping = step
      ? config.overrides[step.id] || config.defaults[step.defaultKey]
      : { providerId: stepOrId, model: '' };
    const provider =
      config.providers.find((item) => item.id === mapping?.providerId) ||
      (await builtins(false)).find((item) => item.id === mapping?.providerId);
    if (!provider || !provider.enabled)
      throw new WorkbenchError(
        '本步骤的接口还未配置或未启用，请打开模型设置。',
        409,
      );
    let resolved = { ...provider, model: mapping.model || provider.model };
    if (provider.id.startsWith('project-'))
      resolved = {
        ...(await builtins(provider.id)).find(
          (item) => item.id === provider.id,
        ),
        model: resolved.model,
      };
    if (provider.apiKeyEnv) {
      resolved.apiKey = process.env[provider.apiKeyEnv];
      if (!resolved.apiKey)
        throw new WorkbenchError(
          `密钥环境变量 ${provider.apiKeyEnv} 尚未设置。`,
          409,
        );
    }
    if (!allowBlank && resolved.adapter !== 'codex_cli' && !resolved.model)
      throw new WorkbenchError('请填写本步骤的完整模型名称。', 409);
    return resolved;
  };
  const modelsFor = async (provider) => {
    if (provider.adapter === 'codex_cli')
      return { models: [], note: 'CLI 使用所填模型，留空沿用 CLI 默认值。' };
    let response;
    try {
      response = await fetcher(
      providerUrl(provider, provider.modelsEndpoint),
      {
        headers: providerHeaders(provider, false),
        redirect: 'error',
        signal: AbortSignal.timeout(20_000),
      },
      );
    } catch {
      throw new WorkbenchError('无法读取模型列表，请检查地址、Key 和网络；也可以手动填写模型名。', 502);
    }
    if (!response.ok)
      throw new WorkbenchError(`模型列表读取失败（HTTP ${response.status}），请检查地址和 Key，或手动填写模型名。`, 502);
    let body;
    try { body = await response.json(); }
    catch { throw new WorkbenchError('模型目录未返回 JSON，可手动填写模型名。', 502); }
    const rows = Array.isArray(body) ? body : body?.data ?? body?.models;
    if (!Array.isArray(rows)) throw new WorkbenchError('接口没有返回可识别的模型列表，可手动填写模型名。', 502);
    if (provider.apiKey && JSON.stringify(rows).includes(provider.apiKey)) throw new WorkbenchError('模型目录包含认证信息，已阻止显示，请核对供应商接口。', 502);
    const names = rows.map((item) => typeof item === 'string' ? item : item?.id || item?.name?.replace(/^models\//, ''))
      .filter((name) => typeof name === 'string' && name.trim() && name.length <= 200);
    return { models: [...new Set(names)].sort((a, b) => a.localeCompare(b)), note: names.length ? undefined : '接口返回了空列表，可手动填写模型名。' };
  };
  const models = async (id) => modelsFor(await resolve(id, true));
  const previewModels = async (candidate) => {
    const config = await load();
    if (String(candidate?.id ?? '').startsWith('project-'))
      throw new WorkbenchError('内置接口请使用已保存的配置读取模型。');
    const provider = sanitizeModel(candidate, config.providers.find((item) => item.id === candidate?.id));
    if (provider.apiKeyEnv) {
      provider.apiKey = process.env[provider.apiKeyEnv];
      if (!provider.apiKey) throw new WorkbenchError('指定的密钥环境变量尚未设置。', 409);
    }
    return modelsFor(provider);
  };
  return {
    list,
    save,
    resolve,
    models,
    previewModels,
    legacyList: async () =>
      (await load()).providers
        .map((item) => ({ ...publicModel(item), description: item.description || '' }))
        .filter(
          (item) => !['openai_images', 'flow_image'].includes(item.adapter),
        ),
  };
}

export async function invokeText(
  provider,
  prompt,
  schema,
  { projectRoot, schemaPath, fetcher = fetch } = {},
) {
  if (provider.adapter === 'codex_cli') {
    const args = [
      'exec',
      '--ephemeral',
      '--ignore-user-config',
      '--sandbox',
      'read-only',
      '--output-schema',
      schemaPath,
      '--color',
      'never',
      '-C',
      projectRoot,
    ];
    if (provider.model) args.push('--model', provider.model);
    args.push('--', '-');
    return runProcess(resolveCodexExecutable(), args, {
      cwd: projectRoot,
      input: prompt,
      timeoutMs: 90 * 60_000,
    });
  }
  let payload;
  if (provider.adapter === 'openai_responses')
    payload = {
      model: provider.model,
      store: false,
      input: [{ role: 'user', content: prompt }],
      text: {
        format: {
          type: 'json_schema',
          name: 'asset_plan',
          strict: true,
          schema,
        },
      },
    };
  else if (provider.adapter === 'openai_compatible_chat') {
    payload = {
      model: provider.model,
      messages: [{ role: 'user', content: prompt }],
    };
    if (provider.structuredOutputs)
      payload.response_format = {
        type: 'json_schema',
        json_schema: { name: 'asset_plan', strict: true, schema },
      };
  } else payload = { model: provider.model, prompt, outputSchema: schema };
  let response;
  try {
    response = await fetcher(providerUrl(provider), {
      method: 'POST',
      headers: providerHeaders(provider),
      body: JSON.stringify(payload),
      redirect: 'error',
      signal: AbortSignal.timeout(20 * 60_000),
    });
  } catch {
    throw new WorkbenchError('模型请求中断，结果未知；不会自动重投。', 502);
  }
  if (!response.ok)
    throw new WorkbenchError(
      `文字接口返回 HTTP ${response.status}，本次未重试。`,
      502,
    );
  const body = await response.json();
  return (
    body.output_text ||
    body.choices?.[0]?.message?.content ||
    body.output
      ?.flatMap((item) => item.content || [])
      .filter((item) => item.type === 'output_text')
      .map((item) => item.text)
      .join('\n') ||
    JSON.stringify(body.result || body)
  );
}
