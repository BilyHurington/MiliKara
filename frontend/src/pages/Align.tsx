// Step 5: run the alignment and manage results.

import {
  ArrowRight, CheckCircle2, ChevronDown, CircleAlert, CircleDashed, Cpu, Eye, FileJson, Play, Settings2, Star, X,
} from 'lucide-react';
import { useEffect, useState, type ComponentProps, type ReactNode } from 'react';
import { api } from '@/lib/api';
import { cn, fmtMs, fmtRelative, readFileText, ROLE_LABEL } from '@/lib/format';
import type { AlignConfig, Job, ProjectView } from '@/lib/types';
import {
  cancelJob, isOpenProject, ppath, refreshProject, run, selectResult, setPV, setStep, toast, trackJob, useApp, useJob, useProject, useView,
  type Step,
} from '@/store/app';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, ConfirmButton, Dialog, DropZone, EmptyState, Field, NumberInput, PageHeader,
  Progress, Segmented, Select, SliderField, Switch, Textarea,
} from '@/components/ui';
import { INTRO } from '@/lib/guide';
import { HelpButton } from '@/components/guide/Help';

export function AlignPage() {
  return (
    <>
      <PageHeader
        eyebrow="第 5 步"
        title="对齐"
        description={INTRO.align}
        actions={<Button variant="primary" onClick={() => setStep('review')} icon={<ArrowRight className="size-4" />}>下一步：人工检查</Button>}
      />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
          <RunCard />
          <ResultsCard />
        </div>
        <Readiness />
      </div>
    </>
  );
}

// ------------------------------------------------------------------ readiness

