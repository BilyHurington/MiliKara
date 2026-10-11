// AI reading round trip through any web chat: copy prompt → paste reply →
// validate → preview diff → apply selected lines. No LLM API, no key.

import { ArrowRight, Bot, ClipboardCopy, ClipboardPaste, FileCheck2, History, RotateCcw, Settings2, Wand2, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { cn, copyText, fmtRelative, readFileText, toKatakana } from '@/lib/format';
import type { Job, PatchLine, ProjectView } from '@/lib/types';
import { cancelJob, ppath, run, setPV, toast, trackJob, useJob, useProject } from '@/store/app';
import { useDraft } from '@/store/drafts';
import { loadSettings, useSimple } from '@/store/simple';
import { AiSettingsForm } from '@/components/AiSettingsForm';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, ConfirmButton, DropZone, Progress, Segmented, Textarea,
} from '@/components/ui';

interface Report {
  ok: boolean;
  snapshot: string | null;
  roundtrip_id: string | null;
  errors: string[];
  warnings: string[];
  lines: (PatchLine & { diff: (PatchLine['diff'][number] & { changed?: boolean; locked?: boolean })[] })[];
  missing_line_ids: string[];
}

const STATUS: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' | 'neutral' | 'info' }> = {
  ok: { label: '可应用', tone: 'ok' },
  unchanged: { label: '无变化', tone: 'neutral' },
  stale_text: { label: '原文已变', tone: 'warn' },
  stale_reading: { label: '读音已变', tone: 'warn' },
  unknown_line: { label: '未知行', tone: 'danger' },
  locked_skipped: { label: '已锁定跳过', tone: 'info' },
  invalid: { label: '无效', tone: 'danger' },
  duplicate: { label: '重复', tone: 'danger' },
};

const RT_STATUS: Record<string, string> = { prompted: '已生成提示词', validated: '已校验', applied: '已应用', rejected: '已拒绝' };

