'use client';

/* oxlint-disable react/react-compiler -- Oxlint's experimental React compiler crashes on this large stateful workbench component. */

import {
  AlertTriangle,
  Bot,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDot,
  Clock3,
  Dices,
  Film,
  FolderOpen,
  LoaderCircle,
  Play,
  RefreshCw,
  Save,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Workflow,
  XCircle,
} from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  NativeSelect,
  NativeSelectOption,
} from '@/components/ui/native-select';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import { ReferenceGallery, ReferenceViewer, StoryboardPrompt, type ReferenceImage } from './storyboard-references';
import { DEFAULT_VIDEO_PROVIDER } from '../server/video-catalog.mjs';

type VideoItem = {
  filename: string;
  url: string;
  modifiedAt?: number;
};

type UnitStatus = '已完成' | '待生成' | '生成中';

type UnitItem = {
  number: number;
  heading: string;
  prompt: string;
  promptHash: string;
  refs: string[];
  referenceImages?: ReferenceImage[];
  referenceError?: string;
  status: UnitStatus;
  videos: VideoItem[];
};

type EpisodeDetail = {
  name: string;
  hasStoryboard: boolean;
  ready: boolean;
  unitCount: number;
  videoCount: number;
  units: UnitItem[];
};

type VideoModel = {
  id: string;
  name: string;
  description: string;
  resolution: string;
  type: string;
  unitCost: number | null;
  producible: number | null;
};

type VideoProvider = {
  id: string;
  label: string;
  tier: string;
  selectable: boolean;
  available: boolean;
  error: string;
  disabledReason: string;
  note: string;
  defaultResolution: string;
  referenceMode: string;
  submissionWarning: string;
  models: VideoModel[];
};

type PreflightCost = {
  amount: number | null;
  unit: string;
  exact: boolean;
  label: string;
  note: string;
};

type PreflightResult = {
  token: string;
  episode: string;
  units: number[];
  provider: string;
  providerLabel: string;
  model: string;
  cost: PreflightCost;
  confirmationKey: string;
  resolution: string;
  referenceMode: string;
  submissionWarning: string;
  expiresAt: string;
  output: string;
};

type JobResult = {
  id: string;
  kind: 'submit';
  episode: string;
  units: number[];
  provider?: string | null;
  model?: string | null;
  status: 'running' | 'succeeded' | 'failed' | 'needs_attention';
  output: string;
  ledger?: string | null;
  outputSuffix?: string | null;
};

type OperationStage =
  | 'idle'
  | 'saving'
  | 'preflight'
  | 'ready'
  | 'submitting'
  | 'submitted'
  | 'error';

type Notice = {
  tone: 'success' | 'warning' | 'error' | 'info';
  text: string;
};

type RepairRule = {
  path: string;
  label: string;
  kind: string;
  readMode: string;
  reason: string;
};

type RepairReferenceImage = {
  reference: string;
  filename: string | null;
  status: 'attached' | 'missing';
};

type RepairPlan = {
  requiredRules: RepairRule[];
  referenceImages: RepairReferenceImage[];
  existingVideos: string[];
  videoBasis: string;
};

type RepairResult = {
  repairId: string;
  reply: string;
  diagnosis: string[];
  revisedPrompt: string;
  changes: string[];
  warnings: string[];
  requiredRules: RepairRule[];
  rulesRead: string[];
  rulesApplied: string[];
  repairLevel: string;
  referenceImages: RepairReferenceImage[];
  videoBasis: string;
  auditPath: string;
};

type RepairMessage = {
  role: 'user' | 'assistant';
  content: string;
};

type WebMcpContext = {
  registerTool: (
    tool: {
      name: string;
      title: string;
      description: string;
      inputSchema: Record<string, unknown>;
      annotations?: Record<string, boolean>;
      execute: (input: unknown) => unknown;
    },
    options?: { signal?: AbortSignal },
  ) => void | Promise<void>;
};

const API_BASE = 'http://127.0.0.1:4317';
const REPAIR_COMPAT_BASE = 'http://127.0.0.1:14317';

function modelPrice(providerId: string, unitCost: number | null | undefined) {
  if (typeof unitCost !== 'number' || !Number.isFinite(unitCost) || unitCost < 0) {
    return '价格未提供';
  }
  return `${unitCost} ${providerId === 'guagu_video' ? '积分' : '计费单位'}/条`;
}

type WorkbenchHealth = {
  ok?: boolean;
  projectRoot?: string;
  repairContextMode?: string;
};

async function repairApiBase() {
  const health = await fetch(`${API_BASE}/api/health`, {
    cache: 'no-store', signal: AbortSignal.timeout(5000),
  }).then((response) => response.json() as Promise<WorkbenchHealth>);
  if (health.repairContextMode === 'source-snapshots-v1') return API_BASE;
  // Existing video jobs keep their original backend until they finish.
  try {
    const compatibility = await fetch(`${REPAIR_COMPAT_BASE}/api/health`, {
      cache: 'no-store', signal: AbortSignal.timeout(5000),
    }).then((response) => response.json() as Promise<WorkbenchHealth>);
    if (compatibility.ok && compatibility.projectRoot === health.projectRoot
      && compatibility.repairContextMode === 'source-snapshots-v1') {
      return REPAIR_COMPAT_BASE;
    }
  } catch {
    // Do not send the repair to a backend known to lack the fix.
  }
  throw new Error('修复后台尚未启动，请重新运行“启动工作台”脚本。原提示词未改变。');
}

const emptyEpisode: EpisodeDetail = {
  name: '',
  hasStoryboard: false,
  ready: false,
  unitCount: 0,
  videoCount: 0,
  units: [],
};

class ApiError extends Error {
  details?: string;

  constructor(message: string, details?: string) {
    super(message);
    this.details = details;
  }
}

async function api<T>(pathname: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const base = pathname === '/api/repair' || pathname === '/api/repair-plan'
    ? await repairApiBase() : API_BASE;
  const response = await fetch(`${base}${pathname}`, {
    cache: 'no-store',
    ...options,
    headers,
  });
  const body = (await response.json().catch(() => ({}))) as {
    error?: string;
    output?: string;
  } & T;
  if (!response.ok) {
    throw new ApiError(body.error ?? '工作台操作失败', body.output);
  }
  return body;
}

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function unitStatusStyle(status: UnitStatus) {
  if (status === '已完成') {
    return 'border-success-border bg-success-soft text-success';
  }
  if (status === '生成中') {
    return 'border-info-border bg-info-soft text-info';
  }
  return 'border-warning-border bg-warning-soft text-warning';
}

function noticeStyle(tone: Notice['tone']) {
  if (tone === 'success') return 'border-success-border bg-success-soft text-success';
  if (tone === 'error') return 'border-danger-border bg-danger-soft text-danger';
  if (tone === 'warning') return 'border-warning-border bg-warning-soft text-warning';
  return 'border-info-border bg-info-soft text-info';
}