function Readiness() {
  const project = useProject()!;
  const view = useView()!;
  const lyricLines = project.lyrics.lines.filter((l) => l.sing && l.kind === 'lyric');
  const hasAudio = project.audio.some((a) => a.role === 'original');
  const audioOk = !!view.audio.original?.available;
  const uncertain = project.lyrics.lines.reduce((n, l) => n + l.segments.filter((s) => s.uncertain && !s.confirmed).length, 0);
  const anchorErrors = view.calibration_issues.filter((i) => i.severity === 'error');
  const lrc = project.mode === 'lrc';

  const items: { ok: boolean | 'warn' | 'skip'; label: string; note: string; step?: Step }[] = [
    { ok: lyricLines.length > 0, label: '歌词', note: lyricLines.length ? `${lyricLines.length} 行参与对齐` : '尚未导入歌词', step: 'input' },
    { ok: hasAudio && audioOk, label: '原曲音频', note: !hasAudio ? '尚未上传' : audioOk ? '已就绪' : '文件缺失，请重新上传', step: 'input' },
    {
      ok: uncertain ? 'warn' : true, label: '读音',
      note: uncertain ? `${uncertain} 处规则读音待确认（可用 AI 注音或手工修正）` : '读音已就绪', step: 'enhance',
    },
    lrc
      ? {
        ok: anchorErrors.length ? false : project.calibration.confirmed ? true : 'warn', label: '首音校准',
        note: anchorErrors.length ? `${anchorErrors.length} 个锚点需要修正` : project.calibration.confirmed ? '已确认' : '尚未确认全局偏移',
        step: 'calibrate',
      }
      : { ok: 'skip', label: '首音校准', note: '普通模式不需要' },
  ];

  return (
    <Card className="self-start">
      <CardHeader title="准备情况" help={<HelpButton k="align.ready" />} description={lrc ? 'LRC 增强模式' : '普通模式'} />
      <ul className="space-y-1 p-2">
        {items.map((it) => {
          const Icon = it.ok === true ? CheckCircle2 : it.ok === 'skip' ? CircleDashed : CircleAlert;
          const color = it.ok === true ? 'text-ok' : it.ok === 'skip' ? 'text-subtle' : it.ok === 'warn' ? 'text-warn' : 'text-danger';
          return (
            <li key={it.label}>
              <button
                disabled={!it.step}
                onClick={() => it.step && setStep(it.step)}
                className="focus-ring flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition enabled:hover:bg-surface-2"
              >
                <Icon className={cn('mt-0.5 size-4 shrink-0', color)} />
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-medium">{it.label}</span>
                  <span className="block text-xs text-muted">{it.note}</span>
                </span>
                {it.step && it.ok !== true && <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-subtle" />}
              </button>
            </li>
          );
        })}
      </ul>
      {anchorErrors.length > 0 && (
        <div className="px-4 pb-4">
          <Callout tone="danger">锚点存在负值、越界或顺序冲突时无法对齐，需要先修正（不会被静默截断）。</Callout>
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ settings + run

function RunCard() {
  const project = useProject()!;
  const view = useView()!;
  const info = useApp((s) => s.info);
  const job = useJob('align');
  const cfg = project.config;
  const [advanced, setAdvanced] = useState(false);
  const hasVocals = !!view.audio.vocals?.available;
  const running = job && (job.status === 'queued' || job.status === 'running');
  const backend = info?.backends.find((b) => b.name === cfg.backend);
  // e.g. a simple-mode task aligned the vocals, while the project setting is still the original
  const active = view.results.find((r) => r.id === project.active_result_id);
  const activeRole = active?.audio_role as 'original' | 'vocals' | undefined;
  const roleDiffers = !!activeRole && activeRole !== cfg.audio_role && (activeRole === 'original' || hasVocals);

  const patchConfig = (partial: Record<string, unknown>) => run(async () => {
    setPV(await api.patch<ProjectView>(ppath(''), { config: partial }));
  }, '保存设置失败');
  const patchDecode = (partial: Partial<AlignConfig['decode']>) => patchConfig({ decode: partial });

  const start = () => run(async () => {
    const j = await api.post<Job>(ppath('/align'), { audio_role: cfg.audio_role });
    trackJob(j, {
      label: '对齐',
      onDone: async (done) => {
        // another project may be open by now: its selection must not change
        if (!isOpenProject(done.project_id)) return;
        await refreshProject();
        if (done.status === 'succeeded' && done.output?.result_id && isOpenProject(done.project_id)) selectResult(done.output.result_id);
      },
    });
  }, '无法开始对齐');

  return (
    <Card>
      <CardHeader icon={<Settings2 className="size-4" />} title="对齐设置" help={<HelpButton k="align.settings" />} description="设置随项目保存。" />
      <CardBody className="space-y-5">
        <div className="grid gap-5 md:grid-cols-2">
          <Field group label="对齐输入音频" hint={hasVocals ? '人声使用未衰减的分离人声；分离不保证更准，异常句可切回原曲比较'
            : view.audio.vocals?.outdated ? '现有人声分轨来自更换前的原曲，不能使用：请在“注音与分离”中重新分离' : '没有人声分轨：可在“注音与分离”中分离或导入'}>
            <Segmented
              value={cfg.audio_role}
              onChange={(v) => patchConfig({ audio_role: v })}
              options={[
                { value: 'original', label: ROLE_LABEL.original },
                { value: 'vocals', label: '人声（未衰减）', disabled: !hasVocals },
              ]}
            />
            {roleDiffers && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-warn-soft px-2.5 py-1.5 text-xs text-warn">
                当前对齐结果用的是「{ROLE_LABEL[activeRole!]}」，这里选的是「{ROLE_LABEL[cfg.audio_role]}」：重新对齐会改用后者。
                <Button size="xs" variant="secondary" onClick={() => patchConfig({ audio_role: activeRole })}>改用{ROLE_LABEL[activeRole!]}</Button>
              </div>
            )}
          </Field>
          <Field label="声学后端">
            <Select value={cfg.backend} onChange={(e) => patchConfig({ backend: e.target.value })}>
              {(info?.backends ?? [{ name: cfg.backend, available: true } as any]).map((b) => (
                <option key={b.name} value={b.name} disabled={!b.available}>
                  {b.name}{b.available ? '' : '（未安装）'}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {backend && (
          <div className="rounded-xl border border-line bg-surface-2/50 px-4 py-3 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <Cpu className="size-4 text-muted" />
              <span className="font-medium">{backend.description}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {backend.default_model && <Badge>模型 {backend.default_model}</Badge>}
              <Badge tone="info">语言 {backend.languages.join(' / ')}</Badge>
              <Badge tone={/nc/i.test(backend.license) ? 'warn' : 'neutral'}>许可 {backend.license}</Badge>
              {!backend.available && <Badge tone="danger">缺少依赖：{(backend.missing ?? []).join(', ') || 'milikara[ml]'}</Badge>}
            </div>
          </div>
        )}
        {view.capability_warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}

        <div className="rounded-xl border border-line">
          <button
            onClick={() => setAdvanced(!advanced)}
            className="focus-ring flex w-full items-center gap-2 rounded-xl px-4 py-3 text-left text-[13px] font-medium"
          >
            <ChevronDown className={cn('size-4 text-muted transition', !advanced && '-rotate-90')} />
            高级解码参数
            <span className="ml-auto text-xs font-normal text-muted">
              σ {cfg.decode.soft_sigma_ms} ms · λ {cfg.decode.soft_lambda} · 尾音 {TAIL_LABEL[cfg.tail.strategy]}
            </span>
          </button>
          {advanced && (
            <div className="space-y-5 border-t border-line px-4 py-4">
              <div className="flex items-start gap-2 rounded-lg bg-surface-2/60 px-3 py-2 text-xs text-muted">
                <span className="min-w-0 flex-1">默认值适合绝大多数歌，一般不用改。只在某些行总是对不好时再试着调整。</span>
                <HelpButton k="align.advanced" />
              </div>
              <div className="space-y-3">
                <div className="text-xs font-semibold text-muted">LRC 歌词时间的约束（仅 LRC 增强）</div>
                <ConfigSlider label="容差 σ" unit="ms" min={50} max={2000} step={10} value={cfg.decode.soft_sigma_ms}
                  onCommit={(v) => patchDecode({ soft_sigma_ms: v })} />
                <ConfigSlider label="先验强度 λ" unit="" min={0} max={20} step={0.5} value={cfg.decode.soft_lambda}
                  onCommit={(v) => patchDecode({ soft_lambda: v })} />
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="左边距" hint="每行往前多找多长">
                  <NumberInput value={cfg.decode.left_margin_ms} suffix="ms" onCommit={(v) => v !== null && patchDecode({ left_margin_ms: v })} />
                </Field>
                <Field label="右边距" hint="可以拖到下一句开头之后多久">
                  <NumberInput value={cfg.decode.right_margin_ms} suffix="ms" onCommit={(v) => v !== null && patchDecode({ right_margin_ms: v })} />
                </Field>
                <Field label="硬锚点容差" hint="人工锚点允许的误差">
                  <NumberInput value={cfg.decode.hard_tolerance_ms} suffix="ms" onCommit={(v) => v !== null && patchDecode({ hard_tolerance_ms: v })} />
                </Field>
                <Field label="紧邻联合阈值" hint="间隔更短的几句放在一起对齐">
                  <NumberInput value={cfg.decode.tight_gap_ms} suffix="ms" onCommit={(v) => v !== null && patchDecode({ tight_gap_ms: v })} />
                </Field>
              </div>
              <Field group label="尾音策略" hint={TAIL_HINT[cfg.tail.strategy]}>
                <Segmented
                  value={cfg.tail.strategy}
                  onChange={(v) => patchConfig({ tail: { strategy: v } })}
                  options={(['off', 'trim', 'energy'] as const).map((v) => ({ value: v, label: TAIL_LABEL[v] }))}
                />
              </Field>
              <Switch
                checked={cfg.retry.enabled}
                onChange={(v) => patchConfig({ retry: { enabled: v } })}
                label={<span>异常句有限重试 <span className="text-muted">（对不好的行自动换几种方法再试一次，比如放宽范围、和相邻句一起对齐、换用原曲或人声）</span></span>}
              />
            </div>
          )}
        </div>

        {job && <JobCard job={job} />}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted">运行中修改歌词或校准不会影响本次任务；完成后旧结果会被标记为过期。人工锁定的时间不会被新结果覆盖。</p>
          <Button variant="primary" size="lg" onClick={start} loading={!!running} disabled={!!running} icon={<Play className="size-4 fill-current" />}>
            {running ? '对齐中…' : '开始对齐'}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

/** SliderField with local state: moves while dragging, saves on release / Enter. */
function ConfigSlider({ value, onCommit, ...rest }: Omit<ComponentProps<typeof SliderField>, 'onChange'> & { onCommit: (v: number) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return <SliderField {...rest} value={v} onChange={setV} onCommit={onCommit} />;
}

const TAIL_LABEL = { off: '关闭', trim: '保守裁短', energy: '人声能量修正' } as const;
const TAIL_HINT = {
  off: '默认：用模型给出的结尾',
  trim: '只把明显过长的尾音裁短一些',
  energy: '按人声实际持续的长度修正一句最后拖长的音（只在附近小范围内调整，人工改过的不动）',
} as const;

function JobCard({ job }: { job: Job }) {
  const live = job.status === 'queued' || job.status === 'running';
  const tone = job.status === 'failed' ? 'danger' : job.status === 'succeeded' ? 'ok' : 'accent';
  return (
    <div className={cn('rounded-xl border px-4 py-3', job.status === 'failed' ? 'border-danger/40 bg-danger-soft' : 'border-line bg-surface-2/50')}>
      <div className="flex items-center gap-2 text-[13px]">
        <span className="font-medium">对齐任务</span>
        <Badge tone={tone === 'accent' ? 'accent' : tone}>
          {{ queued: '排队中', running: '运行中', succeeded: '完成', failed: '失败', cancelled: '已取消' }[job.status]}
        </Badge>
        <span className="tabular ml-auto text-xs text-muted">{Math.round(job.progress * 100)}%</span>
        {live && (
          <ConfirmButton size="xs" variant="ghost" icon={<X className="size-3.5" />} question="取消对齐？" confirmLabel="取消对齐" keepLabel="继续"
            onConfirm={() => void run(() => cancelJob(job.id))}>取消</ConfirmButton>
        )}
      </div>
      {live && <Progress value={job.progress} className="mt-2" label="对齐进度" />}
      <div className={cn('mt-1.5 text-xs break-words', job.status === 'failed' ? 'text-danger' : 'text-muted')}>
        {job.error ?? job.message}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ results

function ResultsCard() {
  const project = useProject()!;
  const view = useView()!;
  const resultId = useApp((s) => s.resultId);
  const [importOpen, setImportOpen] = useState(false);
  const results = [...view.results].reverse();

  const activate = (id: string) => run(async () => {
    setPV(await api.post<ProjectView>(ppath(`/results/${id}/activate`)));
    toast('ok', '已设为当前结果');
  });

  return (
    <Card>
      <CardHeader
        title="对齐结果"
        help={<HelpButton k="align.results" />}
        description="每次对齐生成一个新结果；局部重跑生成局部结果，不会覆盖原结果。"
        actions={<Button size="sm" variant="outline" onClick={() => setImportOpen(true)} icon={<FileJson className="size-4" />}>导入 alignment.json</Button>}
      />
      <div className="p-2">
        {results.length === 0 ? (
          <EmptyState className="m-2" title="还没有结果" description="设置好后点击“开始对齐”" />
        ) : (
          <ul className="space-y-1">
            {results.map((r) => {
              const active = r.id === project.active_result_id;
              return (
                <li key={r.id} className={cn('rounded-xl px-3 py-3 transition', r.id === resultId ? 'bg-accent-soft/50' : 'hover:bg-surface-2')}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] font-medium">{fmtRelative(r.created)}</span>
                    {active && <Badge tone="accent"><Star className="size-3 fill-current" />当前</Badge>}
                    <Badge>{r.mode === 'lrc' ? 'LRC 增强' : '普通'}</Badge>
                    <Badge>{ROLE_LABEL[r.audio_role] ?? r.audio_role}</Badge>
                    <Badge>{r.backend}</Badge>
                    {!r.coverage.full && <Badge tone="info" title={r.parent_result_id ? `基于 ${r.parent_result_id}` : undefined}>局部 · {r.coverage.line_ids.length} 行</Badge>}
                    {r.stale && <Badge tone="warn" dot>已过期</Badge>}
                    <div className="ml-auto flex gap-1">
                      {!active && r.coverage.full && <Button size="xs" variant="ghost" onClick={() => activate(r.id)}>设为当前</Button>}
                      <Button size="xs" variant="soft" onClick={() => { selectResult(r.id); setStep('review'); }} icon={<Eye className="size-3.5" />}>查看</Button>
                    </div>
                  </div>
                  <div className="tabular mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
                    <Metric label="单元" value={r.n_units} />
                    <Metric label="无时间" value={r.n_failed} warn={r.n_failed > 0} />
                    <Metric label="提示" value={r.n_issues} warn={r.n_issues > 0} />
                    <Metric label="人工修改" value={r.n_manual} />
                    {r.coverage.from_ms !== null && <span>范围 {fmtMs(r.coverage.from_ms)} – {fmtMs(r.coverage.to_ms)}</span>}
                  </div>
                  {r.stale && r.stale_reason && <div className="mt-1.5 text-xs text-warn">{r.stale_reason}（仍可查看）</div>}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <ImportResultDialog open={importOpen} onOpenChange={setImportOpen} />
    </Card>
  );
}

function Metric({ label, value, warn }: { label: string; value: ReactNode; warn?: boolean }) {
  return <span>{label} <span className={cn('font-semibold', warn ? 'text-warn' : 'text-fg')}>{value}</span></span>;
}

function ImportResultDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = (t: string) => run(async () => {
    setBusy(true);
    try {
      const pv = await api.post<ProjectView>(ppath('/results/import'), { text: t });
      setPV(pv);
      toast('ok', '已导入结果', pv.result_id ? '已作为非当前结果加入列表' : undefined);
      setText('');
      onOpenChange(false);
    } finally {
      setBusy(false);
    }
  }, '导入失败');
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      wide
      title="导入 alignment.json"
      description="结果中的单元必须属于当前歌词；与当前输入不一致时会被标记为过期。"
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
        <Button variant="primary" loading={busy} disabled={!text.trim()} onClick={() => submit(text)}>导入</Button>
      </>}
    >
      <div className="space-y-3">
        <DropZone compact accept=".json,application/json" title="上传 alignment.json" hint="或在下方粘贴内容"
          onFile={(f) => run(async () => setText(await readFileText(f)))} />
        <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder='{"format": "kara-align/alignment", ...}' className="min-h-56" />
      </div>
    </Dialog>
  );
}
