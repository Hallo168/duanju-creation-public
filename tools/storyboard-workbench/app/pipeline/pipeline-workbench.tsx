'use client';

/* oxlint-disable react/react-compiler -- The studio coordinates persisted server state and long-running jobs. */
/* oxlint-disable jsx-a11y/media-has-caption -- Generated clips have no separate caption file; the workbench must still play the original video. */

import {
  AlertTriangle,
  Bot,
  Brain,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronRight,
  Database,
  FileText,
  Film,
  ImageIcon,
  Layers3,
  LoaderCircle,
  MessageSquareText,
  Plus,
  RefreshCw,
  Save,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  User,
  WandSparkles,
  X,
  Zap,
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
import { Input } from '@/components/ui/input';
import {
  NativeSelect,
  NativeSelectOption,
} from '@/components/ui/native-select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { SourcePanel, type SourceInfo } from './source-panel';
import { LibraryPanel } from './library-panel';
import { sendStudioMessage } from './send-message';
import { TopicControls } from './topic-controls';
import { topicCandidateNumbers, topicMessageText } from './topic-flow';

type RouteMode = 'read_only' | 'guarded_write' | 'local_operation';
type StageState = 'done' | 'ready' | 'in_progress' | 'blocked';

type StudioRoute = {
  id: string;
  name: string;
  description: string;
  mode: RouteMode;
  creditRisk: boolean;
  requiresCli: boolean;
  externalWriteRisk: boolean;
};

type StudioSkill = {
  id: string;
  command: string;
  name: string;
  group: string;
  description: string;
  accent: string;
  requiresEpisode: boolean;
  routes: StudioRoute[];
};

type Story = {
  name: string;
  stages: Record<'topic' | 'script' | 'assets' | 'storyboard' | 'draw', StageState>;
  assetsReady: boolean;
  storyboardReady: boolean;
  assetImageCount: number;
  videoCount: number;
  modifiedAt: number;
};

type StudioCatalog = {
  version: number;
  skills: StudioSkill[];
  stories: Story[];
  guardrails: string[];
  storage: string;
};

type StudioProvider = {
  id: string;
  label: string;
  adapter: 'codex_cli' | 'openai_responses' | 'openai_compatible_chat' | 'custom_webhook';
  enabled: boolean;
  model: string;
  baseUrl: string;
  endpoint: string;
  apiKeyEnv: string;
  structuredOutputs: boolean;
  description: string;
  keyAvailable: boolean;
  configured: boolean;
  secretStored: boolean;
  apiKey?: string;
  clearKey?: boolean;
  modelsEndpoint?: string;
};

type Artifact = {
  path: string;
  action: 'created' | 'updated' | 'verified' | 'proposed' | 'none';
  summary: string;
  at?: string;
};

type Receipt = {
  status: 'waiting_user' | 'completed' | 'blocked';
  rulesRead: string[];
  rulesApplied: string[];
  artifacts: Artifact[];
  nextActions: string[];
  warnings: string[];
  resumeAction?: 'review' | null;
  ruleCount?: number;
  providerLabel?: string;
  model?: string;
  actualModel?: string;
  usage?: { inputTokens: number; outputTokens: number; requests: number; usageAvailable: boolean } | null;
};

type StudioMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  at: string;
  receipt?: Receipt;
};

type MemoryItem = { id: string; content: string; createdAt: string };

type RuleCache = {
  path: string;
  label: string;
  hash: string | null;
  size: number;
  modifiedAt: string | null;
  lastReadAt: string;
};

type StudioSession = {
  id: string;
  title: string;
  skillId: string;
  routeId: string;
  episode: string | null;
  providerId: string;
  model?: string;
  source?: SourceInfo | null;
  reference?: { path: string; name: string } | null;
  ruleCount?: number;
  status: 'active' | 'running' | 'needs_attention';
  summary: string;
  decisions: string[];
  pinnedMemory: MemoryItem[];
  memorySuggestions: MemoryItem[];
  ruleCache: RuleCache[];
  artifacts: Artifact[];
  messages: StudioMessage[];
  lastPreflight: {
    at?: string;
    completedAt?: string;
    rules?: StudioRule[];
    targets?: string[];
  } | null;
  lastJobId: string | null;
  createdAt: string;
  updatedAt: string;
};

type StudioRule = {
  path: string;
  label: string;
  reason: string;
  kind: string;
  readMode: string;
  required: boolean;
  exists: boolean;
  hash: string | null;
  size: number;
  modifiedAt: string | null;
  cacheState?: 'not_read' | 'fresh' | 'stale';
};

type StudioDocument = {
  path: string;
  name: string;
  content: string;
  truncated: boolean;
  hash: string;
  size: number;
  modifiedAt: string;
};

type StudioVideo = {
  unit: number;
  filename: string;
  modifiedAt: number;
  url: string;
};

type StudioContext = {
  sessionId: string;
  story: Story | null;
  documents: StudioDocument[];
  rules: StudioRule[];
  cache: Array<{
    path: string;
    label: string;
    state: 'not_read' | 'fresh' | 'stale';
    lastReadAt: string | null;
    hash: string | null;
  }>;
  videos: StudioVideo[];
  ruleCount?: number;
};

type StudioResult = {
  reply: string;
  status: 'waiting_user' | 'completed' | 'blocked';
  episode: string | null;
  sessionSummary: string;
  rulesRead: string[];
  rulesApplied: string[];
  memoryUpdate: string[];
  decisions: string[];
  artifacts: Artifact[];
  fileChanges: Array<{ path: string; content: string }>;
  nextActions: string[];
  warnings: string[];
};

type StudioJob = {
  id: string;
  kind: 'studio';
  sessionId: string;
  skillId: string;
  routeId: string;
  episode: string | null;
  providerId: string;
  status: 'running' | 'succeeded' | 'failed' | 'needs_attention';
  createdAt: string;
  finishedAt: string | null;
  result: StudioResult | null;
  backups: string[];
  written: string[];
  output: string;
  model?: string;
  providerLabel?: string;
  progress?: { phase: string; requests: number; sourceCount: number } | null;
};

type Notice = { tone: 'success' | 'warning' | 'error' | 'info'; text: string };

const API_HOST = typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)
  ? window.location.hostname
  : '127.0.0.1';
const API_BASE = `http://${API_HOST}:4317`;
const SESSION_KEY = 'storyboard-studio-session';

class ApiError extends Error {
  details?: unknown;

  constructor(message: string, details?: unknown) {
    super(message);
    this.details = details;
  }
}

