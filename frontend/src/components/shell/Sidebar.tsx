import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import {
  Check, ChevronsUpDown, CircleDashed, FolderOpen, HardDrive, Loader2, Plus, TriangleAlert,
} from 'lucide-react';
import { useState } from 'react';
import { cn, fmtRelative } from '@/lib/format';
import { closeProject, loadProjects, openProject, run, setStep, STEPS, useApp, type Step } from '@/store/app';
import { stepStatus, type StepState } from '@/store/steps';
import { Badge } from '@/components/ui';
import { StorageDialog } from '@/components/StorageDialog';
import { StepsHelp, StepsOverviewCard } from '@/components/guide/StepsOverview';
import { UpdateBadge } from './UpdateBadge';

export function Sidebar() {
  const pv = useApp((s) => s.pv);
  const step = useApp((s) => s.step);
  const version = useApp((s) => s.info?.version);
  const pid = useApp((s) => s.pid);
  const history = useApp((s) => s.jobHistory);
  const [storageOpen, setStorageOpen] = useState(false);
  // only the open project's operations mark its steps as running
  const running = new Set(Object.values(history)
    .filter((j) => j.project_id === pid && (j.status === 'queued' || j.status === 'running')).map((j) => j.kind));

  return (
    <aside className="flex w-16 shrink-0 flex-col border-r border-line bg-surface lg:w-64">
      <div className="flex items-center justify-center gap-2.5 px-3 pt-4 pb-3 lg:justify-start lg:px-4">
        <img src="/logo.png" alt="" aria-hidden className="size-9 shrink-0 object-contain" />
        <div className="hidden lg:block">
          <div className="text-[15px] leading-5 font-semibold tracking-tight">MiliKara</div>
          <div className="text-[11px] text-muted">已知歌词时间戳对齐</div>
        </div>
      </div>

      <div className="px-2 pb-3 lg:px-3">
        <ProjectSwitcher />
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3 lg:px-3">
        {pv ? (
          <>
            <StepsOverviewCard className="hidden lg:block" />
            <div className="hidden items-center gap-1 px-2 pb-2 text-[11px] font-semibold tracking-wider text-subtle uppercase lg:flex">工作流程<StepsHelp /></div>
            <ol className="relative space-y-0.5">
              {STEPS.map((st, i) => {
                const status = stepStatus(st.id, pv, running);
                return (
                  <StepItem
                    key={st.id}
                    index={i + 1}
                    label={st.label}
                    note={status.note ?? st.hint}
                    state={status.state}
                    active={step === st.id}
                    last={i === STEPS.length - 1}
                    onClick={() => setStep(st.id as Step)}
                  />
                );
              })}
            </ol>
          </>
        ) : (
          <div className="hidden rounded-xl border border-dashed border-line-strong px-3 py-4 text-center text-xs text-muted lg:block">
            新建或打开一个项目后，这里会显示工作流程
          </div>
        )}
      </nav>

      <div className="border-t border-line px-2 py-2 lg:px-3">
        <button type="button" onClick={() => setStorageOpen(true)} title="存储空间：项目、缓存占用多大，删除不需要的"
          className="focus-ring flex w-full items-center justify-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-muted transition hover:bg-surface-2 hover:text-fg lg:justify-start">
          <HardDrive className="size-4 shrink-0" /><span className="hidden lg:inline">存储空间</span>
        </button>
        <div className="px-2 pt-1 text-center lg:text-left">
          <span className="hidden text-[11px] text-subtle lg:inline">v{version ?? '…'} · 本地运行</span>
          <UpdateBadge className="mt-1 hidden lg:inline-flex" />
        </div>
      </div>
      <StorageDialog open={storageOpen} onOpenChange={(v) => { setStorageOpen(v); if (!v) void loadProjects().catch(() => undefined); }} />
    </aside>
  );
}

