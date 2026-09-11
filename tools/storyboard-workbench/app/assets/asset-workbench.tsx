'use client';

/* oxlint-disable react/react-compiler -- Persisted jobs and local edit buffers have independent lifecycles. */
/* oxlint-disable nextjs/no-img-element -- Local image URLs are served by the desktop API. */
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  FileText,
  ImageIcon,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  Save,
  Settings2,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { API, request, ModelSettings, type Settings } from './model-settings';

type Hashes = { manifest: string; prompts: string; script: string };
type Asset = {
  key: string;
  id: string;
  name: string;
  filename: string;
  number: string;
  kind: string;
  prompt: string;
  promptEditable: boolean;
  exists: boolean;
  outdated: boolean;
  required: boolean;
  declaredStatus: string;
  imageUrl: string;
  size: string;
  source: string;
  refs: string[];
  dependencyIssue: string;
  sourceFile: string;
};
type RunItem = {
  key: string;
  id: string;
  name: string;
  state: string;
  message?: string;
  recoverable?: boolean;
  resolved?: { note: string };
  result?: { filename: string; width: number; height: number };
};
type Run = {
  id: string;
  kind: string;
  status: string;
  phase: string;
  message: string;
  createdAt: string;
  recordPath: string;
  review?: string;
  models: { label: string; model: string }[];
  items: RunItem[];
};
type State = {
  episode: string;
  hashes: Hashes;
  assetsReady: boolean;
  hasScript: boolean;
  hasPlan: boolean;
  characters: string;
  scenes: string;
  notes: string;
  assets: Asset[];
  jobs: Run[];
};
const kindLabel: Record<string, string> = {
  character: '人物写实',
  pencil: '彩铅派生',
  scene_panorama: '全景底图',
  scene_grid: '场景四宫格',
  scene: '场景',
  prop: '剧情道具',
  product_photo: '产品实拍',
  cover: '封面',
};
const stateLabel: Record<string, string> = {
  queued: '等待执行',
  submitting: '请求中',
  received: '已返回图片',
  verifying: '技术核验中',
  completed: '完成',
  blocked: '缺少材料',
  failed: '执行失败',
  unknown_outcome: '结果未知',
  download_failed: '待恢复下载',
  conflict: '文件有更新',
  technical_failed: '技术核验未通过',
  interrupted: '已中断',
};
const field =
  'h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700';

