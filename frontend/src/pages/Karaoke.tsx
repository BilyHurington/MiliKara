// Step 7: karaoke subtitles — presets, settings, a live libass preview at any
// moment, ASS download and one-click burn-in.

import { ArrowRight, ChevronLeft, ChevronRight, Crosshair, Disc3, Download, Film, Flame, Image as ImageIcon, Loader2, Sparkles, Subtitles, Trash2, Upload } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { fmtMs, fmtRelative, parseTime } from '@/lib/format';
import { isEnter, isEscape } from '@/lib/keys';
import type { AudioVersion, BurnedVideo, FontFamily, Job, KaraokeStyle, PictureInfo, ProjectView, SongInfo } from '@/lib/types';
import { player } from '@/audio/player';
import { revealOnWaveform } from '@/audio/waveformRef';
import {
  ppath, resumeJobs, run, setPV, setStep, toast, trackJob, useActiveResult, useApp, useJob, useProject, useResult,
} from '@/store/app';
import { DownloadButton } from '@/components/DownloadButton';
import { RecentExports } from '@/components/RecentExports';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, Input, MultiToggle, PageHeader, Progress, Segmented, Select, SliderField, Tip,
} from '@/components/ui';
import { StylePanel } from '@/components/karaoke/StylePanel';
import { BACKGROUND_ACCEPT } from '@/pages/input/AudioCard';
import { setSimpleDefault } from '@/store/simple';
import { countdownPlan } from '@/lib/countdown';
import { useFitHeight } from '@/lib/useFitHeight';
import { singersSettled } from '@/store/singers';

interface LineSpan { id: string; index: number; text: string; start: number; end: number; countdown: boolean | null }

