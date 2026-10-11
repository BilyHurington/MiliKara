// Reading editor: every sung line as ruby-style segment chips. Rules never
// overwrite manual / AI / confirmed readings; clicking a chip edits it.

import { Languages, Lock, Sparkles, Wand2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/format';
import type { Line, ProjectView, Segment } from '@/lib/types';
import { ppath, run, setPV, toast, useProject, useView } from '@/store/app';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, Dialog, EmptyState, Field, Input, Segmented, Switch, Tip,
} from '@/components/ui';

type Filter = 'all' | 'uncertain' | 'manual';

const SOURCE: Record<Segment['reading_source'], { label: string; chip: string; tone: 'accent' | 'ok' | 'info' | 'neutral' }> = {
  manual: { label: '人工', chip: 'border-ok/50 bg-ok-soft', tone: 'ok' },
  ai: { label: 'AI', chip: 'border-accent/40 bg-accent-soft', tone: 'accent' },
  rule: { label: '规则', chip: 'border-line bg-surface-2', tone: 'neutral' },
  import: { label: '导入', chip: 'border-info/40 bg-info-soft', tone: 'info' },
  none: { label: '无', chip: 'border-dashed border-line-strong bg-surface', tone: 'neutral' },
};

const needsCheck = (s: Segment) => s.uncertain && !s.confirmed;
/** Words (letters, digits) still without a reading: shown as chips too, so they can be given one;
 *  punctuation and spaces stay plain text. */
const readable = (s: Segment) => s.units.length > 0 || /[\p{L}\p{N}]/u.test(s.surface);

