'use client';

import { useEffect, useState } from 'react';
import { BookOpen, LoaderCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { request } from '@/app/assets/model-settings';

type LibraryEntry = { path: string; name: string; modifiedAt: string; size: number; readable: boolean; reusable: boolean };
type LibraryGroup = { id: string; label: string; purpose: string; count: number; entries: LibraryEntry[] };

export function LibraryPanel({ onUse, disabled, refreshKey }: { onUse: (entry: LibraryEntry) => Promise<void>; disabled: boolean; refreshKey?: string | null }) {
  const [groups, setGroups] = useState<LibraryGroup[]>([]);
  const [category, setCategory] = useState('originals');
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [document, setDocument] = useState<{ name: string; content: string } | null>(null);
  useEffect(() => {
    let current = true;
    void request<{ groups: LibraryGroup[] }>('/api/studio/library').then((result) => { if (current) { setGroups(result.groups); setError(''); } }).catch((reason) => { if (current) setError(reason.message); }).finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, [refresh, refreshKey]);
  const selected = groups.find((group) => group.id === category);
  const read = async (entry: LibraryEntry) => {
    setBusy(true); setError('');
    try { setDocument(await request<{ name: string; content: string }>(`/api/studio/library/document?path=${encodeURIComponent(entry.path)}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '资料没有打开。'); }
    finally { setBusy(false); }
  };
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-2"><NativeSelect aria-label="文案库类别" value={category} onChange={(event) => { setCategory(event.target.value); setDocument(null); }} className="min-w-0 flex-1">{groups.map((group) => <NativeSelectOption key={group.id} value={group.id}>{group.label} · {group.count} 份文件</NativeSelectOption>)}</NativeSelect><Button type="button" variant="outline" size="icon" aria-label="刷新文案库" disabled={busy} onClick={() => { setDocument(null); setRefresh((value) => value + 1); }}><RefreshCw className={`size-4 ${busy ? 'animate-spin' : ''}`} /></Button></div>
    {selected && <p className="text-sm leading-6 text-slate-500">{selected.purpose}</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {document ? <section className="space-y-3"><div className="flex items-center gap-2"><h3 className="min-w-0 flex-1 break-all text-base font-medium">{document.name}</h3><Button variant="outline" onClick={() => setDocument(null)}>返回列表</Button></div><pre className="whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-50 p-4 font-sans text-sm leading-7">{document.content}</pre></section> : <div className="space-y-2">{selected?.entries.map((entry) => <article key={entry.path} className="rounded-xl border border-slate-200 bg-white p-3"><div className="flex flex-wrap items-start gap-2"><BookOpen className="mt-1 size-4 shrink-0 text-slate-400" /><div className="min-w-0 flex-1"><h3 className="break-all text-sm font-medium text-slate-800">{entry.name}</h3><p className="mt-1 text-xs text-slate-500">{new Date(entry.modifiedAt).toLocaleDateString('zh-CN')} · {(entry.size / 1024).toFixed(1)} KB</p></div></div><div className="mt-3 flex flex-wrap items-center gap-2">{entry.readable ? <Button size="sm" variant="outline" disabled={busy} onClick={() => void read(entry)}>查看</Button> : <span className="text-xs text-slate-500">含内部规范，后台按原流程调用</span>}{entry.reusable && <Button size="sm" className="ml-auto bg-orange-500 text-white hover:bg-orange-600" disabled={disabled || busy} onClick={() => void onUse(entry)}>用于选题</Button>}</div></article>)}{!busy && !selected?.entries.length && <p className="py-8 text-center text-sm text-slate-500">这个类别还没有资料。</p>}{busy && <div className="flex justify-center py-3"><LoaderCircle className="size-5 animate-spin text-slate-400" /></div>}</div>}
  </div>;
}
