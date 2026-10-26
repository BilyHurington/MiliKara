// Step 7 (optional): singers — for songs with several voices, who sings which line / word, and
// each singer's colours.  The whole lyrics in one view: click line numbers (Shift / ⌘ for more)
// or drag over the words (across lines too), then press a singer's key (1–9, letters; each can be
// changed); 1 + 2 = sung together; 0 clears.
// Assignments are saved at once (one undo step each); the singer list lives in the style.

import { ArrowRight, Eraser, Keyboard, Loader2, Play, Subtitles, Tags, Users } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type RefObject } from 'react';
import { api } from '@/lib/api';
import { cn, fmtMs } from '@/lib/format';
import { ignoreShortcut, isEnter, MOD_KEY } from '@/lib/keys';
import {
  caretAt, caretTimeline, effective, freeKey, idsKey, isSelected, keyIds, keyLabel, keyOf, lineSingers, lookOf, mixBackground, parseCombo, rangeSingers, singerLabel, union,
  usage, withCombo, wordRange, wordsOf, type CaretStep, type Selection, type Word,
} from '@/lib/singers';
import type { KaraokeSingers, KaraokeStyle, Line } from '@/lib/types';
import { player, usePlayhead } from '@/audio/player';
import { revealOnWaveform } from '@/audio/waveformRef';
import { run, setStep, toast, useActiveResult, useApp, useProject } from '@/store/app';
import { assignSingers, flushSingers, removeSinger, saveSingers, applySingerPreset } from '@/store/singers';
import { Badge, Button, Callout, Card, CardBody, CardHeader, Input, Kbd, PageHeader, Segmented, Switch } from '@/components/ui';
import { MarkersDialog } from './singers/MarkersDialog';
import { SingerList } from './singers/SingerList';
import { INTRO } from '@/lib/guide';
import { FirstTip, HelpButton } from '@/components/guide/Help';

const NO_SINGERS: KaraokeSingers = { members: [], mix: 'split', direction: 'vertical' };

interface Row { line: Line; index: number; words: Word[] }

let keySeq = 0;

