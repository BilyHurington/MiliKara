// 极简模式首页：选模式 → 拖入视频 → 粘贴链接或歌词 → 字幕样式 → 开始；下方是任务队列。
// 字幕样式和视频设置在添加任务时绑定到任务上（排队中的任务不受之后修改影响），
// 并自动记住，下一首从同样的选择开始。

import {
  AlertTriangle, ArrowRight, Check, ChevronDown, ChevronRight, CircleDashed, ClipboardPaste, Crosshair, Download, Film, Hand, Image as ImageIcon, Link2, ListMusic,
  Loader2, Music2, Play, RotateCcw, Settings2, Sparkles, Trash2, X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { cn, fmtRelative } from '@/lib/format';
import { errorHint } from '@/lib/errorHints';
import { DiagnosticsButton } from '@/components/DiagnosticsButton';
import type { Mode, PipelineStage, PipelineTask, TaskStyleOptions } from '@/lib/types';
import { run, toast, useApp } from '@/store/app';
import { usePageDraft } from '@/store/drafts';
import {
  addTask, forgetOwnTask, getTaskStyleDraft, hasActiveTasks, markOwnTask, openInDetail, saveSettings, saveTaskStyle, setSimplePage, taskAction,
  useSimple, waitingFor,
} from '@/store/simple';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, ConfirmButton, DropZone, EmptyState, Input, Progress, Segmented, Textarea,
} from '@/components/ui';
import { DownloadLink } from '@/components/DownloadButton';
import { AUDIO_ACCEPT, BACKGROUND_ACCEPT, MEDIA_ACCEPT } from '@/pages/input/AudioCard';
import { CalibrateDialog } from './CalibrateDialog';
import { ReadingsDialog } from './ReadingsDialog';
import { TaskStyleStep } from './TaskStyleStep';

const PROVIDER_LABEL = { manual: '手动（网页聊天）', claude: 'Claude Code', codex: 'Codex', openai: 'API' } as const;

// the picture source of the form (video / audio + background) is remembered in this browser
const SOURCE_KEY = 'kara.simple.source';
function readSource(): 'video' | 'audio' {
  try { return localStorage.getItem(SOURCE_KEY) === 'audio' ? 'audio' : 'video'; } catch { return 'video'; }
}
function saveSource(v: 'video' | 'audio') {
  try { localStorage.setItem(SOURCE_KEY, v); } catch { /* private mode: not remembered */ }
}
const isImage = (f: File) => (f.type.startsWith('image/') && f.type !== 'image/gif') || /\.(png|jpe?g|webp|bmp)$/i.test(f.name);

function FileRow({ file, icon, onClear, note }: { file: File; icon: React.ReactNode; onClear: () => void; note?: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-surface-2/50 px-3 py-2.5">
      {icon}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium">{file.name}</div>
        <div className="text-xs text-muted">{note ? `${note} · ` : ''}{(file.size / 1024 / 1024).toFixed(1)} MB</div>
      </div>
      <Button size="xs" variant="ghost" icon={<X className="size-3.5" />} onClick={onClear}>换一个</Button>
    </div>
  );
}

const EXPLICIT = /^\s*(netease|ncm|163|wyy|qq|qqmusic)\s*[:：]\s*(?:(song|album|playlist)\s*[:：]\s*)?([A-Za-z0-9]+)\s*$/i;
const URL_RE = /https?:\/\/[^\s，。！？、）)"'<>【】「」]+/gi;

/** A URL the server can read lyrics from (NetEase / QQ Music, their short links). */
export function musicPlatform(url: string): 'netease' | 'qq' | null {
  let host = '';
  try { host = new URL(url).hostname.toLowerCase(); } catch { return null; }
  if (host.endsWith('music.163.com') || host === '163cn.tv') return 'netease';
  if (host.endsWith('y.qq.com')) return 'qq';
  return null;
}

/** An album / playlist rather than a song (the URL forms lyrics/fetch/links.py reads; short links
 *  are only known once the server follows them). */