export function AiRoundtripCard() {
  const project = useProject()!;
  const sung = useMemo(() => project.lyrics.lines.filter((l) => l.sing && l.kind === 'lyric'), [project.lyrics.lines]);
  const uncertainIds = useMemo(
    () => sung.filter((l) => l.segments.some((s) => s.uncertain && !s.confirmed)).map((l) => l.id),
    [sung],
  );

  // step 1
  const [scope, setScope] = useState<'all' | 'uncertain'>('all');
  const [prompt, setPrompt] = useState<{ prompt: string; snapshot_id: string; copied: boolean } | null>(null);
  const [busyPrompt, setBusyPrompt] = useState(false);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  // step 2 (the pasted reply is kept while the page is left)
  const [reply, setReply] = useDraft('ai.reply', '');
  const [busyValidate, setBusyValidate] = useState(false);
  // step 3
  const [report, setReport] = useState<{ id: string; report: Report } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busyApply, setBusyApply] = useState(false);

  // a one-click AI reading that finished while this page was not open (or before a reload): show its report
  const aiJob = useJob('ai');
  const restored = useRef<string | null>(null);
  useEffect(() => {
    if (!aiJob || aiJob.status !== 'succeeded' || restored.current === aiJob.id || report) return;
    const out = aiJob.output as { report_id?: string; report?: Report } | null;
    if (!out?.report_id || !out.report) return;
    restored.current = aiJob.id;
    const rt = out.report.roundtrip_id ? project.ai_roundtrips.find((x) => x.id === out.report!.roundtrip_id) : null;
    if (rt && (rt.status === 'applied' || rt.status === 'rejected')) return;  // already dealt with
    showReport({ report_id: out.report_id, report: out.report });
  }, [aiJob, report]); // eslint-disable-line react-hooks/exhaustive-deps

  // text routed here from the lyrics page
  useEffect(() => {
    try {
      const t = sessionStorage.getItem('kara.aiPaste');
      if (t) {
        setReply(t);
        sessionStorage.removeItem('kara.aiPaste');
        toast('info', '已填入 AI 注音补丁', '请点击“校验”');
      }
    } catch { /* ignore */ }
  }, []);

  const makePrompt = () => run(async () => {
    setBusyPrompt(true);
    try {
      const body = scope === 'uncertain' ? { line_ids: uncertainIds } : {};
      const out = await api.post<{ prompt: string; snapshot_id: string }>(ppath('/ai/prompt'), body);
      const copied = await copyText(out.prompt);
      setPrompt({ ...out, copied });
      toast(copied ? 'ok' : 'warn', copied ? '提示词已复制' : '无法自动复制', copied ? '粘贴到任意网页聊天中' : '请在下方文本框中手动全选复制');
    } finally {
      setBusyPrompt(false);
    }
  }, '生成提示词失败');

  const validate = () => run(async () => {
    setBusyValidate(true);
    try {
      showReport(await api.post<{ report_id: string; report: Report }>(ppath('/ai/validate'), { text: reply }));
    } finally {
      setBusyValidate(false);
    }
  }, '校验失败');

  const apply = () => run(async () => {
    if (!report) return;
    setBusyApply(true);
    try {
      const pv = await api.post<ProjectView & { summary?: { applied?: string[]; unchanged?: string[]; skipped?: string[] } }>(
        ppath('/ai/apply'), { report_id: report.id, line_ids: [...selected] },
      );
      setPV(pv);
      const s = pv.summary ?? {};
      toast('ok', 'AI 注音已应用', `应用 ${s.applied?.length ?? 0} 行 · 无变化 ${s.unchanged?.length ?? 0} · 跳过 ${s.skipped?.length ?? 0}`);
      setReport(null);
      setReply('');
    } finally {
      setBusyApply(false);
    }
  }, '应用失败');

  const showReport = (out: { report_id: string; report: Report }) => {
    setReport({ id: out.report_id, report: out.report });
    setSelected(new Set(out.report.lines.filter((l) => l.status === 'ok').map((l) => l.line_id)));
  };

  const lineText = (id: string) => project.lyrics.lines.find((l) => l.id === id)?.text ?? id;
  const lineIndex = (id: string) => project.lyrics.lines.findIndex((l) => l.id === id) + 1;

  return (
    <Card>
      <CardHeader
        icon={<Bot className="size-4" />}
        title="AI 注音"
        description="让 AI 检查每个片段的读音。可以一键交给本机的 Claude Code / Codex 或 API，也可以复制提示词到任意网页聊天再贴回结果。AI 的回复只作为注音补丁：校验并预览后才会应用，不能修改时间、偏移或锁定的读音。"
      />
      <CardBody className="space-y-6">
        <AutoAi scope={scope} setScope={setScope} total={sung.length} uncertainIds={uncertainIds} />

        <div className="flex items-center gap-3 text-xs font-medium text-subtle">
          <span className="h-px flex-1 bg-line" />或者：网页聊天往返（复制提示词 → 粘贴回复）<span className="h-px flex-1 bg-line" />
        </div>

        {/* step 1 */}
        <Step n={1} title="生成并复制提示词" done={!!prompt}>
          <div className="flex flex-wrap items-center gap-3">
            <Segmented<'all' | 'uncertain'> label="提示词范围"
              size="sm"
              value={scope}
              onChange={setScope}
              options={[
                { value: 'all', label: `全部 ${sung.length} 行` },
                { value: 'uncertain', label: `仅待确认 ${uncertainIds.length} 行`, disabled: uncertainIds.length === 0 },
              ]}
            />
            <Button variant="primary" size="sm" icon={<ClipboardCopy className="size-4" />} loading={busyPrompt}
              disabled={sung.length === 0} onClick={makePrompt}>
              复制 AI 提示词
            </Button>
            {prompt && (
              <span className="text-xs text-muted">
                快照 <code className="rounded bg-surface-2 px-1 font-mono">{prompt.snapshot_id}</code>
                {prompt.copied ? ' · 已复制到剪贴板' : ''}
              </span>
            )}
          </div>
          {prompt && !prompt.copied && (
            <div className="mt-3 space-y-2">
              <Callout tone="warn">浏览器不允许自动复制，请手动全选下方内容并复制。</Callout>
              <Textarea ref={promptRef} readOnly value={prompt.prompt} className="h-48" onFocus={(e) => e.currentTarget.select()} />
              <Button size="xs" onClick={() => { promptRef.current?.focus(); promptRef.current?.select(); }}>全选</Button>
            </div>
          )}
          {prompt?.copied && (
            <details className="mt-3 text-xs text-muted">
              <summary className="cursor-pointer select-none hover:text-fg">查看提示词内容</summary>
              <Textarea readOnly value={prompt.prompt} className="mt-2 h-40" />
            </details>
          )}
        </Step>

        {/* step 2 */}
        <Step n={2} title="粘贴 AI 返回的结果" done={!!report}>
          <div className="grid gap-3 lg:grid-cols-[1fr_260px]">
            <Textarea
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              placeholder={'把网页聊天的回复整段粘贴到这里（可以包含说明文字或 ```json 代码块）'}
              className="h-40"
            />
            <div className="flex flex-col gap-3">
              <DropZone accept=".json,.txt,application/json,text/plain" compact
                title="或上传 JSON / 文本文件"
                onFile={(f) => run(async () => setReply(await readFileText(f)), '读取文件失败')} />
              <Button variant="primary" icon={<FileCheck2 className="size-4" />} loading={busyValidate}
                disabled={!reply.trim()} onClick={validate}>
                校验
              </Button>
              {reply && <Button variant="ghost" size="sm" icon={<RotateCcw className="size-4" />} onClick={() => { setReply(''); setReport(null); }}>清空</Button>}
            </div>
          </div>
        </Step>

        {/* step 3 */}
        <Step n={3} title="预览并应用" last>
          {!report ? (
            <p className="text-[13px] text-subtle">校验后在这里逐行预览读音变化，选择要应用的行。</p>
          ) : (
            <ReportView
              report={report.report}
              selected={selected}
              setSelected={setSelected}
              lineText={lineText}
              lineIndex={lineIndex}
              busy={busyApply}
              onApply={apply}
            />
          )}
        </Step>

        {project.ai_roundtrips.length > 0 && (
          <div>
            <div className="mb-2 flex items-center gap-2 text-[13px] font-medium"><History className="size-4 text-muted" />往返记录</div>
            <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line text-[13px]">
              {[...project.ai_roundtrips].reverse().slice(0, 8).map((rt) => (
                <li key={rt.id} className="flex items-center gap-3 px-4 py-2">
                  <span className="text-muted">{fmtRelative(rt.created)}</span>
                  <code className="font-mono text-xs text-subtle">{rt.snapshot_id}</code>
                  <span className="text-muted">{rt.line_ids.length} 行</span>
                  <Badge className="ml-auto" tone={rt.status === 'applied' ? 'ok' : rt.status === 'rejected' ? 'danger' : 'neutral'}>
                    {RT_STATUS[rt.status] ?? rt.status}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

const PROVIDER_LABEL: Record<string, string> = { claude: 'Claude Code', codex: 'Codex', openai: 'API' };

/** One click: the server sends the prompt to the configured CLI / API and validates the reply. */
function AutoAi({ scope, setScope, total, uncertainIds }: {
  scope: 'all' | 'uncertain'; setScope: (s: 'all' | 'uncertain') => void; total: number; uncertainIds: string[];
}) {
  const settings = useSimple((s) => s.settings);
  const settingsError = useSimple((s) => s.settingsError);
  const job = useJob('ai');
  const [editing, setEditing] = useState(false);
  const running = !!job && (job.status === 'queued' || job.status === 'running');
  useEffect(() => { if (!settings && !settingsError) void run(() => loadSettings(), '读取设置失败'); }, [settings, settingsError]);
  if (!settings) {
    return settingsError ? (
      <Callout tone="warn" title="读取 AI 设置失败" actions={<Button size="xs" onClick={() => void run(() => loadSettings(), '读取设置失败')}>重试</Button>}>
        {settingsError}
      </Callout>
    ) : null;
  }
  const ai = settings.ai;
  // one click needs an AI that can be sent the prompt (a CLI or an API); "by hand" is the steps below
  const configured = ai.enabled && ai.provider !== 'manual';
  // what the last reply cost (from the finished job, also after leaving the page)
  const out = job?.status === 'succeeded' ? job.output as { report_id: string; report: Report; meta?: { attempts: { elapsed_s: number; model: string }[]; cost_usd: number | null } } | null : null;
  const m = out?.meta;
  const meta = m ? (() => {
    const secs = m.attempts.reduce((a, x) => a + x.elapsed_s, 0);
    const model = m.attempts.at(-1)?.model;
    return `${Math.round(secs)} 秒${model ? ` · ${model}` : ''}${m.cost_usd != null ? ` · $${m.cost_usd.toFixed(3)}` : ''}${m.attempts.length > 1 ? ' · 自动重试了一次' : ''}`;
  })() : null;

  const start = () => run(async () => {
    const body = scope === 'uncertain' ? { line_ids: uncertainIds } : {};
    const j = await api.post<Job>(ppath('/ai/auto'), body);
    // the report shows up in step 3 once the job is done (see the restore in AiRoundtripCard)
    trackJob(j, { label: 'AI 注音', doneText: 'AI 注音已返回结果：请在第 3 步预览并应用' });
  }, '无法开始 AI 注音');

  return (
    <section className="rounded-xl border border-accent/30 bg-accent-soft/30 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <Wand2 className="size-4 text-accent" />
        <span className="text-sm font-semibold">一键 AI 注音</span>
        {configured && (
          <Badge tone="accent">{PROVIDER_LABEL[ai.provider]}{ai.model ? ` · ${ai.model}` : ''}</Badge>
        )}
        <Button size="xs" variant="ghost" icon={editing ? <X className="size-3.5" /> : <Settings2 className="size-3.5" />}
          onClick={() => setEditing(!editing)}>{editing ? '收起' : configured ? '更改' : '设置'}</Button>
      </div>
      {!configured && !editing && (
        <p className="mt-2 text-xs text-muted">
          {ai.enabled ? '当前设置为手动（网页聊天）：用下面的第 1–3 步复制提示词、粘贴回复。' : 'AI 注音已关闭；下面的第 1–3 步仍可手动网页聊天往返。'}
          要一键注音，点“设置”选择本机已登录的 Claude Code / Codex 命令，或 OpenAI 兼容 API（对所有项目通用）。
        </p>
      )}
      {editing && (
        <div className="mt-3 rounded-lg bg-surface p-3">
          <AiSettingsForm compact />
        </div>
      )}
      {configured && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Segmented<'all' | 'uncertain'> size="sm" label="AI 注音范围" value={scope} onChange={setScope} options={[
            { value: 'all', label: `全部 ${total} 行` },
            { value: 'uncertain', label: `仅待确认 ${uncertainIds.length} 行`, disabled: uncertainIds.length === 0 },
          ]} />
          <Button variant="primary" size="sm" icon={<Wand2 className="size-4" />} loading={running} disabled={total === 0} onClick={start}>
            开始 AI 注音
          </Button>
          {running && (
            <>
              <Progress value={job!.progress} className="w-32" label="AI 注音进度" />
              <span className="text-xs text-muted">{job!.message}</span>
              <ConfirmButton size="xs" variant="ghost" question="取消 AI 注音？" confirmLabel="取消" keepLabel="继续等待"
                onConfirm={() => void run(() => cancelJob(job!.id))}>取消</ConfirmButton>
            </>
          )}
          {!running && meta && <span className="text-xs text-muted">已收到回复：{meta}；在下方第 3 步预览并应用</span>}
          {!running && job?.status === 'failed' && <span className="text-xs text-danger">{job.error}</span>}
        </div>
      )}
    </section>
  );
}

function Step({ n, title, done, last, children }: { n: number; title: string; done?: boolean; last?: boolean; children: React.ReactNode }) {
  return (
    <section className="relative flex gap-4">
      {!last && <span className="absolute top-8 bottom-[-18px] left-[13px] w-px bg-line" aria-hidden />}
      <span className={cn('relative z-[1] grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold',
        done ? 'bg-ok text-white' : 'bg-accent-soft text-accent')}>{n}</span>
      <div className="min-w-0 flex-1 pt-0.5">
        <h4 className="mb-3 text-sm font-semibold">{title}</h4>
        {children}
      </div>
    </section>
  );
}

function ReportView({ report, selected, setSelected, lineText, lineIndex, busy, onApply }: {
  report: Report; selected: Set<string>; setSelected: (s: Set<string>) => void;
  lineText: (id: string) => string; lineIndex: (id: string) => number; busy: boolean; onApply: () => void;
}) {
  const applicable = report.lines.filter((l) => l.status === 'ok');
  const toggle = (id: string) => {
    const s = new Set(selected);
    if (s.has(id)) s.delete(id); else s.add(id);
    setSelected(s);
  };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={report.ok ? 'ok' : 'danger'} dot>{report.ok ? '格式有效' : '存在错误'}</Badge>
        {report.snapshot && <Badge tone="neutral">快照 {report.snapshot}</Badge>}
        <Badge tone="accent">{applicable.length} / {report.lines.length} 行可应用</Badge>
      </div>
      {report.errors.map((e) => <Callout key={e} tone="danger">{e}</Callout>)}
      {report.warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}
      {report.missing_line_ids?.length > 0 && (
        <Callout tone="warn" title={`AI 结果缺少 ${report.missing_line_ids.length} 行`}>
          可能用“同上”省略了重复副歌；这些行保持原读音。
        </Callout>
      )}

      {report.lines.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-line">
          <div className="flex items-center gap-3 border-b border-line bg-surface-2 px-4 py-2 text-xs text-muted">
            <input type="checkbox" className="size-4 accent-[var(--c-accent)]"
              checked={applicable.length > 0 && applicable.every((l) => selected.has(l.line_id))}
              onChange={(e) => setSelected(new Set(e.target.checked ? applicable.map((l) => l.line_id) : []))} />
            全选可应用的行
          </div>
          <ul className="max-h-[480px] divide-y divide-line overflow-y-auto">
            {report.lines.map((l) => {
              const st = STATUS[l.status] ?? { label: l.status, tone: 'neutral' as const };
              const can = l.status === 'ok';
              return (
                <li key={l.line_id} className={cn('flex gap-3 px-4 py-3', !can && 'bg-surface-2/40')}>
                  <input type="checkbox" className="mt-1 size-4 accent-[var(--c-accent)]" disabled={!can}
                    checked={selected.has(l.line_id)} onChange={() => toggle(l.line_id)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="tabular text-xs text-subtle">#{lineIndex(l.line_id) || '?'}</span>
                      <span className="text-[13px] font-medium">{lineText(l.line_id)}</span>
                      <Badge tone={st.tone}>{st.label}</Badge>
                    </div>
                    {l.reasons.length > 0 && <div className="mt-1 text-xs text-muted">{l.reasons.join('；')}</div>}
                    {l.diff.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {l.diff.map((d, i) => <DiffChip key={i} d={d} />)}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-end gap-3">
        <span className="text-xs text-muted">已选 {selected.size} 行；原文或读音已变化的行会被跳过</span>
        <Button variant="primary" icon={<ClipboardPaste className="size-4" />} loading={busy} disabled={selected.size === 0} onClick={onApply}>
          应用所选 {selected.size} 行
        </Button>
      </div>
    </div>
  );
}

function DiffChip({ d }: { d: Report['lines'][number]['diff'][number] }) {
  const changed = d.changed ?? (d.old_units.join('/') !== d.new_units.join('/'));
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs',
      changed ? 'border-accent/40 bg-accent-soft' : 'border-line bg-surface-2 text-muted')}>
      <span className="font-medium text-fg">{d.surface}</span>
      {d.hidden ? (
        <span className="text-subtle">括号里是读音：字幕里不显示</span>
      ) : changed ? (
        <>
          <span className="text-subtle line-through">{d.old_units.join('/') || '—'}</span>
          <ArrowRight className="size-3 text-accent" />
          <span className="font-medium text-accent">{(d.katakana ? d.new_units.map(toKatakana) : d.new_units).join('/') || '—'}</span>
        </>
      ) : (
        <span>{d.new_units.join('/') || d.old_units.join('/')}</span>
      )}
      {d.locked && <Badge tone="info">锁定</Badge>}
    </span>
  );
}
