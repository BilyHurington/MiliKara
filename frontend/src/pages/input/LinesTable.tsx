// The project's current lyric lines: inline edit, kind / sing, anchors, merge, split, delete.

import { Anchor, Combine, ListOrdered, Scissors } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { cn, fmtMs } from '@/lib/format';
import { isEnter, isEscape } from '@/lib/keys';
import type { Line, ProjectView } from '@/lib/types';
import { player } from '@/audio/player';
import { ppath, run, setPV, toast, useProject, useView } from '@/store/app';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, Dialog, EmptyState, Field, IconButton, NumberInput, Select, Switch, Table, Td, Th, Tip,
} from '@/components/ui';
import { DeleteLineButton } from '@/components/DeleteLineButton';
import { KIND_LABEL } from './PreviewPanel';
import { HelpButton } from '@/components/guide/Help';

export function LinesTable() {
  const project = useProject()!;
  const view = useView()!;
  const lines = project.lyrics.lines;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchorLine, setAnchorLine] = useState<Line | null>(null);
  const [splitLine, setSplitLine] = useState<Line | null>(null);
  const lrc = project.mode === 'lrc';

  const patchLine = (id: string, body: Record<string, unknown>) => run(async () => {
    setPV(await api.patch<ProjectView>(ppath(`/lines/${id}`), body));
  }, '修改失败');

  const merge = () => run(async () => {
    const ids = lines.filter((l) => selected.has(l.id)).map((l) => l.id);
    setPV(await api.post<ProjectView>(ppath('/lines/merge'), { line_ids: ids }));
    setSelected(new Set());
    toast('ok', `已合并 ${ids.length} 行`, '保留首行时间与来源信息');
  }, '合并失败');

  const counts = useMemo(() => ({
    sung: lines.filter((l) => l.sing && l.kind === 'lyric').length,
    anchors: lines.filter((l) => l.anchor).length,
  }), [lines]);

  const toggle = (id: string) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  return (
    <Card>
      <CardHeader
        title="当前歌词"
        icon={<ListOrdered className="size-4" />}
        help={<HelpButton k="input.lines" />}
        description="可以直接改文字、类型和是否参与对齐；不唱的行（如结尾的“(終わり)”）可以删除。"
        actions={lines.length > 0 ? (
          <>
            <Badge tone="accent">{counts.sung} 行参与对齐</Badge>
            {counts.anchors > 0 && <Badge tone="ok">{counts.anchors} 个人工锚点</Badge>}
            <Button size="sm" disabled={selected.size < 2} onClick={merge} icon={<Combine className="size-4" />}>
              合并所选{selected.size >= 2 ? `（${selected.size}）` : ''}
            </Button>
          </>
        ) : undefined}
      />
      <CardBody className="space-y-3">
        {view.mode_notice && <Callout tone="warn">{view.mode_notice}</Callout>}
        {view.capability_warnings.map((w) => <Callout key={w} tone="info">{w}</Callout>)}

        {lines.length === 0 ? (
          <EmptyState icon={<ListOrdered className="size-5" />} title="还没有歌词" description="在上方粘贴、上传或通过音乐链接获取歌词" />
        ) : (
          <Table className="max-h-[560px]">
            <thead>
              <tr>
                <Th className="w-9" />
                <Th className="w-10">#</Th>
                <Th className="w-28">{lrc ? '有效句首' : '导入时间'}</Th>
                <Th>文本</Th>
                <Th className="w-28">类型</Th>
                <Th className="w-16">对齐</Th>
                <Th className="w-24 text-right">操作</Th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => {
                const eff = view.effective_starts[l.id];
                const active = l.sing && l.kind === 'lyric';
                return (
                  <tr key={l.id} className={cn('group', selected.has(l.id) && 'bg-accent-soft/40', !active && 'text-muted')}>
                    <Td>
                      <input
                        type="checkbox"
                        className="size-4 cursor-pointer accent-[var(--c-accent)]"
                        checked={selected.has(l.id)}
                        onChange={() => toggle(l.id)}
                        aria-label={`选择第 ${i + 1} 行`}
                      />
                    </Td>
                    <Td className="tabular text-subtle">{i + 1}</Td>
                    <Td className="tabular font-mono text-xs">
                      <TimeCell line={l} eff={lrc ? eff : undefined} />
                    </Td>
                    <Td>
                      <InlineText value={l.text} onCommit={(t) => patchLine(l.id, { text: t })} />
                      {(l.translation || l.romanization) && (
                        <div className="mt-0.5 truncate px-2 text-xs text-subtle">{l.translation ?? l.romanization}</div>
                      )}
                      {(l.source.merged_from.length > 0 || l.source.split_from) && (
                        <div className="mt-0.5 px-2 text-[11px] text-subtle">
                          {l.source.merged_from.length > 0 ? `由 ${l.source.merged_from.length} 行合并` : '由拆分产生'}
                        </div>
                      )}
                    </Td>
                    <Td>
                      <Select className="h-8 text-xs" value={l.kind} onChange={(e) => patchLine(l.id, { kind: e.target.value })} aria-label="类型">
                        {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </Select>
                    </Td>
                    <Td><Switch checked={l.sing} ariaLabel={`第 ${i + 1} 行参与对齐`} onChange={(v) => patchLine(l.id, { sing: v })} /></Td>
                    <Td className="text-right">
                      <div className="flex justify-end gap-0.5 opacity-60 transition group-hover:opacity-100">
                        <IconButton label="单行锚点（原音频绝对时间）" size="xs" onClick={() => setAnchorLine(l)}>
                          <Anchor className={cn('size-3.5', l.anchor && 'text-ok')} />
                        </IconButton>
                        <IconButton label="拆分此行" size="xs" onClick={() => setSplitLine(l)} disabled={[...l.text].length < 2}>
                          <Scissors className="size-3.5" />
                        </IconButton>
                        <DeleteLineButton line={l} index={i + 1} />
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </CardBody>
      {anchorLine && <AnchorDialog line={anchorLine} onClose={() => setAnchorLine(null)} />}
      {splitLine && <SplitDialog line={splitLine} onClose={() => setSplitLine(null)} />}
    </Card>
  );
}

function TimeCell({ line, eff }: { line: Line; eff?: { ms: number; kind: 'soft' | 'hard' } }) {
  if (line.anchor) {
    return (
      <Tip content={`人工锚点（${line.anchor.hard ? '硬' : '软'}，±${line.anchor.tolerance_ms} ms），不随全局平移移动`}>
        <span className="inline-flex items-center gap-1 text-ok"><Anchor className="size-3" />{fmtMs(line.anchor.abs_ms)}</span>
      </Tip>
    );
  }
  if (eff) return <span>{fmtMs(eff.ms)}</span>;
  if (line.imported_start_ms !== null) return <span className="text-muted">{fmtMs(line.imported_start_ms)}</span>;
  return <span className="text-subtle">—</span>;
}

function InlineText({ value, onCommit }: { value: string; onCommit: (v: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = (v: string) => {
    setDraft(null);
    if (v !== value && v.trim()) onCommit(v);
  };
  return (
    <input
      className="focus-ring w-full rounded-md border border-transparent bg-transparent px-2 py-1 text-[13px] transition hover:border-line focus:border-accent focus:bg-surface"
      value={draft ?? value}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={(e) => commit(e.currentTarget.value)}
      onKeyDown={(e) => {
        if (isEnter(e)) e.currentTarget.blur();
        if (isEscape(e)) { setDraft(null); (e.currentTarget as HTMLInputElement).value = value; e.currentTarget.blur(); }
      }}
      title="编辑文本（修改后该行读音会重新生成，旧结果标记为过期）"
      aria-label="歌词文本（回车保存，Esc 取消）"
    />
  );
}

function AnchorDialog({ line, onClose }: { line: Line; onClose: () => void }) {
  const [ms, setMs] = useState<number | null>(line.anchor?.abs_ms ?? null);
  const [hard, setHard] = useState(line.anchor?.hard ?? true);
  const [tol, setTol] = useState<number | null>(line.anchor?.tolerance_ms ?? 80);

  const save = (abs: number | null) => run(async () => {
    setPV(await api.put<ProjectView>(ppath(`/lines/${line.id}/anchor`), { abs_ms: abs, hard, tolerance_ms: tol ?? 80 }));
    toast('ok', abs === null ? '已清除锚点' : '已设置锚点');
    onClose();
  }, '设置锚点失败');

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="单行锚点"
      description={line.text}
      footer={(
        <>
          {line.anchor && <Button variant="ghost" onClick={() => save(null)}>清除锚点</Button>}
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={ms === null} onClick={() => save(ms)}>保存</Button>
        </>
      )}
    >
      <div className="space-y-4">
        <p className="text-[13px] text-muted">
          使用原音频绝对时间，不随全局平移移动。适合中段 / 末段与整体平移不一致（版本或速度差异）时逐行约束；程序不会自动拉伸整曲时间。
        </p>
        <Field group label="句首时间（ms）" hint={ms !== null ? fmtMs(ms) : undefined}>
          <div className="flex gap-2">
            <NumberInput value={ms} onCommit={(v) => setMs(v === null ? null : Math.max(0, Math.round(v)))} suffix="ms" min={0} className="flex-1" />
            <Button size="sm" onClick={() => setMs(Math.round(player.positionMs()))}>取播放头</Button>
          </div>
        </Field>
        <div className="flex flex-wrap items-center gap-6">
          <Switch checked={hard} onChange={setHard} label={hard ? '硬锚点（必须落在容差内）' : '软锚点（作为先验）'} />
          <Field label="容差" className="w-36">
            <NumberInput value={tol} onCommit={(v) => setTol(v === null ? 80 : Math.max(0, Math.round(v)))} suffix="ms" min={0} />
          </Field>
        </div>
      </div>
    </Dialog>
  );
}

function SplitDialog({ line, onClose }: { line: Line; onClose: () => void }) {
  const chars = [...line.text];
  const [at, setAt] = useState(Math.floor(chars.length / 2));

  const save = () => run(async () => {
    // server expects a character index into the Python string (code points)
    setPV(await api.post<ProjectView>(ppath(`/lines/${line.id}/split`), { at }));
    toast('ok', '已拆分', '后半行不带时间，不会被分配伪时间');
    onClose();
  }, '拆分失败');

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="拆分歌词行"
      description="点击两个字之间选择拆分位置"
      footer={<><Button onClick={onClose}>取消</Button><Button variant="primary" disabled={at <= 0 || at >= chars.length} onClick={save}>拆分</Button></>}
    >
      <div className="flex flex-wrap items-center rounded-xl border border-line bg-surface-2/50 p-4 text-lg leading-10">
        {chars.map((c, i) => (
          <span key={i} className="flex items-center">
            {i > 0 && (
              <button
                onClick={() => setAt(i)}
                aria-label={`在第 ${i} 个字后拆分`}
                className={cn('mx-0.5 h-8 w-1.5 rounded-full transition', at === i ? 'bg-accent' : 'bg-transparent hover:bg-line-strong')}
              />
            )}
            <span className={cn(i < at ? 'text-fg' : 'text-accent')}>{c === ' ' ? '␣' : c}</span>
          </span>
        ))}
      </div>
      <div className="mt-3 grid gap-2 text-[13px]">
        <div><span className="text-muted">前半：</span>{chars.slice(0, at).join('')}</div>
        <div><span className="text-muted">后半：</span>{chars.slice(at).join('')}</div>
      </div>
      <p className="mt-3 text-xs text-subtle">拆分保留来源；前半保留原时间，后半没有锚点时不会平均分配时间。</p>
    </Dialog>
  );
}