export function isCollectionLink(url: string): boolean {
  let u: URL;
  try { u = new URL(url); } catch { return false; }
  const host = u.hostname.toLowerCase();
  if (host.endsWith('music.163.com')) {
    const path = u.hash.startsWith('#/') ? u.hash.slice(1) : u.pathname;
    return /\/(album|playlist)\b/.test(path) && !/\/song\b/.test(path);
  }
  if (host.endsWith('y.qq.com')) return /\/(albumDetail|album|playlist)\/|taoge/.test(u.pathname);
  return false;
}

const COLLECTION = '这是专辑或歌单链接：请打开其中一首歌，粘贴那首歌的链接';

/** What the pasted text looks like (mirrors the server's link detection: pipeline.is_music_link). */
export function detectLyrics(text: string): { kind: 'empty' | 'link' | 'badlink' | 'lrc' | 'text'; label: string } {
  const t = text.trim();
  if (!t) return { kind: 'empty', label: '' };
  const lines = t.split('\n').filter((l) => l.trim());
  const timed = lines.filter((l) => /^\s*\[\d+:\d+/.test(l)).length;
  const explicit = EXPLICIT.exec(t);
  if (explicit) {
    if (explicit[2] && explicit[2].toLowerCase() !== 'song') return { kind: 'badlink', label: COLLECTION };
    const where = explicit[1].toLowerCase().startsWith('qq') ? 'QQ 音乐' : '网易云音乐';
    return { kind: 'link', label: `${where}歌曲 ID · 会自动获取歌词` };
  }
  const urls = t.match(URL_RE) ?? [];
  if (urls.length && lines.length <= 3 && !timed) {
    const platform = urls.map(musicPlatform).find(Boolean);
    if (!platform) return { kind: 'badlink', label: '只支持网易云音乐 / QQ 音乐的链接；其他网站请直接粘贴歌词文字' };
    if (urls.every((u) => !musicPlatform(u) || isCollectionLink(u))) return { kind: 'badlink', label: COLLECTION };
    return { kind: 'link', label: `${platform === 'qq' ? 'QQ 音乐' : '网易云音乐'}链接 · 会自动获取歌词` };
  }
  if (timed > 0) return { kind: 'lrc', label: `LRC 歌词 · ${timed} 行带时间` };
  return { kind: 'text', label: `纯文本歌词 · ${lines.length} 行（没有时间）` };
}

export function SimpleHome() {
  const settings = useSimple((s) => s.settings);
  const tasks = useSimple((s) => s.tasks);
  const [mode, setMode] = useState<Mode>(settings?.simple.default_mode ?? 'lrc');
  // the form survives leaving the page (to the settings, the detailed mode …) until the task is added
  const [file, setFile] = usePageDraft<File | null>('simple.file', null);
  // "video": the video's own picture; "audio": the song's audio + a picture / looped video behind the subtitles
  const [source, setSource] = usePageDraft<'video' | 'audio'>('simple.source', readSource);
  const [background, setBackground] = usePageDraft<File | null>('simple.background', null);
  const [lyrics, setLyrics] = usePageDraft('simple.lyrics', '');
  const [name, setName] = usePageDraft('simple.name', '');
  const [busy, setBusy] = useState(false);
  const [upload, setUpload] = useState<number | null>(null);
  // start from the choices still being saved (left and came back quickly), else the saved ones
  const [styleOpts, setStyleOpts] = useState<TaskStyleOptions | null>(() => getTaskStyleDraft() ?? settings?.simple.task_style ?? null);
  const styleDirty = useRef(false);
  const [calibrating, setCalibrating] = useState<string | null>(null);
  const own = useSimple((s) => s.ownTasks);
  const detected = useMemo(() => detectLyrics(lyrics), [lyrics]);
  const active = hasActiveTasks(tasks);

  useEffect(() => {
    if (settings) setMode(settings.simple.default_mode);
  }, [settings?.simple.default_mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // the subtitle choices start from the last ones used, and are saved as they change
  useEffect(() => {
    if (settings && !styleOpts) setStyleOpts(getTaskStyleDraft() ?? settings.simple.task_style);
  }, [settings]); // eslint-disable-line react-hooks/exhaustive-deps
  const pendingStyle = useRef<TaskStyleOptions | null>(null);
  useEffect(() => {
    if (!styleOpts || !styleDirty.current) return;
    pendingStyle.current = styleOpts;
    const t = setTimeout(() => {
      pendingStyle.current = null;
      void saveTaskStyle(styleOpts).catch(() => undefined);
    }, 500);
    return () => clearTimeout(t);
  }, [styleOpts]);
  useEffect(() => () => {  // leaving the page (e.g. to the detailed mode): save what is still pending now
    const p = pendingStyle.current;
    if (p) void saveTaskStyle(p).catch(() => undefined);
  }, []);
  const changeStyle = (next: TaskStyleOptions) => { styleDirty.current = true; setStyleOpts(next); };

  useEffect(() => {
    if (calibrating) return;
    const ready = tasks.find((t) => t.status === 'waiting' && own.includes(t.id));
    if (ready) {
      forgetOwnTask(ready.id);
      setCalibrating(ready.id);
    }
  }, [tasks, calibrating, own]);
  // the dialog for what the task waits for: where the first line starts, or the AI readings by hand
  // the newest few (and every unfinished one); older ones on request
  const [allTasks, setAllTasks] = useState(false);
  const shownTasks = allTasks ? tasks : tasks.filter((t, i) => i < RECENT_TASKS || !FINAL.has(t.status));
  const calTask = tasks.find((t) => t.id === calibrating && waitingFor(t) === 'calibrate' && t.calibration);
  const readTask = tasks.find((t) => t.id === calibrating && waitingFor(t) === 'readings' && t.readings_request);

  const chooseMode = (m: Mode) => {
    setMode(m);
    void run(() => saveSettings({ simple: { default_mode: m } }));
  };

  const start = () => run(async () => {
    if (!file || busy) return;
    setBusy(true);
    try {
      // big files show how far the upload is (small ones are sent at once)
      const bg = source === 'audio' ? background : null;
      const progress = file.size + (bg?.size ?? 0) > 8 * 1024 * 1024 ? (f: number) => setUpload(f) : undefined;
      if (progress) setUpload(0);
      const t = await addTask(file, lyrics, mode, name, styleOpts ?? undefined, progress, bg);
      markOwnTask(t.id);
      toast('ok', '已开始', mode === 'lrc' ? '读取视频和歌词后请确认开头位置，之后全部自动完成' : active ? '前面的任务完成后自动继续' : '马上开始');
      setFile(null);  // (the background stays: the next song often uses the same one)
      setLyrics('');
      setName('');
    } finally {
      setBusy(false);
      setUpload(null);
    }
  }, '无法开始');

  // why “开始制作” cannot be used yet (shown on the button and next to it)
  const blocked = !file ? '先放入视频或音频（第 2 步）'
    : detected.kind === 'empty' ? '先粘贴歌词或音乐链接（第 3 步）'
      : detected.kind === 'badlink' ? (detected.label === COLLECTION ? '这是专辑或歌单链接：请粘贴单曲链接' : '这个链接不能获取歌词：只支持网易云音乐 / QQ 音乐')
        : styleOpts?.source === 'saved' && !styleOpts.saved_id ? '第 4 步选了“保存的预设”，请选择一个预设'
          : null;

  const s = settings?.simple;
  const ai = settings?.ai.enabled ? PROVIDER_LABEL[settings.ai.provider] ?? settings.ai.provider : null;
  const summary = s ? [
    ai ? `AI 注音：${ai}` : 'AI 注音：关',
    `人声分离：${s.separate ? '开' : '关'}`,
    s.auto_export ? '完成后生成视频' : '不自动生成视频',
  ] : [];

  const elsewhere = useApp((st) => st.info?.tasks_elsewhere);
  return (
    <div className="space-y-6">
      {elsewhere && (
        <Callout tone="warn" title="另一个服务进程正在运行任务队列">
          同一个项目目录下还开着另一个 MiliKara 服务，任务由它执行；这里只能查看，不能添加、取消或重试任务。请关掉其中一个后刷新页面。
        </Callout>
      )}
      <Card>
        <CardHeader icon={<Sparkles className="size-4" />} title="做一首卡拉OK" description="放入视频（或音频 + 背景图片 / 视频）和歌词，其余全部自动完成：注音、人声分离、对齐、生成带字幕的视频。" />
        <CardBody className="space-y-6">
          <StepBlock n={1} title="模式">
            <Segmented<Mode> label="模式" value={mode} onChange={chooseMode} options={[
              { value: 'lrc', label: 'LRC 增强（推荐）', title: '使用歌词里的行时间，长前奏和重复副歌更稳' },
              { value: 'plain', label: '普通', title: '只用歌词文字' },
            ]} />
            <p className="mt-1.5 text-xs text-muted">
              {mode === 'lrc'
                ? '使用歌词里的行时间定位每一行。开始后几秒内会请你确认第一句从哪里开始唱（视频和歌词常常差几百毫秒），之后全部自动完成；歌词没有时间会自动改用普通模式。'
                : '只用歌词文字，不需要时间，开始后全部自动完成。'}
            </p>
          </StepBlock>

          <StepBlock n={2} title="视频或音频">
            <Segmented<'video' | 'audio'> label="画面来源" value={source} onChange={(v) => { setSource(v); saveSource(v); }} options={[
              { value: 'video', label: '视频', title: '字幕烧录在视频自己的画面上' },
              { value: 'audio', label: '音频 + 背景', title: '只有音频：配一张图片或一段循环播放的视频作为画面' },
            ]} />
            <div className="mt-2.5 space-y-2">
              {source === 'video' ? (
                file ? <FileRow file={file} icon={<Film className="size-5 shrink-0 text-accent" />} onClear={() => setFile(null)} /> : (
                  <DropZone accept={MEDIA_ACCEPT} onFile={setFile} title="拖入视频（或音频），也可以点击选择"
                    hint="MP4 / MOV / MKV / MP3 / FLAC …；视频会保留画面，字幕直接烧录上去" />
                )
              ) : (
                <>
                  {file ? <FileRow file={file} icon={<Music2 className="size-5 shrink-0 text-accent" />} onClear={() => setFile(null)} /> : (
                    <DropZone compact accept={`${AUDIO_ACCEPT},${MEDIA_ACCEPT}`} onFile={setFile} title="拖入音频，也可以点击选择"
                      hint="MP3 / FLAC / M4A / WAV …（放入视频时只用它的声音）" />
                  )}
                  {background ? (
                    <FileRow file={background} icon={<ImageIcon className="size-5 shrink-0 text-accent" />} onClear={() => setBackground(null)}
                      note={isImage(background) ? '背景图片' : '背景视频 · 循环播放'} />
                  ) : (
                    <DropZone compact accept={BACKGROUND_ACCEPT} onFile={setBackground} title="拖入背景图片或视频（可选）"
                      hint="视频会循环播放、不用它的声音；不选则为纯黑背景。输出画面按背景的比例，长边 1920" />
                  )}
                </>
              )}
            </div>
          </StepBlock>

          <StepBlock n={3} title="歌词">
            <Textarea value={lyrics} onChange={(e) => setLyrics(e.target.value)} className="h-32"
              aria-label="音乐链接或歌词"
              placeholder={'粘贴网易云音乐 / QQ 音乐的歌曲链接（或分享文字），\n或者直接粘贴 LRC / 纯文本歌词'} />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {detected.kind !== 'empty' && (
                <Badge tone={detected.kind === 'link' ? 'accent' : detected.kind === 'badlink' ? 'warn' : detected.kind === 'lrc' ? 'ok' : 'neutral'}>
                  {detected.kind === 'link' || detected.kind === 'badlink' ? <Link2 className="mr-1 inline size-3" /> : <ListMusic className="mr-1 inline size-3" />}
                  {detected.label}
                </Badge>
              )}
              <Input value={name} onChange={(e) => setName(e.target.value)} className="h-8 max-w-64 flex-1 text-[13px]"
                aria-label="歌曲名（可选）" placeholder="歌曲名（可选，链接会自动识别）" />
            </div>
            {mode === 'plain' && detected.kind === 'lrc' && (
              <p className="mt-1.5 text-xs text-warn">
                这是带时间的 LRC，但第 1 步选的是普通模式：歌词里的时间不会被使用。想利用这些时间（长前奏、重复副歌更稳）请改选 LRC 增强。
              </p>
            )}
            {mode === 'lrc' && detected.kind === 'text' && (
              <p className="mt-1.5 text-xs text-muted">纯文本歌词没有时间：任务会自动改用普通模式。</p>
            )}
          </StepBlock>

          {s && styleOpts && (
            <StepBlock n={4} title="字幕与视频">
              <TaskStyleStep value={styleOpts} onChange={changeStyle} settings={s} />
            </StepBlock>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              {summary.map((x) => <span key={x}>{x}</span>)}
              <button className="focus-ring flex items-center gap-1 rounded text-accent hover:underline" onClick={() => setSimplePage('settings')}>
                <Settings2 className="size-3.5" />更改设置
              </button>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-3">
              {upload !== null && (
                <span className="flex items-center gap-2 text-xs text-muted">
                  <Progress value={upload} className="w-32" label="上传进度" />上传中 {Math.round(upload * 100)}%
                </span>
              )}
              {blocked && !busy && <span className="text-xs text-muted" id="start-blocked">{blocked}</span>}
              <Button variant="primary" size="lg" icon={<Play className="size-4" />} loading={busy}
                disabled={!!blocked} disabledReason={blocked} aria-describedby={blocked ? 'start-blocked' : undefined} onClick={start}>
                开始制作
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader icon={<ListMusic className="size-4" />} title="任务" description="按顺序逐个处理；完成的任务点开可以进入详细模式继续调整。" />
        <CardBody className="p-0">
          {tasks.length === 0 ? (
            <EmptyState className="m-4 py-10" title="还没有任务" description="上面放入视频和歌词，点“开始制作”" />
          ) : (
            <>
              <ul className="divide-y divide-line">
                {shownTasks.map((t) => <TaskRow key={t.id} task={t} onCalibrate={() => setCalibrating(t.id)}
                  ahead={tasks.filter((x) => (x.status === 'queued' || x.status === 'running') && x.created < t.created).length} />)}
              </ul>
              {tasks.length > shownTasks.length && (
                <button className="focus-ring flex w-full items-center justify-center gap-1.5 border-t border-line py-2.5 text-[13px] text-muted hover:bg-surface-2 hover:text-fg"
                  onClick={() => setAllTasks(true)}>
                  <ChevronDown className="size-4" />显示更早的 {tasks.length - shownTasks.length} 个任务
                </button>
              )}
            </>
          )}
        </CardBody>
      </Card>
      {calTask && <CalibrateDialog key={calTask.calibration!.line_id} task={calTask} onClose={() => setCalibrating(null)} />}
      {readTask && <ReadingsDialog key={readTask.id} task={readTask} onClose={() => setCalibrating(null)} />}
    </div>
  );
}

function StepBlock({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="flex gap-4">
      <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{n}</span>
      <div className="min-w-0 flex-1">
        <h3 className="mb-2 text-sm font-semibold">{title}</h3>
        {children}
      </div>
    </section>
  );
}

const RECENT_TASKS = 5;
const FINAL = new Set<PipelineTask['status']>(['succeeded', 'failed', 'cancelled', 'interrupted']);

const STATUS: Record<PipelineTask['status'], { label: string; tone: 'accent' | 'ok' | 'danger' | 'neutral' | 'warn' }> = {
  preparing: { label: '读取中', tone: 'accent' },
  waiting: { label: '等待确认', tone: 'warn' },
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '进行中', tone: 'accent' },
  succeeded: { label: '完成', tone: 'ok' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
  interrupted: { label: '已中断', tone: 'warn' },
};

function TaskRow({ task: t, ahead, onCalibrate }: { task: PipelineTask; ahead: number; onCalibrate: () => void }) {
  const st = STATUS[t.status];
  const live = t.status === 'running' || t.status === 'queued' || t.status === 'preparing' || t.status === 'waiting';
  const canOpen = !!t.project_id && !t.project_deleted && t.status !== 'running' && t.status !== 'preparing' && t.status !== 'waiting';
  const act = (a: 'cancel' | 'retry' | 'delete') => run(() => taskAction(t.id, a), '操作失败');
  const [confirmDel, setConfirmDel] = useState(false);
  // a finished task shows what needs a look; its steps and notes on request
  const [details, setDetails] = useState(false);
  const folded = t.status === 'succeeded' && !details;
  const actionable = (w: string) => w.includes('人工检查');
  const warnings = folded ? t.warnings.filter(actionable) : t.warnings;
  const hidden = t.warnings.length - warnings.length;
  const open = () => t.project_id && openInDetail(t.project_id, t.outputs.video ? 'karaoke' : 'review');
  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {canOpen ? (
              <button className="focus-ring max-w-full min-w-0 truncate rounded text-left text-[14px] font-semibold break-all hover:text-accent" onClick={open}
                title={`${t.name || t.media_filename}（在详细模式中打开）`}>
                {t.name || t.media_filename}
              </button>
            ) : <span className="max-w-full min-w-0 truncate text-[14px] font-semibold" title={t.name || t.media_filename}>{t.name || t.media_filename}</span>}
            <Badge tone={st.tone} dot>{st.label}</Badge>
            <Badge tone={t.mode === 'lrc' ? 'accent' : 'neutral'}>{t.mode === 'lrc' ? 'LRC' : '普通'}</Badge>
            {t.style_label && (
              <span className="flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-[11px] text-muted" title="这个任务的字幕样式">
                {(t.style_colors ?? []).map((c) => <span key={c} className="size-2.5 rounded-full ring-1 ring-line-strong" style={{ background: c }} />)}
                {t.style_label}
              </span>
            )}
          </div>
          <div className="mt-0.5 truncate text-xs text-muted">
            {t.media_filename}{t.background_filename ? ` + 背景 ${t.background_filename}` : ''} · {t.lyrics_kind === 'link' ? '音乐链接' : '粘贴的歌词'} · {fmtRelative(t.created)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {t.status === 'succeeded' && (t.outputs.videos?.length ?? 0) > 1 ? t.outputs.videos!.map((v, i) => (
            <DownloadLink key={v.filename} href={v.url} filename={v.filename} variant={i ? 'secondary' : 'primary'}
              icon={<Download className="size-4" />}>下载{v.label}</DownloadLink>
          )) : t.status === 'succeeded' && t.outputs.video && (
            <DownloadLink href={t.outputs.video.url} filename={t.outputs.video.filename} icon={<Download className="size-4" />}>下载视频</DownloadLink>
          )}
          {t.project_deleted && <Badge tone="neutral">项目已删除</Badge>}
          {!t.project_deleted && (t.status === 'failed' || t.status === 'cancelled' || t.status === 'interrupted') && (
            <Button size="sm" variant="secondary" icon={<RotateCcw className="size-4" />} onClick={() => act('retry')}
              title="从没完成的步骤继续，并再试一次出错时跳过的 AI 注音 / 人声分离。已有的分轨和对齐结果会保留；若 AI 注音这次改了读音，会重新对齐（锁定的手动时间保留），也会再次使用 AI 额度">重试</Button>
          )}
          {canOpen && <Button size="sm" variant="ghost" icon={<ArrowRight className="size-4" />} onClick={open}>详细模式</Button>}
          {live && (
            <ConfirmButton size="sm" variant="ghost" icon={<X className="size-4" />} question="取消这个任务？（之后可以重试）"
              confirmLabel="取消任务" keepLabel="继续" onConfirm={() => void act('cancel')}>取消</ConfirmButton>
          )}
          {!live && (confirmDel ? (
            <span className="flex items-center gap-1.5 rounded-lg bg-surface-2 px-2 py-1 text-xs text-muted">
              从列表移除？{t.project_id ? '项目和视频仍保留在详细模式' : ''}
              <Button size="xs" variant="danger" onClick={() => act('delete')}>移除</Button>
              <Button size="xs" variant="ghost" onClick={() => setConfirmDel(false)}>取消</Button>
            </span>
          ) : <Button size="sm" variant="ghost" icon={<Trash2 className="size-4" />} aria-label="移除任务" onClick={() => setConfirmDel(true)} />)}
        </div>
      </div>

      {waitingFor(t) === 'calibrate' && t.calibration && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-warn/40 bg-warn-soft px-3 py-2.5">
          <Hand className="size-4 text-warn" />
          <span className="min-w-0 flex-1 text-[13px]">需要你确认第一句「{t.calibration.line_text}」从哪里开始唱，之后全部自动完成</span>
          <Button size="sm" variant="primary" icon={<Crosshair className="size-4" />} onClick={onCalibrate}>确认开头位置</Button>
        </div>
      )}
      {waitingFor(t) === 'readings' && t.readings_request && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-warn/40 bg-warn-soft px-3 py-2.5">
          <Hand className="size-4 text-warn" />
          <span className="min-w-0 flex-1 text-[13px]">需要你把 AI 注音的提示词发给 AI 聊天网页，并把回复粘贴回来（也可以跳过），之后全部自动完成</span>
          <Button size="sm" variant="primary" icon={<ClipboardPaste className="size-4" />} onClick={onCalibrate}>粘贴 AI 注音结果</Button>
        </div>
      )}
      {t.status === 'queued' && !t.stages.some((s) => s.status === 'done') ? (
        <div className="mt-2 text-xs text-muted">{ahead ? `前面还有 ${ahead} 个任务` : '即将开始'}</div>
      ) : (
        <>
          {t.status === 'queued' && <div className="mt-2 text-xs text-muted">{ahead ? `已确认，排队中（前面还有 ${ahead} 个任务）` : '即将继续'}</div>}
          {t.status === 'running' && (
            <div className="mt-3 flex items-center gap-3">
              <Progress value={t.progress} className="flex-1" />
              <span className="tabular w-10 text-right text-xs text-muted">{Math.round(t.progress * 100)}%</span>
            </div>
          )}
          {folded ? (
            <button className="focus-ring mt-2 flex items-center gap-1 rounded text-xs text-muted hover:text-fg" onClick={() => setDetails(true)} aria-expanded={false}>
              <ChevronRight className="size-3.5" />处理详情（{t.stages.filter((s) => s.status === 'done').length} 步完成{hidden ? ` · ${hidden} 条说明` : ''}）
            </button>
          ) : (
            <>
              <ol className="mt-3 flex flex-wrap gap-1.5" aria-label="处理步骤">
                {t.stages.map((s) => <StageChip key={s.key} s={s} />)}
              </ol>
              {t.status === 'succeeded' && (
                <button className="focus-ring mt-2 flex items-center gap-1 rounded text-xs text-muted hover:text-fg" onClick={() => setDetails(false)} aria-expanded>
                  <ChevronDown className="size-3.5" />收起
                </button>
              )}
            </>
          )}
          {(t.status === 'running' || t.status === 'preparing') && t.message && <div className="mt-2 text-xs text-muted">{t.message}</div>}
        </>
      )}
      {t.error && (
        <div className="mt-2 rounded-lg bg-danger-soft px-3 py-2 text-xs break-words text-danger">
          <div>{t.error}</div>
          {errorHint(t.error) && <div className="mt-1 text-fg/80">{errorHint(t.error)}</div>}
          {t.status === 'failed' && <div className="mt-1 -mb-1"><DiagnosticsButton taskId={t.id} /></div>}
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5 text-xs text-warn">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              {canOpen && actionable(w) ? (
                <button className="focus-ring rounded text-left underline decoration-dotted underline-offset-2 hover:text-accent"
                  onClick={() => openInDetail(t.project_id!, 'review', { issues: true })} title="在详细模式的“人工检查”中打开">{w}</button>
              ) : w}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function StageChip({ s }: { s: PipelineStage }) {
  const icon = {
    done: <Check className="size-3" strokeWidth={3} />,
    running: <Loader2 className="size-3 animate-spin" />,
    failed: <X className="size-3" strokeWidth={3} />,
    skipped: <CircleDashed className="size-3" />,
    waiting: <Hand className="size-3" />,
    pending: null,
  }[s.status];
  const tip = s.status === 'running' ? `${Math.round(s.progress * 100)}% ${s.message}` : s.message || (s.status === 'skipped' ? '已跳过' : '');
  return (
    <li title={tip}
      className={cn('flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium',
        { done: 'border-ok/40 bg-ok-soft text-ok', running: 'border-accent/50 bg-accent-soft text-accent',
          failed: 'border-danger/40 bg-danger-soft text-danger', skipped: 'border-dashed border-line-strong text-subtle',
          waiting: 'border-warn/50 bg-warn-soft text-warn',
          pending: 'border-line text-subtle' }[s.status])}>
      {icon}{s.label}
      {s.status === 'done' && s.message && <span className="font-normal opacity-80">· {s.message}</span>}
    </li>
  );
}
