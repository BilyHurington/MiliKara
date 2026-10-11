// Step 4 of a simple-mode task: the subtitle look of this song — a colour template
// with one or two theme colours, a saved style or the settings' default — plus the
// switches that change from song to song.  The choices are bound to the task when
// it is added and remembered for the next one.

import { Plus, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { COUNTDOWN_DEFAULTS } from '@/lib/countdown';
import type { AppSettings, AudioVersion, EffectKind, KaraokeStyle, TaskStyleOptions, ThemePreview } from '@/lib/types';
import { useApp } from '@/store/app';
import { loadSavedStyles, useLibrary } from '@/store/styles';
import { MultiToggle, Segmented, Select, SliderField, Switch } from '@/components/ui';
import { ColorRow } from '@/components/karaoke/ThemeColors';
import { EFFECTS } from '@/components/karaoke/StylePanel';

type RubyChoice = Exclude<TaskStyleOptions['ruby'], 'style'>;
const TEMPLATE_HINT = {
  plain: '朴素：只有扫光变色和描边，干净清楚',
  glow: '荧光：带荧光边缘和翻译发光（唱到时的特效在下面单独选）',
};

export function TaskStyleStep({ value: o, onChange, settings }: {
  value: TaskStyleOptions;
  onChange: (next: TaskStyleOptions) => void;
  settings: AppSettings['simple'];
}) {
  const saved = useLibrary((s) => s.saved);
  const [theme, setTheme] = useState<KaraokeStyle | null>(null);
  const set = (patch: Partial<TaskStyleOptions>) => onChange({ ...o, ...patch });
  // “降低人声” needs the separation: switched on in the settings and installed on this computer
  const sepInstalled = useApp((s) => s.info?.separation_available ?? true);
  const canSeparate = settings.separate && sepInstalled;
  const noStems = !sepInstalled ? '这台电脑没有安装人声分离组件' : '需要在设置里开启人声分离';
  const themeSeq = useRef(0);

  useEffect(() => { if (!saved) void loadSavedStyles().catch(() => undefined); }, [saved]);
  useEffect(() => {
    if (saved && o.saved_id && !saved.some((x) => x.id === o.saved_id)) set({ saved_id: '' });
  }, [saved, o.saved_id]); // eslint-disable-line react-hooks/exhaustive-deps
  // the template's colours come from the server's palette algorithm
  useEffect(() => {
    if (o.source !== 'template') return;
    let stop = false;
    // debounced (dragging the colour picker changes the colour many times) and numbered: only the latest answer counts
    const my = ++themeSeq.current;
    const t = setTimeout(async () => {
      try {
        const r = await api.post<ThemePreview>('/api/karaoke/theme', { template: o.template, color: o.color, secondary: o.secondary || null });
        if (!stop && my === themeSeq.current) setTheme(r.style);
      } catch { /* keep the last preview */ }
    }, 200);
    return () => { stop = true; clearTimeout(t); };
  }, [o.source, o.template, o.color, o.secondary]);

  const chosen = o.source === 'saved' ? saved?.find((x) => x.id === o.saved_id)?.style ?? null : null;
  const base: KaraokeStyle | null = o.source === 'template' ? theme : o.source === 'saved' ? chosen : settings.karaoke;
  const translation = o.translation ?? base?.translation.enabled ?? false;
  const songInfo = o.song_info ?? base?.info.enabled ?? false;
  const ruby: RubyChoice = o.ruby === 'style' ? (base && !base.ruby.enabled ? 'off' : base?.ruby.script ?? 'hiragana') : o.ruby;
  const rubyTarget = o.ruby_target ?? base?.ruby.target ?? 'all';
  const effect: EffectKind = o.effects ?? base?.effects.kind ?? 'none';
  const cdBase = base?.countdown ?? COUNTDOWN_DEFAULTS;
  const cdIntro = o.countdown_intro ?? cdBase.intro;
  const cdInterlude = o.countdown_interlude ?? cdBase.interlude;
  const audio: AudioVersion[] = o.video_audio ?? settings.video_audio;
  const stemsWanted = audio.some((a) => a === 'instrumental' || a === 'mix');
  const vocal = o.vocal_keep_pct ?? settings.vocal_keep_pct;
  const shown = useMemo(() => base && {
    ...base,
    translation: { ...base.translation, enabled: translation },
    info: { ...base.info, enabled: songInfo },
    effects: { ...base.effects, kind: effect },
  }, [base, translation, songInfo, effect]);

  return (
    <div className="space-y-4">
      <Segmented<TaskStyleOptions['source']> label="字幕样式来源" value={o.source}
        onChange={(v) => set({ source: v, translation: null, song_info: null, ruby: 'style', ruby_target: null, effects: null, countdown_intro: null, countdown_interlude: null })} options={[
        { value: 'template', label: '模版配色' },
        { value: 'saved', label: '保存的预设' },
        { value: 'default', label: '设置里的样式' },
      ]} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,18rem)]">
        <div className="min-w-0 space-y-3">
          {o.source === 'template' && (
            <>
              <div className="space-y-1.5">
                <Segmented<TaskStyleOptions['template']> size="sm" label="配色模版" value={o.template} onChange={(v) => set({ template: v })} options={[
                  { value: 'plain', label: '朴素' }, { value: 'glow', label: '荧光' },
                ]} />
                <p className="text-xs text-muted">{TEMPLATE_HINT[o.template]}</p>
              </div>
              <ColorRow label="主色" value={o.color} onChange={(c) => set({ color: c })} />
              {o.secondary ? (
                <ColorRow label="辅色" value={o.secondary} onChange={(c) => set({ secondary: c })}
                  extra={<button type="button" aria-label="去掉辅色" onClick={() => set({ secondary: '' })}
                    className="focus-ring grid size-6 place-items-center rounded-full text-subtle hover:bg-surface-2 hover:text-fg"><X className="size-3.5" /></button>} />
              ) : (
                <button type="button" onClick={() => set({ secondary: '#F5C400' })}
                  className="focus-ring flex items-center gap-1 rounded text-xs text-accent hover:underline">
                  <Plus className="size-3.5" />加一个辅色（双色：例如橙色扫光 + 黄色荧光）
                </button>
              )}
              <p className="text-xs text-subtle">其余颜色（未唱、描边、阴影、翻译、荧光）按主题色自动搭配，并保证文字在描边上足够清楚。</p>
            </>
          )}
          {o.source === 'saved' && (
            <Select aria-label="选择预设" value={o.saved_id} onChange={(e) => set({ saved_id: e.target.value })}>
              <option value="">选择一个预设…</option>
              {(saved ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}{s.builtin ? '（内置）' : ''}</option>)}
            </Select>
          )}
          {o.source === 'default' && (
            <p className="text-[13px] text-muted">使用设置页“字幕样式”卡片里的完整样式（详细模式里点“设为极简默认”也会改它）。</p>
          )}

          <div className="space-y-3 border-t border-line pt-3">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
              <Switch checked={translation} onChange={(v) => set({ translation: v })} label="显示翻译" />
              <Switch checked={songInfo} onChange={(v) => set({ song_info: v })} label="开头和结尾显示歌曲信息" />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
              <span className="w-14 shrink-0 text-muted">注音</span>
              <Segmented<RubyChoice> size="sm" label="注音" value={ruby} onChange={(v) => set({ ruby: v })} options={[
                { value: 'off', label: '无' }, { value: 'hiragana', label: '平假名' },
                { value: 'katakana', label: '片假名' }, { value: 'romaji', label: '罗马音' },
              ]} />
              {ruby !== 'off' && (
                <Switch checked={rubyTarget === 'kanji'} onChange={(v) => set({ ruby_target: v ? 'kanji' : 'all' })} label="仅汉字" />
              )}
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
              <span className="w-14 shrink-0 text-muted">特效</span>
              <Select aria-label="特效" className="h-8 w-40 text-[13px]" value={effect}
                onChange={(e) => set({ effects: e.target.value as EffectKind })}>
                {(Object.keys(EFFECTS) as EffectKind[]).map((k) => <option key={k} value={k}>{EFFECTS[k].label}</option>)}
              </Select>
              {effect !== 'none' && <span className="text-xs text-subtle">{EFFECTS[effect].hint}</span>}
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
              <span className="w-14 shrink-0 text-muted">倒计时</span>
              <Switch checked={cdIntro} onChange={(v) => set({ countdown_intro: v })} label="第一句前" />
              <Switch checked={cdInterlude} onChange={(v) => set({ countdown_interlude: v })} label="间奏后" />
              <span className="text-xs text-subtle">开唱前几个圆点逐个消失</span>
            </div>
            {base && (
              <p className="text-xs text-subtle">
                {base.timing.advance_ms > 0
                  ? <>这个样式的字幕比实际演唱<b className="font-semibold text-muted">提前 {base.timing.advance_ms} ms</b> 显示：每个字的扫光要走完整个音，提前一点看起来正好在唱。</>
                  : <>这个样式的字幕<b className="font-semibold text-muted">没有提前</b>，和演唱同时开始扫光（扫光走到一半左右才像“唱到”，一般提前约 150 ms 看起来更准时）。</>}
                想改的话，在详细模式“字幕样式 → 时间”里调整“歌词提前显示”。
              </p>
            )}
            {settings.auto_export && (
              <div className="space-y-2 text-[13px]">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  <span className="w-14 shrink-0 text-muted">视频声音</span>
                  <MultiToggle<AudioVersion> size="sm" label="视频声音" value={audio} onChange={(v) => set({ video_audio: v })} options={[
                    { value: 'original', label: '原唱' },
                    { value: 'instrumental', label: '伴唱', disabled: !canSeparate, title: canSeparate ? '去掉人声' : noStems },
                    { value: 'mix', label: '降低人声', disabled: !canSeparate, title: canSeparate ? undefined : noStems },
                    { value: 'none', label: '无声' },
                  ]} />
                  {audio.length > 1 && <span className="text-xs text-subtle">每种声音各生成一个视频</span>}
                </div>
                {stemsWanted && !canSeparate && (
                  <p className="pl-[4.5rem] text-xs text-warn">
                    {!sepInstalled ? '这台电脑没有安装人声分离组件' : '人声分离已在设置里关闭'}，伴唱和降低人声的视频不会生成{audio.every((a) => a === 'instrumental' || a === 'mix') ? '（改用原声）' : ''}。
                  </p>
                )}
                {audio.includes('mix') && canSeparate && (
                  <div className="max-w-md pl-[4.5rem]">
                    <SliderField name="人声保留" label={<span className="text-muted">人声保留</span>} value={vocal}
                      onChange={(v) => set({ vocal_keep_pct: v })} min={0} max={100} step={1} unit="%" trackClassName="min-w-32" />
                    <p className="mt-1 text-xs text-subtle">降低人声时保留多少人声（伴唱是完全去掉人声）。</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <StyleMock style={shown} />
      </div>
    </div>
  );
}

/** A small CSS mock of the look (colours, outline, glow, translation, title card); the real
 * rendering is the libass preview in the detailed mode. */
function StyleMock({ style }: { style: KaraokeStyle | null }) {
  if (!style) return <div className="grid min-h-36 place-items-center rounded-xl bg-surface-2 text-xs text-subtle">选择样式后显示示意</div>;
  const T = style.text;
  const G = style.glow;
  const Tr = style.translation;
  const text = (color: string, glow?: string): React.CSSProperties => ({
    color, WebkitTextStroke: `4px ${T.outline_color}`, paintOrder: 'stroke fill',
    textShadow: glow ? `0 0 5px ${glow}, 0 0 10px ${glow}` : `1px 1px 0 ${T.shadow_color}80`,
  });
  const line = 'きみと歩いた空';
  return (
    <div className="relative min-h-36 overflow-hidden rounded-xl bg-[linear-gradient(135deg,#1c2436,#3a2f45_55%,#1a1a22)] p-3 font-bold"
      aria-label="字幕示意" role="img">
      {style.info.enabled && (
        <div className="mb-2 flex gap-1.5 text-[11px] leading-tight">
          <span className="w-0.5 rounded" style={{ background: style.info.accent || T.color_sung }} />
          <span style={{ color: style.info.color || T.color_unsung, WebkitTextStroke: `2px ${T.outline_color}`, paintOrder: 'stroke fill' }}>
            歌名<br /><span className="font-normal opacity-90">歌手</span>
          </span>
        </div>
      )}
      {Tr.enabled && (
        <div className="mb-3 text-center text-[13px]" style={{ ...text(Tr.color, G.enabled && Tr.glow ? G.color_unsung : undefined), WebkitTextStroke: `3px ${Tr.outline_color}` }}>
          和你一起走过的天空
        </div>
      )}
      <div className="absolute inset-x-3 bottom-4 text-center leading-none" style={{ fontSize: Math.round(22 * T.size / 88) }}>
        <span className="relative inline-block">
          <span style={text(T.color_unsung, G.enabled ? G.color_unsung : undefined)}>{line}</span>
          <span aria-hidden className="absolute inset-0" style={{ ...text(T.color_sung, G.enabled ? G.color_sung : undefined), clipPath: 'inset(0 45% 0 0)' }}>{line}</span>
          {style.effects.kind !== 'none' && (
            <span aria-hidden className="absolute -top-2 left-[52%] text-[11px]" style={{ color: style.effects.color || (G.enabled ? G.color_sung : T.color_sung) }}>✦ ✧</span>
          )}
        </span>
      </div>
    </div>
  );
}
