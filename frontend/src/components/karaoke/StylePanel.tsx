// Subtitle style panel, shared by the detailed 卡拉OK字幕 page and the simple-mode
// settings.  Top: saved styles (预设).  Then collapsible sections:
//   配色 (every colour in one place) · 歌词 · 注音 · 翻译 · 歌曲信息 · 布局 · 时间 · 特效
// Each section shows a one-line summary while closed.  Every control edits the
// style; the parent decides how to save it.

import {
  Check, Download, Languages, Music, Palette, Plus, RotateCcw, Save, Sparkles, Timer, Trash2, Type, X,
  LayoutTemplate, CaseSensitive,
} from 'lucide-react';
import { Children, isValidElement, useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from 'react';
import { cn } from '@/lib/format';
import { isEnter, isEscape } from '@/lib/keys';
import { api } from '@/lib/api';
import type { EffectKind, FontFamily, KaraokeStyle, SongInfo, SongInfoField, ThemePreview } from '@/lib/types';
import { run, toast } from '@/store/app';
import { deleteStyle, loadSavedStyles, sameLook, saveStyle, useLibrary } from '@/store/styles';
import { arrowNav, Badge, Button, Input, Segmented, Select, SliderField, Switch, Textarea, Tip } from '@/components/ui';
import { ColorRow, TEMPLATE_LABEL } from './ThemeColors';
import { COUNTDOWN_DEFAULTS } from '@/lib/countdown';

export type SectionId = 'colors' | 'text' | 'ruby' | 'translation' | 'info' | 'layout' | 'timing' | 'effects';
const SECTION_IDS: SectionId[] = ['colors', 'text', 'ruby', 'translation', 'info', 'layout', 'timing', 'effects'];
type Patch = (fn: (s: KaraokeStyle) => void) => void;

export interface TranslationInfo {
  /** lines of the current lyrics that have a translation (undefined: no project, e.g. the simple-mode settings) */
  lines?: number;
  /** fetch the translation from the lyrics' music platform */
  onFetch?: () => void;
}

/** Lines of the open project with their own countdown setting (the detailed page) */
export interface CountdownLines {
  /** lines set to always / never by hand */
  overrides: number;
  /** every line back to the rules above */
  onReset: () => void;
}

export interface SongInfoEditing {
  /** the project's song data and its own card text (null while loading) */
  data: SongInfo | null;
  /** save the card's own text; null goes back to the chosen fields */
  onText: (text: string | null) => void;
}

// ------------------------------------------------------------------ small building blocks

/** A labelled group (its controls — segmented buttons, sliders — get the row's name). */
function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <div className="space-y-1.5" role="group" aria-labelledby={id}>
      <div id={id} className="text-[13px] font-medium">{label}</div>
      {children}
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

function Num({ name, value, onChange, max, min = 0, step = 1, unit = 'px' }: {
  name: string; value: number; onChange: (v: number) => void; max: number; min?: number; step?: number; unit?: string;
}) {
  return <SliderField name={name} value={value} onChange={onChange} min={min} max={max} step={step} unit={unit} trackClassName="min-w-24" />;
}

export function ColorField({ label, value, onChange, disabled }: { label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <label className={cn('flex items-center gap-2 text-[13px]', disabled && 'opacity-45')}>
      <span className="relative size-7 shrink-0 overflow-hidden rounded-md ring-1 ring-line-strong" style={{ background: value }}>
        <input type="color" aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="absolute inset-0 cursor-pointer opacity-0" />
      </span>
      <span className="min-w-0 flex-1 text-muted">{label}</span>
      <span className="font-mono text-xs text-subtle">{value}</span>
    </label>
  );
}

function FontSelect({ label, value, fonts, fallback, onChange, followLabel }: {
  label: string; value: string; fonts: FontFamily[]; fallback: string; onChange: (v: string) => void; followLabel?: string;
}) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <option value="">{followLabel ?? `默认（${fallback || '系统日文字体'}）`}</option>
      {fonts.map((f) => (
        <option key={f.family} value={f.family}>
          {f.family}{f.names.length > 1 && f.names[1] !== f.family ? ` · ${f.names.find((n) => /[぀-ヿ一-鿿]/.test(n)) ?? ''}` : ''}
        </option>
      ))}
    </Select>
  );
}

interface SectionProps { id: SectionId; icon: ReactNode; title: string; summary: ReactNode; children: ReactNode }

/** One category of the panel (shown when its tab is chosen). */
function Section({ id, title, children }: SectionProps) {
  return (
    <section role="tabpanel" aria-label={title} data-section={id} className="space-y-4 px-1 pt-4 pb-2">
      {children}
    </section>
  );
}

/** The categories as a menu (8 tabs, their current values on hover) and the chosen one below it:
 *  the whole style is never one long list.  The tabs are read from the <Section> children. */
function SectionTabs({ active, onSelect, fill, children }: {
  active: SectionId; onSelect: (id: SectionId) => void; fill?: boolean; children: ReactNode;
}) {
  const items = Children.toArray(children).filter(isValidElement) as ReactElement<SectionProps>[];
  const current = items.find((c) => c.props.id === active) ?? items[0];
  const tabs = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  // another category starts at its top: the panel's own scroll back to 0, or (in a page that
  // scrolls) the tabs back into view
  const picked = useRef(false);
  const choose = (id: SectionId) => {
    if (id === current?.props.id) return;
    picked.current = true;
    onSelect(id);
  };
  useEffect(() => {
    if (!picked.current) return;  // (not on the first render)
    picked.current = false;
    if (fill && box.current) box.current.scrollTop = 0;
    else tabs.current?.scrollIntoView?.({ block: 'nearest' });
  }, [active, fill]);
  return (
    <>
      <div ref={tabs} role="tablist" aria-label="样式分类" onKeyDown={(e) => arrowNav(e, 'tab')}
        className="grid grid-cols-4 gap-1 rounded-xl bg-surface-2 p-1 @xl:grid-cols-8">
        {items.map((c) => {
          const on = c === current;
          return (
            <Tip key={c.props.id} content={on ? null : c.props.summary} keep>
              <button type="button" role="tab" aria-selected={on} tabIndex={on ? 0 : -1} onClick={() => choose(c.props.id)}
                className={cn('focus-ring flex flex-col items-center gap-0.5 rounded-lg px-1 py-1.5 text-[12px] font-medium transition',
                  on ? 'bg-surface text-accent shadow-sm' : 'text-muted hover:text-fg')}>
                {c.props.icon}{c.props.title}
              </button>
            </Tip>
          );
        })}
      </div>
      {/* every category stays mounted (hidden): one still busy (a colour template on its way, a draft
          being typed) keeps working with the current style while another is shown */}
      <div ref={box} className={cn(fill && '-mx-1 min-h-0 flex-1 overflow-y-auto px-1')}>
        {items.map((c) => <div key={c.props.id} hidden={c !== current}>{c}</div>)}
      </div>
    </>
  );
}