export function KaraokePage() {
  const project = useProject()!;
  // subtitles, preview, ASS and the video always use the project's current (active) result
  const result = useActiveResult();
  const viewed = useResult();
  const [style, setStyle] = useState<KaraokeStyle | null>(project.karaoke ?? null);
  const [fonts, setFonts] = useState<{ default: string; families: FontFamily[] }>({ default: '', families: [] });
  const [songInfo, setSongInfo] = useState<SongInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const dirty = useRef(false);
  const pid = project.id;
  const styleCol = useFitHeight();

  useEffect(() => { void resumeJobs(pid); }, [pid]);  // a video made while this page was closed keeps its download link

  useEffect(() => {
    let stop = false;
    setLoadError(null);
    // fonts and song data are optional: the page works without them; only the style is needed
    // (after the 演唱者 page's last change of the singer list is saved)
    void singersSettled().then(() => Promise.allSettled([
      api.get<{ default: string; families: FontFamily[] }>('/api/fonts'),
      api.get<KaraokeStyle>(`/api/projects/${pid}/karaoke`),
      api.get<SongInfo>(`/api/projects/${pid}/karaoke/info`),
    ])).then(([f, k, info]) => {
      if (stop) return;
      if (f.status === 'fulfilled') setFonts(f.value);
      if (info.status === 'fulfilled') setSongInfo(info.value);
      if (k.status === 'fulfilled') {
        if (!dirty.current) setStyle(k.value);
      } else {
        const msg = k.reason?.message ?? String(k.reason);
        // keep working with the style the project view carried, if any
        setStyle((cur) => cur ?? project.karaoke ?? null);
        setLoadError(msg);
        if (project.karaoke) toast('warn', '读取字幕样式失败，暂用项目里的样式', msg);
      }
    });
    return () => { stop = true; };
  }, [pid, attempt]); // eslint-disable-line react-hooks/exhaustive-deps
  const saveInfoText = (text: string | null) => run(async () => {
    const out = await api.put<SongInfo>(`/api/projects/${pid}/karaoke/info`, { text });
    if (useApp.getState().pid === pid) setSongInfo(out);
  }, '保存歌曲信息失败');

  // autosave (debounced); exports and burn-in always use the saved style, so an edit still waiting
  // is saved at once when the page is left (switching step or mode) and before burning
  const pending = useRef<{ pid: string; style: KaraokeStyle } | null>(null);
  const inflight = useRef<Promise<unknown> | null>(null);
  const flush = useCallback(async () => {
    // saves go out one after another: a burn (or the next page) never sees an older style land last
    while (inflight.current || pending.current) {
      if (inflight.current) {
        await inflight.current.catch(() => undefined);
        continue;
      }
      const p = pending.current!;
      pending.current = null;
      const req = api.put(`/api/projects/${p.pid}/karaoke`, p.style);
      inflight.current = req;
      try {
        await req;
      } finally {
        inflight.current = null;
      }
    }
  }, []);
  useEffect(() => {
    if (!style || !dirty.current) return;
    pending.current = { pid: project.id, style };
    const t = setTimeout(() => { void run(flush, '保存字幕样式失败'); }, 600);
    return () => clearTimeout(t);
  }, [style]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { void run(flush, '保存字幕样式失败'); }, [flush]);

  const patch = (fn: (s: KaraokeStyle) => void) => {
    setStyle((prev) => {
      if (!prev) return prev;
      const next = structuredClone(prev);
      fn(next);
      dirty.current = true;
      return next;
    });
  };

  const change = (next: KaraokeStyle) => {
    dirty.current = true;
    setStyle(next);
  };

  const translated = useMemo(() => project.lyrics.lines.filter((l) => l.sing && l.kind === 'lyric' && l.translation?.trim()).length,
    [project.lyrics.lines]);
  const canFetch = project.sources.some((x) => (x.origin === 'netease' || x.origin === 'qq') && x.platform_song_id);
  const fetchTranslation = () => run(async () => {
    const pv = await api.post<ProjectView & { paired: number }>(ppath('/lyrics/fetch-translation'));
    setPV(pv);
    toast('ok', `已获取翻译：${pv.paired} 行`);
  }, '获取翻译失败');

  const resetCountdowns = () => run(async () => {
    for (const l of lines.filter((x) => x.countdown !== null)) {
      setPV(await api.patch<ProjectView>(ppath(`/lines/${l.id}`), { countdown: 'auto' }));
    }
  }, '恢复失败');

  const lines: LineSpan[] = useMemo(() => {
    if (!result) return [];
    const text = new Map(project.lyrics.lines.map((l, i) => [l.id, { t: l.text, i, cd: l.countdown ?? null }]));
    return result.lines
      .filter((l) => l.start_ms !== null && l.end_ms !== null && text.has(l.line_id))
      .map((l) => ({ id: l.line_id, index: (text.get(l.line_id)?.i ?? 0) + 1, text: text.get(l.line_id)?.t ?? '', start: l.start_ms!, end: l.end_ms!,
        countdown: text.get(l.line_id)?.cd ?? null }))
      .sort((a, b) => a.start - b.start);
  }, [result, project.lyrics.lines]);

  if (!result) {
    return (
      <>
        <Header />
        <EmptyState icon={<Subtitles className="size-5" />} title="还没有对齐结果"
          description="卡拉OK字幕使用逐发音单元时间，请先完成对齐。"
          action={<Button variant="primary" onClick={() => setStep('align')} icon={<ArrowRight className="size-4" />}>去对齐</Button>} />
      </>
    );
  }
  if (!style) {
    return (
      <>
        <Header />
        {loadError ? (
          <Callout tone="danger" title="读取字幕样式失败" actions={<Button size="sm" onClick={() => setAttempt((n) => n + 1)}>重试</Button>}>
            {loadError}
          </Callout>
        ) : (
          <div className="flex items-center gap-2 text-sm text-muted" role="status"><Loader2 className="size-4 animate-spin" />加载中…</div>
        )}
      </>
    );
  }

  return (
    <>
      <Header />
      {viewed && viewed.id !== result.id && (
        <Callout tone="info" className="mb-4" title="字幕使用项目的当前结果"
          actions={<Button size="sm" variant="secondary" onClick={() => setStep('align')}>去“对齐”切换当前结果</Button>}>
          你在“人工检查”里看的是另一个结果；这里的预览、ASS 字幕和生成的视频都按当前结果（对齐页标 ● 当前 的那个）生成。
        </Callout>
      )}
      {result.stale && <Callout tone="warn" className="mb-4" title="当前对齐结果已过期">{result.stale_reason}。字幕仍按该结果生成。</Callout>}
      <SingersNote style={style} />
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(300px,340px)] xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-6">
          <PreviewCard style={style} lines={lines} refreshKey={`${songInfo?.text ?? ''}|${lines.map((l) => l.countdown ?? '').join()}`} />
          <BurnCard style={style} patch={patch} beforeBurn={flush} />
          <RecentExports jobKinds={['burn']} match={(n) => /-karaoke.*\.(mp4|mkv|mov)$/i.test(n)} first={3}
            title="导出过的视频" description="这个项目生成过的带字幕视频，最新的在前。" />
        </div>
        {/* next to the preview from 1024 px on (not below the burn card), sticky while scrolling */}
        <div ref={styleCol} className="min-w-0 space-y-6 lg:sticky lg:top-4 lg:flex lg:max-h-[var(--fit-h)] lg:flex-col">
          <Card className="lg:flex lg:min-h-0 lg:flex-col">
            <CardHeader title="字幕样式" actions={
              <Tip content="极简模式之后的任务完整使用这套样式（配色、布局、注音、翻译、时间与特效）：第 4 步会切到“设置里的样式”，注音、翻译、歌曲信息跟随这套样式">
                <Button size="xs" variant="ghost" icon={<Sparkles className="size-3.5" />}
                  onClick={() => run(async () => { await setSimpleDefault(style); toast('ok', '已设为极简模式默认样式', '第 4 步已改为使用“设置里的样式”'); }, '保存失败')}>
                  设为极简默认
                </Button>
              </Tip>
            } />
            <CardBody className="pt-3 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
              <StylePanel style={style} onChange={change} fonts={fonts.families} defaultFont={fonts.default}
                storageKey="detail" fill
                translation={{ lines: translated, onFetch: canFetch ? fetchTranslation : undefined }}
                countdownLines={{ overrides: lines.filter((l) => l.countdown !== null).length, onReset: resetCountdowns }}
                songInfo={{ data: songInfo, onText: (t) => void saveInfoText(t) }} />
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}

