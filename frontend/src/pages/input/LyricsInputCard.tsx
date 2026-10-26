// Lyrics input: paste / upload / music link share one parse → preview → apply flow.

import { ClipboardPaste, Disc3, FileText, Link2, ListMusic, Search, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { isEnter } from '@/lib/keys';
import { useDraft } from '@/store/drafts';
import { api } from '@/lib/api';
import { fmtMs, readFileText } from '@/lib/format';
import type { FetchedSong, LinkResult, LyricsPreview, SongRef } from '@/lib/types';
import { ppath, run, setPV, toast, useProject } from '@/store/app';
import { Badge, Button, Card, CardBody, CardHeader, DropZone, EmptyState, Input, Tabs, Textarea } from '@/components/ui';
import { PreviewPanel } from './PreviewPanel';
import { HelpButton } from '@/components/guide/Help';

export interface PendingPreview {
  preview: LyricsPreview;
  text: string | null; // original text (for re-parse / routing); null for fetched songs
  origin: 'paste' | 'upload' | 'link';
  filename?: string;
  songRef?: { platform: string; song_id: string };
}

const PLATFORM_LABEL: Record<string, string> = { netease: '网易云音乐', qq: 'QQ 音乐' };

export function LyricsInputCard({ onExtraTracks }: { onExtraTracks: (tracks: Record<string, string>) => void }) {
  const project = useProject()!;
  // kept while the page is left (e.g. to check the readings) and come back to
  const [tab, setTab] = useDraft('lyrics.tab', 'paste');
  const [text, setText] = useDraft('lyrics.text', '');
  const [pending, setPending] = useDraft<PendingPreview | null>('lyrics.pending', null);
  const [busy, setBusy] = useState(false);
  const pasteRef = useRef<HTMLTextAreaElement>(null);

  const parse = (body: string, origin: 'paste' | 'upload', filename?: string) => run(async () => {
    if (!body.trim()) {
      toast('warn', '文本为空');
      return;
    }
    setBusy(true);
    try {
      const preview = await api.post<LyricsPreview>(ppath('/lyrics/parse'), { text: body, origin, filename });
      setPending({ preview, text: body, origin, filename });
    } finally {
      setBusy(false);
    }
  }, '解析失败');

  const reparse = () => {
    if (!pending) return;
    if (pending.songRef) return fromSong(pending.songRef.platform, pending.songRef.song_id);
    if (pending.text !== null) return parse(pending.text, pending.origin === 'upload' ? 'upload' : 'paste', pending.filename);
  };

  const fromSong = (platform: string, song_id: string) => run(async () => {
    setBusy(true);
    try {
      const preview = await api.post<LyricsPreview>(ppath('/lyrics/from-song'), { platform, song_id });
      setPending({ preview, text: null, origin: 'link', songRef: { platform, song_id } });
    } finally {
      setBusy(false);
    }
  }, '获取歌词失败');

  const apply = () => run(async () => {
    if (!pending?.preview.preview_id) return;
    setBusy(true);
    try {
      const pv = await api.post<any>(ppath('/lyrics/apply'), { preview_id: pending.preview.preview_id });
      setPV(pv);
      const extra = pending.preview.extra_tracks ?? {};
      if (Object.keys(extra).length) onExtraTracks(extra);
      const n = pv.project.lyrics.lines.length;
      toast('ok', '已应用歌词', `${n} 行${Object.keys(extra).length ? '；可在下方配对翻译 / 音译轨' : ''}`);
      setPending(null);
    } finally {
      setBusy(false);
    }
  }, '应用失败');

  const hasLyrics = project.lyrics.lines.length > 0;

  return (
    <Card>
      <CardHeader
        title="歌词"
        icon={<FileText className="size-4" />}
        help={<HelpButton k="input.lyrics" />}
        description={hasLyrics
          ? '重新导入会替换当前歌词文档；旧结果仍可查看但会标记为过期。'
          : '粘贴、上传文件或通过音乐链接获取；三种方式共用同一解析与预览，确认后才会应用。'}
      />
      <CardBody>
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            {
              value: 'paste',
              label: <><ClipboardPaste className="size-3.5" />粘贴</>,
              content: (
                <div className="space-y-3">
                  <Textarea
                    ref={pasteRef}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    placeholder={'每行一句歌词；也可以直接粘贴 LRC：\n[00:12.30]君と歩いた道\n\n或粘贴 prepared.json / 对齐结果 / AI 回传 JSON'}
                    className="min-h-48"
                  />
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted">
                      {project.mode === 'lrc' ? 'LRC 增强模式：需要带行时间的歌词' : '普通模式：导入 LRC 时只取正文，时间会被忽略'}
                    </span>
                    <Button variant="primary" loading={busy} onClick={() => parse(text, 'paste')} icon={<Search className="size-4" />}>解析预览</Button>
                  </div>
                </div>
              ),
            },
            {
              value: 'upload',
              label: <><Upload className="size-3.5" />上传文件</>,
              content: (
                <DropZone
                  accept=".lrc,.txt,.json,text/plain,application/json"
                  busy={busy}
                  title="拖入或点击选择歌词文件"
                  hint=".lrc / .txt / prepared.json 等；上传后与粘贴走同一解析流程"
                  onFile={(f) => run(async () => {
                    const body = await readFileText(f);
                    setText(body);
                    await parse(body, 'upload', f.name);
                  }, '读取文件失败')}
                />
              ),
            },
            {
              value: 'link',
              label: <><Link2 className="size-3.5" />音乐链接</>,
              content: <LinkTab busy={busy} onUseSong={fromSong} />,
            },
          ]}
        />

        {pending && (
          <div className="mt-5">
            <PreviewPanel
              pending={pending}
              busy={busy}
              onApply={apply}
              onDiscard={() => setPending(null)}
              onReparse={reparse}
              onEditTimes={() => {
                if (pending.text !== null) setText(pending.text);
                setTab('paste');
                setPending(null);
                setTimeout(() => pasteRef.current?.focus(), 50);
              }}
            />
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ------------------------------------------------------------------ music link

function LinkTab({ busy, onUseSong }: { busy: boolean; onUseSong: (platform: string, songId: string) => void }) {
  const [link, setLink] = useDraft('lyrics.link', '');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useDraft<LinkResult | null>('lyrics.linkResult', null);

  const resolve = () => run(async () => {
    if (!link.trim()) return;
    setLoading(true);
    try {
      setResult(await api.post<LinkResult>('/api/lyrics/link', { text: link }));
    } finally {
      setLoading(false);
    }
  }, '获取失败');

  const pickSong = (ref: SongRef, fallbackPlatform: string) => run(async () => {
    setLoading(true);
    try {
      setResult(await api.post<LinkResult>('/api/lyrics/song', { platform: ref.platform || fallbackPlatform, song_id: ref.song_id }));
    } finally {
      setLoading(false);
    }
  }, '获取失败');

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input
          value={link}
          onChange={(e) => setLink(e.target.value)}
          onKeyDown={(e) => { if (isEnter(e)) void resolve(); }}
          aria-label="音乐链接"
          placeholder="网易云 / QQ 音乐单曲链接、分享文案、短链，或 netease:123456 / qq:歌曲MID"
          className="h-10"
        />
        <Button variant="primary" size="lg" loading={loading} onClick={resolve} className="h-10">获取</Button>
      </div>
      <p className="text-xs text-muted">
        只获取歌词与必要的元数据，不下载歌曲音频；专辑 / 歌单链接会先列出歌曲供选择。联网失败不影响已有输入的离线处理。
      </p>

      {result?.kind === 'song' && <SongPreview song={result.song} busy={busy} onUse={() => onUseSong(result.song.platform, result.song.song_id)} />}

      {result?.kind === 'collection' && (
        <div className="rounded-xl border border-line">
          <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
            <ListMusic className="size-4 text-muted" />
            <span className="text-[13px] font-medium">{result.title ?? '歌曲列表'}</span>
            <Badge>{PLATFORM_LABEL[result.platform] ?? result.platform}</Badge>
            <span className="ml-auto text-xs text-muted">{result.songs.length} 首，选择一首</span>
          </div>
          {result.songs.length === 0 ? (
            <EmptyState className="m-3" title="列表为空" />
          ) : (
            <ul className="max-h-80 divide-y divide-line overflow-y-auto">
              {result.songs.map((s) => (
                <li key={s.song_id}>
                  <button
                    onClick={() => pickSong(s, result.platform)}
                    className="focus-ring flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-surface-2"
                  >
                    <Disc3 className="size-4 shrink-0 text-subtle" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium">{s.title}</span>
                      <span className="block truncate text-xs text-muted">{s.artists.join(' / ')}{s.album ? ` · ${s.album}` : ''}</span>
                    </span>
                    <span className="tabular text-xs text-subtle">{s.duration_ms ? fmtMs(s.duration_ms, false) : ''}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

const TRACK_LABEL: Record<string, string> = { original: '原文', translation: '翻译', romanization: '音译' };

function SongPreview({ song, busy, onUse }: { song: FetchedSong; busy: boolean; onUse: () => void }) {
  const tracks = Object.entries(song.tracks).filter(([, t]) => t && t.trim());
  const hasOriginal = !!song.tracks.original?.trim();
  return (
    <div className="flex flex-wrap items-start gap-4 rounded-xl border border-line bg-surface-2/40 p-4">
      <div className="grid size-14 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-indigo-500/20 to-fuchsia-500/20 text-accent">
        <Disc3 className="size-7" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[15px] font-semibold">{song.title ?? '（无标题）'}</span>
          <Badge>{PLATFORM_LABEL[song.platform] ?? song.platform}</Badge>
        </div>
        <div className="mt-0.5 text-[13px] text-muted">
          {song.artists.join(' / ') || '未知歌手'}{song.album ? ` · ${song.album}` : ''}{song.duration_ms ? ` · ${fmtMs(song.duration_ms, false)}` : ''}
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {tracks.length === 0 && <Badge tone="warn">没有可用歌词</Badge>}
          {tracks.map(([k]) => (
            <Badge key={k} tone={song.has_timestamps[k] ? 'accent' : 'neutral'}>
              {TRACK_LABEL[k] ?? k} · {song.has_timestamps[k] ? '带时间' : '无时间'}
            </Badge>
          ))}
        </div>
        {song.notes && song.notes.length > 0 && <div className="mt-2 text-xs text-muted">{song.notes.join('；')}</div>}
        <p className="mt-2 text-xs text-subtle">原文用于对齐；翻译 / 音译应用后可在下方单独配对，默认不参与对齐。</p>
      </div>
      <Button variant="primary" disabled={!hasOriginal} loading={busy} onClick={onUse}>使用该歌曲</Button>
    </div>
  );
}
