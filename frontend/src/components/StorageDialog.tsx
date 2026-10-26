// Disk space: how much each project, the cache and leftovers take, and cleaning them up.
// Opened from the simple mode's settings and the detailed mode's project list.

import { ChevronDown, ChevronRight, HardDrive, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { cn, fmtBytes, fmtRelative } from '@/lib/format';
import type { StorageInfo, StorageProject } from '@/lib/types';
import { deleteProject, loadProjects, run, toast } from '@/store/app';
import { loadTasks } from '@/store/simple';
import { Badge, Button, ConfirmButton, Dialog, IconButton, Spinner } from '@/components/ui';
import { HelpButton } from '@/components/guide/Help';

const PART_LABEL: Record<keyof StorageProject['parts'], string> = {
  media: '视频和原曲', stems: '人声分离', background: '背景', exports: '导出的文件', unused: '残留文件', other: '其他',
};
const PART_COLOR: Record<keyof StorageProject['parts'], string> = {
  media: 'bg-accent', stems: 'bg-ok', background: 'bg-info', exports: 'bg-warn', unused: 'bg-danger', other: 'bg-subtle',
};
const LEFTOVER_LABEL: Record<string, string> = {
  upload: '已导入任务的上传副本', asset: '被替换的旧音频', folder: '没删干净的项目', deleted: '没删干净的项目',
};

export async function loadStorage() {
  return api.get<StorageInfo>('/api/storage');
}

function freedToast(r: StorageInfo) {
  toast('ok', r.freed ? `已释放 ${fmtBytes(r.freed)}` : '已清理');
}

export function StorageDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [info, setInfo] = useState<StorageInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);

  const reload = useCallback(() => run(async () => setInfo(await loadStorage()), '读取占用空间失败'), []);
  useEffect(() => { if (open) void reload(); }, [open, reload]);

  // one action at a time; the answer carries the new sizes
  const act = (key: string, fn: () => Promise<StorageInfo | void>) => run(async () => {
    setBusy(key);
    try {
      const r = await fn();
      if (r) { setInfo(r); freedToast(r); } else await reload();
    } finally {
      setBusy(null);
    }
  }, '清理失败');

  const clean = (body: { cache?: boolean; leftovers?: boolean }) =>
    act(body.cache ? 'cache' : 'leftovers', () => api.post<StorageInfo>('/api/storage/clean', body));
  const cleanProject = (pid: string, body: { exports?: boolean | string[]; stems?: boolean }) =>
    act(`${pid}:${JSON.stringify(body)}`, () => api.post<StorageInfo>(`/api/projects/${pid}/storage/clean`, body));
  const removeProjects = (ids: string[]) => act('delete', async () => {
    const before = info?.projects.filter((p) => ids.includes(p.id)).reduce((s, p) => s + p.size, 0) ?? 0;
    const failed: string[] = [];
    for (const id of ids) {
      try { await deleteProject(id); } catch (e: any) { failed.push(`${info?.projects.find((p) => p.id === id)?.name ?? id}：${e?.message ?? e}`); }
    }
    setChecked(new Set());
    void loadTasks().catch(() => undefined);  // tasks of a deleted project lose their links
    const r = await loadStorage();
    setInfo(r);
    if (failed.length) toast('error', '有项目没有删除', failed.join('\n'));
    else toast('ok', `已删除 ${ids.length} 个项目，释放 ${fmtBytes(before)}`);
  });

  const toggle = (id: string) => setChecked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const chosen = info?.projects.filter((p) => checked.has(p.id)) ?? [];
  const total = info ? info.projects_size + info.cache.size + info.leftovers.size : 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange} wide title="存储空间"
      description={<span className="flex items-center gap-1">{info ? <span>项目保存在 <code className="font-mono text-xs break-all">{info.root}</code></span> : null}<HelpButton k="storage" /></span>}>
      {!info ? (
        <div className="grid place-items-center py-10"><Spinner /></div>
      ) : (
        <div className="space-y-5">
          <section>
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <span className="text-2xl font-semibold tabular">{fmtBytes(total)}</span>
              <span className="text-[13px] text-muted">MiliKara 的项目和缓存</span>
              {info.disk.free != null && (
                <span className="ml-auto text-[13px] text-muted">磁盘剩余 <b className="tabular text-fg">{fmtBytes(info.disk.free)}</b>
                  {info.disk.total ? ` / ${fmtBytes(info.disk.total)}` : ''}</span>
              )}
            </div>
            <Bar className="mt-2 h-2.5" parts={[[info.projects_size, 'bg-accent'], [info.cache.size, 'bg-info'], [info.leftovers.size, 'bg-warn']]} total={total} />
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
              <Legend color="bg-accent" label={`项目 ${info.projects.length} 个`} size={info.projects_size} />
              <Legend color="bg-info" label="缓存" size={info.cache.size} />
              <Legend color="bg-warn" label="残留文件" size={info.leftovers.size} />
              {info.models.size > 0 && <span title={info.models.path ?? undefined}>另有模型 {fmtBytes(info.models.size)}（删掉需要重新下载，不在这里清理）</span>}
            </div>
          </section>

          <section className="space-y-2">
            <h4 className="text-[13px] font-semibold">可以放心清理</h4>
            <CleanRow title="缓存" size={info.cache.size} busy={busy === 'cache'} disabled={info.working}
              disabledReason="有任务或操作正在进行，完成后再清理"
              description="播放用的解码音频、波形和模型的识别结果。需要时会自动重新生成，之后第一次打开或重新对齐会慢一些。"
              onClean={() => clean({ cache: true })} />
            <CleanRow title="残留文件" size={info.leftovers.size} busy={busy === 'leftovers'}
              description={info.leftovers.size > 0
                ? Object.entries(info.leftovers.parts).map(([k, v]) => `${LEFTOVER_LABEL[k] ?? k} ${fmtBytes(v)}`).join(' · ') + '。已经不会再用到。'
                : '已导入任务的上传副本、被替换的旧音频等，已经不会再用到。现在没有。'}
              onClean={() => clean({ leftovers: true })} />
          </section>

          <section className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h4 className="text-[13px] font-semibold">项目（按占用从大到小）</h4>
              {chosen.length > 0 && (
                <span className="ml-auto">
                  <ConfirmButton size="xs" variant="danger" icon={<Trash2 className="size-3.5" />} loading={busy === 'delete'}
                    question={`删除 ${chosen.length} 个项目（${fmtBytes(chosen.reduce((s, p) => s + p.size, 0))}）？无法恢复。`} confirmLabel="删除"
                    onConfirm={() => void removeProjects(chosen.map((p) => p.id))}>
                    删除所选 {chosen.length} 个
                  </ConfirmButton>
                </span>
              )}
            </div>
            {info.projects.length === 0 ? (
              <p className="py-4 text-center text-[13px] text-muted">还没有项目</p>
            ) : (
              <ul className="divide-y divide-line rounded-xl border border-line">
                {info.projects.map((p) => (
                  <ProjectRow key={p.id} p={p} max={info.projects[0].size} checked={checked.has(p.id)} onCheck={() => toggle(p.id)}
                    open={expanded === p.id} onOpen={() => setExpanded(expanded === p.id ? null : p.id)} busy={busy}
                    onClean={(body) => cleanProject(p.id, body)} onDelete={() => removeProjects([p.id])} />
                ))}
              </ul>
            )}
            <p className="text-xs text-muted">删除项目会删掉它的视频、音频、分轨、对齐结果和导出的文件，无法恢复；需要保留的话，先在详细模式里导出项目包（.kara.zip）。</p>
          </section>
        </div>
      )}
    </Dialog>
  );
}

