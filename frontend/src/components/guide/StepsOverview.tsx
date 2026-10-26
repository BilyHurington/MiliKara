// The steps of the detailed mode at a glance: which are needed, which may be skipped.  A card above the
// steps the first time, then behind the "?" beside “工作流程”.

import * as Popover from '@radix-ui/react-popover';
import { CircleHelp } from 'lucide-react';
import { useState } from 'react';
import { STEP_GUIDE } from '@/lib/guide';
import { cn } from '@/lib/format';
import { setStep, STEPS } from '@/store/app';
import { Badge, Button } from '@/components/ui';
import { dismiss, resetTips, useDismissed } from './Help';

function Steps({ onPick }: { onPick?: () => void }) {
  return (
    <ol className="space-y-1.5">
      {STEPS.map((st, i) => {
        const g = STEP_GUIDE[st.id];
        return (
          <li key={st.id}>
            <button type="button" onClick={() => { setStep(st.id); onPick?.(); }}
              className="focus-ring flex w-full items-start gap-2 rounded-lg px-2 py-1 text-left hover:bg-surface-2">
              <span className="tabular w-4 shrink-0 pt-0.5 text-right text-xs text-subtle">{i + 1}</span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-[13px] font-medium">{st.label}
                  <Badge tone={g.tone}>{g.when}</Badge></span>
                <span className="block text-xs text-muted">{g.what}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/** The "?" beside “工作流程”. */
export function StepsHelp() {
  const [open, setOpen] = useState(false);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button type="button" aria-label="每一步做什么"
          className="focus-ring inline-grid size-5 place-items-center rounded-full text-subtle hover:text-accent data-[state=open]:text-accent">
          <CircleHelp className="size-3.5" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="right" align="start" sideOffset={8} collisionPadding={12} aria-label="每一步做什么"
          className="z-50 w-80 animate-in rounded-xl border border-line bg-surface p-3 shadow-[var(--shadow-pop)]">
          <p className="mb-2 px-2 text-xs text-muted">大多数歌：音频与歌词 →（注音）→ 对齐 → 卡拉OK字幕。每一步都可以回头修改；点一步就跳过去。</p>
          <Steps onPick={() => setOpen(false)} />
          <div className="mt-2 border-t border-line px-2 pt-2">
            <button type="button" onClick={resetTips} className="focus-ring rounded text-xs text-accent hover:underline">重新显示使用提示</button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** The card above the steps, the first time the detailed mode is used. */
export function StepsOverviewCard({ className }: { className?: string }) {
  const gone = useDismissed('pro.overview');
  if (gone) return null;
  return (
    <div className={cn('mb-3 rounded-xl border border-accent/25 bg-accent-soft/50 p-3', className)}>
      <div className="text-[13px] font-semibold">第一次用详细模式？</div>
      <p className="mt-1 text-xs leading-5 text-muted">
        下面是全部步骤，每一步都可以回头修改。大多数歌：<b className="text-fg">音频与歌词 →（注音）→ 对齐 → 卡拉OK字幕</b>，
        其余按需要。“工作流程”旁的 ? 随时可以再看每一步做什么。
      </p>
      <Button size="xs" variant="secondary" className="mt-2" onClick={() => dismiss('pro.overview')}>知道了</Button>
    </div>
  );
}
