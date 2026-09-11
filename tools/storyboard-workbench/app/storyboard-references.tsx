'use client';

import { ChevronLeft, ChevronRight, ImageOff, Images, LoaderCircle, Maximize2, Pencil } from 'lucide-react';
import { Fragment, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';

export type ReferenceImage = {
  number: number;
  reference: string;
  assetId: string;
  name: string;
  detail: string;
  filename: string | null;
  status: 'ready' | 'missing' | 'ambiguous';
  message: string;
  thumbnailUrl?: string;
  imageUrl?: string;
};

type GalleryProps = {
  images: ReferenceImage[];
  error?: string;
  apiBase: string;
  onPreview: (index: number) => void;
};

function ReferenceThumbnail({ item, apiBase, onClick }: { item: ReferenceImage; apiBase: string; onClick: () => void }) {
  const [failed, setFailed] = useState(false);
  const available = item.status === 'ready' && Boolean(item.thumbnailUrl);
  return (
    <button
      type="button"
      onClick={onClick}
      className="group min-w-0 overflow-hidden rounded-lg border border-border bg-card text-left transition hover:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      aria-label={`查看图片${item.number}：${item.name || item.reference}${available ? '' : '（需核对）'}`}
    >
      <span className="relative flex h-32 w-full items-center justify-center bg-muted">
        {available && !failed ? (
          <>
            {/* oxlint-disable-next-line next/no-img-element -- The local API already supplies cached thumbnails; no hosted optimizer can access these files. */}
            <img
              src={`${apiBase}${item.thumbnailUrl}`}
              alt={`${item.name} ${item.detail}`}
              loading="lazy"
              decoding="async"
              width={240}
              height={256}
              onError={() => setFailed(true)}
              className="h-full w-full object-contain"
            />
            <span className="absolute right-1.5 bottom-1.5 rounded bg-card/90 p-1 text-muted-foreground"><Maximize2 className="size-3.5" /></span>
          </>
        ) : (
          <span className="flex flex-col items-center gap-2 px-2 text-center text-xs text-warning">
            <ImageOff className="size-6" />
            {failed ? '缩略图未加载，点击看大图' : item.status === 'ambiguous' ? '匹配不确定' : '未找到图片'}
          </span>
        )}
      </span>
      <span className="block p-2">
        <span className="block break-words text-sm font-medium text-foreground">图片{item.number} · {item.name || item.assetId || '待核对'}</span>
        <span className="mt-1 block break-words text-xs leading-5 text-muted-foreground">{[item.assetId, item.detail].filter(Boolean).join(' · ') || item.message}</span>
      </span>
    </button>
  );
}

export function ReferenceGallery({ images, error, apiBase, onPreview }: GalleryProps) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? images : images.slice(0, 4);
  const problems = images.filter((item) => item.status !== 'ready').length;
  return (
    <section className="min-w-0" aria-label="当前单元参考图">
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        <Images className="size-3.5" />
        <span>{images.length} 张 · 按上传顺序</span>
        {problems > 0 && <span className="text-warning">{problems} 张需核对</span>}
      </div>
      {error && <p className="mb-2 rounded-lg border border-warning-border bg-warning-soft p-3 text-sm text-warning" role="alert">{error}</p>}
      {!images.length && !error && <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">本单元没有参考图挂载。</p>}
      <div className="grid grid-cols-2 gap-2">
        {visible.map((item, index) => <ReferenceThumbnail key={`${item.number}-${item.thumbnailUrl || item.reference}`} item={item} apiBase={apiBase} onClick={() => onPreview(index)} />)}
      </div>
      {images.length > 4 && (
        <Button variant="ghost" size="sm" className="mt-2 w-full" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
          {expanded ? '收起参考图' : `展开其余 ${images.length - 4} 张`}
        </Button>
      )}
    </section>
  );
}

export function StoryboardPrompt({ value, dirty, expanded, readOnly, unit, images, onChange, onPreview }: {
  value: string;
  dirty: boolean;
  expanded: boolean;
  readOnly: boolean;
  unit: number;
  images: ReferenceImage[];
  onChange: (value: string) => void;
  onPreview: (index: number) => void;
}) {
  const [mode, setMode] = useState<'auto' | 'edit' | 'preview'>('auto');
  const editing = mode === 'edit' || (mode === 'auto' && dirty);
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{editing ? '编辑提示词' : '点图片编号可看对应参考图'}</span>
        <Button type="button" variant="ghost" size="sm" disabled={readOnly} onClick={() => setMode(editing ? 'preview' : 'edit')}>
          {editing ? <Images className="size-3.5" /> : <Pencil className="size-3.5" />}
          {editing ? '预览提示词' : '编辑提示词'}
        </Button>
      </div>
      {editing ? (
        <Textarea
          value={value}
          readOnly={readOnly}
          aria-label={`编辑单元${unit}提示词`}
          onChange={(event) => onChange(event.target.value)}
          className={`resize-none border-border bg-muted/70 px-3 py-2.5 leading-6 shadow-none focus-visible:border-primary focus-visible:bg-card focus-visible:ring-ring/10 ${expanded ? 'h-[360px]' : 'h-[188px]'}`}
        />
      ) : (
        <div className={`overflow-y-auto rounded-lg border border-border bg-muted/70 px-3 py-2.5 text-sm leading-6 whitespace-pre-wrap break-words ${expanded ? 'h-[360px]' : 'h-[188px]'}`} aria-label={`单元${unit}提示词预览`}>
          {value.split(/(图片[ \t]*\d+)/g).map((part, index) => {
            const match = part.match(/^图片[ \t]*(\d+)$/);
            const imageIndex = match ? images.findIndex((item) => item.number === Number(match[1])) : -1;
            return match && imageIndex >= 0 ? (
              <button type="button" key={index} className="rounded bg-accent px-0.5 font-medium text-primary underline decoration-primary/50 underline-offset-2 hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2" onClick={() => onPreview(imageIndex)} aria-label={`查看单元${unit}的${part}`}>{part}</button>
            ) : <Fragment key={index}>{part}</Fragment>;
          })}
        </div>
      )}
    </div>
  );
}

