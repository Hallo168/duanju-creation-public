'use client';

/* oxlint-disable react/react-compiler -- This editor preserves unsaved remote configuration drafts. */
import { useEffect, useState } from 'react';
import { Plus, Save, LoaderCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

export const API = 'http://127.0.0.1:4317';
export async function request<T>(route: string, body?: unknown): Promise<T> {
  let response;
  try {
    response = await fetch(
      API + route,
      body === undefined
        ? { cache: 'no-store' }
        : {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          },
    );
  } catch {
    throw new Error('没有连上本地服务，请检查工作台服务是否已启动。');
  }
  const result = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new Error(result.error || '操作没有完成。');
  return result;
}

export type Mapping = { providerId: string; model: string };
export type Provider = {
  id: string;
  label: string;
  adapter: string;
  enabled: boolean;
  model: string;
  baseUrl: string;
  endpoint: string;
  editEndpoint: string;
  modelsEndpoint: string;
  apiKeyEnv: string;
  structuredOutputs: boolean;
  readonly?: boolean;
  capabilities: string[];
  secretStored: boolean;
  configured: boolean;
  apiKey?: string;
  clearKey?: boolean;
};
export type Settings = {
  providers: Provider[];
  revision: string;
  defaults: { text: Mapping; image: Mapping };
  overrides: Record<string, Mapping>;
  steps: {
    id: string;
    label: string;
    capability: 'text' | 'image';
    defaultKey: 'text' | 'image';
  }[];
};

const selectClass =
  'h-9 w-full rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-800 disabled:opacity-50';
const protocols = [
  ['openai_compatible_chat', '文字 · Chat 兼容接口'],
  ['openai_responses', '文字 · Responses 接口'],
  ['custom_webhook', '文字 · 自定义 Webhook'],
  ['openai_images', '图片 · Images 兼容接口'],
  ['codex_cli', 'Agent · 本机 Codex CLI'],
];
const endpointFor = (adapter: string) =>
  ({
    openai_compatible_chat: '/chat/completions',
    openai_responses: '/responses',
    openai_images: '/images/generations',
    custom_webhook: '/',
    codex_cli: '',
  })[adapter] || '';

export function ModelSettings({
  open,
  onOpenChange,
  settings,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (value: boolean) => void;
  settings: Settings | null;
  onSaved: (value: Settings) => void;
}) {
  const [draft, setDraft] = useState<Provider | null>(null);
  const [config, setConfig] = useState<Settings | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [catalog, setCatalog] = useState<string[]>([]);
  useEffect(() => {
    if (!open) {
      setConfig(null);
      setDraft(null);
      setNotice('');
    } else if (settings && !config) setConfig(settings);
  }, [open, settings, config]);
  const loadModels = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      const result = await request<{ models: string[]; note?: string }>(
        `/api/workbench/models?providerId=${encodeURIComponent(draft.id)}`,
      );
      setCatalog(result.models);
      setNotice(
        result.note ||
          `已读取 ${result.models.length} 个模型，可在模型名称输入框选择。`,
      );
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : '模型目录读取失败，请直接填写完整名称。',
      );
    } finally {
      setBusy(false);
    }
  };
  const save = async (body: unknown) => {
    setBusy(true);
    setNotice('');
    try {
      const next = await request<Settings>('/api/workbench/settings', body);
      setConfig(next);
      onSaved(next);
      setDraft(null);
      setNotice('已保存。新设置用于之后启动的任务。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };
  const routePicker = (
    label: string,
    capability: 'text' | 'image',
    mapping: Mapping,
    change: (value: Mapping) => void,
  ) => (
    <div className="grid items-center gap-2 rounded-xl bg-slate-50 p-3 sm:grid-cols-[150px_1fr_1fr]">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <select
        aria-label={`${label}接口`}
        className={selectClass}
        value={mapping.providerId}
        onChange={(event) =>
          change({ providerId: event.target.value, model: '' })
        }
      >
        {!config?.providers.some((item) => item.id === mapping.providerId) && (
          <option value={mapping.providerId}>待配置接口</option>
        )}
        {config?.providers
          .filter(
            (item) =>
              item.capabilities.includes(capability) &&
              item.id !== 'project-flow',
          )
          .map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
              {item.enabled ? '' : '（未启用）'}
            </option>
          ))}
      </select>
      <Input
        aria-label={`${label}模型`}
        value={mapping.model}
        placeholder={
          config?.providers.find((item) => item.id === mapping.providerId)
            ?.model || '留空沿用接口默认模型'
        }
        onChange={(event) => change({ ...mapping, model: event.target.value })}
      />
    </div>
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>模型设置</DialogTitle>
          <DialogDescription>
            接口保存一次。步骤可沿用默认模型，也可以单独指定。
          </DialogDescription>
        </DialogHeader>
        {config && (
          <div className="space-y-5">
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">默认模型</h3>
              {routePicker('文字生成', 'text', config.defaults.text, (value) =>
                setConfig({
                  ...config,
                  defaults: { ...config.defaults, text: value },
                }),
              )}
              {routePicker(
                '普通图片',
                'image',
                config.defaults.image,
                (value) =>
                  setConfig({
                    ...config,
                    defaults: { ...config.defaults, image: value },
                  }),
              )}
            </section>
            <details className="rounded-xl border border-slate-200 p-3">
              <summary className="cursor-pointer text-sm font-medium">
                按步骤单独设置
              </summary>
              <div className="mt-3 space-y-3">
                {config.steps.map((step) => (
                  <div key={step.id}>
                    <label className="mb-2 flex items-center gap-2 text-sm text-slate-600">
                      <input
                        type="checkbox"
                        checked={Boolean(config.overrides[step.id])}
                        onChange={(event) => {
                          const overrides = { ...config.overrides };
                          if (event.target.checked)
                            overrides[step.id] = {
                              ...config.defaults[step.defaultKey],
                            };
                          else delete overrides[step.id];
                          setConfig({ ...config, overrides });
                        }}
                      />
                      为“{step.label}”单独选择
                    </label>
                    {config.overrides[step.id] &&
                      routePicker(
                        step.label,
                        step.capability,
                        config.overrides[step.id],
                        (value) =>
                          setConfig({
                            ...config,
                            overrides: {
                              ...config.overrides,
                              [step.id]: value,
                            },
                          }),
                      )}
                  </div>
                ))}
                <p className="text-xs leading-5 text-slate-500">
                  资产方案的独立自查沿用同一模型。四宫格由本地程序处理。
                  彩铅仅在项目启用后由 Flow 派生。
                </p>
              </div>
            </details>
            <Button
              disabled={busy || Boolean(draft)}
              onClick={() =>
                void save({
                  revision: config.revision,
                  defaults: config.defaults,
                  overrides: config.overrides,
                })
              }
            >
              <Save className="size-4" />
              保存模型选择
            </Button>
            <section className="border-t border-slate-200 pt-4">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold">已保存的接口</h3>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    setCatalog([]);
                    setDraft({
                      id: `api-${Date.now().toString(36)}`,
                      label: '',
                      adapter: 'openai_compatible_chat',
                      enabled: true,
                      model: '',
                      baseUrl: '',
                      endpoint: '/chat/completions',
                      editEndpoint: '/images/edits',
                      modelsEndpoint: '/models',
                      apiKeyEnv: '',
                      structuredOutputs: false,
                      capabilities: ['text'],
                      secretStored: false,
                      configured: false,
                    });
                  }}
                >
                  <Plus className="size-4" />
                  接入 API
                </Button>
              </div>
              <div className="flex flex-wrap gap-2">
                {config.providers.map((provider) => (
                  <button
                    type="button"
                    key={provider.id}
                    disabled={provider.readonly || busy}
                    onClick={() => {
                      setDraft({ ...provider, apiKey: '' });
                      setCatalog([]);
                    }}
                    className={`rounded-lg border px-3 py-2 text-left text-xs ${draft?.id === provider.id ? 'border-orange-400 bg-orange-50' : 'border-slate-200 bg-white'} disabled:bg-slate-50`}
                  >
                    <span className="block font-medium">{provider.label}</span>
                    <span className="text-slate-500">
                      {provider.readonly
                        ? '读取项目现有配置'
                        : provider.adapter === 'codex_cli'
                          ? '可对话、可使用本地工具'
                          : provider.capabilities.includes('image')
                            ? '图片接口'
                            : '文字接口'}
                    </span>
                  </button>
                ))}
              </div>
            </section>
            {draft && (
              <section className="space-y-3 rounded-xl border border-orange-200 bg-orange-50/30 p-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <label
                    className="space-y-1 text-xs text-slate-600"
                    htmlFor="provider-label"
                  >
                    接口名称
                    <Input
                      id="provider-label"
                      value={draft.label}
                      onChange={(event) =>
                        setDraft({ ...draft, label: event.target.value })
                      }
                    />
                  </label>
                  <label className="space-y-1 text-xs text-slate-600">
                    接口协议
                    <select
                      className={selectClass}
                      value={draft.adapter}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          adapter: event.target.value,
                          endpoint: endpointFor(event.target.value),
                        })
                      }
                    >
                      {protocols.map(([id, label]) => (
                        <option key={id} value={id}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {draft.adapter !== 'codex_cli' && (
                  <>
                    <label
                      className="block space-y-1 text-xs text-slate-600"
                      htmlFor="provider-url"
                    >
                      API 基础地址
                      <Input
                        id="provider-url"
                        value={draft.baseUrl}
                        placeholder="https://你的接口地址/v1"
                        onChange={(event) =>
                          setDraft({ ...draft, baseUrl: event.target.value })
                        }
                      />
                    </label>
                    <label
                      className="block space-y-1 text-xs text-slate-600"
                      htmlFor="provider-key"
                    >
                      API 密钥
                      <Input
                        id="provider-key"
                        type="password"
                        autoComplete="new-password"
                        value={draft.apiKey || ''}
                        placeholder={
                          draft.secretStored
                            ? '已有密钥，留空保留'
                            : '仅保存在本机服务端'
                        }
                        onChange={(event) =>
                          setDraft({ ...draft, apiKey: event.target.value })
                        }
                      />
                    </label>
                  </>
                )}
                <label
                  className="block space-y-1 text-xs text-slate-600"
                  htmlFor="provider-model"
                >
                  完整模型名称
                  <Input
                    id="provider-model"
                    list="asset-model-catalog"
                    value={draft.model}
                    placeholder={
                      draft.adapter === 'codex_cli'
                        ? '留空沿用 CLI 默认模型'
                        : '填写供应商提供的完整名称'
                    }
                    onChange={(event) =>
                      setDraft({ ...draft, model: event.target.value })
                    }
                  />
                </label>
                <datalist id="asset-model-catalog">
                  {catalog.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </datalist>
                {draft.adapter !== 'codex_cli' &&
                  config.providers.some((item) => item.id === draft.id) && (
                    <button
                      type="button"
                      disabled={busy}
                      className="text-xs text-blue-700 hover:underline"
                      onClick={() => void loadModels()}
                    >
                      读取已保存接口的模型目录
                    </button>
                  )}
                <details>
                  <summary className="cursor-pointer text-xs text-slate-600">
                    高级设置
                  </summary>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    {(
                      [
                        'endpoint',
                        'modelsEndpoint',
                        ...(draft.adapter === 'openai_images'
                          ? ['editEndpoint']
                          : []),
                        'apiKeyEnv',
                      ] as const
                    ).map((key) => (
                      <label
                        key={key}
                        htmlFor={`provider-${key}`}
                        className="space-y-1 text-xs text-slate-600"
                      >
                        {
                          {
                            endpoint: '生成端点',
                            modelsEndpoint: '模型目录端点',
                            editEndpoint: '参考图编辑端点',
                            apiKeyEnv: '从环境变量读取密钥（可选）',
                          }[key]
                        }
                        <Input
                          id={`provider-${key}`}
                          value={String(draft[key as keyof Provider] || '')}
                          onChange={(event) =>
                            setDraft({ ...draft, [key]: event.target.value })
                          }
                        />
                      </label>
                    ))}
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={draft.structuredOutputs}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            structuredOutputs: event.target.checked,
                          })
                        }
                      />
                      接口支持严格 JSON 输出
                    </label>
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={Boolean(draft.clearKey)}
                        onChange={(event) =>
                          setDraft({ ...draft, clearKey: event.target.checked })
                        }
                      />
                      清除已保存密钥
                    </label>
                  </div>
                </details>
                <div className="flex flex-wrap items-center gap-3">
                  <label className="mr-auto flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={draft.enabled}
                      onChange={(event) =>
                        setDraft({ ...draft, enabled: event.target.checked })
                      }
                    />
                    启用接口
                  </label>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setDraft(null)}
                  >
                    收起
                  </Button>
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void save({ revision: config.revision, provider: draft })
                    }
                  >
                    {busy ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <Save className="size-4" />
                    )}
                    保存接口
                  </Button>
                </div>
              </section>
            )}
            {notice && (
              <output className="rounded-lg bg-slate-100 p-3 text-sm text-slate-700">
                {notice}
              </output>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
