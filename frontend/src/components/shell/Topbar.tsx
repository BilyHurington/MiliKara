import * as Popover from '@radix-ui/react-popover';
import { Activity, Download, Moon, Redo2, Sparkles, Sun, Undo2, X } from 'lucide-react';
import { cn } from '@/lib/format';
import { errorHint } from '@/lib/errorHints';
import { DiagnosticsButton } from '@/components/DiagnosticsButton';
import { HelpButton } from '@/components/guide/Help';
import { ShortcutsButton } from '@/components/guide/Shortcuts';
import { MOD_KEY, SHIFT_KEY } from '@/lib/keys';
import { cancelJob, isOpenProject, ppath, projectName, run, setTheme, STEPS, useApp } from '@/store/app';
import { hasActiveTasks, setUi, useSimple } from '@/store/simple';
import { redo, undo } from '@/store/edits';
import { DownloadButton } from '@/components/DownloadButton';
import { Badge, ConfirmButton, IconButton, Kbd, Progress, Tip } from '@/components/ui';

export function Topbar() {
  const pv = useApp((s) => s.pv);
  const step = useApp((s) => s.step);
  const nUndo = useApp((s) => s.undo.length);
  const nRedo = useApp((s) => s.redo.length);
  const stepLabel = STEPS.find((s) => s.id === step)?.label;
  const staleCount = pv?.view.results.filter((r) => r.stale).length ?? 0;
  const theme = useApp((s) => s.theme);

  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-canvas/80 px-4 backdrop-blur-md md:px-6">
      <div className="flex min-w-0 flex-1 items-center gap-2 text-sm whitespace-nowrap">
        {pv ? (
          <>
            <span className="min-w-0 truncate font-medium">{pv.project.name}</span>
            <span className="hidden text-subtle md:inline">/</span>
            <span className="hidden text-muted md:inline">{stepLabel}</span>
            <Badge tone={pv.project.mode === 'lrc' ? 'accent' : 'neutral'} className="ml-1 hidden sm:inline-flex">{pv.project.mode === 'lrc' ? 'LRC 增强' : '普通模式'}</Badge>
            {staleCount > 0 && <Badge className="hidden lg:inline-flex" tone="warn" dot title="改过歌词、读音、校准、模式或换了原曲：这些结果仍可查看，但不再对应现在的输入。到“对齐”重新对齐即可（人工改过的时间会保留）">{staleCount} 个结果已过期</Badge>}
          </>
        ) : (
          <span className="font-medium">项目</span>
        )}
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-1.5">
        {pv && (
          <>
            <IconButton label={`撤销人工修改的时间（${nUndo}） ${MOD_KEY}+Z`} disabledReason="还没有可撤销的时间修改（只记录人工检查中对单元时间的修改）"
              disabled={!nUndo} onClick={() => void undo()}><Undo2 className="size-4" /></IconButton>
            <IconButton label={`重做（${nRedo}） ${MOD_KEY}+${SHIFT_KEY}+Z`} disabledReason="没有可重做的修改"
              disabled={!nRedo} onClick={() => void redo()}><Redo2 className="size-4" /></IconButton>
            <span className="mx-1 h-5 w-px bg-line" />
          </>
        )}
        <JobsIndicator />
        {pv && (
          <Tip content="下载便携项目包（含音频），可在另一台电脑导入">
            <DownloadButton href={ppath('/package?include_audio=1')} big check={false} size="sm" variant="ghost" aria-label="下载项目包"
              icon={<Download className="size-4" />}><span className="hidden md:inline">项目包</span></DownloadButton>
          </Tip>
        )}
        {pv && <ShortcutsButton />}
        <span className="mx-1 h-5 w-px bg-line" />
        {/* same place as the simple mode's “详细模式” switch */}
        <button onClick={() => setUi('simple')} title="切换到极简模式：放入视频和歌词，一键生成卡拉OK视频；任务队列也在那里"
          className="focus-ring flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium text-muted transition hover:bg-surface-2 hover:text-fg">
          <Sparkles className="size-4 text-accent" /><span className="hidden sm:inline">极简模式</span>
          <QueueCount />
        </button>
        <HelpButton k="ui.modes" />
        <button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label="切换主题" title={theme === 'dark' ? '浅色模式' : '深色模式'}
          className="focus-ring grid size-8 place-items-center rounded-lg text-muted transition hover:bg-surface-2 hover:text-fg">
          {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>
      </div>
    </header>
  );
}