/** Songs with several singers: who sings is set on the 演唱者 page (a line here when it is in use). */
function SingersNote({ style }: { style: KaraokeStyle }) {
  const project = useProject()!;
  const n = style.singers?.members.length ?? 0;
  if (!n) return null;
  const lines = project.lyrics.lines.filter((l) => (l.singers?.length ?? 0) || (l.singer_spans?.length ?? 0)).length;
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-line px-4 py-2.5 text-[13px] text-muted">
      <span className="flex -space-x-1">{style.singers!.members.map((m, i) => (
        <span key={i} className="size-3.5 rounded-full ring-2 ring-surface" style={{ background: m.color }} />
      ))}</span>
      <span className="min-w-0 flex-1">{n} 位演唱者 · {lines} 行已指定：这些部分用各自的颜色显示，其余用下面的配色。</span>
      <Button size="xs" variant="ghost" onClick={() => setStep('singers')}>去“演唱者”页</Button>
    </div>
  );
}

function Header() {
  return (
    <PageHeader
      eyebrow="第 8 步（可选）"
      title="卡拉OK字幕"
      description="选择样式并预览任意时刻的画面；导出 ASS 字幕，或一键生成带字幕的视频（把字幕烧录进画面：背景图片 / 循环播放的背景视频、原视频或纯黑）。设置会自动保存；总是使用项目的当前对齐结果。"
      actions={<Button onClick={() => setStep('export')} icon={<ArrowRight className="size-4" />}>下一步：导出</Button>}
    />
  );
}

// ------------------------------------------------------------------ preview