function FullReferenceImage({ item, apiBase }: { item: ReferenceImage; apiBase: string }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  if (item.status !== 'ready' || !item.imageUrl || failed) return (
    <div className="flex min-h-60 flex-col items-center justify-center gap-3 rounded-lg bg-muted p-6 text-center text-warning" role="alert">
      <ImageOff className="size-8" />
      <p>{failed ? '图片未能读取，请刷新工作台后重试。' : item.message || '参考图无法匹配，请核对挂载文件。'}</p>
    </div>
  );
  return (
    <div className="relative flex min-h-60 items-center justify-center rounded-lg bg-muted">
      {!loaded && <span className="absolute flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-5 animate-spin" />正在加载大图</span>}
      {/* oxlint-disable-next-line next/no-img-element -- This viewer intentionally requests the original from the local API only when opened. */}
      <img src={`${apiBase}${item.imageUrl}`} alt={`${item.name} ${item.detail}`} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} className={`max-h-[65vh] w-full object-contain ${loaded ? '' : 'invisible'}`} />
    </div>
  );
}

export function ReferenceViewer({ selection, images, apiBase, onChange }: {
  selection: { unit: number; index: number } | null;
  images: ReferenceImage[];
  apiBase: string;
  onChange: (selection: { unit: number; index: number } | null) => void;
}) {
  const item = selection ? images[selection.index] : null;
  const move = (direction: number) => {
    if (selection && images.length > 1) onChange({ ...selection, index: (selection.index + images.length + direction) % images.length });
  };
  return (
    <Dialog open={Boolean(selection && item)} onOpenChange={(open) => { if (!open) onChange(null); }}>
      <DialogContent className="storyboard-theme dark max-h-[94vh] overflow-y-auto sm:max-w-5xl" onKeyDown={(event) => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); move(event.key === 'ArrowLeft' ? -1 : 1); }
      }}>
        <DialogHeader>
          <DialogTitle>U{selection?.unit} · 图片{item?.number} · {item?.name}</DialogTitle>
          <DialogDescription>{[item?.assetId, item?.detail].filter(Boolean).join(' · ') || '当前单元的参考图'}</DialogDescription>
        </DialogHeader>
        {item && <FullReferenceImage key={`${item.imageUrl || item.reference}-${item.number}`} item={item} apiBase={apiBase} />}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Button variant="outline" onClick={() => move(-1)} disabled={images.length < 2}><ChevronLeft />上一张</Button>
          <span className="text-sm text-muted-foreground" aria-live="polite">{selection ? selection.index + 1 : 0} / {images.length}</span>
          <Button variant="outline" onClick={() => move(1)} disabled={images.length < 2}>下一张<ChevronRight /></Button>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-2 text-xs text-muted-foreground">
          <p className="min-w-0 flex-1 break-all">{item?.filename || item?.reference}</p>
          {item?.imageUrl && <a className="shrink-0 text-primary underline underline-offset-2" href={`${apiBase}${item.imageUrl}`} target="_blank" rel="noreferrer">打开原图</a>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