export function ReadingsCard() {
  const project = useProject()!;
  const view = useView()!;
  const [filter, setFilter] = useState<Filter>('all');
  const [overwrite, setOverwrite] = useState(true);
  const [busy, setBusy] = useState(false);
  const [lastReport, setLastReport] = useState<{ prepared: string[]; kept: string[]; rederived: string[]; regrouped?: string[]; messages: string[] } | null>(null);
  const [editing, setEditing] = useState<{ line: Line; seg: Segment } | null>(null);

  const lines = useMemo(() => project.lyrics.lines.filter((l) => l.sing && l.kind === 'lyric'), [project.lyrics.lines]);
  const counts = useMemo(() => {
    let uncertain = 0;
    let manual = 0;
    for (const l of lines) for (const s of l.segments) {
      if (needsCheck(s)) uncertain += 1;
      if (s.reading_source === 'manual' || s.confirmed) manual += 1;
    }
    return { uncertain, manual };
  }, [lines]);

  const shown = lines.filter((l) => {
    if (filter === 'uncertain') return l.segments.some(needsCheck);
    if (filter === 'manual') return l.segments.some((s) => s.reading_source === 'manual' || s.confirmed);
    return true;
  });

  const prepare = () => run(async () => {
    setBusy(true);
    try {
      const pv = await api.post<ProjectView & { report: any }>(ppath('/readings/prepare'), { overwrite_rule: overwrite });
      setPV(pv);
      setLastReport(pv.report);
      const regrouped = pv.report?.regrouped?.length ?? 0;
      toast('ok', '规则注音完成', `填充 ${pv.report?.prepared?.length ?? 0} 行，保留 ${pv.report?.kept?.length ?? 0} 行`
        + (regrouped ? `，${regrouped} 行按词重新分组` : ''));
    } finally {
      setBusy(false);
    }
  }, '规则注音失败');

  return (
    <Card>
      <CardHeader
        icon={<Languages className="size-4" />}
        title="读音与发音单元"
        description="每个片段上方为原文，下方为获得时间的发音单元。点击片段即可修改读音；人工 / AI / 已确认的读音不会被规则覆盖。"
        actions={
          <>
            <Tip content="关闭时只为还没有读音的片段填充规则读音">
              <span><Switch checked={overwrite} onChange={setOverwrite} label={<span className="text-muted">覆盖旧的规则读音</span>} /></span>
            </Tip>
            <Button variant="soft" size="sm" icon={<Wand2 className="size-4" />} loading={busy} onClick={prepare}>规则注音</Button>
          </>
        }
      />
      <CardBody className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Segmented<Filter> label="筛选读音"
            size="sm"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: `全部 ${lines.length}` },
              { value: 'uncertain', label: `待确认 ${counts.uncertain}` },
              { value: 'manual', label: `人工 ${counts.manual}` },
            ]}
          />
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
            {(['manual', 'ai', 'rule'] as const).map((k) => <Badge key={k} tone={SOURCE[k].tone}>{SOURCE[k].label}</Badge>)}
            <Badge tone="warn">不确定</Badge>
            <span className="flex items-center gap-1"><Lock className="size-3" />已确认</span>
          </div>
        </div>

        <p className="text-xs leading-5 text-subtle">
          日语按拍整理：拗音合并（きゃ），促音「っ」、拨音「ん」、长音「ー」各自保留为单元。单纯改变分组而不改变读音不会改善声学路径；
          真正的提升来自正确读音。
        </p>

        {view.capability_warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}

        {lastReport && (
          <Callout tone="ok" title="规则注音结果">
            填充 {lastReport.prepared.length} 行 · 保留 {lastReport.kept.length} 行（人工/AI/已确认）
            {lastReport.rederived.length > 0 && ` · ${lastReport.rederived.length} 行因原文变化重新生成，需重新确认`}
            {(lastReport.regrouped?.length ?? 0) > 0 && (
              <div className="text-xs">{lastReport.regrouped!.length} 行的片段已按词重新分组（如 好|き → 好き），读音和发音单元不变</div>
            )}
            {lastReport.messages.slice(0, 3).map((m) => <div key={m} className="text-xs">{m}</div>)}
          </Callout>
        )}

        {lines.length === 0 ? (
          <EmptyState icon={<Languages className="size-5" />} title="还没有参与对齐的歌词" description="请先在“音频与歌词”中导入歌词" />
        ) : shown.length === 0 ? (
          <EmptyState icon={<Sparkles className="size-5" />} title="没有符合筛选的行" description={filter === 'uncertain' ? '所有读音都已确认' : undefined} />
        ) : (
          <ol className="divide-y divide-line overflow-hidden rounded-xl border border-line">
            {shown.map((line) => (
              <LineRow key={line.id} line={line} index={project.lyrics.lines.indexOf(line) + 1} onEdit={(seg) => setEditing({ line, seg })} />
            ))}
          </ol>
        )}
      </CardBody>
      {editing && <EditReadingDialog line={editing.line} seg={editing.seg} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function LineRow({ line, index, onEdit }: { line: Line; index: number; onEdit: (s: Segment) => void }) {
  const noUnits = line.segments.every((s) => !readable(s));
  return (
    <li className="flex gap-4 px-4 py-3 hover:bg-surface-2/40">
      <span className="tabular w-7 shrink-0 pt-2 text-right text-xs text-subtle">{index}</span>
      <div className="min-w-0 flex-1">
        {noUnits ? (
          <div className="pt-1.5 text-sm text-muted">{line.text}<span className="ml-2 text-xs text-warn">（尚无读音，点击“规则注音”）</span></div>
        ) : (
          <div className="flex flex-wrap items-end gap-1.5">
            {line.segments.map((seg) => (!readable(seg)
              ? <span key={seg.id} className="px-0.5 pb-1 text-sm text-subtle">{seg.surface}</span>
              : <SegmentChip key={seg.id} seg={seg} onClick={() => onEdit(seg)} />))}
          </div>
        )}
        {line.translation && <div className="mt-1.5 text-xs text-subtle">{line.translation}</div>}
      </div>
    </li>
  );
}

function SegmentChip({ seg, onClick }: { seg: Segment; onClick: () => void }) {
  const src = SOURCE[seg.reading_source] ?? SOURCE.none;
  const warn = needsCheck(seg);
  const tip = [
    `来源：${src.label}`,
    seg.units.length ? '' : '没有读音：点击填写（不填时这里不参与对齐）',
    seg.confirmed ? '已确认' : warn ? '不确定，建议确认' : '',
    seg.note,
    seg.candidates.length ? `候选：${seg.candidates.join(' / ')}` : '',
    seg.lang !== 'ja' ? `语言：${seg.lang}` : '',
  ].filter(Boolean).join(' · ');
  return (
    <Tip content={tip}>
      <button
        onClick={onClick}
        className={cn(
          'focus-ring group relative flex flex-col items-stretch rounded-lg border px-1.5 pt-1 pb-0.5 text-center transition hover:-translate-y-px hover:shadow-sm',
          src.chip,
          warn && 'ring-2 ring-warn/60',
        )}
      >
        <span className="px-0.5 text-[15px] leading-6 font-medium">{seg.surface}</span>
        <span className="flex justify-center divide-x divide-line-strong/60 border-t border-line/70 pt-0.5">
          {seg.units.length ? seg.units.map((u) => (
            <span key={u.id} className="px-1 text-[11px] leading-4 text-muted">{u.reading}</span>
          )) : <span className="px-1 text-[11px] leading-4 font-bold text-warn" aria-label="没有读音">？</span>}
        </span>
        {seg.confirmed && <Lock className="absolute -top-1.5 -right-1.5 size-3.5 rounded-full bg-surface p-0.5 text-ok shadow" />}
        {!seg.confirmed && seg.candidates.length > 0 && (
          <span className="absolute -top-1.5 -right-1.5 grid size-3.5 place-items-center rounded-full bg-warn text-[9px] font-bold text-white">{seg.candidates.length}</span>
        )}
      </button>
    </Tip>
  );
}

/** Same unit splitting the server applies when units are given: '/' or whitespace. */
function splitUnits(text: string): string[] {
  return text.split(/[\s/／]+/).map((x) => x.trim()).filter(Boolean);
}

function EditReadingDialog({ line, seg, onClose }: { line: Line; seg: Segment; onClose: () => void }) {
  const [reading, setReading] = useState(seg.reading ?? '');
  const [units, setUnits] = useState(seg.units.map((u) => u.reading).join(' / '));
  const [confirm, setConfirm] = useState(true);
  const [busy, setBusy] = useState(false);
  const unitList = splitUnits(units);
  const joined = unitList.join('');
  const mismatch = unitList.length > 0 && reading.trim() !== '' && joined !== reading.trim();

  const pick = (cand: string) => {
    setReading(cand);
    setUnits('');
  };

  const save = () => run(async () => {
    setBusy(true);
    try {
      const pv = await api.put<ProjectView>(ppath(`/lines/${line.id}/segments/${seg.id}`), {
        reading: reading.trim(),
        units: unitList.length ? unitList : null,
        confirm,
      });
      setPV(pv);
      toast('ok', `已更新「${seg.surface}」`, `${reading.trim()}${confirm ? ' · 已确认' : ''}`);
      onClose();
    } finally {
      setBusy(false);
    }
  }, '保存读音失败');

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={<>修改读音：<span className="text-accent">{seg.surface}</span></>}
      description={<>所在行：{line.text}</>}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button variant="primary" loading={busy} disabled={!reading.trim() || mismatch} onClick={save}>保存</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="读音" hint={seg.lang === 'ja' ? '平假名或片假名（会统一为平假名）；写实际发音，例如助词 は → わ' : `语言：${seg.lang}`}>
          <Input autoFocus value={reading} onChange={(e) => setReading(e.target.value)} className="font-medium" />
        </Field>
        {seg.candidates.length > 0 && (
          <div>
            <div className="mb-1.5 text-[13px] font-medium">候选读音</div>
            <div className="flex flex-wrap gap-1.5">
              {seg.candidates.map((c) => (
                <Button key={c} size="xs" variant={c === reading ? 'soft' : 'secondary'} onClick={() => pick(c)}>{c}</Button>
              ))}
            </div>
          </div>
        )}
        <Field label="发音单元（可选）" hint="用 / 或空格分隔；留空则按拍自动拆分（拗音合并，促音、拨音、长音保留）">
          <Input value={units} onChange={(e) => setUnits(e.target.value)} placeholder="例如 き / み" />
        </Field>
        <div className="rounded-xl bg-surface-2 px-4 py-3">
          <div className="mb-2 text-xs font-medium text-muted">预览</div>
          <div className="flex flex-wrap items-center gap-1.5">
            {unitList.length ? unitList.map((u, i) => (
              <span key={i} className="rounded-md border border-line bg-surface px-2 py-0.5 text-sm">{u}</span>
            )) : <span className="text-xs text-subtle">将由程序按拍拆分</span>}
          </div>
          {mismatch && <div className="mt-2 text-xs text-danger">单元拼起来（{joined}）必须等于读音（{reading.trim()}）</div>}
        </div>
        <Switch checked={confirm} onChange={setConfirm} label="确认此读音（锁定，规则与 AI 不再覆盖）" />
        <p className="text-xs text-subtle">单元分组改变时会生成新的单元 ID，旧的人工时间不会被误绑到新单元上。</p>
      </div>
    </Dialog>
  );
}