function PreviewCard({ style, lines, refreshKey }: { style: KaraokeStyle; lines: LineSpan[]; refreshKey: string }) {
  const [lineIdx, setLineIdx] = useState(0);
  const [pct, setPct] = useState(40);
  const [custom, setCustom] = useState<number | null>(null);
  const [bg, setBg] = useState<'auto' | 'black'>('auto');
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timeDraft, setTimeDraft] = useState<string | null>(null);

  const line = lines[Math.min(lineIdx, Math.max(0, lines.length - 1))];
  const plan = useMemo(() => countdownPlan(lines, style.countdown), [lines, style.countdown]);
  const t = custom ?? (line ? Math.round(line.start + (line.end - line.start) * pct / 100) : 0);
  // the burn-in audio setting lives in the style but does not change the picture
  const lookKey = JSON.stringify({ ...style, output: undefined });
  const pic = usePicture();
  const w = pic.width;
  const h = pic.height;
  const picKey = JSON.stringify(pic);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await fetch(ppath('/karaoke/preview'), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ style, t_ms: t, background: bg }),
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
    }, 350);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [lookKey, t, bg, refreshKey, picKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = (i: number) => { setCustom(null); setLineIdx(Math.max(0, Math.min(lines.length - 1, i))); };
  const followPlayhead = () => {
    const ms = Math.round(player.positionMs());
    setCustom(ms);
    const i = lines.findIndex((l) => ms < l.end);
    if (i >= 0) setLineIdx(i);
  };
  const listen = () => {
    if (!line) return;
    revealOnWaveform(line.start, line.end);
    player.playRange(line.start, line.end, { loop: true, padMs: 400 });
  };

  return (
    <Card>
      <CardHeader icon={<Subtitles className="size-4" />} title="预览"
        description={`${w}×${h} · ${pictureLabel(pic)} · 与烧录使用同一渲染器（libass）`}
        actions={pic.source !== 'black' ? (
          <Segmented size="sm" label="预览背景" value={bg} onChange={setBg}
            options={[{ value: 'auto', label: pic.source === 'background' ? '背景' : '视频画面' }, { value: 'black', label: '纯黑' }]} />
        ) : undefined} />
      <CardBody className="space-y-4">
        <div className="relative overflow-hidden rounded-xl bg-black ring-1 ring-line" style={{ aspectRatio: `${w} / ${h}` }}>
          {url && <img src={url} alt={`${fmtMs(t)} 的字幕预览`} className="absolute inset-0 size-full object-contain" />}
          {loading && (
            <div className="absolute top-3 right-3 flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 text-xs text-white/80">
              <Loader2 className="size-3.5 animate-spin" />渲染中
            </div>
          )}
          {error && <div className="absolute inset-x-3 bottom-3 rounded-lg bg-danger/90 px-3 py-2 text-xs text-white">{error}</div>}
          <div className="absolute bottom-3 left-3 rounded-md bg-black/60 px-2 py-0.5 font-mono text-xs text-white/85">{fmtMs(t)}</div>
        </div>

        {lines.length > 0 && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="ghost" icon={<ChevronLeft className="size-4" />} disabled={lineIdx <= 0 && custom === null} onClick={() => go(lineIdx - 1)}>上一行</Button>
              <Select className="min-w-0 flex-1" value={String(lineIdx)} onChange={(e) => go(Number(e.target.value))} aria-label="预览歌词行">
                {lines.map((l, i) => <option key={l.id} value={i}>{l.index}. {l.text}（{fmtMs(l.start, false)}）</option>)}
              </Select>
              <Button size="sm" variant="ghost" onClick={() => go(lineIdx + 1)} disabled={lineIdx >= lines.length - 1}>下一行<ChevronRight className="size-4" /></Button>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <div className="min-w-64 flex-1">
                <SliderField name="行内进度" label={<span className="text-muted">行内进度</span>} value={pct}
                  onChange={(v) => { setCustom(null); setPct(v); }} />
              </div>
              {timeDraft === null ? (
                <Tip content="输入具体时间（如 1:02.345）">
                  <button className="focus-ring rounded-md px-1.5 py-1 font-mono text-sm hover:bg-surface-2" onClick={() => setTimeDraft(fmtMs(t))}>{fmtMs(t)}</button>
                </Tip>
              ) : (
                <Input autoFocus className="h-8 w-32 font-mono" value={timeDraft} aria-label="预览时间"
                  onChange={(e) => setTimeDraft(e.target.value)}
                  onBlur={(e) => { const v = parseTime(e.currentTarget.value); setTimeDraft(null); if (v !== null) setCustom(v); }}
                  onKeyDown={(e) => { if (isEnter(e)) e.currentTarget.blur(); if (isEscape(e)) setTimeDraft(null); }} />
              )}
              <Tip content="使用播放器当前位置"><Button size="sm" variant="ghost" icon={<Crosshair className="size-4" />} onClick={followPlayhead}>播放头</Button></Tip>
              <Button size="sm" variant="ghost" onClick={listen}>试听本行</Button>
              {line && <LineCountdown line={line} plan={plan.get(line.id)} onShow={() => setCustom(Math.max(0, line.start - style.timing.advance_ms - 1500))} />}
              {style.info.enabled && (
                <Tip content="跳到开头歌曲信息完全显示的时刻">
                  <Button size="sm" variant="ghost" onClick={() => setCustom(style.info.start_ms + 1200)}>看歌曲信息</Button>
                </Tip>
              )}
            </div>
          </div>
        )}
        {style.ruby.enabled && style.ruby.script === 'romaji' && (
          <p className="text-xs text-muted">罗马音按固定的平文式规则生成（与对齐使用的拼写一致）。</p>
        )}
      </CardBody>
    </Card>
  );
}

