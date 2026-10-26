// Optional vocal separation. Failures are reported, never silently replaced
// by the original; stems keep the original timeline (sync report shown).

import { AudioLines, Check, Cpu, Scissors, X } from 'lucide-react';
import { useState } from 'react';
import { api } from '@/lib/api';
import { cn, fmtMs, ROLE_LABEL } from '@/lib/format';
import type { AudioAsset, Job } from '@/lib/types';
import { cancelJob, isOpenProject, ppath, refreshProject, run, trackJob, useApp, useJob, useProject, useView } from '@/store/app';
import { Badge, Button, Callout, Card, CardBody, CardHeader, ConfirmButton, Progress, Segmented } from '@/components/ui';
import { DiagnosticsButton } from '@/components/DiagnosticsButton';
import { HelpButton } from '@/components/guide/Help';

export function SeparationCard() {
  const project = useProject()!;
  const view = useView()!;
  const info = useApp((s) => s.info);
  const job = useJob('separate');
  const running = job && (job.status === 'queued' || job.status === 'running');
  const presets = info?.separation_presets ?? [];
  const [preset, setPreset] = useState(presets[0]?.name ?? 'melband-roformer');
  const [device, setDevice] = useState<'auto' | 'cpu'>('auto');
  const hasOriginal = project.audio.some((a) => a.role === 'original');
  const stems = project.audio.filter((a) => a.role === 'vocals' || a.role === 'instrumental');
  const available = info?.separation_available ?? false;

  const start = () => run(async () => {
    const j = await api.post<Job>(ppath('/separate'), { preset, device });
    trackJob(j, { label: '人声分离', onDone: (d) => (isOpenProject(d.project_id) ? refreshProject() : undefined) });
  }, '无法开始分离');

  return (
    <Card>
      <CardHeader
        icon={<Scissors className="size-4" />}
        title="人声分离"
        help={<HelpButton k="enhance.separation" />}
        description="可选：分出人声和伴奏，用来对齐、试听，以及做伴唱 / 降低人声的视频。"
        actions={
          <Button variant="primary" size="sm" icon={<Scissors className="size-4" />} loading={!!running}
            disabled={!available || !hasOriginal || !!running} onClick={start}
            disabledReason={!available ? '未安装分离组件（见下方说明）' : !hasOriginal ? '请先在“音频与歌词”中上传原曲' : null}>
            开始分离
          </Button>
        }
      />
      <CardBody className="space-y-4">
        {!available && (
          <Callout tone="warn" title="未安装分离组件">
            安装可选依赖后重启服务：
            <code className="mt-1 block rounded-md bg-surface px-2 py-1 font-mono text-xs">uv pip install -e ".[separation]"</code>
          </Callout>
        )}
        {!hasOriginal && <Callout tone="info">请先在“音频与歌词”中上传原曲。</Callout>}

        <div className="flex flex-wrap items-center gap-3 text-[13px]">
          <span className="font-medium text-muted">运行设备</span>
          <Segmented<'auto' | 'cpu'>
            size="sm" label="运行设备"
            value={device}
            onChange={setDevice}
            options={[
              { value: 'auto', label: '自动（GPU / MPS）', title: '有 GPU 或 Apple 芯片时使用它，速度最快' },
              { value: 'cpu', label: '仅 CPU', title: '更慢，但可避开个别环境下的 GPU 问题' },
            ]}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {presets.map((p) => {
            const on = preset === p.name;
            return (
              <button
                key={p.name}
                type="button"
                onClick={() => setPreset(p.name)}
                disabled={!!running}
                className={cn('focus-ring rounded-xl border p-3.5 text-left transition disabled:opacity-60',
                  on ? 'border-accent bg-accent-soft/60 ring-1 ring-accent' : 'border-line hover:border-line-strong hover:bg-surface-2')}
              >
                <div className="flex items-center gap-2">
                  <Cpu className={cn('size-4', on ? 'text-accent' : 'text-muted')} />
                  <span className="text-[13px] font-semibold">{p.name}</span>
                  <Badge tone="neutral">{p.architecture}</Badge>
                  {on && <Check className="ml-auto size-4 text-accent" />}
                </div>
                <p className="mt-1.5 text-xs leading-5 text-muted">{p.notes}</p>
                <p className="mt-1 truncate font-mono text-[11px] text-subtle" title={p.model_filename}>{p.model_filename}</p>
                <p className="mt-1 text-[11px] text-subtle">{p.license_note}</p>
              </button>
            );
          })}
        </div>

        {job && <JobStatus job={job} />}

        <div>
          <div className="mb-2 flex items-center gap-2 text-[13px] font-medium"><AudioLines className="size-4 text-muted" />现有分轨</div>
          {stems.length === 0 ? (
            <p className="text-[13px] text-subtle">还没有人声 / 伴奏分轨。也可以在“音频与歌词”中导入已有的同源分轨。</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {stems.map((a) => <StemTile key={a.id} asset={a} outdated={!!view.audio[a.role as 'vocals']?.outdated} />)}
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

function JobStatus({ job }: { job: Job }) {
  const live = job.status === 'queued' || job.status === 'running';
  return (
    <div className={cn('rounded-xl border px-4 py-3', job.status === 'failed' ? 'border-danger/40 bg-danger-soft' : 'border-line bg-surface-2/60')}>
      <div className="flex items-center gap-2 text-[13px]">
        <span className="font-medium">分离任务</span>
        <Badge tone={{ queued: 'neutral', running: 'accent', succeeded: 'ok', failed: 'danger', cancelled: 'neutral' }[job.status] as any}>
          {{ queued: '排队中', running: '运行中', succeeded: '完成', failed: '失败', cancelled: '已取消' }[job.status]}
        </Badge>
        {live && <span className="tabular text-xs text-muted">{Math.round(job.progress * 100)}%</span>}
        {live && (
          <span className="ml-auto">
            <ConfirmButton size="xs" variant="ghost" icon={<X className="size-3.5" />} question="取消分离？已完成的部分会丢弃"
              confirmLabel="取消分离" keepLabel="继续" onConfirm={() => void run(() => cancelJob(job.id))}>取消</ConfirmButton>
          </span>
        )}
      </div>
      {live && <Progress value={job.progress} className="mt-2" label="人声分离进度" />}
      <div className={cn('mt-1.5 text-xs break-words', job.status === 'failed' ? 'text-danger' : 'text-muted')}>
        {job.status === 'failed' ? `失败原因：${job.error ?? job.message}（不会自动回退为原曲）` : job.message}
      </div>
      {job.status === 'failed' && <div className="mt-1"><DiagnosticsButton jobId={job.id} /></div>}
    </div>
  );
}

function StemTile({ asset, outdated }: { asset: AudioAsset; outdated: boolean }) {
  const sync = asset.sync_report as Record<string, any> | null;
  return (
    <div className={cn('rounded-xl border px-4 py-3', outdated ? 'border-warn/50 bg-warn-soft/50' : 'border-line')}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-semibold">{ROLE_LABEL[asset.role]}</span>
        <Badge tone="neutral">{asset.source.kind === 'separation' ? '分离' : '导入'}</Badge>
        {outdated ? <Badge tone="warn" dot className="ml-auto">来自更换前的原曲</Badge>
          : sync && <Badge tone={sync.ok ? 'ok' : 'warn'} dot className="ml-auto">{sync.ok ? '同步正常' : '同步未验证'}</Badge>}
      </div>
      {outdated && <div className="mt-1 text-xs text-warn">来自更换前的原曲，需重新分离（点上方“开始分离”）；在此之前不会被使用。</div>}
      <div className="mt-1 text-xs text-muted">
        {fmtMs(asset.duration_ms)} · {asset.sample_rate} Hz · {asset.channels} 声道
        {asset.source.model && <> · {asset.source.model}</>}
      </div>
      {sync && (
        <div className="mt-1.5 text-xs text-subtle">
          偏移 {typeof sync.lag_ms === 'number' ? `${sync.lag_ms.toFixed(1)} ms` : '—'}
          {typeof sync.correlation === 'number' && ` · 相关 ${sync.correlation.toFixed(3)}`}
          {sync.message && <div className="mt-0.5">{sync.message}</div>}
        </div>
      )}
    </div>
  );
}
