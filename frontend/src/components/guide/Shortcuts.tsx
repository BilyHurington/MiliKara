// The keyboard shortcuts of the detailed mode: the ones every page has and the current page's own.
// Opened with "?" or the keyboard button in the top bar.

import { Keyboard } from 'lucide-react';
import { useSyncExternalStore } from 'react';
import { SHORTCUTS, type ShortcutGroup } from '@/lib/guide';
import { MOD_KEY, SHIFT_KEY } from '@/lib/keys';
import { useApp, type Step } from '@/store/app';
import { Button, Dialog, Kbd, Tip } from '@/components/ui';
import { resetTips } from './Help';

const PAGE_GROUP: Partial<Record<Step, ShortcutGroup>> = { calibrate: 'calibrate', review: 'review', singers: 'singers' };

let open = false;
const listeners = new Set<() => void>();
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
export function setShortcutsOpen(v: boolean) {
  open = v;
  for (const l of listeners) l();
}

/** The key that opens the sheet: "?" as typed (whatever the keyboard layout), not while typing. */
export const isShortcutsKey = (e: KeyboardEvent) => e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey;

const keyName = (k: string) => (k === 'Mod' ? MOD_KEY : k === 'Shift' ? SHIFT_KEY : k);

export function ShortcutsDialog() {
  const isOpen = useSyncExternalStore(subscribe, () => open);
  const step = useApp((s) => s.step);
  const page = PAGE_GROUP[step];
  const groups: ShortcutGroup[] = page ? [page, 'global'] : ['global'];
  return (
    <Dialog open={isOpen} onOpenChange={setShortcutsOpen} title="快捷键" description="输入框、对话框里打字时不会触发"
      footer={<Button size="sm" variant="ghost" onClick={() => { resetTips(); setShortcutsOpen(false); }}>重新显示使用提示</Button>}>
      <div className="space-y-5">
        {groups.map((g) => (
          <section key={g}>
            <h3 className="mb-2 text-xs font-semibold text-muted">{SHORTCUTS[g].title}</h3>
            <dl className="space-y-1.5 text-[13px]">
              {SHORTCUTS[g].keys.map(([keys, what]) => (
                <div key={what} className="flex items-baseline gap-3">
                  <dt className="flex w-36 shrink-0 flex-wrap items-center gap-1">
                    {keys.map((k, i) => <span key={k} className="contents">{i > 0 && <span className="text-subtle">+</span>}<Kbd>{keyName(k)}</Kbd></span>)}
                  </dt>
                  <dd className="text-fg/85">{what}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Dialog>
  );
}

export function ShortcutsButton() {
  return (
    <Tip content={<span>快捷键 <Kbd>?</Kbd></span>}>
      <button type="button" aria-label="快捷键" onClick={() => setShortcutsOpen(true)}
        className="focus-ring grid size-8 place-items-center rounded-lg text-muted transition hover:bg-surface-2 hover:text-fg">
        <Keyboard className="size-4" />
      </button>
    </Tip>
  );
}