/** This line's countdown dots: as the style's rules say, or always / never (a small select; rarely used). */
function LineCountdown({ line, plan, onShow }: {
  line: LineSpan; plan: { auto: boolean; on: boolean } | undefined; onShow: () => void;
}) {
  const value = line.countdown === null ? 'auto' : line.countdown ? 'on' : 'off';
  const save = (v: string) => run(async () => {
    setPV(await api.patch<ProjectView>(ppath(`/lines/${line.id}`), { countdown: v }));
  }, '设置倒计时失败');
  return (
    <span className="inline-flex items-center gap-1">
      <Select aria-label="本行倒计时" className="h-8 w-auto text-xs" value={value} onChange={(e) => void save(e.target.value)}>
        <option value="auto">倒计时：自动（{plan?.auto ? '有' : '无'}）</option>
        <option value="on">倒计时：显示</option>
        <option value="off">倒计时：不显示</option>
      </Select>
      {plan?.on && <Tip content="跳到这一行的倒计时"><Button size="sm" variant="ghost" onClick={onShow}>看倒计时</Button></Tip>}
    </span>
  );
}

// ------------------------------------------------------------------ picture (background / video / black)

function usePicture(): PictureInfo {
  const project = useProject();
  const pic = useApp((s) => s.pv?.view.picture);
  // (older servers: no picture in the view)
  return pic ?? { source: project?.video ? 'video' : 'black', width: project?.video?.width ?? 1920, height: project?.video?.height ?? 1080 };
}

function pictureLabel(pic: PictureInfo): string {
  if (pic.source === 'background') return pic.kind === 'video' ? '背景视频（循环播放）' : '背景图片';
  return pic.source === 'video' ? '原视频画面' : '纯黑背景';
}

