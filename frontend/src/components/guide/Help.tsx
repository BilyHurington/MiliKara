// The "?" beside a card or page (a few points from lib/guide.ts and a link to the tutorial) and the
// tips shown the first time somewhere is opened (dismissed for good, until shown again from the settings).

import * as Popover from '@radix-ui/react-popover';
import { CircleHelp, ExternalLink, Lightbulb, X } from 'lucide-react';
import { useSyncExternalStore, type ReactNode } from 'react';
import { cn } from '@/lib/format';
import { HELP, TIPS, TUTORIAL, type HelpKey, type TipKey } from '@/lib/guide';

export function HelpButton({ k, className }: { k: HelpKey; className?: string }) {
  const h = HELP[k];
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" aria-label={`说明：${h.title}`}
          className={cn('focus-ring inline-grid size-6 shrink-0 place-items-center rounded-full text-subtle transition hover:bg-surface-2 hover:text-accent data-[state=open]:text-accent', className)}>
          <CircleHelp className="size-4" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="start" sideOffset={6} collisionPadding={12} aria-label={h.title}
          className="z-50 w-[22rem] max-w-[calc(100vw-2rem)] animate-in rounded-xl border border-line bg-surface p-4 text-[13px] leading-5 shadow-[var(--shadow-pop)]">
          <div className="mb-2 font-semibold">{h.title}</div>
          <ul className="space-y-1.5 text-fg/85">
            {h.points.map((p) => (
              <li key={p} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-accent" aria-hidden />{p}</li>
            ))}
          </ul>
          {h.doc && (
            <a href={`${TUTORIAL}${h.doc}`} target="_blank" rel="noreferrer"
              className="focus-ring mt-3 inline-flex items-center gap-1 rounded text-xs font-medium text-accent hover:underline">
              查看教程里的完整说明<ExternalLink className="size-3" />
            </a>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

// ------------------------------------------------------------------ first-time tips

const KEY = 'kara.guide.dismissed';
const listeners = new Set<() => void>();
let dismissed: Set<string> = load();

function load(): Set<string> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    return new Set(Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);
  } catch {
    return new Set();
  }
}

function save() {
  try { localStorage.setItem(KEY, JSON.stringify([...dismissed])); } catch { /* private window: this session only */ }
  for (const l of listeners) l();
}

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };

/** Whether a tip (or the steps overview) has been dismissed. */
export function useDismissed(key: string): boolean {
  return useSyncExternalStore(subscribe, () => dismissed.has(key));
}

export function dismiss(key: string) {
  if (dismissed.has(key)) return;
  dismissed = new Set(dismissed).add(key);
  save();
}

/** Every tip shows again (“重新显示使用提示”). */
export function resetTips() {
  dismissed = new Set();
  save();
}

/** A tip shown until it is dismissed. */
export function FirstTip({ k, className, children }: { k: TipKey; className?: string; children?: ReactNode }) {
  const gone = useDismissed(k);
  if (gone) return null;
  const t = TIPS[k];
  return (
    <div role="note" aria-label={t.title} className={cn('flex gap-3 rounded-xl border border-accent/25 bg-accent-soft/60 px-4 py-3 text-[13px] leading-5', className)}>
      <Lightbulb className="mt-0.5 size-4 shrink-0 text-accent" />
      <div className="min-w-0 flex-1">
        <div className="font-semibold">{t.title}</div>
        <p className="mt-0.5 text-fg/80">{t.body}</p>
        {children}
      </div>
      <button type="button" onClick={() => dismiss(k)} aria-label={`知道了，不再显示“${t.title}”`}
        className="focus-ring -mt-1 -mr-2 flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-xs text-muted hover:bg-surface hover:text-fg">
        知道了<X className="size-3.5" />
      </button>
    </div>
  );
}
