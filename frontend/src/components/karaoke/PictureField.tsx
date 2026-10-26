// A picture for the subtitles (a singer's, one at the ends of every line): its thumbnail, upload, remove.

import { ImagePlus, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/format';
import { run } from '@/store/app';
import { Spinner, Tip } from '@/components/ui';

export const pictureUrl = (id: string) => `/api/karaoke/pictures/${id}`;

export function PictureField({ value, onChange, label, size = 'md' }: {
  /** the picture's id ('' = none) */
  value: string;
  onChange: (id: string) => void;
  /** what it is, for screen readers and the tooltip (e.g. "天海春香的头像") */
  label: string;
  size?: 'sm' | 'md';
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const upload = (file: File) => run(async () => {
    setBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const r = await api.post<{ id: string }>('/api/karaoke/pictures', form);
      onChange(r.id);
    } finally {
      setBusy(false);
    }
  }, '图片上传失败');
  const box = size === 'sm' ? 'size-8' : 'size-11';
  return (
    <span className="inline-flex items-center gap-1">
      <Tip content={value ? `${label}：点击更换` : `${label}：点击选择图片（PNG、JPEG、WebP）`}>
        <button type="button" aria-label={value ? `更换${label}` : `选择${label}`} disabled={busy}
          onClick={() => input.current?.click()}
          className={cn('focus-ring grid shrink-0 place-items-center overflow-hidden rounded-lg border transition', box,
            value ? 'border-line-strong bg-surface-2 hover:border-accent' : 'border-dashed border-line-strong text-subtle hover:border-accent hover:text-accent')}>
          {busy ? <Spinner className="size-4" />
            : value ? <img src={pictureUrl(value)} alt="" className="size-full object-contain" />
              : <ImagePlus className="size-4" />}
        </button>
      </Tip>
      {value && (
        <button type="button" aria-label={`移除${label}`} onClick={() => onChange('')}
          className="focus-ring rounded-md p-0.5 text-muted hover:bg-surface-2 hover:text-fg">
          <X className="size-3.5" />
        </button>
      )}
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="hidden" aria-hidden tabIndex={-1}
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void upload(f); }} />
    </span>
  );
}
