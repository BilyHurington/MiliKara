// Left column: all lines of the result with issue / manual / candidate badges,
// a filter and checkboxes for local rerun.

import { Check } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { cn, fmtMs } from '@/lib/format';
import { Badge, Segmented } from '@/components/ui';
import type { LineStats } from './helpers';
import { HelpButton } from '@/components/guide/Help';

export type LineFilter = 'all' | 'issues' | 'manual';

export function LineList({ stats, selected, onSelect, checked, onToggleCheck, filter, onFilter }: {
  stats: LineStats[];
  selected: string | null;
  onSelect: (lineId: string) => void;
  checked: Set<string>;
  onToggleCheck: (lineId: string) => void;
  filter: LineFilter;
  onFilter: (f: LineFilter) => void;
}) {
  const shown = stats.filter((s) => (filter === 'issues' ? s.issues.length > 0 || s.failed > 0 : filter === 'manual' ? s.manual > 0 : true));
  const nIssues = stats.filter((s) => s.issues.length > 0 || s.failed > 0).length;
  const nManual = stats.filter((s) => s.manual > 0).length;
  const listRef = useRef<HTMLDivElement>(null);

  // keep the selected row visible
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-line="${selected}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [selected]);
  // another filter: the list from its top, or at the selected row when it is still listed
  const firstFilter = useRef(true);
  useEffect(() => {
    if (firstFilter.current) { firstFilter.current = false; return; }
    const row = listRef.current?.querySelector<HTMLElement>(`[data-line="${selected}"]`);
    if (row) row.scrollIntoView?.({ block: 'nearest' });
    else if (listRef.current) listRef.current.scrollTop = 0;
  }, [filter]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2.5">
        <Segmented<LineFilter> label="筛选歌词行"
          size="sm"
          value={filter}
          onChange={onFilter}
          options={[
            { value: 'all', label: `全部 ${stats.length}` },
            { value: 'issues', label: `有问题 ${nIssues}` },
            { value: 'manual', label: `人工 ${nManual}` },
          ]}
        />
        <HelpButton k="review.list" />
      </div>
      <div ref={listRef} className="max-h-[560px] min-h-0 flex-1 overflow-y-auto p-1.5">
        {shown.length === 0 && <div className="px-3 py-8 text-center text-xs text-muted">没有符合条件的行</div>}
        {shown.map((s) => {
          const isSel = s.line.id === selected;
          const isChecked = checked.has(s.line.id);
          return (
            // two sibling controls (no button inside a button): the check box and the row itself
            <div
              key={s.line.id}
              data-line={s.line.id}
              className={cn(
                'group flex items-start gap-2.5 rounded-lg px-2 py-2 transition',
                isSel ? 'bg-accent-soft ring-1 ring-accent/40' : 'hover:bg-surface-2',
              )}
            >
              <button
                type="button"
                role="checkbox"
                aria-checked={isChecked}
                aria-label={`第 ${s.index + 1} 行用于局部重跑`}
                title="选择用于局部重跑"
                onClick={() => onToggleCheck(s.line.id)}
                className={cn(
                  'focus-ring mt-0.5 grid size-4 shrink-0 place-items-center rounded border transition',
                  isChecked ? 'border-accent bg-accent text-accent-fg' : 'border-line-strong bg-surface opacity-60 group-hover:opacity-100 focus-visible:opacity-100',
                )}
              >
                {isChecked && <Check className="size-3" strokeWidth={3} />}
              </button>
              <button type="button" aria-current={isSel || undefined} onClick={() => onSelect(s.line.id)}
                className="focus-ring min-w-0 flex-1 cursor-pointer rounded text-left">
                <span className="flex items-baseline gap-2">
                  <span className="tabular w-6 shrink-0 text-right text-[11px] text-subtle">{s.index + 1}</span>
                  <span className={cn('truncate text-[13px]', isSel ? 'font-semibold text-fg' : 'text-fg')}>{s.line.text || '（空）'}</span>
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-1 pl-8">
                  <span className="tabular font-mono text-[11px] text-muted">{fmtMs(s.start)}</span>
                  {s.failed > 0 && <Badge tone="danger">{s.failed} 无时间</Badge>}
                  {s.issues.length > 0 && <Badge tone="warn">{s.issues.length} 提示</Badge>}
                  {s.manual > 0 && <Badge tone="ok">{s.manual} 人工</Badge>}
                  {s.candidates > 0 && <Badge tone="accent">{s.candidates} 候选</Badge>}
                </span>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