const Dot = ({ c }: { c: string }) => <span className="inline-block size-2.5 rounded-full ring-1 ring-line-strong align-middle" style={{ background: c }} />;

// ------------------------------------------------------------------ the panel

export function StylePanel({ style, onChange, fonts, defaultFont, defaultSection = 'colors', storageKey, translation, songInfo, countdownLines, fill }: {
  style: KaraokeStyle;
  onChange: (next: KaraokeStyle) => void;
  fonts: FontFamily[];
  defaultFont: string;
  defaultSection?: SectionId;
  /** remember the category shown (per place the panel is used) */
  storageKey?: string;
  translation?: TranslationInfo;
  /** the detailed page edits the card's text; the simple mode only picks the lines */
  songInfo?: SongInfoEditing;
  countdownLines?: CountdownLines;
  /** fill a height-limited parent (a flex column): the tabs stay put, the chosen category scrolls */
  fill?: boolean;
}) {
  const [active, setActive] = useState<SectionId>(() => {
    try {
      const v = storageKey ? localStorage.getItem(`kara.style.${storageKey}.tab`) : null;
      if (v && SECTION_IDS.includes(v as SectionId)) return v as SectionId;
    } catch { /* ignore */ }
    return defaultSection;
  });
  const select = (id: SectionId) => {
    setActive(id);
    try { if (storageKey) localStorage.setItem(`kara.style.${storageKey}.tab`, id); } catch { /* ignore */ }
  };
  const patch: Patch = (fn) => {
    const next = structuredClone(style);
    fn(next);
    // a colour or effect changed by hand: no longer the template's colours
    if (next.theme && themedLook(next) !== themedLook(style)) next.theme = null;
    onChange(next);
  };
  const { layout: L, text: T, ruby: R, translation: Tr, glow: G, timing: M, effects: E, info: I } = style;
  const C = style.countdown ?? COUNTDOWN_DEFAULTS;
  const setCd = (fn: (c: NonNullable<KaraokeStyle['countdown']>) => void) => patch((s) => { s.countdown = { ...C }; fn(s.countdown); });
  const posLabel = { opposite: L.position === 'bottom' ? '画面顶部' : '画面底部', block: '歌词旁', line: '每行下方' };
  const sec = (id: SectionId) => ({ id });

  return (
    <div className={cn('@container', fill && 'flex min-h-0 flex-1 flex-col')}>
      <PresetBar style={style} onChange={onChange} />
      <div className={cn('mt-3', fill && 'flex min-h-0 flex-1 flex-col')}>
        <SectionTabs active={active} onSelect={select} fill={fill}>
        <Section {...sec('colors')} icon={<Palette className="size-4" />} title="配色"
          summary={<span className="flex items-center gap-1.5">{style.theme ? `${TEMPLATE_LABEL[style.theme.template]}模版 · ` : ''}歌词 <Dot c={T.color_unsung} /><Dot c={T.color_sung} /><Dot c={T.outline_color} />
            {G.enabled && <>· 荧光 <Dot c={G.color_unsung} /><Dot c={G.color_sung} /></>}</span>}>
          <ThemeBar style={style} onChange={onChange} />
          <ColorGroup title="歌词">
            <ColorField label="未唱" value={T.color_unsung} onChange={(v) => patch((s) => { s.text.color_unsung = v; })} />
            <ColorField label="已唱（扫光）" value={T.color_sung} onChange={(v) => patch((s) => { s.text.color_sung = v; })} />
            <ColorField label="描边" value={T.outline_color} onChange={(v) => patch((s) => { s.text.outline_color = v; })} />
            <ColorField label="阴影" value={T.shadow_color} onChange={(v) => patch((s) => { s.text.shadow_color = v; })} />
          </ColorGroup>
          <ColorGroup title="注音" action={<Switch checked={R.follow_colors} onChange={(v) => patch((s) => { s.ruby.follow_colors = v; })} label={<span className="text-xs text-muted">跟随歌词</span>} />}>
            {!R.follow_colors ? (
              <>
                <ColorField label="未唱" value={R.color_unsung} onChange={(v) => patch((s) => { s.ruby.color_unsung = v; })} />
                <ColorField label="已唱" value={R.color_sung} onChange={(v) => patch((s) => { s.ruby.color_sung = v; })} />
                <ColorField label="描边" value={R.outline_color} onChange={(v) => patch((s) => { s.ruby.outline_color = v; })} />
              </>
            ) : <p className="text-xs text-subtle">与歌词相同的颜色和描边</p>}
          </ColorGroup>
          <ColorGroup title="翻译">
            <ColorField label="文字" value={Tr.color} onChange={(v) => patch((s) => { s.translation.color = v; })} />
            <ColorField label="描边" value={Tr.outline_color} onChange={(v) => patch((s) => { s.translation.outline_color = v; })} />
          </ColorGroup>
          <ColorGroup title="歌曲信息" action={<Switch checked={!I.color && !I.accent} label={<span className="text-xs text-muted">跟随歌词</span>}
            onChange={(v) => patch((s) => { s.info.color = v ? '' : T.color_unsung; s.info.accent = v ? '' : T.color_sung; })} />}>
            {I.color || I.accent ? (
              <>
                <ColorField label="文字" value={I.color || T.color_unsung} onChange={(v) => patch((s) => { s.info.color = v; })} />
                <ColorField label="强调条" value={I.accent || T.color_sung} onChange={(v) => patch((s) => { s.info.accent = v; })} />
              </>
            ) : <p className="text-xs text-subtle">文字用歌词未唱颜色，强调条用已唱颜色</p>}
          </ColorGroup>
          <ColorGroup title="荧光边缘" action={!G.enabled ? <span className="text-xs text-subtle">在“歌词”中开启</span> : undefined}>
            <ColorField label="未唱时" value={G.color_unsung} disabled={!G.enabled} onChange={(v) => patch((s) => { s.glow.color_unsung = v; })} />
            <ColorField label="唱过后" value={G.color_sung} disabled={!G.enabled} onChange={(v) => patch((s) => { s.glow.color_sung = v; })} />
          </ColorGroup>
        </Section>

        <Section {...sec('text')} icon={<Type className="size-4" />} title="歌词"
          summary={`${T.font || defaultFont || '默认字体'} · ${T.size}px${T.bold ? ' · 粗体' : ''} · 描边 ${T.outline}${G.enabled ? ' · 荧光边缘' : ''}`}>
          <Row label="字体"><FontSelect label="歌词字体" value={T.font} fonts={fonts} fallback={defaultFont} onChange={(v) => patch((s) => { s.text.font = v; })} /></Row>
          <Row label="字号" hint="以 1920 宽的画面为准，生成视频时按视频宽度缩放"><Num name="字号" value={T.size} min={24} max={200} onChange={(v) => patch((s) => { s.text.size = v; })} /></Row>
          <Switch checked={T.bold} onChange={(v) => patch((s) => { s.text.bold = v; })} label="粗体" />
          <Row label="描边宽度"><Num name="描边宽度" value={T.outline} max={16} step={0.5} onChange={(v) => patch((s) => { s.text.outline = v; })} /></Row>
          <Row label="阴影距离"><Num name="阴影距离" value={T.shadow} max={16} step={0.5} onChange={(v) => patch((s) => { s.text.shadow = v; })} /></Row>
          <Row label="阴影不透明度"><Num name="阴影不透明度" unit="%" value={T.shadow_opacity} max={100} onChange={(v) => patch((s) => { s.text.shadow_opacity = v; })} /></Row>
          <Row label="高亮方式">
            <Segmented value={M.highlight} onChange={(v) => patch((s) => { s.timing.highlight = v; })}
              options={[{ value: 'sweep', label: '平滑扫光' }, { value: 'instant', label: '逐字变色' }]} />
          </Row>
          <div className="space-y-4 rounded-xl border border-line p-3">
            <Switch checked={G.enabled} onChange={(v) => patch((s) => { s.glow.enabled = v; })} label={<span className="font-medium">荧光边缘</span>} />
            {G.enabled && (
              <>
                <Row label="大小"><Num name="荧光大小" value={G.size} min={1} max={40} step={0.5} onChange={(v) => patch((s) => { s.glow.size = v; })} /></Row>
                <Row label="柔和"><Num name="荧光柔和" value={G.blur} min={0} max={30} step={0.5} onChange={(v) => patch((s) => { s.glow.blur = v; })} /></Row>
                <Row label="强度"><Num name="荧光强度" unit="%" value={G.strength} min={10} max={100} onChange={(v) => patch((s) => { s.glow.strength = v; })} /></Row>
                <Switch checked={G.ruby} onChange={(v) => patch((s) => { s.glow.ruby = v; })} label="注音也发光" />
                <p className="text-xs text-subtle">颜色在“配色”里调整：未唱与唱过后可以用不同的光。</p>
              </>
            )}
          </div>
        </Section>

        <Section {...sec('ruby')} icon={<CaseSensitive className="size-4" />} title="注音"
          summary={R.enabled ? `${{ hiragana: '平假名', katakana: '片假名', romaji: '罗马音' }[R.script]} · ${R.target === 'kanji' ? '仅汉字' : '全部'} · ${R.size_pct}%${R.sweep === 'base' ? ' · 与歌词对齐' : ''}` : '关闭'}>
          <Switch checked={R.enabled} onChange={(v) => patch((s) => { s.ruby.enabled = v; })} label="显示注音" />
          <div className={cn('space-y-4', !R.enabled && 'pointer-events-none opacity-45')}>
            <Row label="文字">
              <Segmented value={R.script} onChange={(v) => patch((s) => { s.ruby.script = v; })}
                options={[{ value: 'hiragana', label: '平假名' }, { value: 'katakana', label: '片假名' }, { value: 'romaji', label: '罗马音' }]} />
            </Row>
            <Row label="标注位置" hint={R.target === 'kanji' ? '只在汉字和数字上方标注，送假名不重复标注' : '所有假名也标注（平假名注音在平假名上会自动省略）'}>
              <Segmented value={R.target} onChange={(v) => patch((s) => { s.ruby.target = v; })}
                options={[{ value: 'kanji', label: '仅汉字' }, { value: 'all', label: '全部' }]} />
            </Row>
            <Row label="注音过宽时">
              <Segmented value={R.fit} onChange={(v) => patch((s) => { s.ruby.fit = v; })}
                options={[{ value: 'widen', label: '加宽歌词' }, { value: 'overflow', label: '允许超出' }]} />
            </Row>
            <Row label="唱过的部分" hint={R.sweep === 'base'
              ? '注音的覆盖条和下方歌词在同一个位置，上下一条竖线扫过'
              : '注音按每个读音自己的时间变色（如「き」「み」分别扫过）'}>
              <Segmented value={R.sweep} onChange={(v) => patch((s) => { s.ruby.sweep = v; })}
                options={[{ value: 'own', label: '按注音时间' }, { value: 'base', label: '与歌词对齐' }]} />
            </Row>
            <Row label="字号（相对歌词）"><Num name="注音字号" unit="%" min={20} value={R.size_pct} max={80} onChange={(v) => patch((s) => { s.ruby.size_pct = v; })} /></Row>
            <Row label="与歌词的间距"><Num name="注音间距" value={R.gap} min={-20} max={60} onChange={(v) => patch((s) => { s.ruby.gap = v; })} /></Row>
            <Row label="字体"><FontSelect label="注音字体" value={R.font} fonts={fonts} fallback={defaultFont} followLabel="跟随歌词字体" onChange={(v) => patch((s) => { s.ruby.font = v; })} /></Row>
            {!R.follow_colors && (
              <Row label="描边宽度"><Num name="注音描边" value={R.outline} max={12} step={0.5} onChange={(v) => patch((s) => { s.ruby.outline = v; })} /></Row>
            )}
          </div>
        </Section>

        <Section {...sec('translation')} icon={<Languages className="size-4" />} title="翻译"
          summary={Tr.enabled ? `${posLabel[Tr.position]} · ${Tr.size_pct}%` : '关闭'}>
          <Switch checked={Tr.enabled} onChange={(v) => patch((s) => { s.translation.enabled = v; })} label="显示翻译字幕（歌词有翻译时）" />
          {Tr.enabled && translation?.lines === 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
              这首歌的歌词还没有翻译，预览里不会出现。
              {translation.onFetch && <Button size="xs" variant="secondary" icon={<Download className="size-3.5" />} onClick={translation.onFetch}>从音乐平台获取翻译</Button>}
            </div>
          )}
          <div className={cn('space-y-4', !Tr.enabled && 'pointer-events-none opacity-45')}>
            <Row label="位置" hint={{
              opposite: `一次一行，显示在${posLabel.opposite}，跟随正在唱的歌词`,
              block: `一次一行，紧挨在歌词${L.position === 'bottom' ? '上方' : '下方'}`,
              line: '每行歌词下方各自显示（占用更多高度）',
            }[Tr.position]}>
              <Segmented value={Tr.position} onChange={(v) => patch((s) => { s.translation.position = v; })}
                options={[{ value: 'opposite', label: posLabel.opposite }, { value: 'block', label: '歌词旁' }, { value: 'line', label: '每行下方' }]} />
            </Row>
            <Row label="字号（相对歌词）"><Num name="翻译字号" unit="%" min={20} value={Tr.size_pct} max={100} onChange={(v) => patch((s) => { s.translation.size_pct = v; })} /></Row>
            <Row label="字体"><FontSelect label="翻译字体" value={Tr.font} fonts={fonts} fallback={defaultFont} followLabel="跟随歌词字体" onChange={(v) => patch((s) => { s.translation.font = v; })} /></Row>
            <Switch checked={Tr.bold} onChange={(v) => patch((s) => { s.translation.bold = v; })} label="粗体" />
            <Row label="描边宽度"><Num name="翻译描边" value={Tr.outline} max={12} step={0.5} onChange={(v) => patch((s) => { s.translation.outline = v; })} /></Row>
            <Row label="阴影距离"><Num name="翻译阴影" value={Tr.shadow} max={12} step={0.5} onChange={(v) => patch((s) => { s.translation.shadow = v; })} /></Row>
            <Switch checked={Tr.glow} onChange={(v) => patch((s) => { s.translation.glow = v; })} label="开启荧光边缘时，翻译也发光" />
            {Tr.glow && (
              <Switch checked={Tr.singer_glow ?? true} onChange={(v) => patch((s) => { s.translation.singer_glow = v; })}
                label={<span>荧光跟随这一句的演唱者<span className="ml-1 text-xs text-muted">（多人演唱时：几个人的颜色从左到右渐变；文字颜色不变）</span></span>} />
            )}
          </div>
        </Section>

        <Section {...sec('info')} icon={<Music className="size-4" />} title="歌曲信息"
          summary={I.enabled ? `${I.position === 'top-left' ? '左上角' : '右上角'} · ${songInfo?.data?.text != null ? '自定义文字' : I.fields.map((f) => FIELD_LABEL[f]).join(' / ')} · ${I.duration_ms / 1000}s${(I.outro ?? true) ? ` · 结尾 ${(I.outro_duration_ms ?? 7000) / 1000}s` : ''}` : '关闭'}>
          <InfoEditor style={style} patch={patch} songInfo={songInfo} />
        </Section>

        <Section {...sec('layout')} icon={<LayoutTemplate className="size-4" />} title="布局"
          summary={`${L.position === 'bottom' ? '靠底' : '靠顶'} · ${L.lines} 行${L.lines > 1 ? (L.arrangement === 'alternate' ? '左右交替' : '居中') : ''} · 边距 ${L.margin_v}`}>
          <Row label="位置">
            <Segmented value={L.position} onChange={(v) => patch((s) => { s.layout.position = v; })}
              options={[{ value: 'bottom', label: '靠底' }, { value: 'top', label: '靠顶' }]} />
          </Row>
          <Row label="同屏行数" hint={L.lines > 1 ? '下一行提前出现在另一行的位置，便于跟唱' : '每次只显示正在唱的一行'}>
            <Segmented value={String(L.lines)} onChange={(v) => patch((s) => { s.layout.lines = Number(v); })}
              options={[{ value: '1', label: '1 行' }, { value: '2', label: '2 行' }, { value: '3', label: '3 行' }]} />
          </Row>
          {L.lines > 1 && (
            <Row label="排列">
              <Segmented value={L.arrangement} onChange={(v) => patch((s) => { s.layout.arrangement = v; })}
                options={[{ value: 'alternate', label: '左右交替' }, { value: 'center', label: '全部居中' }]} />
            </Row>
          )}
          <Row label="与画面边缘的距离"><Num name="纵向边距" value={L.margin_v} max={400} onChange={(v) => patch((s) => { s.layout.margin_v = v; })} /></Row>
          {L.lines > 1 && <Row label="行与行的间距"><Num name="行间距" value={L.line_spacing} max={200} onChange={(v) => patch((s) => { s.layout.line_spacing = v; })} /></Row>}
          <Row label="左右边距" hint="歌词最宽可以用到的范围：画面宽度 − 两侧边距">
            <Num name="左右边距" value={L.margin_h} max={600} onChange={(v) => patch((s) => { s.layout.margin_h = v; })} />
          </Row>
          {L.lines > 1 && L.arrangement === 'alternate' && (
            <Row label="交替行向中间缩进" hint="短句不会分到两端；放不下的长句自动退回边距处">
              <Num name="向中间缩进" value={L.alternate_indent} max={800} onChange={(v) => patch((s) => { s.layout.alternate_indent = v; })} />
            </Row>
          )}
          <Row label="长句换行" hint={(L.wrap ?? 'auto') === 'off' ? '放不下的行保持一整行（伸向画面边缘，仍放不下时缩小）'
            : (L.wrap ?? 'auto') === 'ai' ? '优先用 AI 注音时建议的换行位置（没有时按空格、标点）；两半像两行一样轮流显示，翻译仍是一整行'
              : '放不下的行在空格、标点处（没有时在两个词之间）分成两半，像两行一样轮流显示；翻译仍是一整行'}>
            <Segmented value={L.wrap ?? 'auto'} onChange={(v) => patch((s) => { s.layout.wrap = v; })}
              options={[{ value: 'auto', label: '空格、标点处' }, { value: 'ai', label: '按 AI 建议' }, { value: 'off', label: '不换行' }]} />
          </Row>
          <Row label="长句离画面边缘至少" hint="换行后仍然放不下的行可以超出左右边距，伸到离边缘这么近；再放不下才缩小">
            <Num name="长句边缘距离" value={L.edge_margin ?? 50} max={400} onChange={(v) => patch((s) => { s.layout.edge_margin = v; })} />
          </Row>
          <Switch checked={L.shrink_long_lines} onChange={(v) => patch((s) => { s.layout.shrink_long_lines = v; })} label="仍然过长的行自动缩小，保证不超出画面" />
          <p className="text-xs text-subtle">像素值以 1920 宽的画面为准；生成视频时按视频宽度等比缩放（竖屏、4:3 等画面里文字占宽度的比例不变）。</p>
        </Section>

        <Section {...sec('timing')} icon={<Timer className="size-4" />} title="时间"
          summary={`提前 ${M.lead_in_ms / 1000}s · 停留 ${M.hold_ms / 1000}s · 淡入淡出 ${M.fade_in_ms}/${M.fade_out_ms}ms${M.advance_ms ? ` · 扫光提前 ${M.advance_ms}ms` : ''}${C.intro || C.interlude ? ` · 倒计时${C.intro && C.interlude ? '' : C.intro ? '（开头）' : '（间奏后）'}` : ''}`}>
          <Row label="提前出现" hint="歌词至少在开唱前这么久出现">
            <Num name="提前出现" unit="ms" value={M.lead_in_ms} max={8000} step={100} onChange={(v) => patch((s) => { s.timing.lead_in_ms = v; })} />
          </Row>
          <Row label="唱完后停留"><Num name="唱完后停留" unit="ms" value={M.hold_ms} max={5000} step={100} onChange={(v) => patch((s) => { s.timing.hold_ms = v; })} /></Row>
          <Row label="淡入（缓进）"><Num name="淡入" unit="ms" value={M.fade_in_ms} max={1500} step={50} onChange={(v) => patch((s) => { s.timing.fade_in_ms = v; })} /></Row>
          <Row label="淡出（缓出）"><Num name="淡出" unit="ms" value={M.fade_out_ms} max={1500} step={50} onChange={(v) => patch((s) => { s.timing.fade_out_ms = v; })} /></Row>
          <Switch checked={M.early_show} onChange={(v) => patch((s) => { s.timing.early_show = v; })} label="位置空出后尽早显示下一行" />
          {M.early_show && (
            <Row label="最多提前" hint="长间奏时不会过早出现">
              <Num name="最多提前" unit="ms" value={M.early_max_ms} min={1000} max={10000} step={500} onChange={(v) => patch((s) => { s.timing.early_max_ms = v; })} />
            </Row>
          )}
          <Switch checked={M.advance_ms > 0} onChange={(v) => patch((s) => { s.timing.advance_ms = v ? 150 : 0; })} label="歌词提前显示（扫光比实际演唱早一点）" />
          {M.advance_ms > 0 && (
            <Row label="提前多少" hint="一般 100–200 ms 看起来更跟手；同样作用于导出的 LRC（alignment.json / CSV 保持原始时间）">
              <Num name="歌词提前" unit="ms" value={M.advance_ms} min={10} max={1000} step={10} onChange={(v) => patch((s) => { s.timing.advance_ms = v; })} />
            </Row>
          )}
          <div className="space-y-3 rounded-xl border border-line p-3">
            <div className="text-[13px] font-medium">开唱倒计时</div>
            <p className="text-xs text-subtle">行首上方显示几个圆点，最后几秒每秒消失一个，最后一个在开始扫光时消失。</p>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              <Switch checked={C.intro} onChange={(v) => setCd((c) => { c.intro = v; })} label="第一句前" />
              <Switch checked={C.interlude} onChange={(v) => setCd((c) => { c.interlude = v; })} label="间奏后" />
            </div>
            {C.interlude && (
              <Row label="停顿多久算间奏" hint="和上一句之间隔了这么久，才在这一句前倒计时">
                <Num name="间奏至少" unit="ms" value={C.min_gap_ms} min={2000} max={30000} step={500} onChange={(v) => setCd((c) => { c.min_gap_ms = v; })} />
              </Row>
            )}
            {(C.intro || C.interlude) && (
              <Row label="圆点数"><Num name="圆点数" unit="个" value={C.dots} min={2} max={5} onChange={(v) => setCd((c) => { c.dots = v; })} /></Row>
            )}
            <p className="text-xs text-subtle">
              {countdownLines
                ? <>单独某一句：在预览里选中这一行，设为“显示”或“不显示”。{countdownLines.overrides > 0 && (
                  <> 已有 {countdownLines.overrides} 句单独设置，<button type="button" className="focus-ring rounded text-accent hover:underline" onClick={countdownLines.onReset}>全部恢复自动</button>。</>)}</>
                : '单独某一句可以在详细模式的“卡拉OK字幕”预览里设置。'}
            </p>
          </div>
        </Section>

        <Section {...sec('effects')} icon={<Sparkles className="size-4" />} title="特效"
          summary={E.kind === 'none' ? '无' : `${EFFECTS[E.kind].label}${E.ruby ? ' · 注音也有' : ''}`}>
          <EffectsEditor style={style} patch={patch} />
        </Section>
        </SectionTabs>
      </div>
    </div>
  );
}