function JobsIndicator() {
  const jobs = Object.values(useApp((s) => s.jobs));
  const inProject = useApp((s) => !!s.pv);
  const active = jobs.filter((j) => j.status === 'queued' || j.status === 'running');
  if (!jobs.length) {
    return (
      <Tip content={<span>没有进行中的操作（对齐、分离、生成视频等会显示在这里）。{inProject && <>快捷键见旁边的键盘按钮，或按 <Kbd>?</Kbd></>}</span>}>
        <span tabIndex={0} className="focus-ring hidden items-center gap-1.5 rounded px-2 text-xs text-subtle lg:flex"><Activity className="size-3.5" />空闲</span>
      </Tip>
    );
  }
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button className={cn('focus-ring flex h-8 items-center gap-2 rounded-lg px-2.5 text-xs font-medium transition',
          active.length ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-surface-2')}>
          {active.length ? <span className="relative flex size-2"><span className="absolute inline-flex size-full animate-ping rounded-full bg-accent opacity-60" /><span className="relative inline-flex size-2 rounded-full bg-accent" /></span> : <Activity className="size-3.5" />}
          <span className="hidden md:inline">{active.length ? `${active.length} 个操作进行中` : '操作'}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} aria-label="进行中的操作" className="z-50 w-80 animate-in rounded-xl border border-line bg-surface p-2 shadow-[var(--shadow-pop)]">
          <div className="px-2 pt-1 pb-2 text-xs font-semibold text-muted">详细模式的操作（极简模式的任务在“极简模式”里）</div>
          <div className="space-y-1">
            {jobs.map((j) => {
              const live = j.status === 'queued' || j.status === 'running';
              return (
                <div key={j.id} className="rounded-lg px-2 py-2 hover:bg-surface-2">
                  <div className="flex flex-wrap items-center gap-2 text-[13px]">
                    <span className="font-medium">{j.label ?? j.kind}</span>
                    {j.project_id && !isOpenProject(j.project_id) && (
                      <span className="max-w-32 truncate text-xs text-muted" title={projectName(j.project_id) ?? undefined}>· {projectName(j.project_id) ?? '其他项目'}</span>
                    )}
                    <Badge tone={{ queued: 'neutral', running: 'accent', succeeded: 'ok', failed: 'danger', cancelled: 'neutral' }[j.status] as any}>
                      {{ queued: '排队中', running: '运行中', succeeded: '完成', failed: '失败', cancelled: '已取消' }[j.status]}
                    </Badge>
                    {live && (
                      <span className="ml-auto">
                        <ConfirmButton size="xs" variant="ghost" aria-label={`取消${j.label ?? ''}`} icon={<X className="size-3.5" />}
                          question="取消这个操作？" confirmLabel="取消操作" keepLabel="继续" onConfirm={() => void run(() => cancelJob(j.id))} />
                      </span>
                    )}
                  </div>
                  {live && <Progress value={j.progress} className="mt-2" label={`${j.label ?? j.kind}进度`} />}
                  <div className={cn('mt-1 truncate text-xs', j.status === 'failed' ? 'text-danger' : 'text-muted')} title={j.error ?? j.message}>
                    {j.error ?? j.message}
                  </div>
                  {j.status === 'failed' && (
                    <div className="mt-1 space-y-1">
                      {errorHint(j.error) && <div className="text-xs text-muted">{errorHint(j.error)}</div>}
                      <DiagnosticsButton jobId={j.id} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** Simple-mode tasks seen from the detailed mode: how many run, how many wait for the user. */
function QueueCount() {
  const tasks = useSimple((s) => s.tasks);
  const waiting = tasks.filter((t) => t.status === 'waiting').length;
  const busy = hasActiveTasks(tasks) ? tasks.filter((t) => ['preparing', 'queued', 'running'].includes(t.status)).length : 0;
  if (!waiting && !busy) return null;
  return (
    <span className={cn('rounded-full px-1.5 text-[11px] font-semibold tabular-nums',
      waiting ? 'bg-warn-soft text-warn' : 'bg-accent-soft text-accent')}
      title={[busy && `${busy} 个任务进行中`, waiting && `${waiting} 个需要确认开头位置`].filter(Boolean).join('，')}>
      {waiting ? `${waiting} 待确认` : busy}
    </span>
  );
}