async function api<T>(pathname: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  if (options?.body != null && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(API_BASE + pathname, { ...options, headers });
  const body = (await response.json().catch(() => ({}))) as T & { error?: string };
  if (!response.ok) throw new ApiError(body.error ?? '工作台操作失败', body);
  return body;
}

function timeLabel(value?: string | number | null) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function bytesLabel(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function noticeClass(tone: Notice['tone']) {
  if (tone === 'success') return 'border-emerald-300 bg-emerald-50 text-emerald-900';
  if (tone === 'error') return 'border-red-300 bg-red-50 text-red-900';
  if (tone === 'warning') return 'border-amber-300 bg-amber-50 text-amber-900';
  return 'border-blue-300 bg-blue-50 text-blue-900';
}

function routeModeMeta(mode: RouteMode) {
  if (mode === 'read_only') return { label: '只讨论', className: 'border-sky-200 bg-sky-50 text-sky-700' };
  if (mode === 'local_operation') return { label: '本机操作', className: 'border-amber-200 bg-amber-50 text-amber-700' };
  return { label: '确认后写盘', className: 'border-violet-200 bg-violet-50 text-violet-700' };
}

function cacheMeta(state: 'not_read' | 'fresh' | 'stale') {
  if (state === 'fresh') return { label: '已实读', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
  if (state === 'stale') return { label: '已变化', className: 'border-amber-200 bg-amber-50 text-amber-700' };
  return { label: '待读取', className: 'border-slate-200 bg-slate-50 text-slate-500' };
}

function SkillIcon({ id, className = 'size-4' }: { id: string; className?: string }) {
  if (id === 'chat') return <MessageSquareText className={className} />;
  if (id === 'xuanti') return <Sparkles className={className} />;
  if (id === 'duibiao') return <Layers3 className={className} />;
  if (id === 'erchuang') return <FileText className={className} />;
  if (id === 'xigao') return <RefreshCw className={className} />;
  if (id === 'zichan') return <ImageIcon className={className} />;
  if (id === 'fenjing') return <Film className={className} />;
  if (id === 'xiufu') return <WandSparkles className={className} />;
  if (id === 'houqi') return <Zap className={className} />;
  if (id === 'fengmian') return <ImageIcon className={className} />;
  return <Database className={className} />;
}

function stageMeta(status: StageState) {
  if (status === 'done') return { label: '完成', dot: 'bg-emerald-500', text: 'text-emerald-700' };
  if (status === 'in_progress') return { label: '进行中', dot: 'bg-amber-500', text: 'text-amber-700' };
  if (status === 'ready') return { label: '可开始', dot: 'bg-blue-500', text: 'text-blue-700' };
  return { label: '等待', dot: 'bg-slate-300', text: 'text-slate-500' };
}

function promptSuggestions(skillId: string, routeId: string) {
  if (skillId === 'chat') return ['你好，我们先聊聊', '我有一个想法，帮我一起理清楚'];
  if (skillId === 'xuanti' && routeId.startsWith('original')) {
    return ['先和我聊这次想测哪种人心', '产品是……，先给我 5 个方向，不要落盘'];
  }
  if (skillId === 'xuanti') return ['对标原稿在……，按当前档位先出 5 题', '先判断这个对标最不能丢的机件'];
  if (skillId === 'duibiao') return ['这是台词稿路径：……', '先用大白话告诉我，它靠什么抓人'];
  if (skillId === 'erchuang') return ['先读选题契约，告诉我成稿重点', '这段台词不够狠，给我三个改法先讨论'];
  if (skillId === 'zichan') return ['先检查本集还缺哪些资产', '只生成资产方案，不要调用生图'];
  if (skillId === 'fenjing') return ['先检查进入分镜的前置条件', '我想讨论单元切分，不要写盘'];
  if (skillId === 'xiufu') return ['单元 3 人脸漂了，先诊断', '单元 8 口型错人，给最小改动候选'];
  if (skillId === 'houqi') return ['先核对本集后期材料是否齐全', '只讨论这集的配乐情绪段落'];
  if (skillId === 'fengmian') return ['先给我三个封面标题与构图方向', '按最新分镜写发布文案'];
  if (skillId === 'fupan') return ['播放量……，完播率……，先诊断', '记录本集数据，但先不要跨库蒸馏'];
  return ['先读取相关内容，告诉我你看到了什么'];
}

function ReceiptSummary({ receipt, onInspect }: { receipt: Receipt; onInspect: (tab: string) => void }) {
  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50/90 p-3 text-xs text-slate-600">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onInspect('rules')}
          className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-white px-2.5 py-1 text-emerald-700 transition hover:bg-emerald-50"
        >
          <BookOpen className="size-3.5" />资料核对 {receipt.ruleCount ?? receipt.rulesRead.length} 项
        </button>

        <button
          type="button"
          onClick={() => onInspect('artifacts')}
          className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-white px-2.5 py-1 text-blue-700 transition hover:bg-blue-50"
        >
          <FileText className="size-3.5" />产物 {receipt.artifacts.length}
        </button>
        <span className="ml-auto text-slate-400">{receipt.status === 'completed' ? '本轮完成' : receipt.status === 'blocked' ? '需要处理' : '等待你继续'}</span>
      </div>
      {receipt.nextActions.length > 0 && (
        <div className="mt-2 border-t border-slate-200 pt-2">
          下一步：{receipt.nextActions.join(' · ')}
        </div>
      )}
    </div>
  );
}

type InspectorProps = {
  session: StudioSession;
  context: StudioContext | null;
  activeTab: string;
  onTabChange: (value: string) => void;
  activeDocument: string;
  onDocumentChange: (value: string) => void;
  memoryText: string;
  onMemoryTextChange: (value: string) => void;
  onAddMemory: () => void;
  onMemoryAction: (action: string, memoryId: string) => void;
  memoryBusy: boolean;
};

function InspectorPanel({
  session,
  context,
  activeTab,
  onTabChange,
  activeDocument,
  onDocumentChange,
  memoryText,
  onMemoryTextChange,
  onAddMemory,
  onMemoryAction,
  memoryBusy,
}: InspectorProps) {
  const currentDocument = context?.documents.find((item) => item.path === activeDocument) ?? null;
  const latestReceipt = [...session.messages].reverse().find((item) => item.receipt)?.receipt;
  const [selectedVideo, setSelectedVideo] = useState('');

  useEffect(() => {
    if (!context?.videos.length) {
      setSelectedVideo('');
      return;
    }
    setSelectedVideo((current) => context.videos.some((item) => item.url === current) ? current : context.videos[0].url);
  }, [context?.videos]);

  return (
    <Tabs value={activeTab} onValueChange={onTabChange} className="flex min-h-0 flex-1 gap-0">
      <div className="border-b border-slate-200 px-3 py-3">
        <TabsList className="grid h-9 w-full grid-cols-4 bg-slate-100">
          <TabsTrigger value="context" className="text-xs">文件</TabsTrigger>
          <TabsTrigger value="rules" className="text-xs">规范</TabsTrigger>
          <TabsTrigger value="memory" className="text-xs">记忆</TabsTrigger>
          <TabsTrigger value="artifacts" className="text-xs">产物</TabsTrigger>
        </TabsList>
      </div>

      <TabsContent value="context" className="min-h-0 data-[hidden]:hidden">
        <ScrollArea className="h-full">
          <div className="space-y-4 p-4">
            <section className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">当前故事</span>
                {context?.story?.storyboardReady && <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700">fenjing_ready</Badge>}
              </div>
              <p className="break-words text-sm font-semibold text-slate-900">{session.episode ?? '尚未绑定故事'}</p>
              {context?.story && (
                <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                  <div className="rounded-lg bg-white p-2"><strong className="block text-base text-slate-900">{context.story.assetImageCount}</strong>参考图</div>
                  <div className="rounded-lg bg-white p-2"><strong className="block text-base text-slate-900">{context.story.videoCount}</strong>视频</div>
                  <div className="rounded-lg bg-white p-2"><strong className="block text-base text-slate-900">{context.documents.length}</strong>文件</div>
                </div>
              )}
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900">上下文文件</h3>
                <span className="text-xs text-slate-400">{context?.documents.length ?? 0} 份</span>
              </div>
              {context?.documents.length ? (
                <div className="space-y-1.5">
                  {context.documents.map((document) => (
                    <button
                      key={document.path}
                      type="button"
                      onClick={() => onDocumentChange(document.path)}
                      className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition ${activeDocument === document.path ? 'border-slate-400 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'}`}
                    >
                      <FileText className="size-4 shrink-0 opacity-70" />
                      <span className="min-w-0 flex-1 truncate text-sm">{document.name}</span>
                      <span className={`text-[11px] ${activeDocument === document.path ? 'text-slate-300' : 'text-slate-400'}`}>{bytesLabel(document.size)}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-300 p-5 text-center text-sm text-slate-500">选择故事后，这里显示选题、成稿、资产和分镜文件。</div>
              )}
            </section>

            {currentDocument && (
              <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
                <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
                  <span className="truncate text-xs font-medium text-slate-600">{currentDocument.path}</span>
                  <span className="shrink-0 text-[11px] text-slate-400">{timeLabel(currentDocument.modifiedAt)}</span>
                </div>
                <pre className="max-h-[38vh] overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs leading-6 text-slate-700">{currentDocument.content}</pre>
                {currentDocument.truncated && <p className="border-t border-slate-200 px-3 py-2 text-xs text-amber-700">文件较长，这里只显示前 120,000 字符；AI 读取不受此预览限制。</p>}
              </section>
            )}
          </div>
        </ScrollArea>
      </TabsContent>

      <TabsContent value="rules" className="min-h-0 data-[hidden]:hidden">
        <ScrollArea className="h-full">
          <div className="space-y-4 p-4">
            {latestReceipt?.rulesApplied.length ? (
              <section className="rounded-xl border border-violet-200 bg-violet-50/70 p-3">
                <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-violet-900">
                  <CheckCircle2 className="size-4" />本轮实际采用
                </div>
                <ul className="space-y-2 text-sm leading-6 text-violet-900/80">
                  {latestReceipt.rulesApplied.map((rule, index) => <li key={`${index}-${rule}`}>{index + 1}. {rule}</li>)}
                </ul>
              </section>
            ) : null}

            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900">后台资料</h3>
                <span className="text-xs text-slate-400">每轮按哈希复核</span>
              </div>
              <div className="space-y-2">
                {!context?.rules.length && <p className="text-sm leading-6 text-slate-500">规范正文由后台按当前流程提供。这里不展示内部规则和读取记录。</p>}
                {(context?.rules ?? []).map((rule) => {
                  const cached = context?.cache.find((item) => item.path === rule.path);
                  const state = cached?.state ?? 'not_read';
                  const meta = cacheMeta(state);
                  return (
                    <div key={rule.path} className="rounded-xl border border-slate-200 bg-white p-3">
                      <div className="flex items-start gap-2">
                        <BookOpen className="mt-0.5 size-4 shrink-0 text-slate-400" />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <p className="font-medium text-slate-900">{rule.label}</p>
                            <Badge className={`text-[10px] ${meta.className}`}>{meta.label}</Badge>
                            <Badge className="border-slate-200 bg-slate-50 text-[10px] text-slate-500">{rule.readMode}</Badge>
                          </div>
                          <p className="mt-1 break-all font-mono text-[11px] leading-5 text-slate-500">{rule.path}</p>
                          <p className="mt-1 text-xs leading-5 text-slate-600">{rule.reason}</p>
                          {cached?.lastReadAt && <p className="mt-1 text-[11px] text-slate-400">上次实读：{timeLabel(cached.lastReadAt)}</p>}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          </div>
        </ScrollArea>
      </TabsContent>

      <TabsContent value="memory" className="min-h-0 data-[hidden]:hidden">
        <ScrollArea className="h-full">
          <div className="space-y-4 p-4">
            <section className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900"><Brain className="size-4" />会话摘要</div>
              <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{session.summary || 'AI 完成第一轮后，会把可恢复的事实摘要写在这里。'}</p>
            </section>

            <section>
              <h3 className="mb-2 text-sm font-semibold text-slate-900">已确认决定</h3>
              {session.decisions.length ? (
                <ul className="space-y-2 text-sm text-slate-700">
                  {session.decisions.map((decision, index) => <li key={`${index}-${decision}`} className="rounded-lg border border-slate-200 bg-white px-3 py-2">{decision}</li>)}
                </ul>
              ) : <p className="text-sm text-slate-500">还没有确认决定。</p>}
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900">钉住的长期记忆</h3>
                <span className="text-xs text-slate-400">每轮都会带上</span>
              </div>
              <div className="flex gap-2">
                <Input value={memoryText} onChange={(event) => onMemoryTextChange(event.target.value)} placeholder="例如：这集必须保留原台词" className="h-9 text-sm" />
                <Button size="sm" onClick={onAddMemory} disabled={memoryBusy || !memoryText.trim()}><Plus className="size-4" /></Button>
              </div>
              <div className="mt-2 space-y-2">
                {session.pinnedMemory.map((memory) => (
                  <div key={memory.id} className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-sm text-amber-950">
                    <span className="min-w-0 flex-1">{memory.content}</span>
                    <button type="button" aria-label="移除记忆" onClick={() => onMemoryAction('remove', memory.id)} className="text-amber-700 transition hover:text-red-600"><Trash2 className="size-4" /></button>
                  </div>
                ))}
              </div>
            </section>

            {session.memorySuggestions.length > 0 && (
              <section>
                <h3 className="mb-2 text-sm font-semibold text-slate-900">AI 提议记住</h3>
                <div className="space-y-2">
                  {session.memorySuggestions.map((memory) => (
                    <div key={memory.id} className="rounded-lg border border-violet-200 bg-violet-50 p-3">
                      <p className="text-sm leading-6 text-violet-950">{memory.content}</p>
                      <div className="mt-2 flex gap-2">
                        <Button size="sm" variant="outline" onClick={() => onMemoryAction('pin-suggestion', memory.id)} disabled={memoryBusy}><Check className="size-3.5" />钉住</Button>
                        <Button size="sm" variant="ghost" onClick={() => onMemoryAction('dismiss-suggestion', memory.id)} disabled={memoryBusy}><X className="size-3.5" />忽略</Button>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>
        </ScrollArea>
      </TabsContent>

      <TabsContent value="artifacts" className="min-h-0 data-[hidden]:hidden">
        <ScrollArea className="h-full">
          <div className="space-y-4 p-4">
            {context?.videos.length ? (
              <section className="overflow-hidden rounded-xl border border-slate-200 bg-slate-950">
                <div className="aspect-[9/16] max-h-[46vh] bg-black">
                  {selectedVideo && <video key={selectedVideo} src={API_BASE + selectedVideo} controls preload="metadata" className="size-full object-contain" />}
                </div>
                <div className="border-t border-slate-800 p-3">
                  <NativeSelect className="w-full" value={selectedVideo} onChange={(event) => setSelectedVideo(event.target.value)}>
                    {context.videos.map((video) => <NativeSelectOption key={video.url} value={video.url}>单元 {video.unit} · {video.filename}</NativeSelectOption>)}
                  </NativeSelect>
                </div>
              </section>
            ) : (
              <section className="rounded-xl border border-dashed border-slate-300 p-5 text-center">
                <Film className="mx-auto size-6 text-slate-400" />
                <p className="mt-2 text-sm text-slate-600">当前故事还没有可播放成片。</p>
                <p className="mt-1 text-xs text-slate-400">不会抽关键帧，只读取成片目录里的原视频。</p>
              </section>
            )}

            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900">会话产物</h3>
                <Link href={session.episode ? `/?episode=${encodeURIComponent(session.episode)}` : '/'} className="text-xs font-medium text-blue-700 hover:underline">打开逐行台</Link>
              </div>
              {session.artifacts.length ? (
                <div className="space-y-2">
                  {[...session.artifacts].reverse().map((artifact, index) => (
                    <div key={`${artifact.path}-${index}`} className="rounded-xl border border-slate-200 bg-white p-3">
                      <div className="flex items-center gap-2">
                        <FileText className="size-4 text-blue-600" />
                        <span className="min-w-0 flex-1 break-all text-sm font-medium text-slate-900">{artifact.path}</span>
                        <Badge className="border-blue-200 bg-blue-50 text-[10px] text-blue-700">{artifact.action}</Badge>
                      </div>
                      {artifact.summary && <p className="mt-2 text-xs leading-5 text-slate-600">{artifact.summary}</p>}
                      {artifact.at && <p className="mt-1 text-[11px] text-slate-400">{timeLabel(artifact.at)}</p>}
                    </div>
                  ))}
                </div>
              ) : <p className="rounded-xl border border-dashed border-slate-300 p-4 text-center text-sm text-slate-500">这个会话还没有写盘产物。</p>}
            </section>
          </div>
        </ScrollArea>
      </TabsContent>
    </Tabs>
  );
}

export const PipelineWorkbench = () => {
  const [catalog, setCatalog] = useState<StudioCatalog | null>(null);
  const [providers, setProviders] = useState<StudioProvider[]>([]);
  const [sessions, setSessions] = useState<StudioSession[]>([]);
  const [session, setSession] = useState<StudioSession | null>(null);
  const [context, setContext] = useState<StudioContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootstrapError, setBootstrapError] = useState('');
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0);
  const [contextLoading, setContextLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [job, setJob] = useState<StudioJob | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [inspectorTab, setInspectorTab] = useState('context');
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [activeDocument, setActiveDocument] = useState('');
  const [memoryText, setMemoryText] = useState('');
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsProviderId, setSettingsProviderId] = useState('codex-cli');
  const [providerDraft, setProviderDraft] = useState<StudioProvider | null>(null);
  const [providerSaving, setProviderSaving] = useState(false);
  const [modelInput, setModelInput] = useState('');
  const [modelCatalog, setModelCatalog] = useState<string[]>([]);
  const [settingsModels, setSettingsModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [settingsModelsLoading, setSettingsModelsLoading] = useState(false);
  const [settingsNotice, setSettingsNotice] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const modelRequest = useRef(0);
  const settingsRequest = useRef(0);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const sendPending = useRef(false);

  const refreshSessions = useCallback(async (activeId?: string) => {
    const result = await api<{ sessions: StudioSession[] }>('/api/studio/sessions');
    setSessions(result.sessions);
    if (activeId) {
      const found = result.sessions.find((item) => item.id === activeId);
      if (found) setSession(found);
    }
    return result.sessions;
  }, []);

  const refreshContext = useCallback(async (sessionId: string) => {
    setContextLoading(true);
    try {
      const result = await api<StudioContext>(`/api/studio/context?sessionId=${encodeURIComponent(sessionId)}`);
      setContext(result);
      setActiveDocument((current) => result.documents.some((item) => item.path === current) ? current : (result.documents[0]?.path ?? ''));
      return result;
    } finally {
      setContextLoading(false);
    }
  }, []);

  const loadSession = useCallback(async (sessionId: string) => {
    const result = await api<{ session: StudioSession }>(`/api/studio/sessions/${encodeURIComponent(sessionId)}`);
    setSession(result.session);
    window.localStorage.setItem(SESSION_KEY, result.session.id);
    const url = new URL(window.location.href);
    url.searchParams.set('sessionId', result.session.id);
    window.history.replaceState(window.history.state, '', url);
    await refreshContext(result.session.id);
    if (result.session.status === 'running' && result.session.lastJobId) {
      setJob(await api<StudioJob>(`/api/studio/jobs/${encodeURIComponent(result.session.lastJobId)}`));
    } else setJob(null);
    return result.session;
  }, [refreshContext]);

  useEffect(() => {
    let cancelled = false;
    const bootstrap = async () => {
      setLoading(true);
      setBootstrapError('');
      try {
        const [catalogResult, providerResult, sessionResult, health] = await Promise.all([
          api<StudioCatalog>('/api/studio/catalog'),
          api<{ providers: StudioProvider[] }>('/api/studio/providers'),
          api<{ sessions: StudioSession[] }>('/api/studio/sessions'),
          api<{ studioApiVersion?: number }>('/api/health'),
        ]);
        if ((health.studioApiVersion ?? 0) < 3) throw new Error('后台服务尚未加载本次更新，请重启工作台后重试。');
        if (cancelled) return;
        setCatalog(catalogResult);
        setProviders(providerResult.providers);
        setSessions(sessionResult.sessions);
        const requestedSession = new URLSearchParams(window.location.search).get('sessionId');
        const savedId = requestedSession || window.localStorage.getItem(SESSION_KEY);
        let selected = sessionResult.sessions.find((item) => item.id === savedId) ?? sessionResult.sessions[0] ?? null;
        if (!selected) {
          const created = await api<{ session: StudioSession }>('/api/studio/sessions', {
            method: 'POST',
            body: JSON.stringify({ skillId: 'chat', routeId: 'conversation', providerId: 'codex-cli' }),
          });
          selected = created.session;
          await refreshSessions(selected.id);
        }
        if (cancelled || !selected) return;
        setSession(selected);
        const assetDiscussion = window.sessionStorage.getItem(`asset-discussion-${selected.id}`);
        if (assetDiscussion) setMessage(assetDiscussion);
        window.localStorage.setItem(SESSION_KEY, selected.id);
        await refreshContext(selected.id);
        if (selected.status === 'running' && selected.lastJobId) setJob(await api<StudioJob>(`/api/studio/jobs/${encodeURIComponent(selected.lastJobId)}`));
      } catch (error) {
        if (!cancelled) {
          const text = error instanceof TypeError
            ? '请先双击项目目录中的「启动工作台.cmd」，待启动完成后点击下方重新连接。'
            : error instanceof Error ? error.message : '本地工作台尚未连接。';
          setBootstrapError(text);
          setNotice({ tone: 'error', text });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void bootstrap();
    return () => { cancelled = true; };
  }, [bootstrapAttempt, refreshContext, refreshSessions]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 7000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [session?.messages.length, job?.status]);

  const runningJobId = job?.status === 'running' ? job.id : null;
  useEffect(() => {
    if (!runningJobId) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const latest = await api<StudioJob>(`/api/studio/jobs/${encodeURIComponent(runningJobId)}`);
        if (cancelled) return;
        if (latest.status === 'running') {
          setJob(latest);
          timer = window.setTimeout(() => void poll(), 1600);
          return;
        }
        await loadSession(latest.sessionId);
        await refreshSessions(latest.sessionId);
        setJob(latest);
        if (latest.status === 'succeeded') {
          setNotice({ tone: 'success', text: latest.result?.status === 'waiting_user' ? 'AI 已回复，可以继续对话。' : '本轮已完成，资料、记忆和产物已刷新。' });
        } else if (latest.status === 'needs_attention') {
          setNotice({ tone: 'warning', text: latest.result?.reply ? '回复已保留，请查看正文上方的提示。' : '本轮需要你补充信息。' });
        } else {
          setNotice({ tone: 'error', text: '本轮没有完成，已有文件保持原样。' });
        }
      } catch {
        if (!cancelled) {
          setNotice({ tone: 'warning', text: '暂时没连上，正在恢复回复。请勿重复发送。' });
          timer = window.setTimeout(() => void poll(), 4000);
        }
      }
    };
    timer = window.setTimeout(() => void poll(), 700);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [loadSession, refreshSessions, runningJobId]);

  const activeSkill = useMemo(() => catalog?.skills.find((item) => item.id === session?.skillId) ?? null, [catalog, session?.skillId]);
  const activeRoute = useMemo(() => activeSkill?.routes.find((item) => item.id === session?.routeId) ?? null, [activeSkill, session?.routeId]);
  const activeProvider = useMemo(() => providers.find((item) => item.id === session?.providerId) ?? null, [providers, session?.providerId]);
  const executing = job?.status === 'running' || session?.status === 'running';
  useEffect(() => { setModelInput(session?.model || activeProvider?.model || ''); }, [session?.id, session?.providerId, session?.model, activeProvider?.model]);
  useEffect(() => { modelRequest.current += 1; setModelCatalog([]); setModelsLoading(false); }, [session?.providerId]);

  const updateSession = useCallback(async (patch: Record<string, unknown>) => {
    if (!session) return;
    setSessionBusy(true);
    try {
      const result = await api<{ session: StudioSession }>(`/api/studio/sessions/${encodeURIComponent(session.id)}`, {
        method: 'POST',
        body: JSON.stringify(patch),
      });
      setSession(result.session);
      setJob(null);
      await refreshSessions(result.session.id);
      await refreshContext(result.session.id);
      return result.session;
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '会话设置没有更新。' });
      return null;
    } finally {
      setSessionBusy(false);
    }
  }, [refreshContext, refreshSessions, session]);

  const createSession = async (skillId = activeSkill?.id, routeId = activeRoute?.id) => {
    if (!activeSkill || !activeRoute) return;
    try {
      const result = await api<{ session: StudioSession }>('/api/studio/sessions', {
        method: 'POST',
        body: JSON.stringify({
          skillId,
          routeId,
          episode: skillId === 'chat' ? null : session?.episode ?? null,
          providerId: skillId === 'chat' ? 'codex-cli' : activeProvider?.id ?? 'codex-cli',
          model: skillId === 'chat' && activeProvider?.adapter !== 'codex_cli' ? '' : modelInput.trim(),
        }),
      });
      setMessage('');
      setJob(null);
      setSourceOpen(false);
      await refreshSessions(result.session.id);
      await loadSession(result.session.id);
      setNotice({ tone: 'success', text: '已创建一条独立会话，旧会话和记忆仍保留。' });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '新会话没有创建。' });
    }
  };

  const openChat = async () => {
    setLibraryOpen(false);
    const existing = sessions.find((item) => item.skillId === 'chat');
    if (!existing) { await createSession('chat', 'conversation'); return; }
    setSessionBusy(true);
    try { setJob(null); setMessage(''); await loadSession(existing.id); }
    catch (error) { setNotice({ tone: 'error', text: error instanceof Error ? error.message : '对话没有打开。' }); }
    finally { setSessionBusy(false); }
  };

  const sendMessage = async (overrideMessage?: string, overrideRoute?: Record<string, unknown>) => {
    const defaultTopicMessage = session?.skillId === 'xuanti' && (session.source || session.reference)
      ? '按本轮选定的对标和当前档位，先给我 5 个选题候选，等我选择，不要写入定案。' : '';
    const submittedMessage = (overrideMessage ?? (message.trim() || defaultTopicMessage)).trim();
    if (!session || !submittedMessage || executing || sessionBusy || sendPending.current) return;
    sendPending.current = true;
    setSending(true);
    try {
      const patch = { ...overrideRoute, model: modelInput.trim() };
      if (Object.keys(overrideRoute ?? {}).length || modelInput.trim() !== (session.model ?? '')) {
        const updated = await updateSession(patch);
        if (!updated) return;
      }
      const result = await sendStudioMessage<StudioJob>(api, {
        sessionId: session.id,
        message: submittedMessage,
        model: modelInput.trim(),
      });
      setJob(result);
      setMessage('');
      setSourceOpen(false);
      setNotice({ tone: 'info', text: '已发送，AI 正在处理。' });
      await loadSession(session.id);
      await refreshSessions();
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '本轮没有发送成功。' });
    } finally {
      sendPending.current = false;
      setSending(false);
    }
  };

  const onMemoryAction = async (action: string, memoryId: string) => {
    if (!session) return;
    setMemoryBusy(true);
    try {
      const result = await api<{ session: StudioSession }>(`/api/studio/sessions/${encodeURIComponent(session.id)}/memory`, {
        method: 'POST',
        body: JSON.stringify({ action, memoryId }),
      });
      setSession(result.session);
      await refreshSessions(result.session.id);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '记忆没有更新。' });
    } finally {
      setMemoryBusy(false);
    }
  };

  const addMemory = async () => {
    if (!session || !memoryText.trim()) return;
    setMemoryBusy(true);
    try {
      const result = await api<{ session: StudioSession }>(`/api/studio/sessions/${encodeURIComponent(session.id)}/memory`, {
        method: 'POST',
        body: JSON.stringify({ action: 'add', content: memoryText.trim() }),
      });
      setSession(result.session);
      setMemoryText('');
      await refreshSessions(result.session.id);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '记忆没有添加。' });
    } finally {
      setMemoryBusy(false);
    }
  };

  const openSettings = (providerId?: string) => {
    setSettingsNotice(''); setShowKey(false);
    settingsRequest.current += 1; setSettingsModels([]); setSettingsModelsLoading(false);
    const selected = providers.find((item) => item.id === (providerId ?? session?.providerId) && item.adapter !== 'codex_cli') ?? providers.find((item) => item.enabled && item.adapter !== 'codex_cli');
    if (selected) { setSettingsProviderId(selected.id); setProviderDraft({ ...selected }); }
    else newProviderDraft();
    setSettingsOpen(true);
  };

  const chooseSettingsProvider = (providerId: string) => {
    setSettingsNotice(''); setShowKey(false);
    settingsRequest.current += 1; setSettingsModels([]); setSettingsModelsLoading(false);
    setSettingsProviderId(providerId);
    const selected = providers.find((item) => item.id === providerId);
    if (selected) setProviderDraft({ ...selected });
  };

  const newProviderDraft = () => {
    setSettingsNotice(''); setShowKey(false);
    settingsRequest.current += 1; setSettingsModels([]); setSettingsModelsLoading(false);
    const id = `api-${Date.now().toString(36)}`;
    const draft: StudioProvider = {
      id,
      label: '新 API',
      adapter: 'openai_compatible_chat',
      enabled: true,
      model: '',
      baseUrl: '',
      endpoint: '/chat/completions',
      apiKeyEnv: '',
      modelsEndpoint: '/models',
      structuredOutputs: false,
      description: '',
      keyAvailable: true,
      configured: false,
      secretStored: false,
    };
    setSettingsProviderId(id);
    setProviderDraft(draft);
  };

  const saveProvider = async () => {
    if (!providerDraft) return;
    setProviderSaving(true);
    setSettingsNotice('正在保存接口…');
    try {
      const result = await api<{ providers: StudioProvider[] }>('/api/studio/providers', {
        method: 'POST',
        body: JSON.stringify({ provider: providerDraft }),
      });
      setProviders(result.providers);
      const saved = result.providers.find((item) => item.id === providerDraft.id);
      if (saved) setProviderDraft(saved);
      if (saved?.enabled) {
        const updated = await updateSession({ providerId: saved.id, model: saved.model });
        if (updated) { setModelInput(saved.model); setSettingsOpen(false); setProviderDraft(null); }
      }
      setNotice({ tone: 'success', text: saved?.enabled ? '接口已保存并用于当前会话。Key 只保存在服务端。' : '接口配置已保存，当前处于停用状态。' });
    } catch (error) {
      setSettingsNotice(error instanceof Error ? error.message : '接口配置没有保存。');
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '接口配置没有保存。' });
    } finally {
      setProviderSaving(false);
    }
  };

  const patchProvider = (patch: Partial<StudioProvider>) => {
    if (['baseUrl', 'apiKey', 'apiKeyEnv', 'adapter', 'modelsEndpoint', 'clearKey'].some((key) => key in patch)) {
      settingsRequest.current += 1; setSettingsModels([]); setSettingsModelsLoading(false);
    }
    setProviderDraft((previous) => previous ? { ...previous, ...patch } : null);
  };

  const loadConversationModels = async () => {
    if (!session) return;
    const token = ++modelRequest.current;
    setModelsLoading(true);
    try {
      const result = await api<{ models: string[]; note?: string }>(`/api/workbench/models?providerId=${encodeURIComponent(session.providerId)}`);
      if (token !== modelRequest.current) return;
      setModelCatalog(result.models);
      setNotice({ tone: 'info', text: result.note || `已获取 ${result.models.length} 个模型，也可以直接输入完整模型名。` });
    } catch (error) {
      if (token === modelRequest.current) setNotice({ tone: 'error', text: error instanceof Error ? error.message : '模型目录读取失败，可直接填写模型名。' });
    } finally { if (token === modelRequest.current) setModelsLoading(false); }
  };

  const loadDraftModels = async () => {
    if (!providerDraft) return;
    const token = ++settingsRequest.current;
    setSettingsModelsLoading(true);
    setSettingsNotice('正在获取模型列表…');
    try {
      const result = await api<{ models: string[]; note?: string }>('/api/workbench/models', { method: 'POST', body: JSON.stringify({ provider: providerDraft }) });
      if (token !== settingsRequest.current) return;
      setSettingsModels(result.models);
      setSettingsNotice(result.note || `已获取 ${result.models.length} 个模型，可在默认模型框中选择。`);
      setNotice({ tone: 'info', text: result.note || `已获取 ${result.models.length} 个模型，请选择默认模型后保存。` });
    } catch (error) {
      if (token === settingsRequest.current) setSettingsNotice(error instanceof Error ? error.message : '模型目录读取失败，可手动填写名称。');
      if (token === settingsRequest.current) setNotice({ tone: 'error', text: error instanceof Error ? error.message : '模型目录读取失败，可手动填写名称。' });
    } finally { if (token === settingsRequest.current) setSettingsModelsLoading(false); }
  };

  const useLibraryEntry = async (entry: { path: string; name: string }) => {
    if (!session) return;
    setSessionBusy(true);
    try {
      const result = await api<{ session: StudioSession }>('/api/studio/sessions', { method: 'POST', body: JSON.stringify({ skillId: 'xuanti', routeId: 'benchmark-tier-1', providerId: session.providerId, model: modelInput.trim(), referencePath: entry.path, title: `选题 · ${entry.name}` }) });
      await refreshSessions(result.session.id); await loadSession(result.session.id);
      setMessage('按本轮选定的对标资料，先给我选题候选。');
      setJob(null); setSourceOpen(false); setLibraryOpen(false);
      setNotice({ tone: 'success', text: '已在新会话中引用这份资料，发送后开始选题。' });
    } catch (error) { setNotice({ tone: 'error', text: error instanceof Error ? error.message : '资料没有引用成功。' }); }
    finally { setSessionBusy(false); }
  };

  const openInspector = (tab: string) => {
    setInspectorTab(tab);
    setInspectorOpen(true);
  };

  if (loading) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#eef2f7] text-slate-700">
        <div className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
          <LoaderCircle className="size-5 animate-spin text-orange-600" />正在恢复 Skill 工作台…
        </div>
      </main>
    );
  }

  if (bootstrapError || !catalog || !session || !activeSkill || !activeRoute) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#eef2f7] px-5 text-slate-700">
        <div className="w-full max-w-md rounded-2xl border border-red-200 bg-white p-6 shadow-sm">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-red-600" />
            <div>
              <h1 className="font-semibold text-slate-950">工作台暂时没有连上本地服务</h1>
              <p className="mt-2 text-sm leading-6 text-slate-600">{bootstrapError || '初始化数据不完整，请重新连接。'}</p>
            </div>
          </div>
          <Button className="mt-5 w-full" onClick={() => setBootstrapAttempt((current) => current + 1)}>
            <RefreshCw className="size-4" />重新连接
          </Button>
        </div>
      </main>
    );
  }

  const skillOrder = (id: string) => id === 'chat' ? 0 : id === 'duibiao' ? 1 : 2;
  const orderedSkills = [...catalog.skills].sort((a, b) => skillOrder(a.id) - skillOrder(b.id));
  const groups = [...new Set(orderedSkills.map((item) => item.group))];
  const modeMeta = routeModeMeta(activeRoute.mode);
  const flow = [
    ['topic', '选题'],
    ['script', '成稿'],
    ['assets', '参考图'],
    ['storyboard', '分镜'],
    ['draw', '抽卡'],
  ] as const;
  const suggestions = promptSuggestions(activeSkill.id, activeRoute.id);
  const isTopic = activeSkill.id === 'xuanti';
  const simpleReplies = isTopic || activeSkill.id === 'erchuang';
  const latestMessage = session.messages.at(-1);
  const lastRequest = [...session.messages].reverse().find((item) => item.role === 'user')?.content;
  const topicCanSend = Boolean(message.trim() || session.source || session.reference);

  return (
    <main className="flex h-dvh min-h-[560px] flex-col overflow-hidden bg-[#f4f6fa] text-slate-900">
      <header className="z-30 flex min-h-16 shrink-0 items-center gap-3 border-b border-slate-800 bg-[#131b2b] px-4 text-white shadow-[0_4px_24px_rgb(15_23_42/18%)] xl:px-5">
        <div className="flex shrink-0 items-center gap-3">
          <div className="grid size-9 place-items-center rounded-xl bg-orange-500 shadow-[inset_0_0_0_1px_rgb(255_255_255/25%)]"><WandSparkles className="size-5" /></div>
          <div className="hidden sm:block">
            <h1 className="text-[17px] font-semibold tracking-wide">短剧工作台</h1>
            {!isTopic && <p className="text-xs text-slate-400">拆解 · 文案库 · 对话 · 创作</p>}
          </div>
        </div>

        <nav aria-label="工作台入口" className="ml-1 hidden items-center rounded-lg border border-slate-700 bg-slate-950/40 p-1 text-sm lg:flex">
          <span className="rounded-md bg-white px-3 py-1.5 font-medium text-slate-900">文案创作</span>
          <Link href={session.episode ? `/assets?episode=${encodeURIComponent(session.episode)}` : '/assets'} className="rounded-md px-3 py-1.5 text-slate-300 transition hover:bg-slate-700 hover:text-white">资产操作台</Link>
          <Link href={session.episode ? `/?episode=${encodeURIComponent(session.episode)}` : '/'} className="rounded-md px-3 py-1.5 text-slate-300 transition hover:bg-slate-700 hover:text-white">逐行分镜 · 视频抽卡</Link>
        </nav>

        <div className="ml-auto flex min-w-0 items-center gap-2">
          <Button variant="outline" className="border-slate-600 bg-transparent text-slate-200 hover:bg-slate-700 hover:text-white sm:hidden" onClick={() => setLibraryOpen((open) => !open)}><BookOpen className="size-4" />{libraryOpen ? '返回对话' : '文案库'}</Button>
          <Button variant="outline" className="border-slate-600 bg-transparent text-slate-200 hover:bg-slate-700 hover:text-white" disabled={executing || sessionBusy} onClick={() => openSettings()}><Settings2 className="size-4" />{isTopic ? '设置' : 'API 设置'}</Button>
          <div className="hidden items-center gap-1.5 text-xs text-slate-300 lg:flex">
            <span className={`size-2 rounded-full ${activeProvider?.configured ? 'bg-emerald-400' : 'bg-amber-400'}`} />
            {activeProvider?.configured ? '已配置' : '待配置'}
          </div>
        </div>
      </header>

      {notice && (
        <div className={`absolute left-1/2 top-20 z-50 flex max-w-[min(92vw,760px)] -translate-x-1/2 items-start gap-2 rounded-xl border px-4 py-3 text-sm shadow-lg ${noticeClass(notice.tone)}`}>
          {notice.tone === 'error' || notice.tone === 'warning' ? <AlertTriangle className="mt-0.5 size-4 shrink-0" /> : <CheckCircle2 className="mt-0.5 size-4 shrink-0" />}
          <span>{notice.text}</span>
          <button type="button" onClick={() => setNotice(null)} className="ml-2"><X className="size-4" /></button>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] sm:grid-cols-[208px_minmax(0,1fr)] lg:grid-cols-[224px_minmax(0,1fr)]">
        <aside aria-label="创作流程" className="hidden min-h-0 flex-col border-r border-slate-800 bg-[#172033] text-slate-100 sm:flex">
          <div className="border-b border-slate-700/80 p-3">
            <div className="flex gap-2">
              <NativeSelect className="min-w-0 flex-1" value={session.id} onChange={(event) => void loadSession(event.target.value)} aria-label="历史会话">
                {sessions.map((item) => <NativeSelectOption key={item.id} value={item.id}>{item.title}</NativeSelectOption>)}
              </NativeSelect>
              <Button size="icon" onClick={() => void createSession()} className="bg-orange-500 text-white hover:bg-orange-600" aria-label="新建会话"><Plus className="size-4" /></Button>
            </div>
            {!isTopic && <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
              <span>{sessions.length} 条可恢复会话</span>
              <span>{timeLabel(session.updatedAt)}</span>
            </div>}
          </div>

          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-5 p-3">
              {groups.map((group) => (
                <section key={group}>
                  <h2 className="mb-2 px-2 text-xs font-medium text-slate-400">{group}</h2>
                  <div className="space-y-1">
                    {orderedSkills.filter((skill) => skill.group === group).map((skill) => {
                      const selected = !libraryOpen && skill.id === activeSkill.id;
                      return (
                        <button
                          key={skill.id}
                          type="button"
                          disabled={executing || sessionBusy || sending}
                          aria-current={selected ? 'page' : undefined}
                          onClick={() => { setLibraryOpen(false); if (skill.id === activeSkill.id) return; if (skill.id === 'chat') { void openChat(); return; } if (skill.id === 'zichan') window.location.href = session.episode ? `/assets?episode=${encodeURIComponent(session.episode)}` : '/assets'; else void updateSession({ skillId: skill.id, routeId: skill.routes[0].id }); }}
                          className={`group flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition disabled:opacity-50 ${selected ? 'bg-orange-500 text-white shadow-sm' : 'text-slate-300 hover:bg-slate-700/60 hover:text-white'}`}
                        >
                          <span className={`grid size-8 shrink-0 place-items-center rounded-lg ${selected ? 'bg-white/15 text-white' : 'bg-slate-700 text-slate-300 group-hover:bg-slate-600'}`}><SkillIcon id={skill.id} /></span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm font-medium">{skill.name}</span>
                          </span>
                          <ChevronRight className={`size-4 ${selected ? 'text-orange-100' : 'text-slate-600'}`} />
                        </button>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          </ScrollArea>

          <div className="border-t border-slate-700/80 p-3">
            <button type="button" aria-current={libraryOpen ? 'page' : undefined} onClick={() => setLibraryOpen(true)} className={`mb-3 flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm font-medium transition ${libraryOpen ? 'bg-orange-500 text-white' : 'text-slate-200 hover:bg-slate-700/60'}`}><BookOpen className="size-5" />我的文案库</button>
            {!isTopic && <div className="flex items-center gap-2 rounded-xl bg-slate-950/35 px-3 py-2 text-xs text-slate-400">
              <ShieldCheck className="size-4 shrink-0 text-emerald-400" />
              资料版本可追溯 · Key 在服务端
            </div>}
          </div>
        </aside>

        {libraryOpen && <section aria-label="我的文案库" className="min-h-0 min-w-0 overflow-y-auto bg-[#f4f6fa] px-5 py-6 lg:px-10">
          <div className="mx-auto max-w-5xl">
            <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
              <div><h2 className="text-xl font-semibold text-slate-950">我的文案库</h2><p className="mt-2 text-sm text-slate-500">查看已有原稿、拆解卡和积累的经验，选一份继续创作。</p></div>
              <Button variant="outline" onClick={() => setLibraryOpen(false)}>返回当前对话</Button>
            </div>
            <LibraryPanel onUse={useLibraryEntry} disabled={executing || sessionBusy} refreshKey={job?.finishedAt} />
          </div>
        </section>}

        <section className={`${libraryOpen ? 'hidden' : 'flex'} min-h-0 min-w-0 flex-col bg-white`}>
          <div className="shrink-0 border-b border-slate-200 bg-white px-4 py-3 xl:px-5">
            <div className="flex items-start gap-3">
              <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-slate-900 text-white"><SkillIcon id={activeSkill.id} className="size-5" /></div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-base font-semibold text-slate-950">{activeSkill.name}</h2>
                   {!isTopic && <Badge className={modeMeta.className}>{modeMeta.label}</Badge>}
                  {activeRoute.creditRisk && <Badge className="border-amber-200 bg-amber-50 text-amber-700">额度确认</Badge>}
                </div>
                <p className="mt-1 text-sm text-slate-500">{isTopic ? '先出 5 个选题，选中后再挑开场。' : activeSkill.description}</p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setInspectorOpen(true)}><Database className="size-4" />{isTopic ? '更多' : '文件与记忆'}{contextLoading && <LoaderCircle className="size-3 animate-spin" />}</Button>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-3">
              <NativeSelect aria-label="选择创作环节" className="w-full sm:hidden" value={activeSkill.id} disabled={executing || sessionBusy || sending} onChange={(event) => { const skill = catalog.skills.find((item) => item.id === event.target.value); if (!skill) return; if (skill.id === 'chat') { void openChat(); return; } if (skill.id === 'zichan') window.location.href = session.episode ? `/assets?episode=${encodeURIComponent(session.episode)}` : '/assets'; else void updateSession({ skillId: skill.id, routeId: skill.routes[0].id }); }}>
                {orderedSkills.map((skill) => <NativeSelectOption key={skill.id} value={skill.id}>{skill.name}</NativeSelectOption>)}
              </NativeSelect>
              {!isTopic && <NativeSelect aria-label="选择工作路线" className="w-full sm:w-56" value={activeRoute.id} disabled={executing || sessionBusy || sending} onChange={(event) => void updateSession({ routeId: event.target.value })}>
                {activeSkill.routes.map((route) => <NativeSelectOption key={route.id} value={route.id}>{route.name}</NativeSelectOption>)}
              </NativeSelect>}
              {!isTopic && ((!['chat', 'duibiao', 'xigao'].includes(activeSkill.id) || session.episode) ?
              <NativeSelect
                aria-label="选择故事"
                className="w-full sm:w-64"
                value={session.episode ?? ''}
                disabled={executing || sessionBusy || sending}
                onChange={(event) => void updateSession({ episode: event.target.value || null })}
              >
                <NativeSelectOption value="">选择或绑定一个故事</NativeSelectOption>
                {catalog.stories.map((story) => <NativeSelectOption key={story.name} value={story.name}>{story.name}</NativeSelectOption>)}
              </NativeSelect> : <span className="text-sm text-slate-500">{activeSkill.id === 'chat' ? '发送即可对话' : activeSkill.id === 'duibiao' ? '拆解不需要先创建故事' : '先讨论，定案后再绑定故事'}</span>)}
            </div>
            {isTopic && <div className="mt-3"><TopicControls session={session} disabled={executing || sessionBusy || sending} refreshKey={job?.finishedAt} onChange={updateSession} onImport={() => setSourceOpen((open) => !open)} /></div>}
            <div className="mt-3 flex gap-2 sm:hidden">
              <NativeSelect className="min-w-0 flex-1" value={session.id} onChange={(event) => void loadSession(event.target.value)} aria-label="历史会话">
                {sessions.map((item) => <NativeSelectOption key={item.id} value={item.id}>{item.title}</NativeSelectOption>)}
              </NativeSelect>
              <Button size="icon" disabled={sessionBusy || sending} onClick={() => void createSession()} aria-label="新建会话"><Plus className="size-4" /></Button>
            </div>
          </div>

          {!isTopic && context?.story && (
            <div className="flex shrink-0 items-center gap-1 border-b border-slate-200 bg-slate-50 px-4 py-2 text-xs xl:px-5">
              {flow.map(([id, label], index) => {
                const meta = stageMeta(context.story!.stages[id]);
                return (
                  <div key={id} className="flex min-w-0 flex-1 items-center gap-1.5">
                    <span className={`size-2 shrink-0 rounded-full ${meta.dot}`} />
                    <span className={`truncate font-medium ${meta.text}`}>{label}</span>
                    {index < flow.length - 1 && <ChevronRight className="ml-auto size-3.5 text-slate-300" />}
                  </div>
                );
              })}
            </div>
          )}

          <ScrollArea className="min-h-0 flex-1 bg-[#f6f8fb]">
            <div className="mx-auto w-full max-w-4xl space-y-5 px-4 py-6 xl:px-8">
              {['duibiao', 'xuanti', 'xigao'].includes(activeSkill.id) && (!isTopic || sourceOpen) && <section>
                {activeSkill.id !== 'duibiao' && <button type="button" aria-expanded={sourceOpen} onClick={() => setSourceOpen((open) => !open)} className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-700"><FileText className="size-4" />{isTopic ? '收起原稿' : session.source ? `原稿 · ${session.source.title}` : '导入 TXT / MD 原稿'}<ChevronRight className={`size-4 transition ${sourceOpen ? 'rotate-90' : ''}`} /></button>}
                <div className={activeSkill.id === 'duibiao' || sourceOpen ? '' : 'hidden'}><SourcePanel key={session.id + (session.source?.id ?? "") + (session.source?.hash ?? "")} sessionId={session.id} sourceInfo={session.source ?? null} disabled={executing || sessionBusy || sending} onChanged={async () => { await loadSession(session.id); await refreshSessions(); if (isTopic) setSourceOpen(false); }} onClear={async () => updateSession({ clearSource: true })} onStart={isTopic ? undefined : () => void sendMessage('请按本轮导入的完整原稿执行现有对标拆解流程，按既有规则查重、存档和入库，需要我拍板的候选单独提出。', { skillId: 'duibiao', routeId: 'full-ingest' })} /></div>
              </section>}
              {!isTopic && session.reference && <div className="flex flex-wrap items-center gap-2 rounded-lg border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-900"><BookOpen className="size-4" /><span className="min-w-0 flex-1 break-all">本轮参考：{session.reference.name}</span><Button variant="ghost" size="sm" disabled={executing || sessionBusy} onClick={() => void updateSession({ referencePath: null })}>取消引用</Button></div>}
              {session.messages.length === 0 ? isTopic ? (
                <div className="py-10 text-center text-slate-500">
                  <Sparkles className="mx-auto mb-4 size-8 text-orange-500" />
                  <p className="text-base">{session.source || session.reference ? '对标已选好，点击下方「生成选题」。' : '选一份对标，或在下方写你的产品和想法。'}</p>
                  <p className="mt-2 text-sm">有特别要求，直接写在输入框里。</p>
                </div>
              ) : activeSkill.id === 'duibiao' ? <p className="px-1 text-sm leading-7 text-slate-500">导入原稿后选择本轮模型，再开始拆解。也可以在下方先补充你的要求。</p> : (
                <div className="py-8">
                  <div className="mx-auto max-w-xl text-center">
                    <div className="mx-auto grid size-12 place-items-center rounded-2xl border border-orange-200 bg-orange-50 text-orange-600"><MessageSquareText className="size-6" /></div>
                    <h3 className="mt-4 text-lg font-semibold text-slate-950">{activeSkill.id === 'chat' ? '和 Codex 聊聊' : '需要推敲时，在这里讨论'}</h3>
                    <p className="mt-2 text-sm leading-6 text-slate-500">{activeSkill.id === 'chat' ? '输入消息开始。可以继续追问，也可以随时新建一段对话。' : `${activeRoute.description} 选择本轮模型，说说你的想法。`}</p>
                  </div>
                  <div className="mx-auto mt-6 grid max-w-2xl gap-3 sm:grid-cols-2">
                    {suggestions.map((suggestion) => (
                      <button key={suggestion} type="button" onClick={() => setMessage(suggestion)} className="rounded-xl border border-slate-200 bg-white p-4 text-left text-sm leading-6 text-slate-700 shadow-sm transition hover:border-orange-300 hover:bg-orange-50/40">
                        <Sparkles className="mb-2 size-4 text-orange-500" />{suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                session.messages.map((item) => (
                  <article key={item.id} className={`flex gap-3 ${item.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                    {item.role === 'assistant' && <div className="grid size-8 shrink-0 place-items-center rounded-xl bg-slate-900 text-white"><Bot className="size-4" /></div>}
                    <div className={`${simpleReplies && item.role === 'assistant' ? 'min-w-0 flex-1' : 'max-w-[88%]'} ${item.role === 'user' ? 'rounded-2xl rounded-br-md bg-[#24324a] px-4 py-3 text-white shadow-sm' : 'rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-slate-800 shadow-sm'}`}>
                      {!(isTopic && topicMessageText(item.content) !== item.content) && item.receipt?.warnings.map((warning, index) => <p key={index} className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900">{warning}</p>)}
                      <div className="whitespace-pre-wrap break-words text-base leading-8">{isTopic && item.role === 'assistant' ? topicMessageText(item.content) : item.content}</div>
                      {isTopic && item === latestMessage && item.role === 'assistant' && item.receipt?.status !== 'blocked' && topicCandidateNumbers(item.content).length > 0 && <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-200 pt-4">{topicCandidateNumbers(item.content).map((number) => <Button key={number} variant="outline" disabled={executing || sending || sessionBusy} onClick={() => void sendMessage(`我选第 ${number} 个选题。请给这个选题出 3 个不同的开场方案，等我选择，先不要写入定案。`)}>选第 {number} 个</Button>)}</div>}
                      {isTopic && item === latestMessage && item.receipt?.status === 'blocked' && lastRequest && <Button className="mt-4 bg-orange-500 text-white hover:bg-orange-600" disabled={executing || sending || sessionBusy} onClick={() => void sendMessage(lastRequest)}><RefreshCw className="size-4" />重新生成</Button>}
                      {activeSkill.id === 'erchuang' && item === latestMessage && item.receipt?.resumeAction === 'review' && <Button className="mt-4 bg-orange-500 text-white hover:bg-orange-600" disabled={executing || sending || sessionBusy} onClick={() => void sendMessage('继续审稿。沿用已保留的本集草稿，按当前选题定案完成独立审稿和必要修订，返回完整最终稿。')}><RefreshCw className="size-4" />继续审稿</Button>}
                      {simpleReplies ? item.role === 'user' ? <p className="mt-2 text-xs text-slate-400">{timeLabel(item.at)}</p> : <details className="mt-3 text-xs text-slate-400"><summary className="cursor-pointer">回复详情 · {timeLabel(item.at)}</summary><div className="mt-2 space-y-2">{item.receipt && <><p>{item.receipt.providerLabel} / {item.receipt.actualModel || item.receipt.model || '默认模型'}</p>{item.receipt.usage?.usageAvailable && <p>输入 {item.receipt.usage.inputTokens} · 输出 {item.receipt.usage.outputTokens} tokens</p>}<ReceiptSummary receipt={item.receipt} onInspect={openInspector} /></>}</div></details> : <>
                        {item.receipt && activeSkill.id !== 'chat' && <ReceiptSummary receipt={item.receipt} onInspect={openInspector} />}
                        <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">{timeLabel(item.at)}{item.role === 'assistant' && item.receipt?.providerLabel && <span className="rounded bg-orange-50 px-2 py-0.5 text-orange-800">{item.receipt.providerLabel} / {item.receipt.actualModel || item.receipt.model || 'CLI 默认模型'}</span>}{item.receipt?.usage?.usageAvailable && <span>输入 {item.receipt.usage.inputTokens} · 输出 {item.receipt.usage.outputTokens} tokens</span>}</p>
                      </>}
                    </div>
                    {item.role === 'user' && <div className="grid size-8 shrink-0 place-items-center rounded-xl bg-orange-500 text-white"><User className="size-4" /></div>}
                  </article>
                ))
              )}

              {job?.status === 'running' && (
                <article className="flex gap-3">
                  <div className="grid size-8 shrink-0 place-items-center rounded-xl bg-slate-900 text-white"><Bot className="size-4" /></div>
                  <div className="rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 shadow-sm">
                    <div className="flex items-center gap-2 text-sm font-medium text-slate-700"><LoaderCircle className="size-4 animate-spin text-orange-500" />{isTopic ? '正在准备选题回复…' : job.progress?.phase || '正在处理这轮任务…'}</div>
                    <p className="mt-1 text-sm text-slate-400">{isTopic ? '完成后会直接显示在这里，无需重复点击。' : '会话已经落盘，可以留在这里等待。'}</p>
                  </div>
                </article>
              )}
              <div ref={messageEndRef} />
            </div>
          </ScrollArea>

          <div className="shrink-0 border-t border-slate-200 bg-white px-4 py-3 xl:px-6">
            <div className="mx-auto max-w-4xl">
              <details open={!isTopic} className="mb-3">
                <summary className={isTopic ? 'mb-3 cursor-pointer text-sm text-slate-500' : 'hidden'}>模型设置 · {modelInput || activeProvider?.label || '默认模型'}</summary>
              <div className="mb-3 flex flex-wrap items-end gap-2"><label className="min-w-0 flex-1 basis-40 space-y-1 text-xs text-slate-500"><span>本轮接口</span><NativeSelect className="w-full" aria-label="本轮接口" value={session.providerId} disabled={executing || sessionBusy || sending} onChange={(event) => void updateSession({ providerId: event.target.value })}>{providers.filter((provider) => provider.enabled || provider.id === session.providerId).map((provider) => <NativeSelectOption key={provider.id} value={provider.id}>{provider.label}{provider.enabled ? '' : ' · 未启用'}</NativeSelectOption>)}</NativeSelect></label><label className="min-w-0 flex-1 basis-48 space-y-1 text-xs text-slate-500"><span>本轮模型</span><Input aria-label="本轮模型" list="studio-conversation-models" value={modelInput} disabled={executing || sessionBusy || sending} onChange={(event) => setModelInput(event.target.value)} placeholder={activeProvider?.adapter === 'codex_cli' ? '留空沿用 CLI 默认模型' : '选择或输入完整模型名'} /><datalist id="studio-conversation-models">{modelCatalog.map((name) => <option key={name} value={name}>{name}</option>)}</datalist></label><Button type="button" variant="outline" disabled={executing || sessionBusy || modelsLoading || sending} onClick={() => void loadConversationModels()}>{modelsLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}获取模型</Button></div>
              </details>
              <div className="rounded-2xl border border-slate-300 bg-white p-2 shadow-[0_8px_30px_rgb(15_23_42/8%)] focus-within:border-slate-500 focus-within:ring-2 focus-within:ring-slate-200">
                <Textarea
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                  aria-label={isTopic ? '选题要求' : '消息'}
                  placeholder={isTopic ? session.source || session.reference ? '补充要求（可不填），例如：二档，现代家庭，产品用甘草金桔。' : '写下产品或想法，例如：甘草金桔，想做家庭亲情题材。' : `和 ${activeSkill.name} AI 继续说…`}
                  className="min-h-20 resize-none border-0 bg-transparent text-base leading-6 shadow-none focus-visible:ring-0"
                  disabled={job?.status === 'running'}
                />
                <div className="flex flex-wrap items-center gap-2 px-1 pb-1">
                  {!isTopic && <Badge className="border-slate-200 bg-slate-50 text-slate-600"><Brain className="mr-1 size-3" />记忆 {session.pinnedMemory.length}</Badge>}
                  <span className="hidden text-xs text-slate-400 sm:inline">Ctrl + Enter 发送</span>
                  <Button className="ml-auto bg-orange-500 text-white hover:bg-orange-600" onClick={() => void sendMessage()} disabled={!(isTopic ? topicCanSend : message.trim()) || sending || executing || sessionBusy}>
                    {sending ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}
                    {isTopic && !message.trim() ? '生成选题' : isTopic && session.messages.length === 0 ? '生成选题' : '发送'}
                  </Button>
                </div>
              </div>
              {!isTopic && <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
                <span>切换模型保留当前对话</span>
                <span>{session.episode ?? '未绑定故事'}</span>
              </div>}
            </div>
          </div>
        </section>

      </div>

      <Dialog open={inspectorOpen} onOpenChange={setInspectorOpen}>
        <DialogContent className="flex h-[88vh] max-w-3xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-slate-200 px-5 py-4">
            <DialogTitle>文件与记忆</DialogTitle>
            <DialogDescription>查看当前故事、对话记忆和已生成的产物。</DialogDescription>
          </DialogHeader>
          <InspectorPanel
            session={session}
            context={context}
            activeTab={inspectorTab}
            onTabChange={setInspectorTab}
            activeDocument={activeDocument}
            onDocumentChange={setActiveDocument}
            memoryText={memoryText}
            onMemoryTextChange={setMemoryText}
            onAddMemory={() => void addMemory()}
            onMemoryAction={(action, memoryId) => void onMemoryAction(action, memoryId)}
            memoryBusy={memoryBusy}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={settingsOpen} onOpenChange={(open) => { setSettingsOpen(open); if (!open) { settingsRequest.current += 1; setProviderDraft(null); setSettingsModelsLoading(false); setShowKey(false); } }}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader><DialogTitle className="flex items-center gap-2"><Settings2 className="size-5" />API 设置</DialogTitle><DialogDescription>填写接口地址和 Key，获取模型列表后选择。Key 只保存在服务端，留空保留已有 Key。</DialogDescription></DialogHeader>
          <div className="flex gap-2"><NativeSelect aria-label="选择要编辑的接口" className="min-w-0 flex-1" value={settingsProviderId} disabled={providerSaving} onChange={(event) => chooseSettingsProvider(event.target.value)}>{providers.map((provider) => <NativeSelectOption key={provider.id} value={provider.id}>{provider.label}</NativeSelectOption>)}{!providers.some((provider) => provider.id === settingsProviderId) && <NativeSelectOption value={settingsProviderId}>{providerDraft?.label || '新接口'}</NativeSelectOption>}</NativeSelect><Button variant="outline" disabled={providerSaving} onClick={newProviderDraft}><Plus className="size-4" />添加接口</Button></div>
          {providerDraft && <fieldset disabled={providerSaving} className="space-y-4">
            <label htmlFor="provider-label" className="block space-y-1.5 text-sm"><span>接口名称</span><Input id="provider-label" value={providerDraft.label} onChange={(event) => patchProvider({ label: event.target.value })} /></label>
            {providerDraft.adapter !== 'codex_cli' && <>
              <label htmlFor="provider-base-url" className="block space-y-1.5 text-sm"><span>API 地址</span><Input id="provider-base-url" value={providerDraft.baseUrl} onChange={(event) => patchProvider({ baseUrl: event.target.value })} placeholder="https://你的接口地址/v1" /></label>
              <label htmlFor="provider-key" className="block space-y-1.5 text-sm"><span>API Key</span><div className="flex gap-2"><Input id="provider-key" type={showKey ? 'text' : 'password'} autoComplete="new-password" value={providerDraft.apiKey || ''} onChange={(event) => patchProvider({ apiKey: event.target.value, apiKeyEnv: event.target.value ? '' : providerDraft.apiKeyEnv })} placeholder={providerDraft.secretStored ? '已有 Key，留空保留' : '填写供应商提供的 Key'} /><Button type="button" variant="outline" onClick={() => setShowKey((value) => !value)}>{showKey ? '隐藏' : '显示'}</Button></div></label>
            </>}
            <label htmlFor="provider-model" className="block space-y-1.5 text-sm"><span>默认模型</span><Input id="provider-model" list="studio-settings-models" value={providerDraft.model} onChange={(event) => patchProvider({ model: event.target.value })} placeholder={providerDraft.adapter === 'codex_cli' ? '留空沿用 CLI 当前默认模型' : '从列表选择，也可手动输入完整模型名'} /><datalist id="studio-settings-models">{settingsModels.map((name) => <option key={name} value={name}>{name}</option>)}</datalist></label>
            <Button type="button" variant="outline" disabled={settingsModelsLoading || providerSaving} onClick={() => void loadDraftModels()}>{settingsModelsLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}获取模型列表</Button>
            <details className="rounded-lg border border-slate-200 p-3"><summary className="cursor-pointer text-sm text-slate-600">高级设置</summary><div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label htmlFor="provider-adapter" className="space-y-1.5 text-sm"><span>接口协议</span><NativeSelect id="provider-adapter" className="w-full" value={providerDraft.adapter} onChange={(event) => { const adapter = event.target.value as StudioProvider['adapter']; patchProvider({ adapter, endpoint: ({ codex_cli: '', openai_compatible_chat: '/chat/completions', openai_responses: '/responses', custom_webhook: '/' })[adapter] }); }}><NativeSelectOption value="openai_compatible_chat">Chat 兼容接口</NativeSelectOption><NativeSelectOption value="openai_responses">Responses 接口</NativeSelectOption><NativeSelectOption value="custom_webhook">自定义 Webhook</NativeSelectOption><NativeSelectOption value="codex_cli">本机 Codex CLI</NativeSelectOption></NativeSelect></label>
              <label htmlFor="provider-endpoint" className="space-y-1.5 text-sm"><span>生成路径</span><Input id="provider-endpoint" value={providerDraft.endpoint} onChange={(event) => patchProvider({ endpoint: event.target.value })} /></label>
              <label htmlFor="provider-models-endpoint" className="space-y-1.5 text-sm"><span>模型目录路径</span><Input id="provider-models-endpoint" value={providerDraft.modelsEndpoint || '/models'} onChange={(event) => patchProvider({ modelsEndpoint: event.target.value })} /></label>
              <label htmlFor="provider-key-env" className="space-y-1.5 text-sm"><span>密钥环境变量（可选）</span><Input id="provider-key-env" value={providerDraft.apiKeyEnv} onChange={(event) => patchProvider({ apiKeyEnv: event.target.value })} placeholder="已填 Key 时可留空" /></label>
              <label htmlFor="provider-structured" className="flex items-start gap-2 text-sm"><Checkbox id="provider-structured" checked={providerDraft.structuredOutputs} onCheckedChange={(value) => patchProvider({ structuredOutputs: value === true })} /><span>接口支持严格 JSON 输出</span></label>
              <label htmlFor="provider-clear-key" className="flex items-start gap-2 text-sm"><Checkbox id="provider-clear-key" checked={Boolean(providerDraft.clearKey)} onCheckedChange={(value) => patchProvider({ clearKey: value === true, ...(value === true ? { apiKey: '' } : {}) })} /><span>清除已保存的 Key</span></label>
              <label htmlFor="provider-enabled" className="flex items-start gap-2 text-sm"><Checkbox id="provider-enabled" checked={providerDraft.enabled} onCheckedChange={(value) => patchProvider({ enabled: value === true })} /><span>启用这个接口</span></label>
            </div></details>
          </fieldset>}
          {settingsNotice && <output aria-live="polite" className="rounded-lg bg-slate-100 p-3 text-sm text-slate-700">{settingsNotice}</output>}
          <DialogFooter><Button variant="outline" onClick={() => { settingsRequest.current += 1; setSettingsOpen(false); setProviderDraft(null); setShowKey(false); }}>取消</Button><Button onClick={() => void saveProvider()} disabled={!providerDraft || providerSaving || settingsModelsLoading}>{providerSaving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}保存并用于当前会话</Button></DialogFooter>
        </DialogContent>
      </Dialog>


    </main>
  );
};