/** Choose / replace / remove the picture or looped video shown behind the subtitles. */
function BackgroundControl({ pic }: { pic: PictureInfo }) {
  const project = useProject()!;
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const upload = (f: File) => run(async () => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', f, f.name);
      setPV(await api.put<ProjectView>(ppath('/background'), fd));
      toast('ok', '已设置背景', '预览和生成的视频都会使用它');
    } finally { setBusy(false); }
  }, '无法使用这个背景');
  const remove = () => run(async () => {
    setPV(await api.del<ProjectView>(ppath('/background')));
  }, '无法移除背景');
  const [coverBusy, setCoverBusy] = useState(false);
  const fromCover = () => run(async () => {
    setCoverBusy(true);
    try {
      setPV(await api.post<ProjectView>(ppath('/background/cover'), {}));
      toast('ok', '已用歌曲封面做背景', '封面模糊铺满画面，封面本身在上方居中');
    } finally { setCoverBusy(false); }
  }, '无法使用封面');
  const hasCover = useApp((s) => !!s.pv?.view.cover);
  const bg = project.background;
  return (
    <div className="space-y-1.5 text-xs text-subtle">
      {bg ? (
        <div className="flex min-w-0 items-center gap-1.5">
          <ImageIcon className="size-3.5 shrink-0" />
          <span className="min-w-0 truncate" title={bg.filename ?? ''}>{bg.filename}</span>
        </div>
      ) : (
        <div>{pic.source === 'video' ? '可以换成一张图片或一段循环播放的视频' : '可以用一张图片或一段循环播放的视频作为画面'}</div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <Button size="xs" variant="outline" loading={busy} icon={<Upload className="size-3.5" />} onClick={() => input.current?.click()}>
          {bg ? '更换背景' : '选择背景…'}
        </Button>
        {hasCover && (
          <Button size="xs" variant="outline" loading={coverBusy} icon={<Disc3 className="size-3.5" />} onClick={() => void fromCover()}
            title="从歌词所用的音乐链接取封面：模糊后铺满画面，封面本身放在上方居中">
            用歌曲封面
          </Button>
        )}
        {bg && <Button size="xs" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => void remove()}>
          移除{project.video ? '（回到原视频）' : ''}
        </Button>}
      </div>
      <input ref={input} type="file" accept={BACKGROUND_ACCEPT} className="hidden" aria-label="选择背景文件"
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void upload(f); }} />
    </div>
  );
}

// ------------------------------------------------------------------ export / burn

