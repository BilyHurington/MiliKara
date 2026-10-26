// The singers of the karaoke style: colour and name of each, and how parts sung together look.

import { BookmarkPlus, ChevronDown, Keyboard, Plus, RotateCcw, Trash2, Users, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/format';
import { isEnter, isEscape } from '@/lib/keys';
import {
  freeKey, freePair, idsKey, keyLabel, keyOf, mixBackground, parseCombo, singerLabel, withKey, withNewSinger, type KeyOwner,
} from '@/lib/singers';
import type { KaraokeSinger, KaraokeSingers, SingerColors, SingerCombo, SingerDirection, SingerMix, SingerPreset } from '@/lib/types';
import { run, toast } from '@/store/app';
import { deleteSingerPreset, loadSingerPresets, saveSingerPreset } from '@/store/singers';
import { Button, Card, CardBody, CardHeader, ConfirmButton, Input, NumberInput, Segmented, Select, Switch, Tip } from '@/components/ui';
import { ColorField } from '@/components/karaoke/StylePanel';
import { PictureField, pictureUrl } from '@/components/karaoke/PictureField';
import { HelpButton } from '@/components/guide/Help';

type Detail = 'color_unsung' | 'color_sung' | 'outline_color' | 'glow_unsung' | 'glow_sung';
const DETAILS: [Detail, keyof SingerColors, string][] = [
  ['color_unsung', 'unsung', '未唱'], ['color_sung', 'sung', '已唱（扫光）'], ['outline_color', 'outline', '描边'],
  ['glow_unsung', 'glow_unsung', '荧光 · 未唱时'], ['glow_sung', 'glow_sung', '荧光 · 唱过后'],
];

export function SingerList({ singers, onChange, onRemove, onUsePreset, usage, glow }: {
  singers: KaraokeSingers;
  onChange: (next: KaraokeSingers) => void;
  onRemove: (n: number) => void;
  /** use a saved set of singers (the server matches parts already assigned by name) */
  onUsePreset: (id: string, name: string) => void;
  /** lines each singer (1-based) sings */
  usage: (n: number) => number;
  /** the style has the glow edge on (its colours matter) */
  glow: boolean;
}) {
  const { members } = singers;
  const [open, setOpen] = useState<number | null>(null);
  const [resolved, setResolved] = useState<SingerColors[]>([]);
  // every colour with the derived ones filled in (shown in the details)
  const key = JSON.stringify(members);
  useEffect(() => {
    if (!members.length) return;
    let stop = false;
    const t = setTimeout(() => {
      void api.post<SingerColors[]>('/api/karaoke/singer-colors', { members })
        .then((r) => { if (!stop) setResolved(r); }).catch(() => undefined);
    }, 250);
    return () => { stop = true; clearTimeout(t); };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const edit = (i: number, patch: Partial<KaraokeSinger>) =>
    onChange({ ...singers, members: members.map((m, j) => (j === i ? { ...m, ...patch } : m)) });
  const add = () => onChange(withNewSinger(singers));
  const combos = singers.combos ?? [];
  const noKey = freeKey(singers) === null;
  const addCombo = () => {
    const ids = freePair(singers);
    if (!ids) {
      toast('info', '每两位演唱者都已经有组合了', '可以把某个组合改成三个人，如 1+2+3');
      return;
    }
    onChange({ ...singers, combos: [...combos, { key: freeKey(singers) ?? '', singers: ids }] });
  };
  const ownerName = (o: KeyOwner) => ('singer' in o ? singerLabel(members, o.singer) : `组合 ${combos[o.combo]?.singers.join('+')}`);
  const setKey = (o: KeyOwner, k: string) => {
    const { next, swapped } = withKey(singers, o, k);
    if (next === singers) return;
    onChange(next);
    if (swapped) toast('info', `和${ownerName(swapped)}交换了快捷键`, `${ownerName(swapped)}现在是 ${keyLabel(('singer' in o ? members[o.singer - 1].key : combos[o.combo].key)) || '无快捷键'}`);
  };
  const sample = members.slice(0, 3).map((m) => m.color);

  return (
    <Card>
      <CardHeader icon={<Users className="size-4" />} title="演唱者" help={<HelpButton k="singers.list" />}
        description="每人选一个主色，未唱、描边、荧光的颜色会自动搭配。右边的方块是快捷键，点一下再按新的键可以修改。" />
      <CardBody className="space-y-3">
        <PresetBar singers={singers} onUse={onUsePreset} />
        {members.length === 0 && <p className="text-[13px] text-muted">还没有演唱者。添加后，在左边选中歌词，按快捷键指定是谁唱的；也可以载入保存过的预设。</p>}
        <ol className="space-y-2">
          {members.map((m, i) => (
            <li key={i} className="rounded-xl border border-line p-2.5">
              <div className="flex items-center gap-2">
                <span className="grid size-6 shrink-0 place-items-center rounded-md text-xs font-bold text-white shadow-sm" style={{ background: m.color }}>{i + 1}</span>
                <label className="relative size-7 shrink-0 cursor-pointer overflow-hidden rounded-md ring-1 ring-line-strong" style={{ background: m.color }}>
                  <input type="color" aria-label={`${singerLabel(members, i + 1)}的颜色`} value={m.color}
                    onChange={(e) => edit(i, { color: e.target.value.toUpperCase() })} className="absolute inset-0 cursor-pointer opacity-0" />
                </label>
                {m.picture && <img src={pictureUrl(m.picture)} alt="" className="size-7 shrink-0 rounded-md object-contain" />}
                <Input className="h-8 min-w-0 flex-1" value={m.name} maxLength={40} placeholder={`演唱者 ${i + 1}`}
                  aria-label={`演唱者 ${i + 1} 的名字`} onChange={(e) => edit(i, { name: e.target.value })} />
                <Tip content="简称：写在每句旁边（如 A、春香），分色列表里写在名字后面；不填就用名字">
                  <Input className="h-8 w-16 shrink-0" value={m.label ?? ''} maxLength={12} placeholder="简称"
                    aria-label={`${singerLabel(members, i + 1)}的简称`} onChange={(e) => edit(i, { label: e.target.value })} />
                </Tip>
                <span className="shrink-0 text-xs text-subtle" title="唱到的行数">{usage(i + 1)} 行</span>
                <KeyButton value={m.key} owner={singerLabel(members, i + 1)} onChange={(k) => setKey({ singer: i + 1 }, k)} />
                <button type="button" className="focus-ring rounded-md p-1 text-muted hover:bg-surface-2" aria-expanded={open === i}
                  aria-label={`${singerLabel(members, i + 1)}的详细颜色`} onClick={() => setOpen(open === i ? null : i)}>
                  <ChevronDown className={cn('size-4 transition', open === i && 'rotate-180')} />
                </button>
              </div>
              {open === i && (
                <div className="mt-3 space-y-2 border-t border-line pt-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[13px]">头像<span className="ml-1.5 text-xs text-subtle">标注和分色列表里显示</span></span>
                    <PictureField label={`${singerLabel(members, i + 1)}的头像`} value={m.picture ?? ''} onChange={(v) => edit(i, { picture: v })} />
                  </div>
                  {DETAILS.filter(([f]) => glow || !f.startsWith('glow')).map(([field, role, label]) => (
                    <ColorField key={field} label={`${label}${m[field] ? '' : '（自动）'}`} value={m[field] || resolved[i]?.[role] || m.color}
                      onChange={(v) => edit(i, { [field]: v } as Partial<KaraokeSinger>)} />
                  ))}
                  <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                    <Button size="xs" variant="ghost" icon={<RotateCcw className="size-3.5" />}
                      disabled={DETAILS.every(([f]) => !m[f])}
                      onClick={() => edit(i, { color_unsung: '', color_sung: '', outline_color: '', glow_unsung: '', glow_sung: '' })}>
                      全部自动
                    </Button>
                    <ConfirmButton size="xs" variant="ghost" icon={<Trash2 className="size-3.5" />}
                      question={usage(i + 1) ? `${usage(i + 1)} 行里这位演唱者唱的部分会回到默认配色，后面的编号前移` : '删除这位演唱者？'}
                      confirmLabel="删除" onConfirm={() => onRemove(i + 1)}>
                      删除
                    </ConfirmButton>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ol>
        <Button size="sm" variant="outline" icon={<Plus className="size-4" />} onClick={add}
          title={noKey ? '快捷键已经用完：新的演唱者没有快捷键，可以点按钮指定，或把别的快捷键让给它' : undefined}>
          添加演唱者
        </Button>

        {members.length >= 2 && (
          <div className="space-y-2 border-t border-line pt-3">
            <div className="flex items-center gap-1.5 text-[13px] font-medium"><Keyboard className="size-4 text-muted" />组合快捷键</div>
            <p className="text-xs text-subtle">常一起唱的几个人可以存到一个键上，例如把 1+2 存到 {keyLabel(freeKey(singers) ?? '3')}：以后按一下就指定为两人一起唱。组合可以起个名字（如 全员），标注和分色列表里就写这个名字。</p>
            {combos.map((c, ci) => (
              <ComboRow key={ci} combo={c} count={members.length} colors={c.singers.map((n) => members[n - 1]?.color).filter(Boolean) as string[]}
                defaults={singers}
                keyButton={<KeyButton value={c.key} owner={`组合 ${c.singers.join('+')}`} onChange={(k) => setKey({ combo: ci }, k)} />}
                onLook={(look) => onChange({ ...singers, combos: combos.map((x, j) => (j === ci ? { ...x, ...look } : x)) })}
                onLabel={(label) => onChange({ ...singers, combos: combos.map((x, j) => (j === ci ? { ...x, label } : x)) })}
                onChange={(ids) => {
                  if (combos.some((x, j) => j !== ci && idsKey(x.singers) === idsKey(ids))) {
                    toast('info', `组合 ${ids.join('+')} 已经有了`, '同一组演唱者（同样的顺序）只能有一个组合');
                    return false;
                  }
                  onChange({ ...singers, combos: combos.map((x, j) => (j === ci ? { ...x, singers: ids } : x)) });
                  return true;
                }}
                onRemove={() => onChange({ ...singers, combos: combos.filter((_, j) => j !== ci) })} />
            ))}
            <Button size="xs" variant="outline" icon={<Plus className="size-3.5" />} onClick={addCombo}>添加组合</Button>
          </div>
        )}

        <div className="space-y-2.5 border-t border-line pt-3">
          <div className="text-[13px] font-medium">几个人一起唱的部分（默认）</div>
          <div className="flex flex-wrap items-center gap-2">
            <Segmented size="sm" label="一起唱的效果" value={singers.mix} onChange={(v) => onChange({ ...singers, mix: v })}
              options={[{ value: 'split', label: '分色' }, { value: 'gradient', label: '渐变' }]} />
            <Segmented size="sm" label="一起唱的方向" value={singers.direction} onChange={(v) => onChange({ ...singers, direction: v })}
              options={[{ value: 'vertical', label: '上下' }, { value: 'horizontal', label: '左右' }]} />
            {sample.length >= 2 && (
              <Tip content="示意：实际效果看预览">
                <span aria-hidden className="ml-auto text-2xl leading-none font-black"
                  style={{ backgroundImage: mixBackground(sample, singers.mix, singers.direction), WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>
                  {singers.direction === 'vertical' ? '歌' : '一起唱'}
                </span>
              </Tip>
            )}
          </div>
          <p className="text-xs text-subtle">
            {singers.direction === 'vertical'
              ? `每个字${singers.mix === 'split' ? '上下分成几段' : '从上到下渐变'}：第一个人在上，依次往下（如 1+2：上半 1 号、下半 2 号）。`
              : `一起唱的一整段${singers.mix === 'split' ? '从左到右分成几段' : '从左到右渐变'}：第一个人在左，依次往右。`}
            组合可以有自己的效果（在上面的组合里选）。翻译的文字不变色，荧光跟着这一行的演唱者（几个人时从左到右渐变）。
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted">注音</span>
            <Segmented size="sm" label="一起唱时注音的颜色" value={singers.ruby ?? 'auto'} onChange={(v) => onChange({ ...singers, ruby: v })}
              options={[{ value: 'auto', label: '自动' }, { value: 'split', label: '跟着分色' }, { value: 'first', label: '第一位的颜色' }]} />
          </div>
          <p className="text-xs text-subtle">
            {{
              auto: '上下分色时注音字太小，整个用第一位（最上面那个人）的颜色；左右分色时跟着所在的位置。',
              split: '注音和歌词一样分色：上下分色时注音也分成上下几段，左右时跟着所在的位置。',
              first: '注音整个用第一位演唱者的颜色，不分色。',
            }[singers.ruby ?? 'auto']}
          </p>
        </div>

        {members.length >= 1 && <LegendAndLabels singers={singers} onChange={onChange} />}
      </CardBody>
    </Card>
  );
}

/** The legend after the title card and who sings each line, beside it. */
function LegendAndLabels({ singers, onChange }: { singers: KaraokeSingers; onChange: (next: KaraokeSingers) => void }) {
  const labels = singers.labels ?? 'off';
  const legend = singers.legend ?? true;
  return (
    <div className="space-y-2.5 border-t border-line pt-3">
      <div className="text-[13px] font-medium">分色列表和演唱者标注</div>
      <div className="flex flex-wrap items-center gap-2">
        <Switch checked={legend} onChange={(v) => onChange({ ...singers, legend: v })} label="开头显示分色列表" />
        {legend && (
          <span className="flex items-center gap-1.5 text-xs text-muted">
            显示
            <NumberInput className="w-20" value={(singers.legend_ms ?? 5000) / 1000} step={0.5} min={1} max={30} suffix="秒"
              onCommit={(v) => { if (v !== null) onChange({ ...singers, legend_ms: Math.round(Math.min(30, Math.max(1, v)) * 1000) }); }} />
          </span>
        )}
      </div>
      <p className="text-xs text-subtle">歌曲信息之后，在同一个角落列出每位演唱者的颜色（有名字的组合也列出）。歌里至少有两个人唱时才显示。</p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">每句标注演唱者</span>
        <Segmented size="sm" label="每句标注演唱者" value={labels} onChange={(v) => onChange({ ...singers, labels: v })}
          options={[{ value: 'off', label: '不显示' }, { value: 'start', label: '句首' }, { value: 'end', label: '句尾' }]} />
      </div>
      {singers.members.some((m) => m.picture) && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">有头像的演唱者</span>
          <Segmented size="sm" label="有头像的演唱者标注" value={singers.label_content ?? 'both'}
            onChange={(v) => onChange({ ...singers, label_content: v })}
            options={[{ value: 'both', label: '头像和名字' }, { value: 'picture', label: '只用头像' }, { value: 'text', label: '只用名字' }]} />
        </div>
      )}
      {labels !== 'off' && (
        <p className="text-xs text-subtle">
          写简称（没有就写名字），用各自的颜色；简称里可以有彩色 emoji。两人一起唱分上下两小行，和歌词一样高；三人以上平均分到两行；有名字的组合只写组合名。头像在每人的详细设置里上传。
        </p>
      )}
    </div>
  );
}

/** One saved combination: its key and who sings together (edited as "1+2"). */
const LOOKS: { value: string; label: string }[] = [
  { value: 'split-vertical', label: '上下分色' }, { value: 'split-horizontal', label: '左右分色' },
  { value: 'gradient-vertical', label: '上下渐变' }, { value: 'gradient-horizontal', label: '左右渐变' },
];
const lookName = (mix: SingerMix, direction: SingerDirection) => LOOKS.find((l) => l.value === `${mix}-${direction}`)!.label;

function ComboRow({ combo, count, colors, defaults, keyButton, onChange, onLook, onLabel, onRemove }: {
  combo: SingerCombo; count: number; colors: string[]; defaults: KaraokeSingers; keyButton: React.ReactNode;
  /** false: refused (the text goes back) */
  onChange: (ids: number[]) => boolean;
  onLook: (look: { mix: SingerMix | null; direction: SingerDirection | null }) => void;
  onLabel: (label: string) => void;
  onRemove: () => void;
}) {
  const [text, setText] = useState(combo.singers.join('+'));
  useEffect(() => { setText(combo.singers.join('+')); }, [idsKey(combo.singers)]); // eslint-disable-line react-hooks/exhaustive-deps
  const commit = () => {
    const ids = parseCombo(text, count);
    if (!(ids.length >= 2 && idsKey(ids) !== idsKey(combo.singers) && onChange(ids))) setText(combo.singers.join('+'));
  };
  const own = combo.mix || combo.direction ? `${combo.mix || defaults.mix}-${combo.direction || defaults.direction}` : '';
  const mix = combo.mix || defaults.mix;
  const direction = combo.direction || defaults.direction;
  return (
    <div className="flex items-center gap-2">
      <span className="size-6 shrink-0 rounded-md shadow-sm" aria-hidden
        style={{ background: colors.length ? mixBackground(colors, mix, direction) : 'var(--color-line)' }} />
      <Input className="h-8 w-20" value={text} aria-label={`组合 ${combo.singers.join('+')} 的演唱者`} placeholder="如 1+2"
        onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }} />
      <Input className="h-8 w-16 shrink-0" value={combo.label ?? ''} maxLength={12} placeholder="名字"
        aria-label={`组合 ${combo.singers.join('+')} 的名字`} onChange={(e) => onLabel(e.target.value)} />
      <Select className="h-8 min-w-0 flex-1 text-xs" aria-label={`组合 ${combo.singers.join('+')} 的效果`} value={own}
        onChange={(e) => {
          const [m, d] = e.target.value ? e.target.value.split('-') : [null, null];
          onLook({ mix: m as SingerMix | null, direction: d as SingerDirection | null });
        }}>
        <option value="">默认（{lookName(defaults.mix, defaults.direction)}）</option>
        {LOOKS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
      </Select>
      <span>{keyButton}</span>
      <button type="button" className="focus-ring rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg"
        aria-label={`删除组合 ${combo.singers.join('+')}`} onClick={onRemove}>
        <X className="size-4" />
      </button>
    </div>
  );
}

/** A singer's / combination's key: click, then press the new key (Backspace: none, Esc: keep). */
function KeyButton({ value, owner, onChange }: { value: string; owner: string; onChange: (k: string) => void }) {
  const [listening, setListening] = useState(false);
  return (
    <Tip content={listening ? '按新的键：1–9、A–Z（L、P 除外）· Backspace 清空 · Esc 取消' : '快捷键：点一下，再按新的键'} keep>
      <button type="button" aria-label={`${owner}的快捷键：${value ? keyLabel(value) : '无'}，点击修改`} aria-pressed={listening}
        onClick={() => setListening((v) => !v)} onBlur={() => setListening(false)}
        onKeyDown={(e) => {
          if (!listening || e.metaKey || e.ctrlKey || e.altKey || e.key === 'Tab' || e.key === 'Shift') return;
          // the key is this button's: not the page's shortcut, nor a click
          e.preventDefault();
          e.stopPropagation();
          if (isEscape(e)) setListening(false);
          else if (e.key === 'Backspace' || e.key === 'Delete') { onChange(''); setListening(false); }
          else if (keyOf(e.key)) { onChange(keyOf(e.key)); setListening(false); }
          else if (!isEnter(e)) toast('info', `「${e.key === ' ' ? '空格' : e.key}」不能用作快捷键`, '可以用 1–9 和 A–Z（L、P 已用于循环和试听）');
        }}
        className={cn('focus-ring grid h-6 min-w-6 shrink-0 place-items-center rounded-md border px-1 font-mono text-[11px] font-semibold transition',
          listening ? 'animate-pulse border-accent bg-accent-soft text-accent' : value ? 'border-line-strong bg-surface-2 text-fg hover:border-accent'
            : 'border-dashed border-line-strong text-subtle hover:border-accent')}>
        {listening ? '…' : value ? keyLabel(value) : '—'}
      </button>
    </Tip>
  );
}

/** Saved sets of singers (names, colours, keys, combinations) for songs with the same singers. */
function PresetBar({ singers, onUse }: { singers: KaraokeSingers; onUse: (id: string, name: string) => void }) {
  const [presets, setPresets] = useState<SingerPreset[] | null>(null);
  const [picked, setPicked] = useState('');
  const [naming, setNaming] = useState<string | null>(null);
  const reload = () => run(async () => setPresets(await loadSingerPresets()), '读取演唱者预设失败');
  useEffect(() => { void reload(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const chosen = presets?.find((p) => p.id === picked);
  const save = (name: string) => run(async () => {
    const p = await saveSingerPreset(name.trim(), singers);
    setNaming(null);
    await reload();
    setPicked(p.id);
    toast('ok', `已保存演唱者预设「${p.name}」`, `${p.singers.members.length} 位演唱者${p.singers.combos?.length ? `、${p.singers.combos.length} 个组合` : ''}，其他歌曲可以直接载入`);
  }, '保存预设失败');
  const suggested = chosen?.name ?? singers.members.map((m) => m.name.trim()).filter(Boolean).join('、');

  if (naming !== null) {
    const exists = presets?.some((p) => p.name === naming.trim());
    return (
      <div className="space-y-1 rounded-xl bg-surface-2/60 p-2">
        <div className="flex items-center gap-1.5">
          <Input autoFocus className="h-8 min-w-0 flex-1" value={naming} placeholder="预设名称，如 团体名" aria-label="演唱者预设名称"
            onChange={(e) => setNaming(e.target.value)}
            onKeyDown={(e) => { if (isEnter(e) && naming.trim()) void save(naming); if (isEscape(e)) setNaming(null); }} />
          <Button size="xs" variant="primary" disabled={!naming.trim()} onClick={() => void save(naming)}>{exists ? '覆盖' : '保存'}</Button>
          <Button size="xs" variant="ghost" onClick={() => setNaming(null)}>取消</Button>
        </div>
        <p className="px-1 text-[11px] text-subtle">{exists ? '同名的预设会被覆盖。' : ''}保存演唱者的名字、颜色、快捷键、组合和一起唱的效果。</p>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Select aria-label="演唱者预设" className="h-8 min-w-0 flex-1" value={picked} onChange={(e) => setPicked(e.target.value)}>
        <option value="">{presets?.length ? '演唱者预设…' : '还没有演唱者预设'}</option>
        {(presets ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}（{p.singers.members.length} 位）</option>)}
      </Select>
      {chosen && (singers.members.length ? (
        <ConfirmButton size="xs" variant="secondary" question="替换现在的演唱者？已指定的部分按名字对应" confirmLabel="载入"
          onConfirm={() => onUse(chosen.id, chosen.name)}>载入</ConfirmButton>
      ) : <Button size="xs" variant="secondary" onClick={() => onUse(chosen.id, chosen.name)}>载入</Button>)}
      {chosen && (
        <ConfirmButton size="xs" variant="ghost" icon={<Trash2 className="size-3.5" />} question={`删除预设「${chosen.name}」？`} confirmLabel="删除"
          aria-label={`删除预设 ${chosen.name}`}
          onConfirm={() => void run(async () => { await deleteSingerPreset(chosen.id); setPicked(''); await reload(); }, '删除预设失败')} />
      )}
      <Button size="xs" variant="ghost" icon={<BookmarkPlus className="size-3.5" />} disabled={!singers.members.length}
        onClick={() => setNaming(suggested)}>存为预设</Button>
    </div>
  );
}
