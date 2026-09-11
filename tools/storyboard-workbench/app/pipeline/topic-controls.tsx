'use client';

import { useEffect, useState } from 'react';
import { FileText, LoaderCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { request } from '@/app/assets/model-settings';

type Entry = { path: string; name: string; reusable: boolean };
type TopicSession = {
  routeId: string;
  source?: { title: string } | null;
  reference?: { path: string; name: string } | null;
};

export function TopicControls({ session, disabled, refreshKey, onChange, onImport }: {
  session: TopicSession;
  disabled: boolean;
  refreshKey?: string | null;
  onChange: (patch: Record<string, unknown>) => Promise<unknown>;
  onImport: () => void;
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    void request<{ groups: Array<{ id: string; entries: Entry[] }> }>('/api/studio/library')
      .then(({ groups }) => {
        if (cancelled) return;
        setEntries((groups.find((group) => group.id === 'originals')?.entries ?? []).filter((entry) => entry.reusable).sort((a, b) => b.name.localeCompare(a.name, 'zh-CN', { numeric: true })));
        setError('');
      })
      .catch(() => { if (!cancelled) setError('对标列表暂时没加载出来。'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshKey, attempt]);

  const benchmark = Boolean(session.source || session.reference || session.routeId.startsWith('benchmark-tier-'));
  const tier = session.routeId.startsWith('benchmark-tier-') ? session.routeId : 'benchmark-tier-1';
  const value = session.reference?.path ?? (session.source ? 'uploaded' : '');
  return (
    <div className="mx-auto flex max-w-4xl flex-wrap items-end gap-3">
      <label className="min-w-0 flex-1 basis-64 space-y-1.5 text-sm text-slate-600">
        <span>对标</span>
        <NativeSelect aria-label="选择对标" className="w-full" value={value} disabled={disabled || loading} onChange={(event) => {
          const selected = event.target.value;
          if (selected === 'uploaded') return;
          void onChange({ referencePath: selected || null, clearSource: true, routeId: selected ? tier : 'original-commerce' });
        }}>
          <NativeSelectOption value="">{loading ? '正在加载对标…' : '不选对标，做原创'}</NativeSelectOption>
          {session.source && !session.reference && <NativeSelectOption value="uploaded">{session.source.title}（已导入）</NativeSelectOption>}
          {session.reference && !entries.some((entry) => entry.path === session.reference?.path) && <NativeSelectOption value={session.reference.path}>{session.reference.name}</NativeSelectOption>}
          {entries.map((entry) => <NativeSelectOption key={entry.path} value={entry.path}>{entry.name}</NativeSelectOption>)}
        </NativeSelect>
      </label>
      <Button variant="outline" disabled={disabled} onClick={onImport}><FileText className="size-4" />{session.source ? '查看原稿' : '导入原稿'}</Button>
      <label className="space-y-1.5 text-sm text-slate-600">
        <span>{benchmark ? '改编幅度' : '内容类型'}</span>
        <NativeSelect aria-label={benchmark ? '改编幅度' : '内容类型'} value={benchmark ? tier : session.routeId} disabled={disabled} onChange={(event) => void onChange({ routeId: event.target.value })}>
          {benchmark ? <>
            <NativeSelectOption value="benchmark-tier-1">一档 · 换人物场景</NativeSelectOption>
            <NativeSelectOption value="benchmark-tier-2">二档 · 换讲法</NativeSelectOption>
            <NativeSelectOption value="benchmark-tier-3">三档 · 借灵感原创</NativeSelectOption>
          </> : <>
            <NativeSelectOption value="original-commerce">带货</NativeSelectOption>
            <NativeSelectOption value="original-traffic">流量</NativeSelectOption>
          </>}
        </NativeSelect>
      </label>
      {error && <p role="alert" className="basis-full text-sm text-red-700">{error}<Button size="sm" variant="ghost" disabled={loading} onClick={() => { setLoading(true); setAttempt((current) => current + 1); }}>{loading && <LoaderCircle className="size-4 animate-spin" />}重新加载</Button></p>}
    </div>
  );
}