/** The fields a colour template sets; changing any of them by hand makes the style "自定义". */
function themedLook(s: KaraokeStyle) {
  const { text: t, ruby: r, translation: tr, info: i } = s;
  return JSON.stringify([t.color_unsung, t.color_sung, t.outline_color, t.shadow_color, r.follow_colors, r.color_unsung,
    r.color_sung, r.outline_color, tr.color, tr.outline_color, tr.glow, i.color, i.accent, s.glow, s.effects]);
}

/**
 * Take only what a colour template decides (the fields of `themedLook` and the
 * theme itself) from `themed` into `current`: edits made while the request was
 * on its way (size, layout, timing …) survive.
 */
export function mergeThemeColors(current: KaraokeStyle, themed: KaraokeStyle): KaraokeStyle {
  const next = structuredClone(current);
  const { text: t, ruby: r, translation: tr, info: i } = themed;
  Object.assign(next.text, { color_unsung: t.color_unsung, color_sung: t.color_sung, outline_color: t.outline_color, shadow_color: t.shadow_color });
  Object.assign(next.ruby, { follow_colors: r.follow_colors, color_unsung: r.color_unsung, color_sung: r.color_sung, outline_color: r.outline_color });
  Object.assign(next.translation, { color: tr.color, outline_color: tr.outline_color, glow: tr.glow });
  Object.assign(next.info, { color: i.color, accent: i.accent });
  next.glow = structuredClone(themed.glow);
  next.effects = structuredClone(themed.effects);
  next.theme = themed.theme ? { ...themed.theme } : null;
  return next;
}