function BurnCard({ style, patch, beforeBurn }: {
  style: KaraokeStyle; patch: (fn: (s: KaraokeStyle) => void) => void; beforeBurn: () => Promise<void>;
}) {
  const view = useApp((s) => s.pv?.view);
  const pic = usePicture();
  const job = useJob('burn');
  const [background, setBackground] = useState<'auto' | 'black'>('auto');
  const [audios, setAudios] = useState<AudioVersion[]>(['original']);
  const [quality, setQuality] = useState<'standard' | 'high'>('standard');
  // the latest finished video of this project (kept after leaving the page)
  const out = job?.status === 'succeeded' && job.output
    ? job.output as { url: string; filename: string; warnings: string[]; videos?: BurnedVideo[] } : null;
  // one video each sound (a job from before several versions: the one it made)
  const videos: { url: string; filename: string; label?: string }[] = out ? (out.videos ?? [out]) : [];
  const canMix = !!view?.audio.vocals?.available && !!view?.audio.instrumental?.available;
  const stemsOutdated = !!(view?.audio.vocals?.outdated || view?.audio.instrumental?.outdated);
  const noStems = stemsOutdated ? '分轨来自更换前的原曲：请先重新分离' : '需要先分离人声';
  const running = job && (job.status === 'queued' || job.status === 'running');
  const vocalPct = style.output?.vocal_keep_pct ?? 20;
  const setVocalPct = (v: number) => patch((s) => { s.output = { ...s.output, vocal_keep_pct: v }; });

  const start = () => run(async () => {
    await beforeBurn();  // the burn uses the saved style: save the latest edit first
    const j = await api.post<Job>(ppath('/karaoke/burn'), { background, audios, quality, vocal_keep_pct: vocalPct });
    trackJob(j, { label: audios.length > 1 ? `生成 ${audios.length} 个视频（烧录字幕）` : '生成视频（烧录字幕）' });
  }, '无法开始生成视频');

  return (
    <Card>
      <CardHeader icon={<Film className="size-4" />} title="导出字幕 / 生成视频"
        description="ASS 可用于任何支持 ASS 的播放器或剪辑软件；生成视频会把字幕烧录进画面（H.264 MP4），和极简模式的“生成视频”相同。" />
      <CardBody className="space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          <DownloadButton href={ppath('/export/karaoke-ass?download=1')} before={beforeBurn} variant="outline" icon={<Download className="size-4" />}>
            下载 ASS 字幕
          </DownloadButton>
          <span className="text-xs text-muted">{pic.source === 'video' ? '时间已与原视频对齐（含音轨起点偏移）' : '时间从音频起点开始'}；使用当前结果和已保存的样式</span>
        </div>

        <div className="grid gap-4 rounded-xl border border-line p-4 md:grid-cols-3">
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">背景</div>
            <Segmented size="sm" label="背景" value={pic.source !== 'black' ? background : 'black'} onChange={setBackground}
              options={[{ value: 'auto', label: pic.source === 'background' ? (pic.kind === 'video' ? '背景视频' : '背景图片') : '原视频',
                disabled: pic.source === 'black' }, { value: 'black', label: '纯黑' }]} />
            <BackgroundControl pic={pic} />
          </div>
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">音频</div>
            <MultiToggle<AudioVersion> size="sm" label="音频" value={audios} onChange={setAudios} options={[
              { value: 'original', label: '原唱' },
              { value: 'instrumental', label: '伴唱', disabled: !canMix, title: canMix ? '去掉人声' : noStems },
              { value: 'mix', label: '降低人声', disabled: !canMix, title: canMix ? undefined : noStems },
              { value: 'none', label: '无声' },
            ]} />
            <div className="text-xs text-subtle">可以多选：每种声音各生成一个视频</div>
          </div>
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">画质</div>
            <Segmented size="sm" label="画质" value={quality} onChange={setQuality} options={[{ value: 'standard', label: '标准（较快）' }, { value: 'high', label: '高' }]} />
          </div>
          {audios.includes('mix') && canMix && (
            <div className="space-y-1.5 md:col-span-3">
              <div className="text-[13px] font-medium">人声保留</div>
              <SliderField name="人声保留" value={vocalPct} onChange={setVocalPct} min={0} max={100} step={1} unit="%"
                trackClassName="min-w-40" />
              <div className="text-xs text-subtle">降低人声时保留多少人声（伴唱是完全去掉人声）；伴奏保持 100%。只用于这里的烧录，不影响“导出”页的混音。</div>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-3">
          {running && (
            <div className="flex min-w-60 flex-1 items-center gap-3">
              <Progress value={job!.progress ?? 0} className="flex-1" label="生成视频进度" />
              <span className="text-xs text-muted">{job!.message}</span>
            </div>
          )}
          <Button variant="primary" onClick={start} loading={!!running} icon={<Flame className="size-4" />}>一键烧录（生成视频）</Button>
        </div>
        {job?.status === 'failed' && <Callout tone="danger" title="生成视频失败">{job.error ?? job.message}</Callout>}
        {out && (
          <Callout tone="ok" title={videos.length > 1 ? `已生成 ${videos.length} 个视频` : out.filename}
            actions={videos.length === 1 ? <DownloadButton href={out.url} big filename={out.filename} size="sm" variant="primary" icon={<Download className="size-4" />}>下载视频</DownloadButton> : undefined}>
            {out.warnings.length ? out.warnings.join('；') : `生成完成（${fmtRelative(job!.finished ?? job!.created)}）。`}
            {videos.length > 1 && (
              <ul className="mt-2 space-y-1.5">
                {videos.map((v, i) => (
                  <li key={v.filename} className="flex flex-wrap items-center gap-2">
                    <DownloadButton href={v.url} big filename={v.filename} size="xs" variant={i ? 'secondary' : 'primary'}
                      icon={<Download className="size-3.5" />}>下载{v.label}</DownloadButton>
                    <span className="min-w-0 truncate text-xs text-muted">{v.filename}</span>
                  </li>
                ))}
              </ul>
            )}
          </Callout>
        )}
        <p className="text-xs text-subtle">生成视频耗时约为歌曲时长的 0.3–1 倍，可以离开本页，操作在后台继续（右上角可查看进度或取消）；完成后回到这里也能下载。</p>
        <Badge tone="neutral">输出 {pic.width}×{pic.height} · {background === 'black' ? '纯黑背景' : pictureLabel(pic)}</Badge>
      </CardBody>
    </Card>
  );
}

