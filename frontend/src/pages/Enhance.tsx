// Step 3 (optional): readings, AI round trip and vocal separation.
//
// The reading list is as long as the song, so the three tasks are picked from
// a row of status cards at the top instead of being stacked: separation and
// the AI round trip are always one click away, and their state (progress,
// last round trip, open readings) stays visible.  Panels stay mounted while
// hidden, so a half-done AI round trip survives a look at the readings.

import { ArrowRight, Bot, Languages, Loader2, Scissors } from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { cn, fmtRelative } from '@/lib/format';
import { resumeJobs, setStep, useApp, useJob, useProject, useView } from '@/store/app';
import { useSimple } from '@/store/simple';
import { arrowNav, Button, PageHeader } from '@/components/ui';
import { AiRoundtripCard } from './enhance/AiRoundtrip';
import { ReadingsCard } from './enhance/Readings';
import { SeparationCard } from './enhance/Separation';
import { loadEnhanceTab, saveEnhanceTab, type EnhanceTab } from './enhance/tab';
import { INTRO } from '@/lib/guide';

type Task = EnhanceTab;
type Tone = 'ok' | 'warn' | 'accent' | 'neutral';

const RT_STATUS: Record<string, string> = { prompted: '已生成提示词', validated: '已校验', applied: '已应用', rejected: '已拒绝' };

export function EnhancePage() {
  const project = useProject()!;
  const view = useView()!;
  const info = useApp((s) => s.info);
  const sepJob = useJob('separate');
  const aiJob = useJob('ai');
  const aiOneClick = useSimple((s) => !!s.settings?.ai.enabled && s.settings.ai.provider !== 'manual');
  const [tab, setTabState] = useState<Task>(loadEnhanceTab);
  const setTab = (t: Task) => {
    setTabState(t);
    saveEnhanceTab(t);
  };
  // finished operations (AI report, separation result) are known after a reload / on return
  useEffect(() => { void resumeJobs(project.id); }, [project.id]);
  const outdated = !!(view.audio.vocals?.outdated || view.audio.instrumental?.outdated);
  const next = project.mode === 'lrc' ? 'calibrate' : 'align';

  const readings = useMemo(() => {
    const sung = project.lyrics.lines.filter((l) => l.sing && l.kind === 'lyric');
    const segs = sung.flatMap((l) => l.segments);
    return {
      lines: sung.length,
      empty: sung.filter((l) => l.segments.every((s) => s.units.length === 0)).length,
      uncertain: segs.filter((s) => s.uncertain && !s.confirmed).length,
    };
  }, [project.lyrics.lines]);

  const lastRt = project.ai_roundtrips.at(-1);
  const stems = project.audio.filter((a) => a.role === 'vocals' || a.role === 'instrumental');
  const sepRunning = !!sepJob && (sepJob.status === 'queued' || sepJob.status === 'running');

  const cards: { id: Task; icon: ReactNode; title: string; status: ReactNode; tone: Tone }[] = [
    {
      id: 'readings', icon: <Languages className="size-4" />, title: '读音与发音单元',
      ...(readings.lines === 0 ? { status: '还没有歌词', tone: 'neutral' as Tone }
        : readings.empty ? { status: `${readings.empty} 行还没有读音`, tone: 'warn' as Tone }
          : readings.uncertain ? { status: `${readings.lines} 行 · ${readings.uncertain} 处待确认`, tone: 'warn' as Tone }
            : { status: `${readings.lines} 行 · 读音已就绪`, tone: 'ok' as Tone }),
    },
    {
      id: 'ai', icon: <Bot className="size-4" />, title: 'AI 注音',
      ...(aiJob && (aiJob.status === 'queued' || aiJob.status === 'running') ? {
        status: <span className="flex items-center gap-1.5"><Loader2 className="size-3 animate-spin" />等待 AI 回复</span>,
        tone: 'accent' as Tone,
      } : lastRt ? {
        status: `上次${RT_STATUS[lastRt.status] ?? lastRt.status} · ${fmtRelative(lastRt.applied_at ?? lastRt.created)}`,
        tone: (lastRt.status === 'applied' ? 'ok' : 'accent') as Tone,
      } : { status: aiOneClick ? '未使用 · 可一键交给 AI' : '未使用', tone: 'neutral' as Tone }),
    },
    {
      id: 'separation', icon: <Scissors className="size-4" />, title: '人声分离',
      ...(sepRunning ? {
        status: <span className="flex items-center gap-1.5"><Loader2 className="size-3 animate-spin" />分离中 {Math.round((sepJob!.progress ?? 0) * 100)}%</span>,
        tone: 'accent' as Tone,
      } : sepJob?.status === 'failed' ? { status: '上次分离失败', tone: 'warn' as Tone }
        : outdated ? { status: '分轨来自更换前的原曲，需重新分离', tone: 'warn' as Tone }
        : stems.length ? { status: `已有 ${stems.map((a) => (a.role === 'vocals' ? '人声' : '伴奏')).join(' + ')}`, tone: 'ok' as Tone }
          : { status: info?.separation_available === false ? '未安装分离组件' : '未分离（可选）', tone: 'neutral' as Tone }),
    },
  ];

  return (
    <>
      <PageHeader
        eyebrow="第 3 步（可选）"
        title="注音与人声分离"
        description={INTRO.enhance}
        actions={
          <Button variant="primary" onClick={() => setStep(next)} icon={<ArrowRight className="size-4" />}>
            下一步：{next === 'calibrate' ? '首音校准' : '对齐'}
          </Button>
        }
      />
      <div role="tablist" aria-label="注音与人声分离" className="mb-5 grid gap-3 sm:grid-cols-3" onKeyDown={(e) => arrowNav(e, 'tab')}>
        {cards.map((c) => (
          <TaskTab key={c.id} {...c} active={tab === c.id} onClick={() => setTab(c.id)} />
        ))}
      </div>
      <div role="tabpanel" id="enh-panel-readings" aria-labelledby="enh-tab-readings" hidden={tab !== 'readings'}><ReadingsCard /></div>
      <div role="tabpanel" id="enh-panel-ai" aria-labelledby="enh-tab-ai" hidden={tab !== 'ai'}><AiRoundtripCard /></div>
      <div role="tabpanel" id="enh-panel-separation" aria-labelledby="enh-tab-separation" hidden={tab !== 'separation'}><SeparationCard /></div>
    </>
  );
}

const DOT: Record<Tone, string> = { ok: 'bg-ok', warn: 'bg-warn', accent: 'bg-accent', neutral: 'bg-line-strong' };

function TaskTab({ id, icon, title, status, tone, active, onClick }: {
  id: Task; icon: ReactNode; title: string; status: ReactNode; tone: Tone; active: boolean; onClick: () => void;
}) {
  return (
    <button
      type="button" role="tab" id={`enh-tab-${id}`} aria-controls={`enh-panel-${id}`} aria-selected={active}
      tabIndex={active ? 0 : -1} onClick={onClick}
      className={cn(
        'focus-ring flex min-w-0 items-start gap-3 rounded-xl border px-4 py-3 text-left transition',
        active ? 'border-accent bg-accent-soft/60 ring-1 ring-accent' : 'border-line bg-surface hover:border-line-strong hover:bg-surface-2',
      )}
    >
      <span className={cn('mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg',
        active ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-muted')}>{icon}</span>
      <span className="min-w-0">
        <span className="block text-[14px] font-semibold">{title}</span>
        <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
          <span className={cn('size-1.5 shrink-0 rounded-full', DOT[tone])} />
          <span className="truncate">{status}</span>
        </span>
      </span>
    </button>
  );
}