export const Workbench = () => {
  const [episode, setEpisode] = useState<EpisodeDetail>(emptyEpisode);
  const [episodeNames, setEpisodeNames] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [loadingEpisode, setLoadingEpisode] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [expanded, setExpanded] = useState<number[]>([1]);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'全部' | UnitStatus>('全部');
  const [activeVideo, setActiveVideo] = useState<number | null>(null);
  const [selectedVideos, setSelectedVideos] = useState<Record<number, string>>({});
  const [referenceSelection, setReferenceSelection] = useState<{ unit: number; index: number } | null>(null);
  const [savingUnits, setSavingUnits] = useState<number[]>([]);
  const [generatingUnits, setGeneratingUnits] = useState<number[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [operationOpen, setOperationOpen] = useState(false);
  const [operationStage, setOperationStage] = useState<OperationStage>('idle');
  const [operationUnits, setOperationUnits] = useState<number[]>([]);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [currentJob, setCurrentJob] = useState<JobResult | null>(null);
  const [operationError, setOperationError] = useState('');
  const [operationDetails, setOperationDetails] = useState('');
  const [submissionConfirmed, setSubmissionConfirmed] = useState(false);
  const [providers, setProviders] = useState<VideoProvider[]>([]);
  const [providersLoading, setProvidersLoading] = useState(false);
  const providersRequestInFlight = useRef(false);
  const [selectedProvider, setSelectedProvider] = useState(DEFAULT_VIDEO_PROVIDER);
  const [selectedModel, setSelectedModel] = useState('');
  const [repairOpen, setRepairOpen] = useState(false);
  const [repairUnit, setRepairUnit] = useState<number | null>(null);
  const [repairIssue, setRepairIssue] = useState('');
  const [repairMessages, setRepairMessages] = useState<RepairMessage[]>([]);
  const [repairPlan, setRepairPlan] = useState<RepairPlan | null>(null);
  const [repairResult, setRepairResult] = useState<RepairResult | null>(null);
  const [repairCandidate, setRepairCandidate] = useState('');
  const [repairLoading, setRepairLoading] = useState(false);
  const [repairPlanLoading, setRepairPlanLoading] = useState(false);
  const [repairError, setRepairError] = useState('');
  const [repairErrorDetails, setRepairErrorDetails] = useState('');
  const [draftRepairIds, setDraftRepairIds] = useState<Record<number, string>>({});

  const loadEpisode = useCallback(async (name: string) => {
    setLoadingEpisode(true);
    try {
      const detail = await api<EpisodeDetail>(`/api/episodes/${encodeURIComponent(name)}`);
      setEpisode(detail);
      setConnected(true);
      setSelected([]);
      setDrafts({});
      setDraftRepairIds({});
      setExpanded(detail.units.length ? [detail.units[0].number] : []);
      setActiveVideo(detail.units.find((unit) => unit.videos.length)?.number ?? null);
      setSelectedVideos({});
      setReferenceSelection(null);
    } catch (error) {
      setConnected(false);
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '读取剧集失败',
      });
    } finally {
      setLoadingEpisode(false);
    }
  }, []);

  const loadProviders = useCallback(async () => {
    if (providersRequestInFlight.current) return;
    providersRequestInFlight.current = true;
    setProvidersLoading(true);
    try {
      const result = await api<{ providers: VideoProvider[] }>('/api/providers', {
        signal: AbortSignal.timeout(15_000),
      });
      setProviders(result.providers);
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError')
          ? '刷新渠道等待超时，已保留原列表，可以重新刷新。'
          : error instanceof Error ? error.message : '读取视频渠道失败',
      });
    } finally {
      providersRequestInFlight.current = false;
      setProvidersLoading(false);
    }
  }, []);

  const loadEpisodeNames = useCallback(async () => {
    const result = await api<{ episodes: string[] }>('/api/episodes');
    setEpisodeNames(result.episodes);
    setConnected(true);
    return result.episodes;
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadEpisodeNames()
      .then(async (names) => {
        if (cancelled) return;
        const requestedEpisode = new URLSearchParams(window.location.search).get('episode');
        const initialEpisode =
          requestedEpisode && names.includes(requestedEpisode)
            ? requestedEpisode
            : names[0];
        if (initialEpisode) await loadEpisode(initialEpisode);
      })
      .catch(() => {
        if (!cancelled) setConnected(false);
      })
      .finally(() => {
        if (!cancelled) setLoadingCatalog(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadEpisode, loadEpisodeNames]);

  useEffect(() => {
    const refreshNames = () => void loadEpisodeNames().catch(() => undefined);
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshNames();
    };
    window.addEventListener('focus', refreshNames);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', refreshNames);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [loadEpisodeNames]);

  useEffect(() => {
    let lastAutomaticRefresh = 0;
    const refreshProviders = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastAutomaticRefresh > 30_000) {
        lastAutomaticRefresh = Date.now();
        void loadProviders();
      }
    };
    const timer = window.setTimeout(() => void loadProviders(), 0);
    window.addEventListener('focus', refreshProviders);
    document.addEventListener('visibilitychange', refreshProviders);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('focus', refreshProviders);
      document.removeEventListener('visibilitychange', refreshProviders);
    };
  }, [loadProviders]);

  useEffect(() => {
    if (!providers.length || selectedModel) return;
    const preferred = providers.find((provider) => provider.id === selectedProvider);
    if (!preferred?.selectable) return;
    const timer = window.setTimeout(() => {
      setSelectedModel(preferred.models[0]?.id ?? '');
    }, 0);
    return () => window.clearTimeout(timer);
  }, [providers, selectedModel, selectedProvider]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    const context = (document as Document & { modelContext?: WebMcpContext }).modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();

    const registrations = [
      context.registerTool(
        {
          name: 'read_storyboard_units',
          title: '读取当前分镜单元',
          description: '读取工作台当前剧集可见的分镜单元、状态和成片数量，不修改文件。',
          inputSchema: {
            type: 'object',
            properties: {
              query: { type: 'string', description: '可选的单元号或提示词关键词' },
            },
            additionalProperties: false,
          },
          annotations: { readOnlyHint: true, untrustedContentHint: true },
          execute(input) {
            const value = input as { query?: unknown };
            const keyword = typeof value?.query === 'string' ? value.query.trim().toLowerCase() : '';
            const units = episode.units.filter(
              (unit) =>
                !keyword ||
                `u${unit.number}`.includes(keyword) ||
                unit.prompt.toLowerCase().includes(keyword),
            );
            return {
              episode: episode.name,
              units: units.map((unit) => ({
                unit: unit.number,
                status: generatingUnits.includes(unit.number) ? '生成中' : unit.status,
                videos: unit.videos.length,
                edited: drafts[unit.number] !== undefined && drafts[unit.number] !== unit.prompt,
              })),
            };
          },
        },
        { signal: lifecycle.signal },
      ),
      context.registerTool(
        {
          name: 'stage_storyboard_prompt_edit',
          title: '暂存分镜提示词修改',
          description: '把一个单元的新提示词填入可见编辑框，但不保存文件、不会提交生成。',
          inputSchema: {
            type: 'object',
            properties: {
              unit: { type: 'integer', minimum: 1 },
              prompt: { type: 'string', minLength: 1 },
            },
            required: ['unit', 'prompt'],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: false },
          execute(input) {
            const value = input as { unit?: unknown; prompt?: unknown };
            const unitNumber = Number(value?.unit);
            const prompt = typeof value?.prompt === 'string' ? value.prompt.trim() : '';
            if (!episode.units.some((unit) => unit.number === unitNumber) || !prompt) {
              throw new Error('单元不存在或提示词为空');
            }
            setDrafts((current) => ({ ...current, [unitNumber]: prompt }));
            setExpanded((current) =>
              current.includes(unitNumber) ? current : [...current, unitNumber],
            );
            return { episode: episode.name, unit: unitNumber, staged: true, saved: false };
          },
        },
        { signal: lifecycle.signal },
      ),
    ];

    for (const registration of registrations) {
      void Promise.resolve(registration).catch(() => undefined);
    }
    return () => lifecycle.abort();
  }, [drafts, episode.name, episode.units, generatingUnits]);

  const visibleUnits = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return episode.units.filter((unit) => {
      const liveStatus: UnitStatus = generatingUnits.includes(unit.number) ? '生成中' : unit.status;
      const matchesStatus = statusFilter === '全部' || liveStatus === statusFilter;
      const matchesQuery =
        !normalized ||
        `u${unit.number}`.includes(normalized) ||
        unit.prompt.toLowerCase().includes(normalized) ||
        unit.refs.some((ref) => ref.toLowerCase().includes(normalized)) ||
        unit.referenceImages?.some((ref) => `${ref.name} ${ref.detail} ${ref.filename || ''}`.toLowerCase().includes(normalized));
      return matchesStatus && matchesQuery;
    });
  }, [episode.units, generatingUnits, query, statusFilter]);

  const selectedProviderDetail = useMemo(
    () => providers.find((provider) => provider.id === selectedProvider) ?? null,
    [providers, selectedProvider],
  );

  const selectedModelDetail = useMemo(
    () =>
      selectedProviderDetail?.models.find((model) => model.id === selectedModel) ?? null,
    [selectedModel, selectedProviderDetail],
  );

  const dirtyUnits = useMemo(
    () =>
      episode.units
        .filter(
          (unit) =>
            drafts[unit.number] !== undefined && drafts[unit.number] !== unit.prompt,
        )
        .map((unit) => unit.number),
    [drafts, episode.units],
  );

  const toggleSelected = (unit: number) => {
    setSelected((current) =>
      current.includes(unit)
        ? current.filter((item) => item !== unit)
        : [...current, unit],
    );
  };

  const toggleExpanded = (unit: number) => {
    setExpanded((current) =>
      current.includes(unit)
        ? current.filter((item) => item !== unit)
        : [...current, unit],
    );
  };

  const saveUnit = async (unitNumber: number, silent = false) => {
    const unit = episode.units.find((item) => item.number === unitNumber);
    const prompt = drafts[unitNumber] ?? unit?.prompt ?? '';
    if (!unit || prompt === unit.prompt) return true;
    if (prompt.includes('```')) {
      setNotice({ tone: 'error', text: `U${unitNumber} 中不能包含三个连续反引号。` });
      return false;
    }
    setSavingUnits((current) => [...current, unitNumber]);
    try {
      const result = await api<{
        promptHash: string;
        backupName?: string;
        repairLinked?: boolean;
      }>('/api/save', {
        method: 'POST',
        body: JSON.stringify({
          episode: episode.name,
          unit: unitNumber,
          prompt,
          baseHash: unit.promptHash,
          repairId: draftRepairIds[unitNumber] ?? null,
        }),
      });
      setEpisode((current) => ({
        ...current,
        units: current.units.map((item) =>
          item.number === unitNumber
            ? { ...item, prompt, promptHash: result.promptHash }
            : item,
        ),
      }));
      setDrafts((current) => {
        const next = { ...current };
        delete next[unitNumber];
        return next;
      });
      setDraftRepairIds((current) => {
        const next = { ...current };
        delete next[unitNumber];
        return next;
      });
      if (!silent) {
        setNotice({
          tone: 'success',
          text: result.repairLinked
            ? `U${unitNumber} 已保存，AI 修复规范与修复记录已关联，旧文件已备份。`
            : `U${unitNumber} 已保存为新版本，旧文件已备份。`,
        });
      }
      return true;
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : `U${unitNumber} 保存失败`,
      });
      return false;
    } finally {
      setSavingUnits((current) => current.filter((item) => item !== unitNumber));
    }
  };

  const runPreflight = async (
    units: number[],
    providerId = selectedProvider,
    modelId = selectedModel,
  ) => {
    setOperationStage('preflight');
    setOperationError('');
    setOperationDetails('');
    setPreflight(null);
    setSubmissionConfirmed(false);
    try {
      const result = await api<PreflightResult>('/api/preflight', {
        method: 'POST',
        body: JSON.stringify({
          episode: episode.name,
          units,
          provider: providerId,
          model: modelId,
        }),
      });
      setPreflight(result);
      setOperationDetails(result.output);
      setOperationStage('ready');
    } catch (error) {
      const details = error instanceof ApiError ? error.details ?? '' : '';
      const message = /fenjing_ready[^\r\n]*不是 true/.test(details)
        ? '本集分镜尚未就绪，请待修改或核验完成后点击“重新预检”。尚未提交视频。'
        : error instanceof Error ? error.message : '预检失败';
      setOperationError(message);
      setOperationDetails(details);
      setOperationStage('error');
    }
  };

  const beginSubmission = async (units: number[]) => {
    if (!connected) {
      setNotice({ tone: 'error', text: '本地项目尚未连接，不能保存或抽卡。' });
      return;
    }
    if (!selectedProviderDetail?.selectable || !selectedModelDetail) {
      setNotice({ tone: 'error', text: '请先选择可抽卡的视频渠道和完整模型。' });
      return;
    }
    const normalized = [...new Set(units)].sort((left, right) => left - right);
    if (!normalized.length) return;
    const busyUnits = normalized.filter((unit) => generatingUnits.includes(unit));
    if (busyUnits.length) {
      setNotice({
        tone: 'warning',
        text: `U${busyUnits.join('、U')} 正在抽卡，本次没有重复提交。`,
      });
      return;
    }
    setOperationUnits(normalized);
    setOperationOpen(true);
    setOperationStage('saving');
    setOperationError('');
    setOperationDetails('');
    setPreflight(null);
    setCurrentJob(null);
    setSubmissionConfirmed(false);

    for (const unitNumber of normalized) {
      if (!dirtyUnits.includes(unitNumber)) continue;
      const saved = await saveUnit(unitNumber, true);
      if (!saved) {
        setOperationError(`U${unitNumber} 保存失败，已停止预检。`);
        setOperationStage('error');
        return;
      }
    }
    await runPreflight(normalized, selectedProvider, selectedModel);
  };

  const monitorJob = async (jobId: string, units: number[]) => {
    for (;;) {
      await delay(4000);
      try {
        const job = await api<JobResult>(`/api/jobs/${jobId}`);
        setCurrentJob(job);
        setOperationDetails(job.output);
        if (job.status === 'running') continue;

        if (job.status === 'succeeded') {
          setOperationStage('submitted');
          setNotice({ tone: 'success', text: '抽卡任务已完成，正在刷新卡池。' });
          setGeneratingUnits((current) => current.filter((unit) => !units.includes(unit)));
          await loadEpisode(episode.name);
          return;
        }
        const needsAttention = job.status === 'needs_attention';
        setOperationError(
          needsAttention
            ? '任务结果未知，工作台已停止自动重试，请核对平台任务和积分。'
            : '抽卡任务没有完成。',
        );
        setOperationStage('error');
        return;
      } catch (error) {
        setOperationError(error instanceof Error ? error.message : '读取任务状态失败');
        setOperationStage('error');
        return;
      }
    }
  };

  const confirmSubmission = async () => {
    if (!preflight || !submissionConfirmed) return;
    setOperationStage('submitting');
    setOperationError('');
    try {
      const result = await api<{
        jobId: string;
        ledger: string;
        outputSuffix: string;
        provider: string;
        providerLabel: string;
        model: string;
      }>('/api/submit', {
        method: 'POST',
        body: JSON.stringify({
          token: preflight.token,
          confirmed: true,
          confirmationKey: preflight.confirmationKey,
        }),
      });
      setGeneratingUnits((current) => [...new Set([...current, ...operationUnits])]);
      const job: JobResult = {
        id: result.jobId,
        kind: 'submit',
        episode: episode.name,
        units: operationUnits,
        status: 'running',
        output: '',
        ledger: result.ledger,
        outputSuffix: result.outputSuffix,
        provider: result.provider,
        model: result.model,
      };
      setCurrentJob(job);
      setOperationStage('submitted');
      setNotice({
        tone: 'info',
        text: `${result.providerLabel} · ${result.model} 已接收 U${operationUnits.join('、U')}，本次每个单元各抽 1 张，完成后会进入对应卡池。`,
      });
      void monitorJob(result.jobId, operationUnits);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : '抽卡提交失败');
      setOperationDetails(error instanceof ApiError ? error.details ?? '' : '');
      setOperationStage('error');
    }
  };

  const handleEpisodeChange = (name: string) => {
    if (dirtyUnits.length) {
      setNotice({ tone: 'warning', text: '当前还有未保存修改，请先保存后再切换剧集。' });
      return;
    }
    void loadEpisode(name);
  };

  const refreshCurrent = async () => {
    setLoadingCatalog(true);
    try {
      const names = await loadEpisodeNames();
      if (dirtyUnits.length) {
        setNotice({
          tone: 'warning',
          text: '剧集列表已更新，当前未保存修改已保留；保存后再刷新单元。',
        });
        return;
      }
      const currentName = names.includes(episode.name) ? episode.name : names[0];
      if (currentName) await loadEpisode(currentName);
      else setEpisode(emptyEpisode);
    } catch (error) {
      setConnected(false);
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '刷新剧集列表失败',
      });
    } finally {
      setLoadingCatalog(false);
    }
  };

  const changeProvider = (providerId: string) => {
    const provider = providers.find((item) => item.id === providerId);
    setSelectedProvider(providerId);
    setSelectedModel(provider?.models[0]?.id ?? '');
  };

  const openRepair = async (unitNumber: number) => {
    const unit = episode.units.find((item) => item.number === unitNumber);
    if (!unit) return;
    setRepairUnit(unitNumber);
    setRepairIssue('');
    setRepairMessages([]);
    setRepairResult(null);
    setRepairCandidate(drafts[unitNumber] ?? unit.prompt);
    setRepairError('');
    setRepairErrorDetails('');
    setRepairPlan(null);
    setRepairOpen(true);
    setRepairPlanLoading(true);
    try {
      const plan = await api<RepairPlan>('/api/repair-plan', {
        method: 'POST',
        body: JSON.stringify({ episode: episode.name, unit: unitNumber, issue: '' }),
      });
      setRepairPlan(plan);
    } catch (error) {
      setRepairError(error instanceof Error ? error.message : '无法读取修复规范清单');
    } finally {
      setRepairPlanLoading(false);
    }
  };

  const sendRepairMessage = async () => {
    const issue = repairIssue.trim();
    const unitNumber = repairUnit;
    if (!issue || unitNumber === null || repairLoading) return;
    const unit = episode.units.find((item) => item.number === unitNumber);
    if (!unit) return;
    const history = repairMessages;
    const prompt = repairCandidate || drafts[unitNumber] || unit.prompt;
    setRepairMessages((current) => [...current, { role: 'user', content: issue }]);
    setRepairIssue('');
    setRepairLoading(true);
    setRepairError('');
    setRepairErrorDetails('');
    try {
      const result = await api<RepairResult>('/api/repair', {
        method: 'POST',
        body: JSON.stringify({
          episode: episode.name,
          unit: unitNumber,
          prompt,
          issue,
          history,
        }),
      });
      setRepairResult(result);
      setRepairCandidate(result.revisedPrompt);
      setRepairPlan({
        requiredRules: result.requiredRules,
        referenceImages: result.referenceImages,
        existingVideos: repairPlan?.existingVideos ?? unit.videos.map((video) => video.filename),
        videoBasis: result.videoBasis,
      });
      setRepairMessages((current) => [
        ...current,
        { role: 'assistant', content: result.reply },
      ]);
    } catch (error) {
      setRepairError(error instanceof Error ? error.message : 'AI 修复没有完成');
      setRepairErrorDetails(error instanceof ApiError ? error.details ?? '' : '');
    } finally {
      setRepairLoading(false);
    }
  };

  const applyRepairCandidate = () => {
    if (repairUnit === null || !repairResult || !repairCandidate.trim()) return;
    setDrafts((current) => ({ ...current, [repairUnit]: repairCandidate.trim() }));
    setDraftRepairIds((current) => ({
      ...current,
      [repairUnit]: repairResult.repairId,
    }));
    setExpanded((current) =>
      current.includes(repairUnit) ? current : [...current, repairUnit],
    );
    setNotice({
      tone: 'success',
      text: `U${repairUnit} 的 AI 候选稿已放入编辑框，尚未保存。`,
    });
  };

  const repairRulesToShow = repairResult?.requiredRules ?? repairPlan?.requiredRules ?? [];
  const repairReferencesToShow =
    repairResult?.referenceImages ?? repairPlan?.referenceImages ?? [];

  return (
    <main className="storyboard-theme dark min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-40 border-b border-border bg-sidebar text-foreground shadow-[0_4px_20px_rgb(15_23_42/18%)]">
        <div className="flex min-h-16 items-center gap-4 px-5 xl:px-7">
          <div className="flex min-w-fit items-center gap-3">
            <div className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground shadow-[inset_0_0_0_1px_rgb(255_255_255/18%)]">
              <Film className="size-5" />
            </div>
            <div>
              <h1 className="text-[17px] font-semibold tracking-wide">分镜工作台</h1>
              <p className="text-xs text-muted-foreground">逐单元修改 · 抽卡 · 看成片</p>
            </div>
          </div>

          <div className="mx-2 h-8 w-px bg-secondary" />

          <Link
            href="/pipeline"
            className="flex shrink-0 items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
          >
            <Workflow className="size-4" />
            文案工作台
          </Link>

          <div className="flex min-w-0 items-center gap-2">
            <FolderOpen className="size-4 shrink-0 text-muted-foreground" />
            <NativeSelect
              value={episode.name}
              onChange={(event) => handleEpisodeChange(event.target.value)}
              disabled={!connected || loadingCatalog || loadingEpisode}
              className="w-[min(36vw,360px)] text-foreground"
              aria-label="选择剧集"
            >
              {!episode.name && (
                <NativeSelectOption value="" disabled>
                  {loadingCatalog ? '正在读取剧情文件夹…' : '请选择剧集'}
                </NativeSelectOption>
              )}
              {[...new Set([...episodeNames, ...(episode.name ? [episode.name] : [])])].map((name) => (
                <NativeSelectOption key={name} value={name}>
                  {name}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>

          <div className="ml-auto flex items-center gap-4">
            <span
              className={`hidden items-center gap-2 text-sm sm:flex ${connected ? 'text-success' : 'text-warning'}`}
            >
              <span
                className={`size-2 rounded-full ${connected ? 'bg-success' : 'bg-warning'}`}
              />
              {connected ? '已连接本地项目' : '本地服务未连接'}
            </span>
            <div className="hidden text-right lg:block">
              <p className="text-[11px] text-muted-foreground">抽卡渠道</p>
              <p
                className="max-w-[340px] truncate text-sm font-medium"
                title={`${selectedProviderDetail?.label ?? ''} · ${selectedModelDetail?.id ?? ''}`}
              >
                {selectedProviderDetail?.label ?? '渠道未就绪'} ·{' '}
                {selectedModelDetail?.name ?? '请选择模型'}
              </p>
            </div>
          </div>
        </div>
      </header>

      {notice && (
        <output
          className={`fixed right-5 top-20 z-50 max-w-md rounded-lg border px-4 py-3 text-sm font-medium shadow-lg ${noticeStyle(notice.tone)}`}
        >
          {notice.text}
        </output>
      )}

      <section className="border-b bg-card px-5 py-4 xl:px-7">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-5 pr-4">
            <div>
              <p className="text-xs text-muted-foreground">全部单元</p>
              <p className="mt-0.5 text-xl font-semibold tabular-nums">{episode.unitCount}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">已有成片</p>
              <p className="mt-0.5 text-xl font-semibold tabular-nums text-success">
                {episode.videoCount}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">未保存修改</p>
              <p className="mt-0.5 text-xl font-semibold tabular-nums text-primary">
                {dirtyUnits.length}
              </p>
            </div>
            <Badge
              className={
                episode.ready
                  ? 'border border-success-border bg-success-soft text-success'
                  : 'border border-warning-border bg-warning-soft text-warning'
              }
            >
              {!episode.name
                ? '尚未选择剧集'
                : !episode.hasStoryboard
                  ? '待生成分镜'
                  : episode.ready ? '分镜已放行' : '分镜未放行'}
            </Badge>
          </div>

          <div className="relative ml-auto min-w-[220px] flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索单元、提示词或参考图"
              className="h-10 w-full rounded-lg border bg-muted pl-9 pr-3 text-sm outline-none transition focus:border-primary focus:bg-card focus:ring-3 focus:ring-ring/10"
            />
          </div>

          <NativeSelect
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}
            className="w-28"
            aria-label="筛选状态"
          >
            <NativeSelectOption value="全部">全部状态</NativeSelectOption>
            <NativeSelectOption value="已完成">已完成</NativeSelectOption>
            <NativeSelectOption value="待生成">待生成</NativeSelectOption>
            <NativeSelectOption value="生成中">生成中</NativeSelectOption>
          </NativeSelect>

          <Button variant="outline" className="h-10" onClick={() => void refreshCurrent()} disabled={loadingCatalog || loadingEpisode}>
            <RefreshCw className={loadingCatalog || loadingEpisode ? 'animate-spin' : ''} data-icon="inline-start" />
            刷新
          </Button>
          <Button
            className="h-10 bg-primary px-4 text-primary-foreground hover:bg-primary/90"
            disabled={
              !connected ||
              !episode.hasStoryboard ||
              loadingCatalog ||
              loadingEpisode ||
              selected.length === 0 ||
              selected.some((unit) => generatingUnits.includes(unit))
            }
            onClick={() => void beginSubmission(selected)}
          >
            <Dices data-icon="inline-start" />
            所选各抽 1 张 {selected.length ? `(${selected.length})` : ''}
          </Button>
        </div>

        <div className="mt-4 grid items-end gap-3 rounded-xl border border-border bg-muted/80 p-3 md:grid-cols-2 xl:grid-cols-[220px_minmax(360px,1fr)_minmax(280px,0.8fr)_auto]">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-muted-foreground">视频渠道</span>
            <NativeSelect
              value={selectedProvider}
              onChange={(event) => changeProvider(event.target.value)}
              disabled={!providers.length}
              className="w-full bg-card"
              aria-label="选择视频渠道"
            >
              {providers.length ? (
                providers.map((provider) => (
                  <NativeSelectOption
                    key={provider.id}
                    value={provider.id}
                    disabled={!provider.selectable}
                  >
                    {provider.label} · {provider.id === DEFAULT_VIDEO_PROVIDER
                      ? '默认' : provider.tier === '默认' ? '可选' : provider.tier}
                    {!provider.selectable ? '（不可用）' : ''}
                  </NativeSelectOption>
                ))
              ) : (
                <NativeSelectOption value="guagu_video">正在读取渠道…</NativeSelectOption>
              )}
            </NativeSelect>
          </label>

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-muted-foreground">完整模型</span>
            <NativeSelect
              value={selectedModel}
              onChange={(event) => setSelectedModel(event.target.value)}
              disabled={!selectedProviderDetail?.selectable}
              className="w-full bg-card"
              aria-label="选择完整视频模型"
            >
              {selectedModel && !selectedModelDetail && (
                <NativeSelectOption value={selectedModel} disabled>
                  原模型不在最新列表，请重新选择 · {selectedModel}
                </NativeSelectOption>
              )}
              {selectedProviderDetail?.models.length ? (
                selectedProviderDetail.models.map((model) => (
                  <NativeSelectOption key={model.id} value={model.id}>
                    {model.id}
                    {` · ${modelPrice(selectedProvider, model.unitCost)}`}
                    {model.name !== model.id ? ` · ${model.name}` : ''}
                  </NativeSelectOption>
                ))
              ) : (
                <NativeSelectOption value="">没有可用模型</NativeSelectOption>
              )}
            </NativeSelect>
          </label>

          <div className="min-w-0 pb-0.5 text-sm leading-5 text-muted-foreground">
            <p className="mb-1 break-words text-base font-semibold text-foreground" aria-live="polite">
              {selectedModelDetail
                ? `单价：${modelPrice(selectedProvider, selectedModelDetail.unitCost)}`
                : '请选择模型查看价格'}
            </p>
            <p className="break-words font-medium text-foreground" title={selectedModelDetail?.id}>
              {selectedModelDetail?.id ?? '等待选择模型'}
              {selectedModelDetail?.resolution ? ` · ${selectedModelDetail.resolution}` : ''}
            </p>
            <p className="truncate" title={selectedProviderDetail?.error || selectedProviderDetail?.note}>
              {selectedProviderDetail?.error || selectedProviderDetail?.note || '刷新后读取实时渠道与模型。'}
            </p>
          </div>

          <Button
            variant="outline"
            className="h-9 bg-card"
            onClick={() => void loadProviders()}
            disabled={providersLoading}
          >
            <RefreshCw
              className={providersLoading ? 'animate-spin' : ''}
              data-icon="inline-start"
            />
            刷新渠道
          </Button>
        </div>
      </section>

      <section className="px-5 py-5 xl:px-7">
        {visibleUnits.length === 0 ? (
          <div aria-live="polite" className="rounded-xl border border-border bg-card px-6 py-12 text-center">
            <FolderOpen className="mx-auto mb-3 size-8 text-muted-foreground" />
            <h2 className="text-lg font-semibold text-foreground">
              {loadingCatalog || loadingEpisode
                ? '正在读取剧情文件夹'
                : !connected
                  ? '本地服务尚未连接'
                  : !episode.name
                    ? '还没有剧情文件夹'
                    : !episode.hasStoryboard
                      ? '本集尚未生成分镜'
                      : '没有可显示的分镜单元'}
            </h2>
            <p className="mt-2 text-base text-muted-foreground">
              {loadingCatalog || loadingEpisode
                ? '正在同步本地剧集与分镜文件。'
                : !connected
                  ? '连接本地服务后，点击上方“刷新”重新读取。'
                  : !episode.name
                    ? '创建剧情文件夹后，点击上方“刷新”即可显示。'
                    : !episode.hasStoryboard
                      ? `${episode.name} 已读取。完成本集分镜后，点击上方“刷新”加载单元。`
                      : '请检查本集分镜内容，或调整搜索词和状态筛选。'}
            </p>
          </div>
        ) : (
        <div className="rounded-xl border border-border bg-card shadow-[0_8px_30px_rgb(15_23_42/6%)]">
          <div>
            <div className="flex grid-cols-[76px_minmax(0,1fr)_280px_142px] items-center gap-4 rounded-t-xl border-b bg-muted px-4 py-3 text-sm font-semibold text-muted-foreground lg:grid">
              <div className="flex items-center gap-2">
                <Checkbox
                  aria-label="全选当前显示单元"
                  checked={
                    visibleUnits.length > 0 &&
                    visibleUnits.every((unit) => selected.includes(unit.number))
                  }
                  onCheckedChange={() => {
                    const visibleNumbers = visibleUnits.map((unit) => unit.number);
                    const allSelected = visibleNumbers.every((unit) => selected.includes(unit));
                    setSelected((current) =>
                      allSelected
                        ? current.filter((unit) => !visibleNumbers.includes(unit))
                        : [...new Set([...current, ...visibleNumbers])],
                    );
                  }}
                />
                <span className="hidden lg:inline">单元</span><span className="lg:hidden">全选当前单元</span>
              </div>
              <span className="hidden lg:inline">分镜提示词</span>
              <span className="hidden lg:inline">参考图</span>
              <span className="hidden lg:inline">视频 / 操作</span>
            </div>

            <div className="divide-y divide-border">
              {visibleUnits.map((unit) => {
                const isExpanded = expanded.includes(unit.number);
                const currentPrompt = drafts[unit.number] ?? unit.prompt;
                const isDirty = currentPrompt !== unit.prompt;
                const liveStatus: UnitStatus = generatingUnits.includes(unit.number)
                  ? '生成中'
                  : unit.status;
                const selectedVideoName = selectedVideos[unit.number];
                const video =
                  unit.videos.find((item) => item.filename === selectedVideoName) ?? unit.videos[0];
                const shouldLoadVideo = activeVideo === unit.number && Boolean(video);
                const isSaving = savingUnits.includes(unit.number);

                return (
                  <article
                    key={`${episode.name}-${unit.number}`}
                    className={`grid grid-cols-[44px_minmax(0,1fr)] items-start gap-4 px-4 py-4 transition lg:grid-cols-[76px_minmax(0,1fr)_280px_142px] ${
                      selected.includes(unit.number) ? 'bg-accent/50' : 'bg-card'
                    }`}
                  >
                    <div className="col-span-2 flex items-center gap-2 pt-0.5 lg:col-span-1">
                      <Checkbox
                        aria-label={`选择单元${unit.number}`}
                        checked={selected.includes(unit.number)}
                        onCheckedChange={() => toggleSelected(unit.number)}
                      />
                      <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-sidebar text-base font-bold text-foreground shadow-sm">
                        U{unit.number}
                      </div>
                    </div>

                    <div className="col-span-2 min-w-0 lg:col-span-1">
                      <p className="mb-2 break-words text-sm font-medium text-foreground">{unit.heading}</p>
                      <StoryboardPrompt
                        value={currentPrompt}
                        dirty={isDirty}
                        expanded={isExpanded}
                        unit={unit.number}
                        images={unit.referenceImages ?? []}
                        onPreview={(index) => setReferenceSelection({ unit: unit.number, index })}
                        readOnly={loadingCatalog || loadingEpisode}
                        onChange={(value) =>
                          setDrafts((current) => ({
                            ...current,
                            [unit.number]: value,
                          }))
                        }
                      />
                      <div className="mt-2 flex items-center gap-2">
                        <button
                          onClick={() => toggleExpanded(unit.number)}
                          className="inline-flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-primary"
                        >
                          {isExpanded ? (
                            <ChevronUp className="size-4" />
                          ) : (
                            <ChevronDown className="size-4" />
                          )}
                          {isExpanded ? '收起' : '展开更多'}
                        </button>
                        <span className="text-xs text-muted-foreground">{currentPrompt.length} 字</span>
                        {isDirty && (
                          <Badge className="border border-primary/30 bg-accent text-accent-foreground">
                            未保存
                          </Badge>
                        )}
                      </div>
                    </div>

                    <div className="col-span-2 min-w-0 lg:col-span-1">
                      <ReferenceGallery images={unit.referenceImages ?? []} error={unit.referenceError || (unit.referenceImages === undefined ? '参考图尚未加载，请刷新工作台。' : '')} apiBase={API_BASE} onPreview={(index) => setReferenceSelection({ unit: unit.number, index })} />
                    </div>

                    <div className="col-span-2 flex min-w-0 gap-4 lg:col-span-1 lg:block">
                    <div className="w-28 shrink-0 lg:w-auto">
                      <div className="relative aspect-[9/16] max-h-[214px] overflow-hidden rounded-lg bg-background shadow-inner">
                        {shouldLoadVideo ? (
                          /* oxlint-disable-next-line jsx-a11y/media-has-caption -- Generated clips do not have a caption sidecar. */
                          <video
                            key={video.filename}
                            src={`${API_BASE}${video.url}`}
                            controls
                            preload="metadata"
                            playsInline
                            className="h-full w-full object-contain"
                          />
                        ) : (
                          <button
                            onClick={() => setActiveVideo(unit.number)}
                            disabled={!video}
                            className="group absolute inset-0 grid place-items-center text-foreground disabled:cursor-default"
                            aria-label={video ? `播放单元${unit.number}视频` : '暂无视频'}
                          >
                            <span className="grid size-11 place-items-center rounded-full bg-foreground/15 backdrop-blur transition group-hover:scale-105 group-hover:bg-primary group-hover:text-primary-foreground group-disabled:bg-foreground/8">
                              {video ? (
                                <Play className="ml-0.5 size-5 fill-current" />
                              ) : (
                                <Clock3 className="size-5 text-muted-foreground" />
                              )}
                            </span>
                            <span className="absolute bottom-2 text-[11px] text-muted-foreground">
                              {video ? '点击载入视频' : '暂无成片'}
                            </span>
                          </button>
                        )}
                      </div>
                      {unit.videos.length > 1 ? (
                        <NativeSelect
                          value={video?.filename}
                          onChange={(event) => {
                            setSelectedVideos((current) => ({
                              ...current,
                              [unit.number]: event.target.value,
                            }));
                            setActiveVideo(unit.number);
                          }}
                          className="mt-1.5 w-full"
                          size="sm"
                          aria-label={`选择单元${unit.number}的抽卡视频版本`}
                        >
                          {unit.videos.map((item, index) => (
                            <NativeSelectOption key={item.filename} value={item.filename}>
                              {index === 0 ? '最新卡 · ' : `旧卡 ${index} · `}{item.filename}
                            </NativeSelectOption>
                          ))}
                        </NativeSelect>
                      ) : (
                        <p className="mt-1.5 truncate text-[11px] text-muted-foreground" title={video?.filename}>
                          {video ? `1 张卡 · ${video.filename}` : '卡池为空'}
                        </p>
                      )}
                    </div>

                    <div className="flex min-w-0 flex-1 flex-col items-end gap-2 lg:mt-3">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${unitStatusStyle(liveStatus)}`}
                      >
                        {liveStatus === '已完成' ? (
                          <CheckCircle2 className="size-3.5" />
                        ) : liveStatus === '生成中' ? (
                          <LoaderCircle className="size-3.5 animate-spin" />
                        ) : (
                          <CircleDot className="size-3.5" />
                        )}
                        {liveStatus}
                      </span>
                      {liveStatus === '生成中' && <Progress value={45} className="w-full" />}
                      <Button
                        variant="outline"
                        className="w-full border-assistant-border bg-assistant-soft text-assistant hover:bg-assistant-soft hover:text-assistant"
                        disabled={!connected}
                        onClick={() => void openRepair(unit.number)}
                      >
                        <Sparkles data-icon="inline-start" />
                        AI 修复
                      </Button>
                      <Button
                        variant={isDirty ? 'default' : 'outline'}
                        className={
                          isDirty
                            ? 'w-full bg-primary text-primary-foreground hover:bg-primary/90'
                            : 'w-full'
                        }
                        disabled={!connected || !isDirty || isSaving}
                        onClick={() => void saveUnit(unit.number)}
                      >
                        {isSaving ? (
                          <LoaderCircle className="animate-spin" data-icon="inline-start" />
                        ) : (
                          <Save data-icon="inline-start" />
                        )}
                        {isSaving ? '保存中' : '保存新版本'}
                      </Button>
                      <Button
                        variant="outline"
                        className="w-full"
                        disabled={!connected || liveStatus === '生成中'}
                        onClick={() => void beginSubmission([unit.number])}
                      >
                        {liveStatus === '生成中' ? (
                          <LoaderCircle className="animate-spin" data-icon="inline-start" />
                        ) : (
                          <Dices data-icon="inline-start" />
                        )}
                        {liveStatus === '生成中'
                          ? '抽卡中'
                          : unit.videos.length
                            ? '再抽 1 张'
                            : '抽第 1 张'}
                      </Button>
                      <p className="mt-auto text-right text-[11px] leading-4 text-muted-foreground">
                        每次先预检并确认
                        <br />
                        旧卡全部保留
                      </p>
                    </div>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </div>
        )}

        <footer className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
          <span className="inline-flex items-center gap-2">
            <ShieldCheck className="size-4 text-success" />
            保存会先备份旧提示词；每次抽卡使用新账本和新视频后缀
          </span>
          <span>当前显示 {visibleUnits.length} 个单元</span>
        </footer>
      </section>

      <Dialog
        open={operationOpen}
        onOpenChange={(open) => {
          setOperationOpen(open);
          if (!open && operationStage !== 'submitting') {
            setOperationStage('idle');
          }
        }}
      >
        <DialogContent className="storyboard-theme dark max-h-[86vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-lg">
              {operationStage === 'error' ? (
                <XCircle className="size-5 text-danger" />
              ) : operationStage === 'ready' ? (
                <ShieldCheck className="size-5 text-success" />
              ) : operationStage === 'submitted' ? (
                <CheckCircle2 className="size-5 text-success" />
              ) : (
                <LoaderCircle className="size-5 animate-spin text-primary" />
              )}
              {operationStage === 'saving' && '正在保存修改'}
              {operationStage === 'preflight' && '正在进行抽卡预检'}
              {operationStage === 'ready' && '抽卡预检完成'}
              {operationStage === 'submitting' && '正在创建抽卡任务'}
              {operationStage === 'submitted' && '抽卡任务已经交给生成队列'}
              {operationStage === 'error' && '本次操作已停止'}
            </DialogTitle>
            <DialogDescription>
              剧集：{episode.name}　单元：U{operationUnits.join('、U') || '—'}　每个单元 1 张
            </DialogDescription>
          </DialogHeader>

          {(operationStage === 'saving' || operationStage === 'preflight') && (
            <div className="rounded-lg border border-info-border bg-info-soft p-4 text-sm text-info">
              <p className="font-medium">尚未确认抽卡，不会扣积分</p>
              <p className="mt-1 text-info">正在检查提示词、参考图、渠道状态和费用信息。</p>
              <Progress value={operationStage === 'saving' ? 28 : 68} className="mt-4" />
            </div>
          )}

          {operationStage === 'ready' && preflight && (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-lg border bg-muted p-3">
                  <p className="text-xs text-muted-foreground">渠道</p>
                  <p className="mt-1 text-base font-semibold">{preflight.providerLabel}</p>
                </div>
                <div className="rounded-lg border bg-muted p-3">
                  <p className="text-xs text-muted-foreground">完整模型</p>
                  <p className="mt-1 break-all text-sm font-semibold">{preflight.model}</p>
                </div>
                <div className="rounded-lg border bg-muted p-3">
                  <p className="text-xs text-muted-foreground">本次抽卡</p>
                  <p className="mt-1 text-base font-semibold">
                    {preflight.units.length} 个单元 · 各 1 张 · {preflight.resolution}
                  </p>
                </div>
                <div className="rounded-lg border bg-muted p-3">
                  <p className="text-xs text-muted-foreground">费用</p>
                  <p
                    className={`mt-1 text-base font-semibold ${
                      preflight.cost.amount === null ? 'text-warning' : 'text-primary'
                    }`}
                  >
                    {preflight.cost.label}
                  </p>
                </div>
              </div>

              <div className="rounded-lg border border-info-border bg-info-soft p-4 text-sm text-info">
                <p className="font-medium">参考图传递方式</p>
                <p className="mt-1 leading-6 text-info">{preflight.referenceMode}</p>
              </div>

              <div className="flex items-start gap-3 rounded-lg border border-warning-border bg-warning-soft p-4 text-sm text-warning">
                <AlertTriangle className="mt-0.5 size-5 shrink-0" />
                <div>
                  <p className="font-medium">抽卡前请确认渠道风险</p>
                  <p className="mt-1 leading-6 text-warning">{preflight.submissionWarning}</p>
                  <p className="mt-1 leading-6 text-warning">
                    本渠道失败后会停止，不会自动切换到其他渠道，也不会自动重复提交。
                  </p>
                </div>
              </div>

              <p className="text-xs leading-5 text-muted-foreground">{preflight.cost.note}</p>

              <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-success-border bg-success-soft p-4 text-sm text-success">
                <Checkbox
                  checked={submissionConfirmed}
                  onCheckedChange={(checked) => setSubmissionConfirmed(Boolean(checked))}
                  className="mt-0.5"
                />
                <span>
                  <strong>
                    {preflight.cost.amount === null
                      ? `我确认给 U${preflight.units.join('、U')} 各抽 1 张新卡，并接受该渠道可能产生无法预估的费用或额度消耗。`
                      : `我确认给 U${preflight.units.join('、U')} 各抽 1 张新卡，${preflight.cost.label}。`}
                  </strong>
                  {preflight.provider === '12ai_video' && (
                    <span className="mt-1 block font-medium text-warning">
                      我也确认参考图将在实际提交后上传到 imgbb 公网地址。
                    </span>
                  )}
                  <span className="mt-1 block text-success">
                    本次抽卡会创建新的任务账本和输出后缀，不覆盖已有提示词备份或旧卡。
                  </span>
                </span>
              </label>
            </div>
          )}

          {operationStage === 'submitting' && (
            <div className="rounded-lg border border-info-border bg-info-soft p-4 text-sm text-info">
              <p className="font-medium">正在安全创建抽卡任务</p>
              <p className="mt-1">只有平台确认接收后，工作台才会记录为已提交。</p>
              <Progress value={72} className="mt-4" />
            </div>
          )}

          {operationStage === 'submitted' && (
            <div className="rounded-lg border border-success-border bg-success-soft p-4 text-sm text-success">
              <p className="font-medium">
                {currentJob?.status === 'succeeded' ? '新卡生成和下载已经完成' : '抽卡任务正在后台生成'}
              </p>
              <p className="mt-1 text-success">
                你可以关闭这个窗口继续修改其他单元。完成后，新卡会出现在对应行的卡池最前面。
              </p>
              {currentJob?.ledger && (
                <p className="mt-2 break-all text-xs text-success">账本：{currentJob.ledger}</p>
              )}
            </div>
          )}

          {operationStage === 'error' && (
            <div className="flex items-start gap-3 rounded-lg border border-danger-border bg-danger-soft p-4 text-sm text-danger">
              <AlertTriangle className="mt-0.5 size-5 shrink-0" />
              <div>
                <p className="font-medium">{operationError}</p>
                <p className="mt-1 text-danger">没有自动重试，也不会重复提交。</p>
              </div>
            </div>
          )}

          {operationDetails && (
            <details className="rounded-lg border bg-background text-foreground">
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium">查看运行详情</summary>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap border-t border-border p-4 text-xs leading-5">
                {operationDetails}
              </pre>
            </details>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setOperationOpen(false)}>
              {operationStage === 'submitted' ? '关闭，继续工作' : '关闭'}
            </Button>
            {operationStage === 'ready' && preflight && (
              <Button
                className="bg-primary text-primary-foreground hover:bg-primary/90"
                disabled={!submissionConfirmed}
                onClick={() => void confirmSubmission()}
              >
                <Send data-icon="inline-start" />
                确认抽卡
              </Button>
            )}
            {operationStage === 'error' && (
              <Button variant="outline" onClick={() => void beginSubmission(operationUnits)}>
                <RefreshCw data-icon="inline-start" />
                重新预检
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ReferenceViewer selection={referenceSelection} images={episode.units.find((unit) => unit.number === referenceSelection?.unit)?.referenceImages ?? []} apiBase={API_BASE} onChange={setReferenceSelection} />

      <Dialog open={repairOpen} onOpenChange={setRepairOpen}>
        <DialogContent className="storyboard-theme dark max-h-[92vh] overflow-y-auto sm:max-w-6xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-lg">
              <Bot className="size-5 text-assistant" />
              U{repairUnit ?? '—'} · AI 对话修复
            </DialogTitle>
            <DialogDescription>
              你描述成片问题，Codex CLI 读取本单元与对应规范后给出完整候选稿；候选稿不会自动保存。
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-5 lg:grid-cols-[0.9fr_1.1fr]">
            <section className="min-w-0 space-y-4">
              <div className="rounded-lg border border-info-border bg-info-soft p-3 text-sm leading-6 text-info">
                <p className="font-medium">不抽关键帧，也不让 AI 假装看过视频</p>
                <p className="text-info">
                  {repairPlan?.videoBasis ??
                    'AI 只依据你的问题描述、当前提示词、前后单元和实际挂载的参考图判断。'}
                </p>
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <h3 className="font-semibold text-foreground">修复对话</h3>
                  <span className="text-xs text-muted-foreground">请具体描述哪里翻车</span>
                </div>
                <div className="max-h-56 min-h-32 space-y-2 overflow-y-auto rounded-lg border bg-muted p-3">
                  {repairMessages.length === 0 && !repairLoading ? (
                    <p className="text-sm leading-6 text-muted-foreground">
                      例如：人物转头后换脸、两个人同时张嘴、上一单元湿发到了这里变干、门口人物凭空出现。
                    </p>
                  ) : (
                    repairMessages.map((message, index) => (
                      <div
                        key={`${message.role}-${index}`}
                        className={`rounded-lg px-3 py-2 text-sm leading-6 ${
                          message.role === 'user'
                            ? 'ml-8 bg-secondary text-foreground'
                            : 'mr-8 border border-assistant-border bg-card text-foreground'
                        }`}
                      >
                        <p className="mb-0.5 text-xs font-semibold opacity-70">
                          {message.role === 'user' ? '你' : 'AI'}
                        </p>
                        <p className="whitespace-pre-wrap">{message.content}</p>
                      </div>
                    ))
                  )}
                  {repairLoading && (
                    <div className="mr-8 flex items-center gap-2 rounded-lg border border-assistant-border bg-card px-3 py-3 text-sm text-assistant">
                      <LoaderCircle className="size-4 animate-spin" />
                      正在读取规范并生成完整候选稿…
                    </div>
                  )}
                </div>
                <Textarea
                  value={repairIssue}
                  onChange={(event) => setRepairIssue(event.target.value)}
                  placeholder="描述这次要修复的现象和你希望保留的内容"
                  className="mt-2 min-h-24 resize-y bg-card leading-6"
                  disabled={repairLoading}
                />
                <Button
                  className="mt-2 w-full bg-primary text-primary-foreground hover:bg-primary/90"
                  disabled={!repairIssue.trim() || repairLoading || repairPlanLoading}
                  onClick={() => void sendRepairMessage()}
                >
                  {repairLoading ? (
                    <LoaderCircle className="animate-spin" data-icon="inline-start" />
                  ) : (
                    <Send data-icon="inline-start" />
                  )}
                  {repairLoading ? '修复中' : repairMessages.length ? '继续对话修复' : '让 AI 生成候选稿'}
                </Button>
              </div>

              {repairError && (
                <div className="flex items-start gap-2 rounded-lg border border-danger-border bg-danger-soft p-3 text-sm text-danger">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p>{repairError}</p>
                    {repairErrorDetails && (
                      <details className="mt-2">
                        <summary className="cursor-pointer">查看错误详情</summary>
                        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">
                          {repairErrorDetails}
                        </pre>
                      </details>
                    )}
                  </div>
                </div>
              )}

              <details open className="rounded-lg border bg-card">
                <summary className="flex cursor-pointer items-center gap-2 px-3 py-2.5 font-medium text-foreground">
                  <BookOpen className="size-4 text-assistant" />
                  本次应读规范与上下文（{repairRulesToShow.length}）
                </summary>
                <div className="max-h-64 space-y-2 overflow-y-auto border-t p-3">
                  {repairPlanLoading ? (
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                      <LoaderCircle className="size-4 animate-spin" /> 正在建立必读清单…
                    </p>
                  ) : (
                    repairRulesToShow.map((rule) => (
                      <div key={rule.path} className="rounded-md bg-muted p-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium text-foreground">{rule.label}</span>
                          <Badge variant="outline">{rule.readMode}</Badge>
                          <Badge variant="outline">{rule.kind}</Badge>
                        </div>
                        <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{rule.path}</p>
                        <p className="mt-1 text-xs leading-5 text-muted-foreground">{rule.reason}</p>
                      </div>
                    ))
                  )}
                </div>
              </details>

              <details className="rounded-lg border bg-card">
                <summary className="cursor-pointer px-3 py-2.5 font-medium text-foreground">
                  参考图附件（{repairReferencesToShow.filter((item) => item.status === 'attached').length}/
                  {repairReferencesToShow.length}）与已有视频
                </summary>
                <div className="space-y-2 border-t p-3 text-sm">
                  {repairReferencesToShow.map((item) => (
                    <p key={item.reference} className="flex items-start gap-2">
                      {item.status === 'attached' ? (
                        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
                      ) : (
                        <XCircle className="mt-0.5 size-4 shrink-0 text-danger" />
                      )}
                      <span>
                        {item.reference} → {item.filename ?? '未找到，不会假装已附加'}
                      </span>
                    </p>
                  ))}
                  <p className="border-t pt-2 text-xs leading-5 text-muted-foreground">
                    已有视频：{repairPlan?.existingVideos.join(' / ') || '暂无'}。仅记录文件名，不读取画面。
                  </p>
                </div>
              </details>
            </section>

            <section className="min-w-0 space-y-4 lg:border-l lg:pl-5">
              <div>
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <h3 className="font-semibold text-foreground">完整候选提示词</h3>
                  <span className="text-xs text-muted-foreground">
                    {repairResult ? 'AI 候选，可继续人工微调' : '当前编辑基准'}
                  </span>
                </div>
                <Textarea
                  value={repairCandidate}
                  onChange={(event) => setRepairCandidate(event.target.value)}
                  className="h-[420px] resize-y bg-background p-4 font-mono text-sm leading-6 text-foreground"
                  disabled={repairLoading}
                />
              </div>

              {repairResult ? (
                <div className="space-y-3">
                  <div className="rounded-lg border border-assistant-border bg-assistant-soft p-3">
                    <p className="text-xs text-assistant">本次修复级别</p>
                    <p className="mt-1 font-semibold text-assistant">{repairResult.repairLevel}</p>
                  </div>

                  <details open className="rounded-lg border bg-card">
                    <summary className="cursor-pointer px-3 py-2.5 font-medium text-foreground">
                      实际读取规范（{repairResult.rulesRead.length}）
                    </summary>
                    <ul className="space-y-1 border-t p-3 text-sm text-muted-foreground">
                      {repairResult.rulesRead.map((rule) => (
                        <li key={rule} className="break-all font-mono text-xs">{rule}</li>
                      ))}
                    </ul>
                  </details>

                  <details open className="rounded-lg border bg-card">
                    <summary className="cursor-pointer px-3 py-2.5 font-medium text-foreground">
                      实际采用规则（{repairResult.rulesApplied.length}）
                    </summary>
                    <ul className="list-disc space-y-1.5 border-t p-3 pl-8 text-sm leading-6 text-foreground">
                      {repairResult.rulesApplied.map((rule, index) => (
                        <li key={`${rule}-${index}`}>{rule}</li>
                      ))}
                    </ul>
                  </details>

                  <details className="rounded-lg border bg-card">
                    <summary className="cursor-pointer px-3 py-2.5 font-medium text-foreground">
                      诊断、改动与提醒
                    </summary>
                    <div className="space-y-3 border-t p-3 text-sm leading-6 text-foreground">
                      <div>
                        <p className="font-medium">原因判断</p>
                        <ul className="list-disc pl-5">
                          {repairResult.diagnosis.map((item, index) => (
                            <li key={`${item}-${index}`}>{item}</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <p className="font-medium">具体改动</p>
                        <ul className="list-disc pl-5">
                          {repairResult.changes.map((item, index) => (
                            <li key={`${item}-${index}`}>{item}</li>
                          ))}
                        </ul>
                      </div>
                      {repairResult.warnings.length > 0 && (
                        <div className="rounded-md border border-warning-border bg-warning-soft p-2 text-warning">
                          <p className="font-medium">仍需注意</p>
                          <ul className="list-disc pl-5">
                            {repairResult.warnings.map((item, index) => (
                              <li key={`${item}-${index}`}>{item}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <p className="break-all text-xs text-muted-foreground">
                        修复审计：{repairResult.auditPath}
                      </p>
                    </div>
                  </details>
                </div>
              ) : (
                <div className="rounded-lg border border-dashed p-4 text-sm leading-6 text-muted-foreground">
                  AI 返回后，这里会明确列出修复级别、实读规范、实际采用规则和具体改动。
                </div>
              )}
            </section>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setRepairOpen(false)}>
              关闭
            </Button>
            <Button
              className="bg-primary text-primary-foreground hover:bg-primary/90"
              disabled={!repairResult || !repairCandidate.trim() || repairLoading}
              onClick={() => {
                applyRepairCandidate();
                setRepairOpen(false);
              }}
            >
              <Sparkles data-icon="inline-start" />
              应用到编辑框（不保存）
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
};
