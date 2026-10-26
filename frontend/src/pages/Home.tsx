// No project open: create, import or open one.

import { ArrowRight, FileMusic, FolderInput, Clock, ListMusic, Timer, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { cn, fmtBytes, fmtRelative } from '@/lib/format';
import { isEnter } from '@/lib/keys';
import type { Mode, ProjectListItem, ProjectView } from '@/lib/types';
import { adoptProject, deleteProject, loadProjects, openProject, run, toast, useApp } from '@/store/app';
import { Badge, Button, Card, CardBody, CardHeader, DropZone, EmptyState, Input, Tip } from '@/components/ui';
import { StorageButton } from '@/components/StorageDialog';
import { ModeChoice } from './Mode';
import { HelpButton } from '@/components/guide/Help';

export function HomePage() {
  const projects = useApp((s) => s.projects);
  const [name, setName] = useState('');
  const [mode, setMode] = useState<Mode>('plain');
  const [busy, setBusy] = useState(false);
  const creating = useRef(false);
  useEffect(() => { void run(() => loadProjects()); }, []);  // tasks may have added projects meanwhile

  const create = () => {
    if (creating.current) return;  // Enter and the button (or a double click) create one project only
    creating.current = true;
    return run(async () => {
      setBusy(true);
      try {
        const pv = await api.post<ProjectView>('/api/projects', { name: name.trim() || '未命名歌曲', mode });
        adoptProject(pv, 'input');
        void loadProjects().catch(() => undefined);
        toast('ok', '已创建项目', pv.project.name);
      } finally {
        creating.current = false;
        setBusy(false);
      }
    }, '创建失败');
  };

  const importFile = (file: File) => run(async () => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const pv = await api.post<ProjectView>('/api/projects/import', fd);
    await loadProjects();
    await openProject(pv.project.id);
    toast('ok', '已导入项目', pv.project.name);
  }, '导入失败');

  return (
    <div className="space-y-8">
      <section className="relative overflow-hidden rounded-3xl border border-line bg-surface px-8 py-10 shadow-[var(--shadow-card)]">
        <div className="pointer-events-none absolute -top-24 -right-16 size-80 rounded-full bg-gradient-to-br from-indigo-500/25 to-fuchsia-500/20 blur-3xl" />
        <div className="relative max-w-2xl">
          <Tip content="音频、视频和项目文件只保存在这台电脑上。只有你主动使用时才会联网：AI 注音会把歌词发送给所选的 AI 服务，音乐链接会从网易云 / QQ 音乐获取歌词。">
            <span className="inline-flex"><Badge tone="accent">本地运行 · 音频与项目只存在本机</Badge></span>
          </Tip>
          <h1 className="mt-4 text-3xl font-semibold tracking-tight">把已知歌词精确对齐到每一个发音</h1>
          <p className="mt-3 text-[15px] leading-7 text-muted">
            输入音频与歌词（可选 LRC），得到可复用的逐发音单元时间 —— 原音频起点起算的整数毫秒。
            可选 AI 注音往返、人声分离与 LRC 首音校准，结果可人工检查、锁定并导出。
          </p>
          <div className="mt-6 flex flex-wrap gap-6 text-[13px] text-muted">
            <span className="flex items-center gap-2"><ListMusic className="size-4 text-accent" />逐单元时间</span>
            <span className="flex items-center gap-2"><Timer className="size-4 text-accent" />LRC 锚点约束</span>
            <span className="flex items-center gap-2"><FileMusic className="size-4 text-accent" />降低人声的混音</span>
          </div>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="新建项目" help={<HelpButton k="home.new" />} description="歌词带时间（LRC）选 LRC 增强，只有文字选普通；之后仍可切换" />
          <CardBody className="space-y-5">
            <Input placeholder="歌曲名，例如：夜に駆ける" value={name} onChange={(e) => setName(e.target.value)}
              aria-label="歌曲名" onKeyDown={(e) => { if (isEnter(e)) void create(); }} className="h-10 text-[15px]" />
            <ModeChoice value={mode} onChange={setMode} />
            <div className="flex justify-end">
              <Button variant="primary" size="lg" loading={busy} onClick={create} icon={<ArrowRight className="size-4" />}>创建并开始</Button>
            </div>
          </CardBody>
        </Card>

        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader title="导入项目" help={<HelpButton k="home.import" />} description="项目包 .kara.zip 或 project.json" icon={<FolderInput className="size-4" />} />
            <CardBody>
              <DropZone accept=".json,.zip,application/json,application/zip" onFile={importFile}
                title="拖入或点击选择项目文件" hint="音频缺失时可在项目中重新上传" compact />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="最近项目" icon={<Clock className="size-4" />} actions={<StorageButton />} />
            <div className="p-2">
              {projects.length === 0 ? (
                <EmptyState className="m-2 py-8" title="还没有项目" description="新建一个项目开始对齐" />
              ) : (
                <ul className="max-h-80 overflow-y-auto">
                  {projects.map((p) => <ProjectRow key={p.id} p={p} />)}
                </ul>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function ProjectRow({ p }: { p: ProjectListItem }) {
  const [confirm, setConfirm] = useState(false);
  if (confirm) {
    return (
      <li className="rounded-lg bg-danger-soft/60 px-3 py-2.5 text-[13px]">
        <div className="font-medium">删除「{p.name}」？</div>
        <div className="mt-0.5 text-xs text-muted">音频、分轨、对齐结果和导出的视频都会一起删除，无法恢复。</div>
        <div className="mt-2 flex gap-2">
          <Button size="xs" variant="danger" icon={<Trash2 className="size-3.5" />}
            onClick={() => run(async () => { await deleteProject(p.id); toast('ok', `已删除「${p.name}」`); }, '删除失败')}>删除</Button>
          <Button size="xs" variant="ghost" onClick={() => setConfirm(false)}>取消</Button>
        </div>
      </li>
    );
  }
  return (
    <li className="group relative">
      <button
        onClick={() => run(() => openProject(p.id), '打开项目失败')}
        className={cn('focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2.5 pr-11 text-left transition hover:bg-surface-2')}
      >
        <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted group-hover:bg-accent-soft group-hover:text-accent">
          <FileMusic className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium">{p.name}</div>
          <div className="text-xs text-muted">{fmtRelative(p.updated)}{p.size != null && <> · {fmtBytes(p.size)}</>}</div>
        </div>
        <Badge tone={p.mode === 'lrc' ? 'accent' : 'neutral'}>{p.mode === 'lrc' ? 'LRC 增强' : '普通'}</Badge>
        <ArrowRight className="size-4 text-subtle opacity-0 transition group-hover:opacity-100" />
      </button>
      <button type="button" aria-label={`删除项目 ${p.name}`} title="删除项目" onClick={() => setConfirm(true)}
        className="focus-ring absolute top-1/2 right-2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-subtle opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100 hover:bg-danger-soft hover:text-danger focus:opacity-100 [@media(hover:none)]:opacity-100">
        <Trash2 className="size-3.5" />
      </button>
    </li>
  );
}