function ProjectRow({ p, max, checked, onCheck, open, onOpen, busy, onClean, onDelete }: {
  p: StorageProject; max: number; checked: boolean; onCheck: () => void; open: boolean; onOpen: () => void; busy: string | null;
  onClean: (body: { exports?: boolean | string[]; stems?: boolean }) => void; onDelete: () => void;
}) {
  const parts = (Object.keys(PART_LABEL) as (keyof StorageProject['parts'])[]).filter((k) => p.parts[k] > 0);
  const locked = p.busy ? '有任务或操作正在处理这个项目，完成后再清理' : undefined;
  return (
    <li className="px-3 py-2.5">
      <div className="flex items-center gap-3">
        <input type="checkbox" className="size-4 shrink-0 accent-[var(--color-accent)]" aria-label={`选择 ${p.name}`}
          checked={checked} disabled={p.busy} onChange={onCheck} />
        <button className="focus-ring flex min-w-0 flex-1 items-center gap-2 rounded text-left" onClick={onOpen} aria-expanded={open}>
          {open ? <ChevronDown className="size-4 shrink-0 text-subtle" /> : <ChevronRight className="size-4 shrink-0 text-subtle" />}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-medium">{p.name}</span>
            <span className="block text-xs text-muted">{fmtRelative(p.updated)}</span>
          </span>
          {p.busy && <Badge tone="info">处理中</Badge>}
          <span className="hidden w-28 sm:block"><Bar className="h-1.5" total={max} parts={parts.map((k) => [p.parts[k], PART_COLOR[k]])} /></span>
          <span className="w-16 shrink-0 text-right text-[13px] font-medium tabular">{fmtBytes(p.size)}</span>
        </button>
      </div>
      {open && (
        <div className="mt-2 ml-7 space-y-1.5 rounded-lg bg-surface-2/60 px-3 py-2.5 text-[13px]">
          {parts.map((k) => (
            <div key={k} className="flex flex-wrap items-center gap-2">
              <span className={cn('size-2 shrink-0 rounded-full', PART_COLOR[k])} />
              <span>{PART_LABEL[k]}</span>
              <span className="text-muted tabular">{fmtBytes(p.parts[k])}</span>
              <span className="ml-auto">
                {k === 'stems' && p.stems && (
                  <ConfirmButton size="xs" variant="ghost" disabled={!!locked} disabledReason={locked} loading={busy === `${p.id}:{"stems":true}`}
                    question="删掉分轨？之后降低人声或重新对齐时要重新分离。" confirmLabel="删除" onConfirm={() => onClean({ stems: true })}>删除分轨</ConfirmButton>
                )}
                {k === 'exports' && p.exports.length > 1 && (
                  <ConfirmButton size="xs" variant="ghost" disabled={!!locked} disabledReason={locked} loading={busy === `${p.id}:{"exports":true}`}
                    question={`删除全部 ${p.exports.length} 个导出的文件？`} confirmLabel="删除" onConfirm={() => onClean({ exports: true })}>全部删除</ConfirmButton>
                )}
                {k === 'media' && <span className="text-xs text-subtle">删除项目时一起删除</span>}
              </span>
              {k === 'exports' && (
                <ul className="w-full space-y-0.5 pl-4">
                  {p.exports.map((e) => (
                    <li key={e.filename} className="flex items-center gap-2 text-xs">
                      <span className="min-w-0 flex-1 truncate" title={e.filename}>{e.filename}</span>
                      <span className="text-muted tabular">{fmtBytes(e.size)}</span>
                      <IconButton label={`删除 ${e.filename}`} disabled={!!locked} disabledReason={locked} size="xs" variant="ghost"
                        icon={<Trash2 className="size-3.5" />} onClick={() => onClean({ exports: [e.filename] })} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          <div className="flex justify-end border-t border-line pt-2">
            <ConfirmButton size="xs" variant="danger" icon={<Trash2 className="size-3.5" />} disabled={!!locked} disabledReason={locked}
              question={`删除「${p.name}」（${fmtBytes(p.size)}）？无法恢复。`} confirmLabel="删除" onConfirm={onDelete}>删除项目</ConfirmButton>
          </div>
        </div>
      )}
    </li>
  );
}

function CleanRow({ title, size, description, busy, disabled, disabledReason, onClean }: {
  title: string; size: number; description: string; busy: boolean; disabled?: boolean; disabledReason?: string; onClean: () => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-line px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium">{title} <span className="ml-1 text-muted tabular">{fmtBytes(size)}</span></div>
        <p className="mt-0.5 text-xs text-muted">{description}</p>
      </div>
      <Button size="xs" variant="secondary" loading={busy} disabled={size === 0 || disabled} disabledReason={disabled ? disabledReason : undefined}
        onClick={onClean}>清理</Button>
    </div>
  );
}

function Bar({ parts, total, className }: { parts: [number, string][]; total: number; className?: string }) {
  return (
    <div className={cn('flex w-full overflow-hidden rounded-full bg-surface-3', className)} aria-hidden>
      {total > 0 && parts.map(([v, color], i) => v > 0 && <span key={i} className={color} style={{ width: `${(v / total) * 100}%` }} />)}
    </div>
  );
}

function Legend({ color, label, size }: { color: string; label: string; size: number }) {
  return <span className="flex items-center gap-1.5"><span className={cn('size-2 rounded-full', color)} />{label} <span className="tabular text-fg">{fmtBytes(size)}</span></span>;
}

/** A button that opens the dialog; the project list is reloaded when it closes (sizes changed). */
export function StorageButton({ label = '存储空间', size = 'xs', variant = 'ghost' }: {
  label?: string; size?: 'xs' | 'sm'; variant?: 'ghost' | 'secondary';
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size={size} variant={variant} icon={<HardDrive className="size-3.5" />} onClick={() => setOpen(true)}>{label}</Button>
      <StorageDialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) void loadProjects().catch(() => undefined); }} />
    </>
  );
}