type ThemeChoice = { template: 'plain' | 'glow'; color: string; secondary: string };

/** Colour template + one or two theme colours; every colour below is re-derived from them. */
function ThemeBar({ style, onChange }: { style: KaraokeStyle; onChange: (s: KaraokeStyle) => void }) {
  // requests are debounced (dragging the colour picker fires dozens of changes) and numbered:
  // only the answer to the latest choice is applied, on top of the style as it is by then
  const latest = useRef({ style, onChange });
  latest.current = { style, onChange };
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pending, setPending] = useState<ThemeChoice | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const th = pending ?? style.theme ?? null;
  const template = th?.template ?? (style.glow.enabled ? 'glow' : 'plain');
  const color = th?.color ?? style.text.color_sung.toUpperCase();
  const secondary = th?.secondary ?? '';
  const apply = (t: 'plain' | 'glow', c: string, c2: string) => {
    setPending({ template: t, color: c, secondary: c2 });
    const my = ++seq.current;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      timer.current = null;
      try {
        const r = await api.post<ThemePreview>('/api/karaoke/theme', { template: t, color: c, secondary: c2 || null, base: latest.current.style });
        if (my !== seq.current) return;  // a newer choice is on its way
        latest.current.onChange(mergeThemeColors(latest.current.style, r.style));
      } catch (e: any) {
        if (my === seq.current) toast('error', '应用配色失败', e?.message ?? String(e));
      } finally {
        if (my === seq.current) setPending(null);
      }
    }, 200);
  };
  return (
    <div className="space-y-2.5 rounded-xl border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold tracking-wide text-muted">配色模版</span>
        <Segmented<'plain' | 'glow'> size="sm" label="配色模版" value={(th?.template ?? '') as 'plain'} onChange={(v) => apply(v, color, secondary)}
          options={[{ value: 'plain', label: '朴素' }, { value: 'glow', label: '荧光' }]} />
        {!th && <Badge tone="warn">自定义</Badge>}
      </div>
      <ColorRow label="主色" value={color} onChange={(c) => apply(template, c, secondary)} />
      {secondary ? (
        <ColorRow label="辅色" value={secondary} onChange={(c) => apply(template, color, c)}
          extra={<button type="button" aria-label="去掉辅色" onClick={() => apply(template, color, '')}
            className="focus-ring grid size-6 place-items-center rounded-full text-subtle hover:bg-surface-2 hover:text-fg"><X className="size-3.5" /></button>} />
      ) : (
        <button type="button" onClick={() => apply(template, color, '#F5C400')}
          className="focus-ring flex items-center gap-1 rounded text-xs text-accent hover:underline">
          <Plus className="size-3.5" />加一个辅色
        </button>
      )}
      <p className="text-xs text-subtle">
        {th ? '选模版或主题色会重新搭配下面所有颜色和荧光 / 特效；之后在下面单独修改会变为“自定义”。'
          : '当前颜色是单独调整的。选一个模版或主题色会按它重新搭配下面所有颜色。'}
      </p>
    </div>
  );
}

