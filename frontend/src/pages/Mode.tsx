// Step 1: plain vs LRC-enhanced mode. Switching keeps inputs and manual edits;
// results that no longer match are marked stale by the server.

import { ArrowRight, Check, Timer, Type } from 'lucide-react';
import { useState } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/format';
import { isEnter, isEscape } from '@/lib/keys';
import type { Mode, ProjectView } from '@/lib/types';
import { loadProjects, ppath, run, setPV, setStep, toast, useProject, useView } from '@/store/app';
import { arrowNav, Button, Callout, Card, CardBody, CardHeader, Input, PageHeader } from '@/components/ui';
import { INTRO } from '@/lib/guide';
import { HelpButton } from '@/components/guide/Help';

const MODES: { value: Mode; title: string; icon: typeof Type; points: string[] }[] = [
  {
    value: 'plain',
    title: '普通模式',
    icon: Type,
    points: ['音频 + 已知歌词（或注音 JSON）', '不使用任何外部时间锚点，整段有序对齐', '导入 LRC 时只取正文，并明确提示时间被忽略'],
  },
  {
    value: 'lrc',
    title: 'LRC 增强模式',
    icon: Timer,
    points: ['音频 + 带行时间的歌词', '先标记首个发音校准全局偏移', '句首锚点约束细对齐；无有效时间时要求补充或切换，不静默降级'],
  },
];

export function ModeChoice({ value, onChange }: { value: Mode; onChange: (m: Mode) => void }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="对齐模式" onKeyDown={(e) => arrowNav(e)}>
      {MODES.map((m) => {
        const Icon = m.icon;
        const on = value === m.value;
        return (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(m.value)}
            className={cn(
              'focus-ring relative rounded-2xl border p-4 text-left transition',
              on ? 'border-accent bg-accent-soft/60 ring-1 ring-accent' : 'border-line hover:border-line-strong hover:bg-surface-2',
            )}
          >
            <div className="flex items-center gap-2.5">
              <div className={cn('grid size-8 place-items-center rounded-lg', on ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-muted')}>
                <Icon className="size-4" />
              </div>
              <span className="font-semibold">{m.title}</span>
              {on && <Check className="ml-auto size-4 text-accent" />}
            </div>
            <ul className="mt-3 space-y-1.5 text-[13px] leading-5 text-muted">
              {m.points.map((p) => (
                <li key={p} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-current" />{p}</li>
              ))}
            </ul>
          </button>
        );
      })}
    </div>
  );
}

export function ModePage() {
  const project = useProject()!;
  const view = useView()!;
  const [name, setName] = useState(project.name);

  const setMode = (mode: Mode) => {
    if (mode === project.mode) return;
    void run(async () => {
      setPV(await api.patch<ProjectView>(ppath(''), { mode }));
      toast('ok', `已切换到${mode === 'lrc' ? ' LRC 增强' : '普通'}模式`, '输入与人工修改均已保留；不再适用的结果会标记为过期');
    });
  };

  const rename = () => {
    if (name.trim() && name.trim() !== project.name) {
      void run(async () => {
        setPV(await api.patch<ProjectView>(ppath(''), { name: name.trim() }));
        await loadProjects();
      });
    }
  };

  const stale = view.results.filter((r) => r.stale);

  return (
    <>
      <PageHeader
        eyebrow="第 1 步"
        title="选择对齐模式"
        description={INTRO.mode}
        actions={<Button variant="primary" onClick={() => setStep('input')} icon={<ArrowRight className="size-4" />}>下一步：音频与歌词</Button>}
      />
      <div className="space-y-6">
        <Card>
          <CardHeader title="模式" help={<HelpButton k="mode.choice" />} />
          <CardBody className="space-y-4">
            <ModeChoice value={project.mode} onChange={setMode} />
            {view.mode_notice && <Callout tone="warn">{view.mode_notice}</Callout>}
            {stale.length > 0 && (
              <Callout tone="info" title={`${stale.length} 个结果已过期`}>
                {stale[0].stale_reason}。旧结果仍可在“人工检查”中查看，但不再对应当前设置。
              </Callout>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="项目名称" />
          <CardBody>
            <Input value={name} aria-label="项目名称" onChange={(e) => setName(e.target.value)} onBlur={rename} className="max-w-md"
              onKeyDown={(e) => {
                if (isEnter(e)) e.currentTarget.blur();  // saved once, on blur
                if (isEscape(e)) setName(project.name);
              }} />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