export function SingersPage() {
  const project = useProject()!;
  const pid = project.id;
  const markers = useApp((s) => s.pv?.view.singer_markers ?? 0);
  const [style, setStyle] = useState<KaraokeStyle | null>(project.karaoke ?? null);
  const [markersOpen, setMarkersOpen] = useState(false);

  useEffect(() => {
    let stop = false;
    void api.get<KaraokeStyle>(`/api/projects/${pid}/karaoke`)
      .then((k) => { if (!stop) setStyle(k); })
      .catch(() => { if (!stop) setStyle((cur) => cur ?? project.karaoke ?? null); });
    return () => { stop = true; };
  }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { void flushSingers(); }, []);

  const singers = style?.singers ?? NO_SINGERS;
  const members = singers.members;
  const changeSingers = (next: KaraokeSingers) => {
    setStyle((s) => (s ? { ...s, singers: next } : s));
    saveSingers(pid, next);
  };
  const remove = (n: number) => run(async () => {
    const pv = await removeSinger(pid, n);
    if (pv.project.karaoke?.singers) setStyle((s) => (s ? { ...s, singers: pv.project.karaoke!.singers! } : s));
    toast('ok', `已删除${singerLabel(members, n)}`, pv.changed ? `${pv.changed} 行的指定已随之调整` : undefined);
  }, '删除演唱者失败');
  const usePreset = (id: string, name: string) => run(async () => {
    const pv = await applySingerPreset(pid, id);
    if (pv.project.karaoke?.singers) setStyle((s) => (s ? { ...s, singers: pv.project.karaoke!.singers! } : s));
    toast('ok', `已使用演唱者预设「${name}」`, [
      pv.lines ? `${pv.lines} 行已指定的部分按名字对应到预设里的演唱者` : '',
      pv.kept.length ? `预设里没有的 ${pv.kept.join('、')} 在歌词里有指定，已保留在最后` : '',
    ].filter(Boolean).join('；') || undefined);
  }, '使用预设失败');
  const afterMarkers = () => {
    const k = useApp.getState().pv?.project.karaoke;
    if (k?.singers) setStyle((s) => (s ? { ...s, singers: k.singers! } : s));
  };

  // ------------------------------------------------------------------ rows and selection

  const rows: Row[] = useMemo(() => project.lyrics.lines
    .map((line, i) => ({ line, index: i + 1, words: wordsOf(line) }))
    .filter((r) => r.line.kind === 'lyric' && r.line.sing), [project.lyrics.lines]);
  const [sel, setSel] = useState<Selection>(new Map());
  const anchor = useRef<[number, number] | null>(null);
  const lineAnchor = useRef<number | null>(null);
  const drag = useRef<{ from: [number, number]; base: Selection } | null>(null);
  // the last assignment by key (1 → + → 2 makes it 1+2, one undo step)
  const [combo, setComboState] = useState<{ ids: number[]; key: string; plus: boolean } | null>(null);
  // (also kept in a ref: "+" and the next number can come faster than a re-render)
  const comboNow = useRef(combo);
  const setCombo = (v: typeof combo) => { comboNow.current = v; setComboState(v); };

  // a selection of lines that no longer exist (merged, split, new lyrics) is dropped
  useEffect(() => {
    setSel((s) => {
      const ids = new Set(rows.map((r) => r.line.id));
      const texts = new Map(rows.map((r) => [r.line.id, r.line.text.length]));
      const next: Selection = new Map([...s].filter(([id, r]) => ids.has(id) && r.every(([, b]) => b <= texts.get(id)!)));
      return next.size === s.size ? s : next;
    });
  }, [rows]);

  const select = (next: Selection) => { setSel(next); setCombo(null); };
  const wholeLine = (r: Row): Selection => new Map([[r.line.id, [[0, r.line.text.length]]]]);

  const clickLine = (li: number, e: ReactMouseEvent) => {
    const r = rows[li];
    const mod = e.metaKey || e.ctrlKey;
    if (e.shiftKey && lineAnchor.current !== null) {
      const [a, b] = [Math.min(lineAnchor.current, li), Math.max(lineAnchor.current, li)];
      let next: Selection = mod ? new Map(sel) : new Map();
      for (let i = a; i <= b; i++) next = union(next, wholeLine(rows[i]));
      select(next);
      return;
    }
    lineAnchor.current = li;
    anchor.current = [li, 0];
    if (mod) {
      const next = new Map(sel);
      const full = (sel.get(r.line.id) ?? []).some(([x, y]) => x <= 0 && y >= r.line.text.length);
      if (full) next.delete(r.line.id); else next.set(r.line.id, [[0, r.line.text.length]]);
      select(next);
    } else {
      select(wholeLine(r));
    }
  };

  const downOnWord = (li: number, wi: number, e: ReactMouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    dropFocus();
    const mod = e.metaKey || e.ctrlKey;
    if (e.shiftKey && anchor.current) {
      select(union(mod ? sel : new Map(), wordRange(rows, anchor.current, [li, wi])));
      return;
    }
    anchor.current = [li, wi];
    lineAnchor.current = li;
    drag.current = { from: [li, wi], base: mod ? sel : new Map() };
    select(union(drag.current.base, wordRange(rows, [li, wi], [li, wi])));
  };

  useEffect(() => {
    const move = (e: MouseEvent) => {
      const d = drag.current;
      if (!d) return;
      const el = (document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null)?.closest<HTMLElement>('[data-word]');
      if (!el) return;
      const [li, wi] = el.dataset.word!.split(':').map(Number);
      setSel(union(d.base, wordRange(rows, d.from, [li, wi])));
    };
    const up = () => { drag.current = null; };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, [rows]);

  // ------------------------------------------------------------------ assigning

  const label = (ids: number[]) => (ids.length ? ids.map((n) => singerLabel(members, n)).join(' + ') : '默认配色');
  const apply = useCallback((ids: number[], key?: string) => {
    if (!sel.size) {
      toast('info', '先选中歌词', '点行号选整行，或在歌词上拖动选择几个词');
      return;
    }
    void assignSingers(sel, ids, ids.length ? `指定 ${label(ids)}` : '清除演唱者', key);
  }, [sel, members]); // eslint-disable-line react-hooks/exhaustive-deps

  const pressKey = (k: string) => {
    const got = keyIds(singers, k);
    if (!got) {
      toast('info', members.length ? `快捷键 ${keyLabel(k)} 还没有用` : '还没有演唱者',
        members.length ? '右边每位演唱者和组合旁边是它的快捷键，点一下可以修改' : '先在右边添加演唱者');
      return;
    }
    pressIds(got);
  };
  /** A singer / combination chosen (key or button): after "+", added to what was just assigned. */
  const pressIds = (got: number[]) => {
    const cur = comboNow.current;
    if (cur?.plus) {
      const ids = [...cur.ids, ...got.filter((x) => !cur.ids.includes(x))];
      setCombo({ ids, key: cur.key, plus: false });
      apply(ids, cur.key);
      return;
    }
    const key = `k${++keySeq}`;
    setCombo({ ids: got, key, plus: false });
    apply(got, key);
  };
  /** Keep what was just assigned together on a free key. */
  const saveCombo = (ids: number[]) => {
    const { next, key } = withCombo(singers, ids);
    if (key === null) {
      toast('info', '快捷键已经用完', '先删掉一个组合，或把某位演唱者的快捷键清空');
      return;
    }
    if (next !== singers) changeSingers(next);
    toast('ok', `按 ${keyLabel(key)} 就是 ${label(ids)}`);
  };

  // ------------------------------------------------------------------ listening / preview times

  const result = useActiveResult();
  const times = useMemo(() => new Map((result?.units ?? []).filter((u) => u.start_ms !== null && u.end_ms !== null)
    .map((u) => [u.unit_id, [u.start_ms!, u.end_ms!] as [number, number]])), [result]);
  const selTimes = useMemo((): [number, number] | null => {
    let a = Infinity;
    let b = -Infinity;
    for (const r of rows) {
      const ranges = sel.get(r.line.id);
      if (!ranges) continue;
      let pos = 0;
      for (const seg of r.line.segments) {
        const s0 = pos;
        pos += seg.surface.length;
        if (!ranges.some(([x, y]) => x < pos && s0 < y)) continue;
        for (const u of seg.units) {
          const t = times.get(u.id);
          if (t) { a = Math.min(a, t[0]); b = Math.max(b, t[1]); }
        }
      }
    }
    return a < b ? [a, b] : null;
  }, [sel, rows, times]);
  // the playhead on the lyrics (a caret moving with the singing); double-click a word to go there
  const steps = useMemo(() => caretTimeline(rows.map((r) => r.line), times), [rows, times]);
  const lyricsRef = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  const seekWord = (li: number, wi: number) => {
    const r = rows[li];
    const w = r?.words[wi];
    const st = w && steps.find((x) => x.lineId === r.line.id && x.c1 > w.start);
    if (!st) return;
    player.seek(st.start);
    revealOnWaveform(st.start);
  };
  const listen = () => {
    if (!selTimes) {
      toast('info', result ? '选中的部分还没有时间' : '还没有对齐结果', result ? undefined : '对齐后可以试听和预览');
      return;
    }
    revealOnWaveform(selTimes[0], selTimes[1]);
    player.playRange(selTimes[0], selTimes[1], { loop: true, padMs: 300 });
  };

  // ------------------------------------------------------------------ keyboard

  const keys = useRef({ pressKey, apply, rows, listen });
  keys.current = { pressKey, apply, rows, listen };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (ignoreShortcut(e)) return;
      const k = keys.current;
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'a') {
        e.preventDefault();
        let all: Selection = new Map();
        for (const r of k.rows) all = union(all, new Map([[r.line.id, [[0, r.line.text.length]]]]));
        setSel(all);
        setCombo(null);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (keyOf(e.key)) {
        e.preventDefault();
        k.pressKey(keyOf(e.key));
      } else if (e.key === '+' || e.key === '=' || e.code === 'NumpadAdd') {
        if (comboNow.current) {
          e.preventDefault();
          setCombo({ ...comboNow.current, plus: true });
        }
      } else if (e.key === '0' || e.key === 'Backspace' || e.key === 'Delete') {
        e.preventDefault();
        setCombo(null);
        k.apply([]);
      } else if (e.key === 'Escape') {
        setSel(new Map());
        setCombo(null);
      } else if (e.key.toLowerCase() === 'p') {
        e.preventDefault();
        k.listen();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // ------------------------------------------------------------------ view

  const [comboText, setComboText] = useState('');
  const applyComboText = () => {
    const ids = parseCombo(comboText, members.length);
    if (!ids.length) {
      toast('info', '请输入演唱者编号', `如 1+2（现有 ${members.length} 位）`);
      return;
    }
    apply(ids);
    setCombo({ ids, key: `k${++keySeq}`, plus: false });
    setComboText('');
  };
  const nLines = [...sel.values()].length;
  const full = rows.filter((r) => (sel.get(r.line.id) ?? []).some(([a, b]) => a <= 0 && b >= r.line.text.length)).length;
  const summary = !nLines ? '未选择：点行号选整行，或在歌词上拖动选择几个词（可以跨行）'
    : full === nLines ? `已选 ${nLines} 行` : `已选 ${nLines} 行中的部分歌词`;
  const colorOf = (n: number) => members[n - 1]?.color;
  // the side column's height: what is visible of the scrolling page (above the dock)
  const side = useRef<HTMLDivElement>(null);
  const [sideMax, setSideMax] = useState<number | null>(null);
  useEffect(() => {
    const main = side.current?.closest('main');
    if (!main || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setSideMax(main.clientHeight - 32));
    ro.observe(main);
    return () => ro.disconnect();
  }, []);
  const assigned = rows.filter((r) => (r.line.singers?.length ?? 0) || (r.line.singer_spans?.length ?? 0)).length;

  return (
    <>
      <PageHeader
        eyebrow="第 7 步（可选）"
        title="演唱者"
        description={INTRO.singers}
        actions={<Button onClick={() => setStep('karaoke')} icon={<ArrowRight className="size-4" />}>下一步：卡拉OK字幕</Button>}
      />
      <FirstTip k="singers.first" className="mb-4" />
      {markers > 0 && (
        <Callout tone="info" className="mb-4" title={`有 ${markers} 行歌词开头写着演唱者（如「A：」「【成员】」）`}
          actions={<Button size="sm" variant="secondary" icon={<Tags className="size-4" />} onClick={() => setMarkersOpen(true)}>识别并指定…</Button>}>
          可以按这些名字自动建立演唱者、给这些行指定，并把名字从歌词里去掉。
        </Callout>
      )}
      <MarkersDialog open={markersOpen} onOpenChange={setMarkersOpen} onApplied={afterMarkers} />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(300px,340px)] xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card className="min-w-0">
          <div className="sticky top-0 z-10 space-y-2.5 rounded-t-[var(--radius-card)] border-b border-line bg-surface px-5 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 text-[13px] text-muted" role="status">{summary}</span>
              {combo?.plus && <Badge tone="accent">{combo.ids.join('+')}+ …</Badge>}
              {combo && combo.ids.length > 1 && !combo.plus && !(singers.combos ?? []).some((c) => idsKey(c.singers) === idsKey(combo.ids)) && (
                <Button size="xs" variant="ghost" icon={<Keyboard className="size-3.5" />} onClick={() => saveCombo(combo.ids)}
                  title="以后按一个键就指定为这几个人一起唱">
                  把 {combo.ids.join('+')} 存到 {keyLabel(freeKey(singers) ?? '…')}
                </Button>
              )}
              {steps.length > 0 && <Switch checked={follow} onChange={setFollow} label={<span className="text-xs text-muted">跟随播放</span>} />}
              <Button size="xs" variant="ghost" icon={<Play className="size-3.5" />} onClick={listen} disabled={!nLines}>试听</Button>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {members.map((m, i) => (
                <Button key={i} size="xs" variant="outline" disabled={!nLines} onClick={() => pressIds([i + 1])}
                  title={`指定为 ${singerLabel(members, i + 1)}${m.key ? `（按 ${keyLabel(m.key)}）` : ''}`}>
                  <span className="grid size-4 place-items-center rounded text-[10px] font-bold text-white" style={{ background: m.color }}>{i + 1}</span>
                  <span className="max-w-24 truncate">{singerLabel(members, i + 1)}</span>
                  {m.key && <Kbd>{keyLabel(m.key)}</Kbd>}
                </Button>
              ))}
              {(singers.combos ?? []).map((c, ci) => (
                <Button key={`c${ci}`} size="xs" variant="outline" disabled={!nLines} onClick={() => pressIds(c.singers)}
                  title={`指定为 ${label(c.singers)} 一起唱${c.key ? `（按 ${keyLabel(c.key)}）` : ''}`}>
                  <span className="size-4 rounded" aria-hidden
                    style={{ background: mixBackground(c.singers.map((n) => colorOf(n) ?? '#888'), lookOf(singers, c.singers).mix, lookOf(singers, c.singers).direction) }} />
                  <span className="max-w-28 truncate">{c.singers.join('+')}</span>
                  {c.key && <Kbd>{keyLabel(c.key)}</Kbd>}
                </Button>
              ))}
              {members.length > 1 && (
                <span className="inline-flex items-center gap-1">
                  <Input className="h-7 w-20 text-xs" placeholder="如 1+2" value={comboText} aria-label="一起唱的演唱者"
                    onChange={(e) => setComboText(e.target.value)} onKeyDown={(e) => { if (isEnter(e)) applyComboText(); }} />
                  <Button size="xs" variant="outline" disabled={!nLines || !comboText.trim()} onClick={applyComboText}>一起唱</Button>
                </span>
              )}
              <Button size="xs" variant="ghost" icon={<Eraser className="size-3.5" />} disabled={!nLines} onClick={() => apply([])}>清除</Button>
            </div>
          </div>
          <CardBody className="space-y-0.5 px-3">
            <p className="px-2 pb-2 text-xs leading-5 text-subtle">
              按演唱者或组合的快捷键指定（上面按钮里的键，在右边点一下可以修改）· 先按 <Kbd>1</Kbd> 再按 <Kbd>+</Kbd> <Kbd>2</Kbd> 为两人一起唱，可以存成组合 · <Kbd>0</Kbd> 清除 ·
              {' '}<Kbd>P</Kbd> 试听 · <Kbd>Esc</Kbd> 取消选择 · <Kbd>{MOD_KEY}</Kbd>+<Kbd>A</Kbd> 全选 · <Kbd>{MOD_KEY}</Kbd>+<Kbd>Z</Kbd> 撤销；
              行号 Shift / {MOD_KEY} 点选多行，在歌词上按住 {MOD_KEY} 拖动可以追加；<Kbd>Space</Kbd> 播放时竖线标出正在唱的位置，双击歌词从那里播放
            </p>
            {rows.length === 0 && <p className="px-2 py-6 text-center text-sm text-muted">还没有歌词</p>}
            <div ref={lyricsRef} className="relative space-y-0.5">
              {rows.map((r, li) => (
                <LyricRow key={r.line.id} row={r} li={li} sel={sel} colorOf={colorOf} singers={singers}
                  onLine={clickLine} onWord={downOnWord} onSeek={seekWord} names={(ids) => label(ids)} />
              ))}
              {steps.length > 0 && <PlayCaret steps={steps} host={lyricsRef} follow={follow} />}
            </div>
          </CardBody>
        </Card>

        {/* sticky beside the lyrics, scrolling on its own when taller than the visible part of the page */}
        <div ref={side} className="min-w-0 space-y-6 lg:sticky lg:top-4 lg:overflow-y-auto lg:overscroll-contain"
          style={sideMax ? { maxHeight: sideMax } : undefined}>
          <PreviewCard style={style} times={selTimes} hasResult={!!result} refresh={`${assigned}-${JSON.stringify(project.lyrics.lines.map((l) => [l.singers, l.singer_spans]))}`} />
          {style && (
            <SingerList singers={singers} onChange={changeSingers} onRemove={(n) => void remove(n)} glow={style.glow.enabled}
              onUsePreset={(id, name) => void usePreset(id, name)}
              usage={(n) => usage(project.lyrics.lines, n)} />
          )}
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ one line of the lyrics

/** Selecting with the mouse leaves no control focused: Space then plays (a focused button would take it). */
function dropFocus() {
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
}

function LyricRow({ row, li, sel, colorOf, singers, onLine, onWord, onSeek, names }: {
  row: Row; li: number; sel: Selection; colorOf: (n: number) => string | undefined; singers: KaraokeSingers;
  onLine: (li: number, e: ReactMouseEvent) => void; onWord: (li: number, wi: number, e: ReactMouseEvent) => void;
  onSeek: (li: number, wi: number) => void;
  names: (ids: number[]) => string;
}) {
  const { line, words, index } = row;
  const chars = effective(lineSingers(line));
  const own = (line.singers ?? []).filter((n) => colorOf(n));
  const full = (sel.get(line.id) ?? []).some(([a, b]) => a <= 0 && b >= line.text.length);
  const groups: { ids: number[]; words: { w: Word; wi: number }[] }[] = [];
  words.forEach((w, wi) => {
    const ids = rangeSingers(chars, w.start, w.end, line.text);
    const last = groups[groups.length - 1];
    if (last && idsKey(last.ids) === idsKey(ids)) last.words.push({ w, wi });
    else groups.push({ ids, words: [{ w, wi }] });
  });
  const paint = (ids: number[]) => {
    const cols = ids.map(colorOf).filter((c): c is string => !!c);
    if (!cols.length) return undefined;
    if (cols.length === 1) return { color: cols[0] };
    const look = lookOf(singers, ids);
    return { backgroundImage: mixBackground(cols, look.mix, look.direction), WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' };
  };
  return (
    <div data-row={line.id} className={cn('group flex scroll-mt-32 scroll-mb-8 items-start gap-2 rounded-lg px-2 py-1',
      full ? 'bg-accent-soft' : 'hover:bg-surface-2/60 data-[playing]:bg-surface-2/70')}>
      <button type="button" onMouseDown={(e) => { e.preventDefault(); dropFocus(); }} onClick={(e) => onLine(li, e)}
        aria-pressed={full} aria-label={`选择第 ${index} 行`}
        className="focus-ring mt-1 w-8 shrink-0 rounded text-right font-mono text-xs text-subtle hover:text-fg">
        {index}
      </button>
      <span aria-hidden className="mt-1.5 h-5 w-1.5 shrink-0 rounded-full"
        style={{ background: own.length ? mixBackground(own.map((n) => colorOf(n)!), lookOf(singers, own).mix, 'vertical') : 'var(--color-line)' }} />
      <div data-text={line.id} className="min-w-0 flex-1 cursor-text text-[17px] leading-8 font-medium select-none">
        {groups.map((g, gi) => {
          // a run of words with the same singers is painted as one piece (side by side: left to right across it)
          const style = paint(g.ids);
          return (
            <span key={gi} style={style}>
              {g.words.map(({ w, wi }) => {
                // a space is nobody's: never selected, highlighted or coloured on its own
                if (!w.text.trim()) return <span key={wi} onMouseDown={(e) => e.preventDefault()}>{w.text}</span>;
                const picked = isSelected(sel, line.id, w.start, w.end) && !full;
                // characters within the word sung by others (only data made elsewhere splits a word)
                const odd = [...line.text.slice(w.start, w.end)].some((_, i) => idsKey(chars[w.start + i] ?? []) !== idsKey(g.ids));
                return (
                  <span key={wi} data-word={`${li}:${wi}`} onMouseDown={(e) => onWord(li, wi, e)} onDoubleClick={() => onSeek(li, wi)}
                    className={cn('rounded-[3px] py-0.5', picked && 'outline-2 outline-accent/70', picked && g.ids.length < 2 && 'bg-accent/15')}>
                    {odd
                      ? [...line.text.slice(w.start, w.end)].map((ch, ci) => <span key={ci} style={paint(chars[w.start + ci] ?? [])}>{ch}</span>)
                      : w.text}
                  </span>
                );
              })}
            </span>
          );
        })}
      </div>
      {own.length > 0 && <span className="mt-1.5 max-w-40 shrink-0 truncate text-xs text-subtle">{names(own)}</span>}
    </div>
  );
}

// ------------------------------------------------------------------ the playhead

/** Where character position `pos` (fractional: part way through a character) is drawn inside `text`. */
function caretRect(text: Element, pos: number): { x: number; top: number; height: number } | null {
  const walker = document.createTreeWalker(text, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n as Text);
  const total = nodes.reduce((a, n) => a + n.length, 0);
  if (!total || typeof document.createRange !== 'function') return null;
  const i = Math.max(0, Math.min(total - 1, Math.floor(pos)));
  const f = pos >= total ? 1 : pos - Math.floor(pos);
  let off = i;
  for (const n of nodes) {
    if (off < n.length) {
      const range = document.createRange();
      range.setStart(n, off);
      range.setEnd(n, off + 1);
      const r = range.getClientRects?.()[0] ?? range.getBoundingClientRect?.();
      if (!r || (!r.width && !r.height)) return null;
      return { x: r.left + r.width * Math.max(0, Math.min(1, f)), top: r.top, height: r.height };
    }
    off -= n.length;
  }
  return null;
}

/** A caret on the lyrics at the playing position; the row being sung is marked and (while playing, with
 * `follow`) kept in view.  Drawn by moving one element every frame, the lyrics never re-render for it. */
function PlayCaret({ steps, host, follow }: { steps: CaretStep[]; host: RefObject<HTMLDivElement | null>; follow: boolean }) {
  const t = usePlayhead();
  const bar = useRef<HTMLDivElement>(null);
  const last = useRef<string | null>(null);
  const wasPlaying = useRef(false);
  // the lyrics' layout changed (width, wrapped lines): place it again, also while paused
  const [layout, setLayout] = useState(0);
  useEffect(() => {
    const el = host.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setLayout((n) => n + 1));
    ro.observe(el);
    return () => ro.disconnect();
  }, [host]);
  useLayoutEffect(() => {
    const el = host.current;
    const b = bar.current;
    if (!el || !b) return;
    const at = caretAt(steps, t);
    const row = at ? el.querySelector(`[data-row="${at.lineId}"]`) : null;
    if ((at?.lineId ?? null) !== last.current) {
      el.querySelectorAll('[data-row][data-playing]').forEach((x) => x.removeAttribute('data-playing'));
      row?.setAttribute('data-playing', '');
    }
    // keep the row being sung in view: when it changes and when playing starts
    if (at && follow && player.playing && (at.lineId !== last.current || !wasPlaying.current)) row?.scrollIntoView?.({ block: 'nearest' });
    last.current = at?.lineId ?? null;
    wasPlaying.current = player.playing;
    const text = at ? el.querySelector(`[data-text="${at.lineId}"]`) : null;
    const r = text && at ? caretRect(text, at.pos) : null;
    if (!at || !r) {
      b.style.display = 'none';
      return;
    }
    const box = el.getBoundingClientRect();
    b.style.display = 'block';
    b.style.transform = `translate(${r.x - box.left - 1}px, ${r.top - box.top - 2}px)`;
    b.style.height = `${r.height + 4}px`;
    b.style.opacity = at.waiting ? '0.4' : '1';
  }, [t, steps, follow, host, layout]);
  return <div ref={bar} aria-hidden className="pointer-events-none absolute top-0 left-0 hidden w-0.5 rounded-full bg-accent shadow-[0_0_6px_var(--color-accent)]" />;
}

// ------------------------------------------------------------------ preview

function PreviewCard({ style, times, hasResult, refresh }: {
  style: KaraokeStyle | null; times: [number, number] | null; hasResult: boolean; refresh: string;
}) {
  const pid = useApp((s) => s.pid);
  const [when, setWhen] = useState<'mid' | 'before' | 'after'>('mid');
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // the moment of the singing to show (before it starts, half-way, just done); the lyrics are shown
  // `advance_ms` ahead of the singing, so the frame showing that moment is that much earlier
  const moment = times ? (when === 'before' ? times[0] - 150 : when === 'after' ? times[1] : Math.round((times[0] + times[1]) / 2)) : null;
  const t = moment === null ? null : moment - (style?.timing.advance_ms ?? 0);
  const look = JSON.stringify({ ...style, output: undefined });

  useEffect(() => {
    if (!style || t === null || !pid) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await fetch(`/api/projects/${pid}/karaoke/preview`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ style, t_ms: Math.max(0, t), background: 'auto' }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        setUrl((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(blob); });
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 400);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [look, t, refresh, pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const pic = useApp((s) => s.pv?.view.picture);
  return (
    <Card>
      <CardHeader icon={<Subtitles className="size-4" />} title="预览" help={<HelpButton k="singers.preview" />}
        actions={<Segmented size="sm" label="预览时刻" value={when} onChange={setWhen}
          options={[{ value: 'before', label: '唱之前' }, { value: 'mid', label: '唱到一半' }, { value: 'after', label: '唱完' }]} />} />
      <CardBody className="space-y-2">
        {!hasResult ? (
          <p className="text-[13px] text-muted">对齐后可以在这里预览字幕效果（指定演唱者不需要对齐结果）。</p>
        ) : t === null ? (
          <p className="text-[13px] text-muted"><Users className="mr-1 inline size-4" />选中歌词后，这里显示它唱到时的画面。</p>
        ) : (
          <div className="relative overflow-hidden rounded-xl bg-black ring-1 ring-line" style={{ aspectRatio: `${pic?.width ?? 1920} / ${pic?.height ?? 1080}` }}>
            {url && <img src={url} alt={`${fmtMs(t)} 的字幕预览`} className="absolute inset-0 size-full object-contain" />}
            {loading && (
              <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-black/60 px-2 py-0.5 text-xs text-white/80">
                <Loader2 className="size-3.5 animate-spin" />渲染中
              </div>
            )}
            {error && <div className="absolute inset-x-2 bottom-2 rounded-lg bg-danger/90 px-2 py-1 text-xs text-white">{error}</div>}
            <div className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 font-mono text-[11px] text-white/85">{fmtMs(t)}</div>
          </div>
        )}
        <p className="text-xs text-subtle">与烧录使用同一渲染器；其他样式在“卡拉OK字幕”里调整。</p>
      </CardBody>
    </Card>
  );
}
