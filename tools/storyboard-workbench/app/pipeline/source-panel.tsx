'use client';

import { useEffect, useRef, useState } from 'react';
import { FileUp, FileText, LoaderCircle, Save, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { request } from '@/app/assets/model-settings';

export type SourceInfo = { id: string; filename: string; title: string; hash: string; size: number; encoding: string; edited: boolean; text?: string };

export function SourcePanel({ sessionId, sourceInfo, disabled, onChanged, onClear, onStart }: {
  sessionId: string;
  sourceInfo: SourceInfo | null;
  disabled: boolean;
  onChanged: () => Promise<void>;
  onClear: () => Promise<unknown>;
  onStart?: () => void;
}) {
  const [source, setSource] = useState<SourceInfo | null>(null);
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const operation = useRef(0);
  useEffect(() => {
    const token = ++operation.current;
    if (!sourceInfo?.id) return;
    void request<{ source: SourceInfo }>(`/api/studio/source?sessionId=${encodeURIComponent(sessionId)}`).then((result) => {
      if (token !== operation.current) return;
      setSource(result.source); setText(result.source.text ?? ''); setTitle(result.source.title); setError('');
    }).catch((reason) => { if (token === operation.current) setError(reason.message); });
    return () => { operation.current += 1; };
  }, [sessionId, sourceInfo?.id, sourceInfo?.hash]);
  const dirty = Boolean(source && (text !== source.text || title !== source.title));
  const upload = async (files: FileList | File[]) => {
    if (busy || disabled) return;
    if (files.length !== 1) { setError('每次请导入一份 TXT 或 MD 原稿。'); return; }
    const file = files[0];
    if (!/\.(txt|md)$/i.test(file.name)) { setError('仅支持 TXT 和 MD 文件。'); return; }
    if (file.size > 2 * 1024 * 1024 || !file.size) { setError('文件为空或超过 2 MB，未导入。'); return; }
    if (dirty) { setError('请先保存预览中的修改，再更换文件。'); return; }
    const token = operation.current;
    setBusy(true); setError('');
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 16384) binary += String.fromCharCode(...bytes.subarray(offset, offset + 16384));
      await request('/api/studio/source', { sessionId, filename: file.name, base64: btoa(binary) });
      if (token === operation.current) await onChanged();
    } catch (reason) { if (token === operation.current) setError(reason instanceof Error ? reason.message : '文件没有导入。'); }
    finally { setBusy(false); if (input.current) input.current.value = ''; }
  };
  const save = async () => {
    if (!source || busy) return;
    setBusy(true); setError('');
    try {
      await request('/api/studio/source', { sessionId, sourceId: source.id, expectedHash: source.hash, title, text });
      await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '原稿未保存。'); }
    finally { setBusy(false); }
  };
  return (
    <section aria-label="对标原稿" className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
      <div className={`flex min-h-36 flex-wrap items-center gap-4 rounded-xl border-2 border-dashed p-6 ${dragging ? 'border-orange-400 bg-orange-50' : 'border-slate-300 bg-slate-50'}`}
        onDragOver={(event) => { event.preventDefault(); if (!disabled && !busy) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files); }}>
        <FileUp className="size-8 shrink-0 text-orange-500" />
        <div className="min-w-0 flex-1"><p className="text-base font-medium text-slate-800">把 TXT / MD 文件拖到这里</p><p className="mt-2 text-sm text-slate-500">每次一份，最多 2 MB；导入后可预览和修改。</p></div>
        <input ref={input} type="file" accept=".txt,.md,text/plain,text/markdown" className="hidden" aria-label="选择原稿文件" onChange={(event) => { if (event.target.files?.length) void upload(event.target.files); }} />
        <Button type="button" variant="outline" disabled={disabled || busy} onClick={() => input.current?.click()}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <FileText className="size-4" />}选择文件</Button>
      </div>
      {source && <>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500"><FileText className="size-3.5" /><span className="break-all">{source.filename}</span><span>{source.encoding} · {Array.from(text).length} 字符</span><Button type="button" size="icon" variant="ghost" className="ml-auto size-7" aria-label="取消本轮原稿引用" disabled={disabled || busy} onClick={() => void onClear()}><X className="size-3.5" /></Button></div>
        <label htmlFor="studio-source-title" className="block space-y-1.5 text-sm text-slate-700"><span>对标名称</span><Input id="studio-source-title" value={title} disabled={disabled || busy} onChange={(event) => setTitle(event.target.value)} /></label>
        <label htmlFor="studio-source-text" className="block space-y-1.5 text-sm text-slate-700"><span>原稿预览</span><Textarea id="studio-source-text" rows={5} value={text} disabled={disabled || busy} onChange={(event) => setText(event.target.value)} className="max-h-72 resize-y text-base leading-7" /></label>
        <div className="flex flex-wrap items-center gap-2"><span className="mr-auto text-xs text-slate-500">{dirty ? '有修改尚未保存' : '原文件与输入版本已保留'}</span><Button type="button" variant="outline" disabled={disabled || busy || !dirty} onClick={() => void save()}><Save className="size-4" />保存修改</Button>{onStart && <Button type="button" className="bg-orange-500 text-white hover:bg-orange-600" disabled={disabled || busy || dirty} onClick={onStart}>准备拆解入库</Button>}</div>
      </>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  );
}
