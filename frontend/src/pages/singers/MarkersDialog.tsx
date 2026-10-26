// Lyrics that name their singers ("A：…", "（XX）…", "【成员】…"): pick the names that really are
// singers, assign those lines and (optionally) take the names out of the lyrics.

import { useEffect, useState } from 'react';
import { run, toast, useApp } from '@/store/app';
import { applyMarkers, loadMarkers, type Markers } from '@/store/singers';
import { Button, Callout, Dialog, Switch } from '@/components/ui';
import { HelpButton } from '@/components/guide/Help';

export function MarkersDialog({ open, onOpenChange, onApplied }: {
  open: boolean; onOpenChange: (v: boolean) => void; onApplied?: () => void;
}) {
  const pid = useApp((s) => s.pid)!;
  const hasResults = useApp((s) => (s.pv?.project.results.length ?? 0) > 0);
  const [data, setData] = useState<Markers | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [strip, setStrip] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setData(null);
    void run(async () => {
      const m = await loadMarkers(pid);
      setData(m);
      setChosen(new Set(m.names));
    }, '读取演唱者标记失败');
  }, [open, pid]);

  const apply = () => run(async () => {
    setBusy(true);
    try {
      const pv = await applyMarkers(pid, [...chosen], strip);
      toast('ok', pv.messages[0] ?? '已指定演唱者', pv.messages.slice(1).join('；') || undefined, 7000);
      onOpenChange(false);
      onApplied?.();
    } finally {
      setBusy(false);
    }
  }, '识别演唱者失败');

  const toggle = (n: string) => setChosen((s) => { const x = new Set(s); if (x.has(n)) x.delete(n); else x.add(n); return x; });
  const shown = data?.lines.slice(0, 8) ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange} wide title="识别歌词里的演唱者"
      description={<span className="flex items-start gap-1">这些行的开头写着演唱者。勾选真正是演唱者的名字，确认后按名字建立演唱者、给这些行指定。<HelpButton k="singers.markers" /></span>}
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
        <Button variant="primary" loading={busy} disabled={!chosen.size} onClick={() => void apply()}>指定 {data ? data.lines.length : ''} 行</Button>
      </>}>
      {!data ? <p className="text-sm text-muted">读取中…</p> : (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">找到的名字</div>
            <div className="flex flex-wrap gap-2">
              {data.names.map((n) => (
                <label key={n} className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-[13px]">
                  <input type="checkbox" checked={chosen.has(n)} onChange={() => toggle(n)} />
                  {n}
                  {data.existing.some((e) => e.trim().toLowerCase() === n.toLowerCase()) && <span className="text-xs text-subtle">（已有）</span>}
                </label>
              ))}
            </div>
            <p className="text-xs text-subtle">“全员”“合”“ALL”之类表示所有人一起唱，会指定为这里勾选的全部演唱者。</p>
          </div>
          <div className="space-y-1">
            <div className="text-[13px] font-medium">例如</div>
            <ul className="space-y-0.5 text-[13px]">
              {shown.map((l) => (
                <li key={l.line_id} className="truncate">
                  <mark className="rounded bg-accent-soft px-0.5 text-accent">{l.prefix}</mark>{l.text.slice(l.prefix.length)}
                </li>
              ))}
              {data.lines.length > shown.length && <li className="text-xs text-subtle">… 共 {data.lines.length} 行</li>}
            </ul>
          </div>
          <Switch checked={strip} onChange={setStrip} label="同时去掉歌词开头的这些名字（推荐，字幕里不会再显示）" />
          {strip && hasResults && (
            <Callout tone="warn" title="歌词文字会改动">
              去掉名字后这些行的文字变了，当前对齐结果会标为过期（其余部分的时间保留）。名字原本也被当作歌词对齐了，建议之后重新对齐。
            </Callout>
          )}
        </div>
      )}
    </Dialog>
  );
}