export function AssetWorkbench() {
  const [episodes, setEpisodes] = useState<string[]>([]);
  const [episode, setEpisode] = useState('');
  const [data, setData] = useState<State | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [active, setActive] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState('all');
  const [drafts, setDrafts] = useState<
    Record<string, { text: string; hashes: Hashes }>
  >({});
  const [draftsLoaded, setDraftsLoaded] = useState(false);
  const [notes, setNotes] = useState('');
  const [planOpen, setPlanOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [resolveNotes, setResolveNotes] = useState<Record<string, string>>({});
  const upload = useRef<HTMLInputElement>(null);
  const epoch = useRef(0);
  const current = data?.assets.find((item) => item.key === active);
  const draftKey = `${episode}/${active}`;
  const text = drafts[draftKey]?.text ?? current?.prompt ?? '';
  const dirty = Object.keys(drafts).some((key) =>
    key.startsWith(`${episode}/`),
  );
  const anyDraft = Object.keys(drafts).length > 0;
  const running = data?.jobs.some((job) => job.status === 'running') || false;
  const disabled = busy || running;

  useEffect(() => {
    try {
      const saved = window.sessionStorage.getItem('workbench-asset-drafts');
      if (saved) setDrafts(JSON.parse(saved));
    } catch {
      /* Keep malformed browser data separate from canonical files. */
    }
    setDraftsLoaded(true);
  }, []);
  useEffect(() => {
    if (!draftsLoaded) return;
    try {
      window.sessionStorage.setItem(
        'workbench-asset-drafts',
        JSON.stringify(drafts),
      );
    } catch {
      setNotice('浏览器暂时无法保留临时编辑，请先保存提示词再离开页面。');
    }
  }, [drafts, draftsLoaded]);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      request<{ episodes: string[] }>('/api/workbench/catalog'),
      request<Settings>('/api/workbench/settings'),
    ])
      .then(([catalog, config]) => {
        if (cancelled) return;
        setEpisodes(catalog.episodes);
        setSettings(config);
        const desired =
          new URLSearchParams(window.location.search).get('episode') ||
          window.localStorage.getItem('workbench-assets-episode');
        setEpisode(
          catalog.episodes.includes(desired || '')
            ? desired!
            : catalog.episodes[0] || '',
        );
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = useCallback(async (name: string, serial: number) => {
    const next = await request<State>(
      `/api/workbench/assets?episode=${encodeURIComponent(name)}`,
    );
    if (epoch.current !== serial) return;
    setData(next);
    setError('');
    setActive((key) =>
      next.assets.some((item) => item.key === key)
        ? key
        : next.assets[0]?.key || '',
    );
    setSelected((keys) =>
      keys.filter((key) => next.assets.some((item) => item.key === key)),
    );
  }, []);
  useEffect(() => {
    if (!episode) return;
    epoch.current += 1;
    const serial = epoch.current;
    setData(null);
    setSelected([]);
    setActive('');
    setNotice('');
    setPlanOpen(false);
    setNotes('');
    window.localStorage.setItem('workbench-assets-episode', episode);
    window.history.replaceState(
      {},
      '',
      `/assets?episode=${encodeURIComponent(episode)}`,
    );
    void refresh(episode, serial).catch((reason) => {
      if (serial === epoch.current) setError(reason.message);
    });
  }, [episode, refresh]);
  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const serial = epoch.current;
    const poll = async () => {
      try {
        await refresh(episode, serial);
      } catch (reason) {
        if (!cancelled)
          setNotice(
            reason instanceof Error
              ? reason.message
              : '查询中断，执行记录仍保留',
          );
      }
      if (!cancelled) timer = setTimeout(poll, 2500);
    };
    timer = setTimeout(poll, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [episode, running, refresh]);
  useEffect(() => {
    if (!anyDraft) return;
    const prevent = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', prevent);
    return () => window.removeEventListener('beforeunload', prevent);
  }, [anyDraft]);

  const act = async (route: string, extra: Record<string, unknown> = {}) => {
    if (!data) return;
    const serial = epoch.current;
    setBusy(true);
    setNotice('');
    try {
      await request(route, { episode, hashes: data.hashes, ...extra });
      await refresh(episode, serial);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '操作未完成');
      throw reason;
    } finally {
      setBusy(false);
    }
  };
  const perform = (route: string, extra?: Record<string, unknown>) => {
    void act(route, extra).catch(() => {});
  };
  const save = async () => {
    const draft = drafts[draftKey];
    if (!draft || !current) return;
    try {
      await act('/api/workbench/assets/prompt', {
        key: active,
        prompt: draft.text,
        hashes: draft.hashes,
      });
      setDrafts((all) => {
        const next = { ...all };
        delete next[draftKey];
        return next;
      });
      setNotice('提示词已保存，旧图保留。');
    } catch {
      /* Keep the edit buffer. */
    }
  };
  const generate = (keys: string[]) =>
    perform('/api/workbench/assets/generate', { keys });
  const missing =
    data?.assets
      .filter((item) => item.required && (!item.exists || item.outdated))
      .map((item) => item.key) || [];
  const visible =
    data?.assets.filter(
      (item) =>
        filter === 'all' ||
        (filter === 'missing' && item.required && (!item.exists || item.outdated)) ||
        (filter === 'characters' &&
          ['character', 'pencil'].includes(item.kind)) ||
        (filter === 'scenes' && item.kind.startsWith('scene')) ||
        (filter === 'objects' && ['prop', 'product_photo'].includes(item.kind)),
    ) || [];
  const planMapping =
    settings?.overrides['assets.plan'] || settings?.defaults.text;
  const imageMapping =
    settings?.overrides['assets.image'] || settings?.defaults.image;
  const modelName = (mapping: typeof planMapping) => {
    const provider = settings?.providers.find(
      (item) => item.id === mapping?.providerId,
    );
    return provider
      ? `${provider.label} · ${mapping?.model || provider.model || '默认模型'}`
      : '待配置';
  };
  const discussion = async () => {
    if (!current || !data) return;
    setBusy(true);
    try {
      const result = await request<{ session: { id: string } }>(
        '/api/studio/sessions',
        {
          skillId: 'zichan',
          routeId: 'discuss',
          episode,
          providerId: planMapping?.providerId || 'codex-cli',
          title: `${current.id} 资产讨论`,
        },
      );
      const message = `讨论 ${episode} 的 ${current.id}（${current.name}）。当前文件：资产图/${current.filename}。\n当前提示词：\n${text}\n\n我的调整想法：`;
      window.sessionStorage.setItem(
        `asset-discussion-${result.session.id}`,
        message,
      );
      window.location.href = `/pipeline?sessionId=${result.session.id}`;
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '对话没有打开');
    } finally {
      setBusy(false);
    }
  };
  const importFile = async (file?: File) => {
    if (!file || !current) return;
    if (file.size > 20_000_000) {
      setNotice('请选择小于 20 MB 的实拍照片。');
      return;
    }
    try {
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () =>
          typeof reader.result === 'string'
            ? resolve(reader.result.split(',')[1])
            : reject(new Error('无法读取照片'));
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      await act('/api/workbench/assets/import', { key: active, data: encoded });
    } catch {
      /* Notice comes from act. */
    }
    if (upload.current) upload.current.value = '';
  };

  return (
    <main className="min-h-screen bg-[#f5f6f8] text-slate-900">
      <header className="flex min-h-16 flex-wrap items-center gap-4 bg-[#172033] px-5 py-3 text-white lg:px-8">
        <Link
          href="/assets"
          className="flex items-center gap-2 text-base font-semibold"
        >
          <span className="grid size-8 place-items-center rounded-lg bg-orange-500">
            <ImageIcon className="size-5" />
          </span>
          短剧工作台
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          <span className="rounded-md bg-white/15 px-3 py-2">资产</span>
          <Link
            className="rounded-md px-3 py-2 text-slate-300 hover:bg-white/10"
            href={episode ? `/?episode=${encodeURIComponent(episode)}` : '/'}
          >
            分镜与视频
          </Link>
          <Link
            className="rounded-md px-3 py-2 text-slate-300 hover:bg-white/10"
            href="/pipeline"
          >
            Agent 对话
          </Link>
        </nav>
        <select
          className={`${field} ml-auto max-w-[300px]`}
          aria-label="选择故事"
          value={episode}
          disabled={busy}
          onChange={(event) => setEpisode(event.target.value)}
        >
          {episodes.map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
        <button
          type="button"
          aria-label="模型设置"
          className="rounded-lg p-2 text-slate-300 hover:bg-white/10"
          onClick={() => setSettingsOpen(true)}
        >
          <Settings2 className="size-5" />
        </button>
      </header>
      <div className="mx-auto max-w-[1600px] px-4 py-6 lg:px-8">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight">资产</h1>
              {data && (
                <span
                  className={`rounded-full px-2.5 py-1 text-xs ${data.assetsReady ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-200 text-slate-600'}`}
                >
                  {data.assetsReady
                    ? '可进入分镜'
                    : data.hasPlan
                      ? '待补齐'
                      : '等待方案'}
                </span>
              )}
            </div>
            <p className="mt-2 text-sm text-slate-500">
              整理提示词，生成参考图。完成这一段后，再进入分镜。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              disabled={disabled || !data?.hasScript || dirty}
              onClick={() => setPlanOpen(!planOpen)}
            >
              <FileText className="size-4" />
              {data?.hasPlan ? '调整资产方案' : '生成资产方案'}
              <ChevronDown className="size-3" />
            </Button>
            <Button
              className="bg-orange-600 text-white hover:bg-orange-700"
              disabled={disabled || !missing.length || dirty}
              onClick={() => generate(missing)}
            >
              {running ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Sparkles className="size-4" />
              )}
              生成缺失与待重做 · {missing.length}
            </Button>
          </div>
        </div>
        <div className="mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-500">
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="hover:text-orange-700"
          >
            方案：{modelName(planMapping)}
          </button>
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="hover:text-orange-700"
          >
            图片：{modelName(imageMapping)}
          </button>
          <span>四宫格 · 本地派生</span>
          <button
            type="button"
            className="ml-auto inline-flex items-center gap-1 hover:text-slate-900"
            disabled={busy}
            onClick={() => {
              void refresh(episode, epoch.current).catch((reason) =>
                setNotice(reason.message),
              );
            }}
          >
            <RefreshCw className="size-3" />
            刷新
          </button>
        </div>
        {notice && (
          <output className="mb-4 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span className="flex-1">{notice}</span>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => setNotice('')}
            >
              <X className="size-4" />
            </button>
          </output>
        )}
        {error && (
          <div
            role="alert"
            className="mb-4 rounded-xl border border-red-200 bg-white p-5 text-sm text-red-700"
          >
            {error}
            <Button
              variant="outline"
              className="ml-4"
              onClick={() => window.location.reload()}
            >
              重新连接
            </Button>
          </div>
        )}
        {planOpen && (
          <section className="mb-5 rounded-2xl border border-slate-200 bg-white p-5">
            <h2 className="mb-2 text-sm font-semibold">
              {data?.hasPlan ? '本次要调整什么' : '本集的资产要求'}
            </h2>
            <Textarea
              aria-label="资产方案要求"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder={
                data?.hasPlan
                  ? '例如：只调整男主的服装，其他资产沿用。'
                  : '可留空，按二创稿和本集已有约定生成。'
              }
              className="min-h-20"
            />
            <div className="mt-3 flex items-center justify-between gap-3">
              <p className="text-xs text-slate-500">
                读取本集资料 → 生成方案 → 同一模型独立自查 → 保存
              </p>
              <Button
                disabled={
                  disabled || dirty || Boolean(data?.hasPlan && !notes.trim())
                }
                onClick={() => perform('/api/workbench/assets/plan', { notes })}
              >
                开始生成方案
              </Button>
            </div>
          </section>
        )}
        {!data && !error && (
          <div className="flex h-64 items-center justify-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="size-5 animate-spin" />
            正在读取故事资产…
          </div>
        )}
        {data && (
          <>
            {!data.hasScript && (
              <p className="mb-4 rounded-xl bg-white p-4 text-sm text-slate-600">
                本集还没有二创稿。完成成稿后，就可以在这里生成资产方案。
              </p>
            )}
            {!data.assets.length ? (
              <section className="rounded-2xl border border-dashed border-slate-300 bg-white px-8 py-16 text-center">
                <ImageIcon className="mx-auto size-10 text-slate-300" />
                <h2 className="mt-4 font-semibold">从本集剧本整理资产</h2>
                <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500">
                  生成方案后，人物、场景、道具和实拍产品会出现在这里。每项都可以单独编辑和执行。
                </p>
                <Button
                  className="mt-5"
                  disabled={!data.hasScript || disabled}
                  onClick={() => setPlanOpen(true)}
                >
                  准备资产方案
                </Button>
                {data.hasPlan && (
                  <p className="mt-3 text-xs text-amber-700">
                    已有清单暂未解析出条目，请检查“编号分配表”的固定标题与列名。
                  </p>
                )}
              </section>
            ) : (
              <div className="grid gap-5 lg:grid-cols-[310px_minmax(0,1fr)]">
                <aside className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
                  <div className="border-b border-slate-100 p-4">
                    <div className="mb-3 flex items-center justify-between">
                      <h2 className="text-sm font-semibold">
                        本集资产{' '}
                        <span className="ml-1 font-normal text-slate-400">
                          {data.assets.length}
                        </span>
                      </h2>
                      <select
                        className="max-w-[130px] rounded-md border border-slate-200 px-2 py-1 text-xs"
                        aria-label="筛选资产"
                        value={filter}
                        onChange={(event) => setFilter(event.target.value)}
                      >
                        {[
                          ['all', '全部'],
                          ['missing', '缺失与待重做'],
                          ['characters', '人物'],
                          ['scenes', '场景'],
                          ['objects', '道具与产品'],
                        ].map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <label className="flex items-center gap-2 text-xs text-slate-500">
                        <input
                          type="checkbox"
                          checked={
                            visible.filter((item) => item.required).length >
                              0 &&
                            visible
                              .filter((item) => item.required)
                              .every((item) => selected.includes(item.key))
                          }
                          disabled={disabled}
                          onChange={(event) =>
                            setSelected(
                              event.target.checked
                                ? [
                                    ...new Set([
                                      ...selected,
                                      ...visible
                                        .filter((item) => item.required)
                                        .map((item) => item.key),
                                    ]),
                                  ]
                                : selected.filter(
                                    (key) =>
                                      !visible.some((item) => item.key === key),
                                  ),
                            )
                          }
                        />
                        选择当前列表
                      </label>
                      <span className="text-xs text-slate-400">
                        已选 {selected.length}
                      </span>
                    </div>
                  </div>
                  <div className="max-h-[620px] overflow-y-auto p-2">
                    {visible.map((item) => (
                      <div
                        key={item.key}
                        className={`mb-1 flex items-center gap-2 rounded-xl px-2 ${active === item.key ? 'bg-orange-50 ring-1 ring-inset ring-orange-200' : 'hover:bg-slate-50'}`}
                      >
                        <input
                          aria-label={`选择 ${item.id}`}
                          type="checkbox"
                          disabled={disabled || !item.required}
                          checked={selected.includes(item.key)}
                          onChange={(event) =>
                            setSelected(
                              event.target.checked
                                ? [...selected, item.key]
                                : selected.filter((key) => key !== item.key),
                            )
                          }
                        />
                        <button
                          type="button"
                          className="flex min-w-0 flex-1 items-center gap-3 py-3 text-left"
                          onClick={() => setActive(item.key)}
                        >
                          <span
                            className={`grid size-9 shrink-0 place-items-center rounded-lg text-xs font-semibold ${item.exists && !item.outdated ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}
                          >
                            {item.id}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium">
                              {item.name}
                            </span>
                            <span className="mt-1 block text-[11px] text-slate-500">
                              {kindLabel[item.kind]} ·{' '}
                              {item.declaredStatus === '彩铅暂时停用'
                                ? '暂时停用'
                                : item.outdated
                                ? '待重做'
                                : item.exists
                                  ? '已有图片'
                                  : '缺图'}
                            </span>
                          </span>
                          {item.exists && !item.outdated && (
                            <Check className="size-3.5 text-emerald-600" />
                          )}
                        </button>
                      </div>
                    ))}
                  </div>
                  <div className="border-t border-slate-100 p-3">
                    <Button
                      className="w-full"
                      variant="outline"
                      disabled={disabled || !selected.length || dirty}
                      onClick={() => generate(selected)}
                    >
                      <RefreshCw className="size-4" />
                      生成所选 · {selected.length}
                    </Button>
                    <p className="mt-2 text-center text-[11px] text-slate-400">
                      只执行所选范围，结果保存为新版本
                    </p>
                  </div>
                </aside>
                {current && (
                  <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
                    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
                      <div>
                        <h2 className="font-semibold">
                          {current.id} · {current.name}
                        </h2>
                        <p className="mt-1 text-xs text-slate-500">
                          {kindLabel[current.kind]}
                          {current.kind !== 'product_photo'
                            ? ` · 请求 ${current.size}`
                            : ''}
                        </p>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={disabled || dirty}
                        onClick={() => void discussion()}
                      >
                        <MessageSquareText className="size-4" />与 Agent 讨论
                      </Button>
                    </div>
                    <div className="grid gap-5 p-5 xl:grid-cols-[minmax(200px,0.75fr)_minmax(280px,1fr)]">
                      <div>
                        <div className="flex min-h-[280px] items-center justify-center overflow-hidden rounded-xl border border-slate-100 bg-[#f3f4f6]">
                          {current.imageUrl ? (
                            <a
                              href={API + current.imageUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="block w-full"
                            >
                              <img
                                src={API + current.imageUrl}
                                alt={current.name}
                                className="mx-auto max-h-[480px] w-full object-contain"
                              />
                            </a>
                          ) : (
                            <div className="px-4 py-16 text-center text-slate-400">
                              <ImageIcon className="mx-auto mb-3 size-8" />
                              <p className="text-sm">
                                {current.kind === 'product_photo'
                                  ? '等待产品实拍照片'
                                  : '图片生成后显示在这里'}
                              </p>
                            </div>
                          )}
                        </div>
                        <p className="mt-3 break-all text-[11px] leading-5 text-slate-400">
                          {current.filename}
                        </p>
                        {current.outdated && (
                          <p className="mt-2 text-xs text-amber-700">
                            显示的是旧版图片，新的提示词或参考图尚未执行。
                          </p>
                        )}
                      </div>
                      <div className="flex min-w-0 flex-col">
                        <div className="mb-2 flex items-center justify-between">
                          <h3 className="text-sm font-medium">
                            {current.kind === 'product_photo'
                              ? '实拍来源'
                              : '生成提示词'}
                          </h3>
                          {drafts[draftKey] && (
                            <span className="text-xs text-orange-600">
                              有未保存修改
                            </span>
                          )}
                        </div>
                        {current.promptEditable ? (
                          <Textarea
                            aria-label={`${current.id} 提示词`}
                            className="min-h-[320px] flex-1 resize-y bg-slate-50/50 text-sm leading-7"
                            value={text}
                            disabled={disabled}
                            onChange={(event) =>
                              setDrafts((all) => ({
                                ...all,
                                [draftKey]: {
                                  text: event.target.value,
                                  hashes: all[draftKey]?.hashes || data.hashes,
                                },
                              }))
                            }
                          />
                        ) : (
                          <div className="min-h-32 rounded-xl bg-slate-50 p-4 text-sm leading-7 text-slate-600">
                            {current.kind === 'product_photo' ? (
                              <>
                                <p>
                                  {current.sourceFile ||
                                    '从本地导入该产品的真实照片。'}
                                </p>
                                <p className="mt-2 text-xs text-slate-400">
                                  可上传 PNG、JPEG、WebP，单张不超过 20 MB。
                                </p>
                              </>
                            ) : current.kind === 'scene_grid' ? (
                              '由对应全景底图按现有参数派生四个方向视角。'
                            ) : current.kind === 'pencil' ? (
                              current.declaredStatus === '彩铅暂时停用'
                                ? '彩铅已暂时停用，后续人物参考使用写实图。'
                                : '不改变图片，把图片风格转换成素描彩铅画'
                            ) : (
                              current.prompt || '该条目由原有流程管理。'
                            )}
                          </div>
                        )}
                        {current.refs.length > 0 && (
                          <div className="mt-3 text-xs leading-5 text-slate-500">
                            参考图：
                            {current.refs
                              .map(
                                (key) =>
                                  data.assets.find((item) => item.key === key)
                                    ?.name,
                              )
                              .join('、')}
                          </div>
                        )}
                        {current.dependencyIssue && (
                          <p className="mt-3 text-xs leading-5 text-amber-700">
                            {current.dependencyIssue}
                          </p>
                        )}
                        <div className="mt-4 flex flex-wrap justify-end gap-2">
                          {drafts[draftKey] && (
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={disabled}
                              onClick={() =>
                                setDrafts((all) => {
                                  const next = { ...all };
                                  delete next[draftKey];
                                  return next;
                                })
                              }
                            >
                              使用文件中的文本
                            </Button>
                          )}
                          {current.promptEditable && (
                            <Button
                              size="sm"
                              disabled={disabled || !drafts[draftKey]}
                              onClick={() => void save()}
                            >
                              <Save className="size-4" />
                              保存提示词
                            </Button>
                          )}
                          {current.kind === 'product_photo' && (
                            <Button
                              size="sm"
                              disabled={disabled || dirty}
                              onClick={() => upload.current?.click()}
                            >
                              <Upload className="size-4" />
                              导入实拍图
                            </Button>
                          )}
                          {current.required &&
                            current.kind !== 'product_photo' && (
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={disabled || dirty}
                                onClick={() => generate([current.key])}
                              >
                                {current.exists ? '重做此图' : '生成此图'}
                              </Button>
                            )}
                        </div>
                      </div>
                    </div>
                  </section>
                )}
              </div>
            )}
            <section className="mt-5 rounded-2xl border border-slate-200 bg-white p-5">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold">执行记录</h2>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={disabled || dirty || !data.hasPlan}
                  onClick={() => perform('/api/workbench/assets/check')}
                >
                  核对已有文件
                </Button>
                {data.assetsReady && (
                  <Link
                    href={`/?episode=${encodeURIComponent(episode)}`}
                    className="inline-flex items-center gap-1 text-sm text-emerald-700"
                  >
                    进入分镜
                    <ArrowRight className="size-4" />
                  </Link>
                )}
              </div>
              {!data.jobs.length ? (
                <p className="py-2 text-sm text-slate-400">
                  本页还没有执行记录。已有资产直接从本集文件读取。
                </p>
              ) : (
                <div className="space-y-3">
                  {data.jobs.slice(0, 8).map((job, index) => (
                    <details
                      key={job.id}
                      open={index === 0}
                      className="rounded-xl border border-slate-100 bg-slate-50/60 p-3"
                    >
                      <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-sm">
                        {job.status === 'running' ? (
                          <LoaderCircle className="size-4 animate-spin text-orange-500" />
                        ) : job.status === 'completed' ? (
                          <CheckCircle2 className="size-4 text-emerald-600" />
                        ) : (
                          <span className="size-2 rounded-full bg-amber-500" />
                        )}
                        <span className="font-medium">{job.phase}</span>
                        <span className="ml-auto text-xs text-slate-400">
                          {new Date(job.createdAt).toLocaleString('zh-CN')}
                        </span>
                      </summary>
                      <p className="mt-3 text-sm text-slate-600">
                        {job.message}
                      </p>
                      <p className="mt-1 text-xs text-slate-400">
                        {job.models
                          .map((model) => `${model.label} · ${model.model}`)
                          .join(' / ')}
                      </p>
                      <div className="mt-3 space-y-2">
                        {job.items.map((item) => (
                          <div
                            key={item.key}
                            className="rounded-lg bg-white px-3 py-2"
                          >
                            <div className="flex flex-wrap items-center gap-2 text-xs">
                              <span className="font-medium">
                                {item.id} · {item.name}
                              </span>
                              <span
                                className={`ml-auto ${item.state === 'completed' ? 'text-emerald-600' : 'text-slate-500'}`}
                              >
                                {stateLabel[item.state] || item.state}
                              </span>
                              {item.result && (
                                <span className="text-slate-400">
                                  {item.result.width} × {item.result.height}
                                </span>
                              )}
                            </div>
                            {item.message && item.state !== 'completed' && (
                              <p className="mt-2 text-xs leading-5 text-amber-700">
                                {item.message}
                              </p>
                            )}
                            {item.recoverable && (
                              <Button
                                className="mt-2"
                                size="sm"
                                variant="outline"
                                disabled={disabled || dirty}
                                onClick={() =>
                                  perform('/api/workbench/assets/recover', {
                                    jobId: job.id,
                                    key: item.key,
                                    action: 'recover',
                                  })
                                }
                              >
                                恢复已返回图片
                              </Button>
                            )}
                            {!item.resolved &&
                              [
                                'unknown_outcome',
                                'download_failed',
                                'conflict',
                                'technical_failed',
                                'received',
                                'verifying',
                              ].includes(item.state) && (
                                <details className="mt-2">
                                  <summary className="cursor-pointer text-xs text-slate-500">
                                    已核实原平台结果，需要重新生成
                                  </summary>
                                  <Textarea
                                    className="mt-2 min-h-16 text-xs"
                                    aria-label={`${item.id} 结果核实说明`}
                                    placeholder="填写原平台的任务状态、是否已扣费及本次处理决定。"
                                    value={
                                      resolveNotes[job.id + item.key] || ''
                                    }
                                    onChange={(event) =>
                                      setResolveNotes({
                                        ...resolveNotes,
                                        [job.id + item.key]: event.target.value,
                                      })
                                    }
                                  />
                                  <Button
                                    className="mt-2"
                                    size="sm"
                                    variant="outline"
                                    disabled={disabled}
                                    onClick={() =>
                                      perform('/api/workbench/assets/recover', {
                                        jobId: job.id,
                                        key: item.key,
                                        action: 'resolve',
                                        note: resolveNotes[job.id + item.key],
                                      })
                                    }
                                  >
                                    记录核实结果
                                  </Button>
                                </details>
                              )}
                            {item.resolved && (
                              <p className="mt-2 text-xs text-slate-500">
                                已核实：{item.resolved.note}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                      {job.review && (
                        <details className="mt-3 text-xs text-slate-600">
                          <summary className="cursor-pointer">
                            查看方案自查
                          </summary>
                          <p className="mt-2 whitespace-pre-wrap leading-6">
                            {job.review}
                          </p>
                        </details>
                      )}
                      <p className="mt-3 break-all text-[11px] text-slate-400">
                        记录保存在本集：{job.recordPath}
                      </p>
                    </details>
                  ))}
                </div>
              )}
            </section>
            <details className="mt-4 px-1 text-xs text-slate-500">
              <summary className="cursor-pointer">本集上游材料与约定</summary>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <pre className="overflow-auto whitespace-pre-wrap rounded-xl bg-white p-4 leading-6">
                  {data.characters || '角色表尚未提供'}
                </pre>
                <pre className="overflow-auto whitespace-pre-wrap rounded-xl bg-white p-4 leading-6">
                  {data.scenes || '场景表尚未提供'}
                </pre>
              </div>
              {data.notes && (
                <p className="mt-3 whitespace-pre-wrap leading-6">
                  {data.notes}
                </p>
              )}
            </details>
          </>
        )}
      </div>
      <input
        ref={upload}
        type="file"
        className="hidden"
        accept="image/png,image/jpeg,image/webp"
        aria-label="上传实拍图"
        onChange={(event) => void importFile(event.target.files?.[0])}
      />
      <ModelSettings
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        settings={settings}
        onSaved={setSettings}
      />
    </main>
  );
}
