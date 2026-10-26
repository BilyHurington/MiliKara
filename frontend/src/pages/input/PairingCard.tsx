// Translation / romanization track pairing with an editable preview.
// These tracks are kept separate and do not take part in alignment.

import { Check, Languages, Search, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useDraft } from '@/store/drafts';
import { api } from '@/lib/api';
import { readFileText } from '@/lib/format';
import type { ProjectView, TrackPreview } from '@/lib/types';
import { ppath, run, setPV, toast, useProject } from '@/store/app';
import { Badge, Button, Card, CardBody, CardHeader, DropZone, Input, Segmented, Select, Table, Td, Textarea, Th } from '@/components/ui';
import { HelpButton } from '@/components/guide/Help';

type Kind = 'translation' | 'romanization';

const METHOD: Record<string, { label: string; tone: 'ok' | 'info' | 'warn' | 'neutral' }> = {
  time: { label: '时间一致', tone: 'ok' },
  nearest: { label: '最近时间', tone: 'info' },
  order: { label: '按顺序', tone: 'warn' },
  manual: { label: '手动', tone: 'neutral' },
};

export function PairingCard({ extraTracks }: { extraTracks: Record<string, string> }) {
  const project = useProject()!;
  const [kind, setKind] = useDraft<Kind>('pairing.kind', 'translation');
  const [text, setText] = useDraft('pairing.text', '');
  const [preview, setPreview] = useDraft<TrackPreview | null>('pairing.preview', null);
  const [edits, setEdits] = useDraft<Record<string, string>>('pairing.edits', {});
  const [busy, setBusy] = useState(false);
  const seenTracks = useRef(extraTracks);

  // a fetched song offered extra tracks: prefill (a remembered draft is not overwritten on return)
  useEffect(() => {
    if (seenTracks.current === extraTracks) return;
    seenTracks.current = extraTracks;
    const k = (['translation', 'romanization'] as Kind[]).find((x) => extraTracks[x]);
    if (k) {
      setKind(k);
      setText(extraTracks[k]);
      setPreview(null);
    }
  }, [extraTracks]);

  const lyricLines = useMemo(() => project.lyrics.lines.filter((l) => l.kind === 'lyric'), [project.lyrics.lines]);
  const existing = lyricLines.filter((l) => (kind === 'translation' ? l.translation : l.romanization)).length;

  const doPreview = (body = text, filename?: string) => run(async () => {
    if (!body.trim()) return;
    setBusy(true);
    try {
      const pv = await api.post<TrackPreview>(ppath('/lyrics/track/preview'), { text: body, kind, origin: filename ? 'upload' : 'paste', filename });
      setPreview(pv);
      const init: Record<string, string> = {};
      for (const p of pv.pairs) init[p.line_id] = p.text;
      setEdits(init);
    } finally {
      setBusy(false);
    }
  }, '配对预览失败');

  // candidate texts for the per-line select: every text from the track
  const candidates = useMemo(() => {
    if (!preview) return [];
    const all = [...preview.pairs.map((p) => p.text), ...preview.unmatched];
    return Array.from(new Set(all.filter(Boolean)));
  }, [preview]);

  const methodOf = (lineId: string) => {
    const p = preview?.pairs.find((x) => x.line_id === lineId);
    if (!p) return edits[lineId] ? 'manual' : null;
    return edits[lineId] === p.text ? p.method : 'manual';
  };

  const apply = () => run(async () => {
    const pairs = Object.entries(edits).filter(([, t]) => t.trim()).map(([line_id, t]) => ({ line_id, text: t }));
    setBusy(true);
    try {
      setPV(await api.post<ProjectView>(ppath('/lyrics/track/apply'), { kind, pairs }));
      toast('ok', `已配对${kind === 'translation' ? '翻译' : '音译'}`, `${pairs.length} 行`);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }, '应用失败');

  if (!lyricLines.length) return null;

  return (
    <Card>
      <CardHeader
        title="翻译 / 音译轨"
        icon={<Languages className="size-4" />}
        help={<HelpButton k="input.pairing" />}
        description="翻译和音译只用于字幕，按时间自动配到原文行上，可以逐行修改。"
        actions={existing > 0 ? <Badge tone="ok">已配对 {existing} 行</Badge> : undefined}
      />
      <CardBody className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Segmented<Kind> label="轨道类型"
            value={kind}
            onChange={(k) => { setKind(k); setPreview(null); setText(extraTracks[k] ?? ''); }}
            options={[{ value: 'translation', label: '翻译' }, { value: 'romanization', label: '音译' }]}
          />
          {Object.keys(extraTracks).some((k) => k === 'translation' || k === 'romanization') && (
            <span className="text-xs text-muted">已从获取的歌曲中预填</span>
          )}
        </div>

        {!preview && (
          <div className="grid gap-3 lg:grid-cols-[1fr_260px]">
            <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="粘贴翻译或音译（LRC 或纯文本）" className="min-h-36" />
            <div className="flex flex-col gap-3">
              <DropZone
                accept=".lrc,.txt,text/plain"
                compact
                title="上传文件"
                hint=".lrc / .txt"
                onFile={(f) => run(async () => { const body = await readFileText(f); setText(body); await doPreview(body, f.name); })}
              />
              <Button variant="primary" loading={busy} disabled={!text.trim()} onClick={() => doPreview()} icon={<Search className="size-4" />}>
                预览配对
              </Button>
            </div>
          </div>
        )}

        {preview && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <Badge tone="accent">{preview.pairs.length} 行已配对</Badge>
              {preview.unmatched_line_ids.length > 0 && <Badge tone="warn">{preview.unmatched_line_ids.length} 行无对应</Badge>}
              {preview.unmatched.length > 0 && <Badge>{preview.unmatched.length} 条未使用</Badge>}
              <span className="ml-auto flex gap-2">
                <Button size="sm" variant="ghost" icon={<X className="size-4" />} onClick={() => setPreview(null)}>返回编辑</Button>
                <Button size="sm" variant="primary" loading={busy} icon={<Check className="size-4" />} onClick={apply}>应用配对</Button>
              </span>
            </div>
            <Table className="max-h-[420px]">
              <thead>
                <tr><Th className="w-10">#</Th><Th className="w-[38%]">原文</Th><Th>{kind === 'translation' ? '翻译' : '音译'}</Th><Th className="w-24">方式</Th></tr>
              </thead>
              <tbody>
                {lyricLines.map((l, i) => {
                  const m = methodOf(l.id);
                  const val = edits[l.id] ?? '';
                  return (
                    <tr key={l.id}>
                      <Td className="tabular text-subtle">{i + 1}</Td>
                      <Td className="max-w-0 truncate" title={l.text}>{l.text}</Td>
                      <Td>
                        <div className="flex gap-2">
                          <Select
                            className="h-8 w-40 shrink-0 text-xs"
                            value={candidates.includes(val) ? val : ''}
                            onChange={(e) => setEdits((s) => ({ ...s, [l.id]: e.target.value }))}
                            aria-label="从轨道中选择"
                          >
                            <option value="">（不配对 / 自定义）</option>
                            {candidates.map((c, k) => <option key={k} value={c}>{c.length > 24 ? `${c.slice(0, 24)}…` : c}</option>)}
                          </Select>
                          <Input
                            className="h-8 text-[13px]"
                            value={val}
                            placeholder="（无）"
                            onChange={(e) => setEdits((s) => ({ ...s, [l.id]: e.target.value }))}
                          />
                        </div>
                      </Td>
                      <Td>{m ? <Badge tone={METHOD[m]?.tone ?? 'neutral'}>{METHOD[m]?.label ?? m}</Badge> : <span className="text-xs text-subtle">—</span>}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
            {preview.unmatched.length > 0 && (
              <details className="rounded-xl border border-line px-4 py-2.5 text-[13px]">
                <summary className="cursor-pointer text-muted">未使用的轨道文本（{preview.unmatched.length}）</summary>
                <ul className="mt-2 space-y-1 text-xs text-muted">
                  {preview.unmatched.map((t, k) => <li key={k}>· {t}</li>)}
                </ul>
              </details>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