function ColorGroup({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-line p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-xs font-semibold tracking-wide text-muted">{title}</span>
        {action}
      </div>
      <div className="grid gap-2.5 @md:grid-cols-2">{children}</div>
    </div>
  );
}

// ------------------------------------------------------------------ saved styles

function PresetBar({ style, onChange }: { style: KaraokeStyle; onChange: (s: KaraokeStyle) => void }) {
  const saved = useLibrary((s) => s.saved);
  const [naming, setNaming] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => { if (!saved) void run(() => loadSavedStyles(), '读取预设失败'); }, [saved]);
  const current = saved?.find((x) => x.name === style.preset) ?? null;
  const modified = !!current && !sameLook(current.style, style);

  const pick = (id: string) => {
    const s = saved?.find((x) => x.id === id);
    if (!s) return;
    // a preset without singers keeps this project's (the parts already assigned keep their colours)
    const next = structuredClone(s.style);
    onChange({ ...next, output: style.output, singers: next.singers?.members.length ? next.singers : style.singers });
    setConfirmDelete(false);
  };
  const [savingAs, setSavingAs] = useState(false);
  const saveAs = (name: string) => {
    if (savingAs || !name.trim()) return;  // Enter and the button save once
    setSavingAs(true);
    return run(async () => {
      try {
        const s = await saveStyle(name.trim(), style);
        onChange({ ...style, preset: s.name });
        setNaming(null);
        toast('ok', `已保存预设「${s.name}」`);
      } finally {
        setSavingAs(false);
      }
    }, '保存预设失败');
  };
  const overwrite = () => current && run(async () => {
    await saveStyle(current.name, style, current.id);
    toast('ok', `已更新预设「${current.name}」`);
  }, '保存预设失败');
  const remove = () => current && run(async () => {
    await deleteStyle(current.id);
    onChange({ ...style, preset: '' });
    setConfirmDelete(false);
    toast('ok', `已删除预设「${current.name}」`);
  }, '删除预设失败');

  return (
    <div className="rounded-xl bg-surface-2/70 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-muted">预设</span>
        <Select aria-label="预设" className="h-8 min-w-36 flex-1 text-[13px]" value={current?.id ?? ''} onChange={(e) => pick(e.target.value)}>
          {!current && <option value="">（未保存的样式）</option>}
          {(saved ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}{s.builtin ? '（内置）' : ''}</option>)}
        </Select>
        {modified && <Badge tone="warn">已修改</Badge>}
        {current && !current.builtin && modified && (
          <Button size="xs" variant="primary" icon={<Save className="size-3.5" />} onClick={overwrite}>保存</Button>
        )}
        <Button size="xs" variant="secondary" icon={<Save className="size-3.5" />} onClick={() => setNaming(naming === null ? '' : null)}>另存为</Button>
        {current && !current.builtin && (confirmDelete ? (
          <Button size="xs" variant="danger" icon={<Trash2 className="size-3.5" />} onClick={remove}>确认删除</Button>
        ) : (
          <Button size="xs" variant="ghost" aria-label="删除预设" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmDelete(true)} />
        ))}
      </div>
      {naming !== null && (
        <div className="mt-2 flex items-center gap-2">
          <Input autoFocus aria-label="预设名称" className="h-8 flex-1 text-[13px]" placeholder="给这套样式起个名字" value={naming}
            onChange={(e) => setNaming(e.target.value)}
            onKeyDown={(e) => { if (isEnter(e) && naming.trim()) void saveAs(naming); if (isEscape(e)) setNaming(null); }} />
          <Button size="xs" variant="primary" icon={<Check className="size-3.5" />} disabled={!naming.trim()} loading={savingAs} onClick={() => void saveAs(naming)}>保存</Button>
          <Button size="xs" variant="ghost" aria-label="取消" icon={<X className="size-3.5" />} onClick={() => setNaming(null)} />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ song info (title card)

const FIELD_ORDER: SongInfoField[] = ['title', 'artist', 'album', 'lyricist', 'composer', 'arranger'];
const FIELD_LABEL: Record<SongInfoField, string> = { title: '歌名', artist: '歌手', album: '专辑', lyricist: '作词', composer: '作曲', arranger: '编曲' };
const CREDITS: SongInfoField[] = ['lyricist', 'composer', 'arranger'];

/** The card's lines from the chosen fields (the same rule as the server). */
export function autoInfoText(fields: SongInfoField[], data: SongInfo['fields']) {
  return FIELD_ORDER.filter((f) => fields.includes(f) && data[f])
    .map((f) => (CREDITS.includes(f) ? `${FIELD_LABEL[f]}：${data[f]}` : data[f]!)).join('\n');
}

function InfoEditor({ style, patch, songInfo }: { style: KaraokeStyle; patch: Patch; songInfo?: SongInfoEditing }) {
  const I = style.info;
  const data = songInfo?.data ?? null;
  const custom = data?.text ?? null;
  const toggleField = (f: SongInfoField, on: boolean) => patch((s) => {
    const set = new Set(s.info.fields);
    if (on) set.add(f); else set.delete(f);
    s.info.fields = FIELD_ORDER.filter((x) => set.has(x));
  });
  return (
    <>
      <Switch checked={I.enabled} onChange={(v) => patch((s) => { s.info.enabled = v; })} label="显示歌曲信息（开头，以及结尾）" />
      <div className={cn('space-y-4', !I.enabled && 'pointer-events-none opacity-45')}>
        <Row label="位置">
          <Segmented value={I.position} onChange={(v) => patch((s) => { s.info.position = v; })}
            options={[{ value: 'top-left', label: '左上角' }, { value: 'top-right', label: '右上角' }]} />
        </Row>
        <Row label="显示哪些行" hint={custom !== null ? '正在使用下面的自定义文字，勾选暂不生效'
          : data ? '没有数据的行不会显示' : '每首歌有数据的行才会显示（来自音乐平台和歌词里的“作词：…”等行）'}>
          <div className={cn('grid gap-1.5 @xs:grid-cols-2', custom !== null && 'opacity-45')}>
            {FIELD_ORDER.map((f) => {
              const value = data?.fields[f];
              return (
                <label key={f} className="flex min-w-0 items-center gap-2 rounded-lg px-1.5 py-1 text-[13px] hover:bg-surface-2">
                  <input type="checkbox" className="size-4 shrink-0 accent-[var(--color-accent)]" aria-label={`显示${FIELD_LABEL[f]}`}
                    checked={I.fields.includes(f)} disabled={custom !== null} onChange={(e) => toggleField(f, e.target.checked)} />
                  <span className="shrink-0">{FIELD_LABEL[f]}</span>
                  {data && <span className={cn('min-w-0 truncate text-xs', value ? 'text-muted' : 'text-subtle')}>{value || '无'}</span>}
                </label>
              );
            })}
          </div>
        </Row>
        {songInfo && data && <InfoText auto={autoInfoText(I.fields, data.fields)} custom={custom} onText={songInfo.onText} />}
        <Row label="标题字号" hint="其余各行约为标题的一半多一点"><Num name="标题字号" value={I.size} min={20} max={120} onChange={(v) => patch((s) => { s.info.size = v; })} /></Row>
        <Row label="与画面边缘的距离"><Num name="信息边距" value={I.margin} max={200} onChange={(v) => patch((s) => { s.info.margin = v; })} /></Row>
        <Row label="出现时间" hint="从音频开头算起"><Num name="信息出现时间" unit="ms" value={I.start_ms} max={10000} step={100} onChange={(v) => patch((s) => { s.info.start_ms = v; })} /></Row>
        <Row label="显示多久" hint="顶部要出现歌词或翻译时会提前淡出（至少显示 2 秒）">
          <Num name="信息显示时长" unit="ms" value={I.duration_ms} min={1000} max={20000} step={500} onChange={(v) => patch((s) => { s.info.duration_ms = v; })} />
        </Row>
        <Switch checked={I.outro ?? true} onChange={(v) => patch((s) => { s.info.outro = v; })} label="结尾也显示（同样的内容和样式，一直显示到歌曲结束）" />
        {(I.outro ?? true) && (
          <Row label="结尾显示多久" hint="从歌曲结束往前算；顶部最后一句歌词或翻译消失后才出现（至少显示 2 秒）">
            <Num name="结尾信息显示时长" unit="ms" value={I.outro_duration_ms ?? 7000} min={1000} max={20000} step={500}
              onChange={(v) => patch((s) => { s.info.outro_duration_ms = v; })} />
          </Row>
        )}
      </div>
    </>
  );
}

function InfoText({ auto, custom, onText }: { auto: string; custom: string | null; onText: (t: string | null) => void }) {
  const [draft, setDraft] = useState<string | null>(null); // what is being typed, saved shortly after
  // text still waiting for its save when the panel goes away (another step, another mode) is saved then
  const unsaved = useRef<{ text: string; onText: typeof onText } | null>(null);
  useEffect(() => {
    if (draft === null || draft === (custom ?? auto)) {
      unsaved.current = null;
      return;
    }
    unsaved.current = { text: draft, onText };
    const t = setTimeout(() => {
      unsaved.current = null;
      onText(draft);
    }, 500);
    return () => clearTimeout(t);
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => {
    const u = unsaved.current;
    if (u) u.onText(u.text);
  }, []);
  return (
    <Row label={<span className="flex items-center gap-2">显示的文字{custom !== null && <Badge tone="accent">自定义</Badge>}</span>}
      hint="每行一条，第一行是标题（大字）。可以改成任何内容，例如「赤城みりあ（CV：黒沢ともよ）」。">
      <Textarea aria-label="歌曲信息文字" rows={4} className="min-h-24 font-sans" value={draft ?? custom ?? auto}
        onChange={(e) => setDraft(e.target.value)} />
      {custom !== null && (
        <Button size="xs" variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={() => { setDraft(null); onText(null); }}>
          恢复为按勾选自动生成
        </Button>
      )}
    </Row>
  );
}

// ------------------------------------------------------------------ effects

export const EFFECTS: Record<EffectKind, { label: string; hint: string }> = {
  none: { label: '无', hint: '' },
  pulse: { label: '光晕扩散', hint: '唱到的字向外扩散出一圈光晕' },
  ring: { label: '光环爆开', hint: '唱到的字的轮廓向外爆开一圈柔和的光环' },
  shine: { label: '闪光扫过', hint: '一道亮光扫过唱到的字' },
  sparkle: { label: '星光迸发', hint: '唱到的字周围迸出小星星' },
  petals: { label: '花瓣飘落', hint: '唱到的字上飘落几片樱花花瓣' },
  hearts: { label: '爱心飘升', hint: '唱到的字上弹出小爱心并飘走' },
  ball: { label: '跳跃小球', hint: '经典卡拉OK：小球跟着演唱在字与字之间跳动（只在歌词上，不含注音）' },
};
const PARTICLES: EffectKind[] = ['sparkle', 'petals', 'hearts', 'ball'];

function EffectsEditor({ style, patch }: { style: KaraokeStyle; patch: Patch }) {
  const { glow: G, text: T, effects: E } = style;
  const auto = G.enabled ? G.color_sung : T.color_sung;
  return (
    <>
      <Row label="唱到每个字时" hint={E.kind === 'none' ? '特效跟着演唱逐字出现在歌词周围，预览和导出的视频完全一致' : EFFECTS[E.kind].hint}>
        <Segmented className="flex-wrap" value={E.kind} onChange={(v) => patch((s) => { s.effects.kind = v; })}
          options={(Object.keys(EFFECTS) as EffectKind[]).map((k) => ({ value: k, label: EFFECTS[k].label }))} />
      </Row>
      {E.kind !== 'none' && (
        <>
          {PARTICLES.includes(E.kind) && E.kind !== 'ball' && (
            <Row label="数量"><Num name="特效数量" unit="%" value={E.amount} min={20} max={200} step={10} onChange={(v) => patch((s) => { s.effects.amount = v; })} /></Row>
          )}
          <Row label={{ pulse: '扩散范围', ring: '光环大小', shine: '光带宽度' }[E.kind as string] ?? '大小'}>
            <Num name="特效大小" unit="%" value={E.size} min={40} max={250} step={10} onChange={(v) => patch((s) => { s.effects.size = v; })} />
          </Row>
          {E.kind !== 'shine' && (
            <div className="space-y-2">
              <Switch checked={!!E.color} onChange={(v) => patch((s) => { s.effects.color = v ? auto : ''; })} label="自定义颜色" />
              {E.color ? <ColorField label="特效颜色" value={E.color} onChange={(v) => patch((s) => { s.effects.color = v; })} />
                : <p className="text-xs text-subtle">使用{G.enabled ? '荧光边缘（唱过后）' : '歌词已唱'}的颜色 <Dot c={auto} /></p>}
            </div>
          )}
          {PARTICLES.includes(E.kind) && (
            <div className="space-y-1">
              <Switch checked={E.behind} onChange={(v) => patch((s) => { s.effects.behind = v; })} label="放在字幕后面（不遮挡文字）" />
              <p className="text-xs text-subtle">{E.behind ? '粒子画在歌词、注音和翻译的下层，经过文字时被文字挡住' : '粒子画在最上层，可能盖住正在唱的字'}</p>
            </div>
          )}
          {E.kind !== 'ball' && <Switch checked={E.ruby} onChange={(v) => patch((s) => { s.effects.ruby = v; })} label="注音唱到时也触发" />}
        </>
      )}
    </>
  );
}