function StepItem({ index, label, note, state, active, last, onClick }: {
  index: number; label: string; note?: string; state: StepState; active: boolean; last: boolean; onClick: () => void;
}) {
  const dot = {
    done: <Check className="size-3.5" strokeWidth={3} />,
    running: <Loader2 className="size-3.5 animate-spin" />,
    attention: <TriangleAlert className="size-3.5" />,
    skipped: <CircleDashed className="size-3.5" />,
    optional: <span className="text-[11px] font-semibold">{index}</span>,
    todo: <span className="text-[11px] font-semibold">{index}</span>,
  }[state];
  const dotColor = {
    done: 'bg-ok text-white',
    running: 'bg-accent text-accent-fg',
    attention: 'bg-warn text-white',
    skipped: 'bg-surface-2 text-subtle ring-1 ring-line',
    optional: 'bg-surface-2 text-muted ring-1 ring-line-strong',
    todo: 'bg-surface-2 text-muted ring-1 ring-line-strong',
  }[state];
  return (
    <li className="relative">
      {!last && <span className="absolute top-9 bottom-[-6px] left-1/2 w-px bg-line lg:left-[21px]" aria-hidden />}
      <button
        onClick={onClick}
        title={note ? `${label} · ${note}` : label}
        className={cn(
          'focus-ring group relative flex w-full items-start justify-center gap-3 rounded-lg px-2 py-2 text-left transition lg:justify-start',
          active ? 'bg-accent-soft' : 'hover:bg-surface-2',
          state === 'skipped' && !active && 'opacity-60',
        )}
      >
        <span className={cn('relative z-[1] mt-0.5 grid size-6 shrink-0 place-items-center rounded-full transition', active && state !== 'done' && state !== 'attention' ? 'bg-accent text-accent-fg' : dotColor)}>
          {dot}
        </span>
        <span className="hidden min-w-0 lg:block">
          <span className={cn('block text-[13px] leading-5 font-medium', active ? 'text-accent' : 'text-fg')}>{label}</span>
          {note && <span className="block truncate text-[11px] leading-4 text-muted">{note}</span>}
        </span>
      </button>
    </li>
  );
}

function ProjectSwitcher() {
  const pv = useApp((s) => s.pv);
  const projects = useApp((s) => s.projects);
  return (
    <DropdownMenu.Root onOpenChange={(open) => { if (open) void run(() => loadProjects()); }}>
      <DropdownMenu.Trigger asChild>
        <button className="focus-ring flex w-full items-center justify-center gap-2.5 rounded-xl border border-line bg-surface-2/60 px-2 py-2 text-left transition hover:bg-surface-2 lg:justify-start lg:px-3">
          <FolderOpen className="size-4 shrink-0 text-muted" />
          <span className="hidden min-w-0 flex-1 lg:block">
            <span className="block truncate text-[13px] font-medium">{pv ? pv.project.name : '未打开项目'}</span>
            <span className="block text-[11px] text-muted">{pv ? (pv.project.mode === 'lrc' ? 'LRC 增强模式' : '普通模式') : `${projects.length} 个项目`}</span>
          </span>
          <ChevronsUpDown className="hidden size-4 shrink-0 text-subtle lg:block" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={6}
          className="z-50 w-72 animate-in rounded-xl border border-line bg-surface p-1.5 shadow-[var(--shadow-pop)]"
        >
          <DropdownMenu.Label className="px-2 py-1.5 text-[11px] font-semibold tracking-wider text-subtle uppercase">最近项目</DropdownMenu.Label>
          <div className="max-h-72 overflow-y-auto">
            {projects.length === 0 && <div className="px-2 py-3 text-xs text-muted">还没有项目</div>}
            {projects.map((p) => (
              <DropdownMenu.Item
                key={p.id}
                onSelect={() => { if (p.id !== pv?.project.id) void run(() => openProject(p.id), '打开项目失败'); }}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-[13px] outline-none data-[highlighted]:bg-surface-2"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{p.name}</span>
                  <span className="block text-[11px] text-muted">{fmtRelative(p.updated)}</span>
                </span>
                <Badge tone={p.mode === 'lrc' ? 'accent' : 'neutral'}>{p.mode === 'lrc' ? 'LRC' : '普通'}</Badge>
                {pv?.project.id === p.id && <Check className="size-4 text-accent" />}
              </DropdownMenu.Item>
            ))}
          </div>
          <DropdownMenu.Separator className="my-1 h-px bg-line" />
          <DropdownMenu.Item
            onSelect={() => closeProject()}
            className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-[13px] font-medium outline-none data-[highlighted]:bg-surface-2"
          >
            <Plus className="size-4" /> 新建 / 导入项目
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
