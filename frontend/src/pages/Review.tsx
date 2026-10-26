// Step 6: manual review. Units can be listened to, edited numerically or by
// dragging on the waveform, locked, reverted to the model time, compared with
// retry candidates and local reruns. Nothing is ever overwritten silently.

import { AlertTriangle, ArrowRight, ListChecks, RefreshCw, Sparkles, Wand2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { player } from '@/audio/player';
import { STATUS_COLORS } from '@/audio/waveform';
import { revealOnWaveform } from '@/audio/waveformRef';
import { api } from '@/lib/api';
import { fmtRelative, ROLE_LABEL } from '@/lib/format';
import type { Issue, Job } from '@/lib/types';
import {
  REVIEW_LIST_WIDTH, isOpenProject, ppath, refreshProject, run, selectResult, setLayoutSize, setStep, toast, trackJob, useApp, useJobRunning,
  useProject, useResult, useView,
} from '@/store/app';
import { ignoreShortcut, MOD_KEY, SHIFT_KEY } from '@/lib/keys';
import { Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, Kbd, PageHeader, ResizeHandle, Select, Stat, Tabs } from '@/components/ui';
import { CandidatesPanel, RerunsPanel } from './review/Alternatives';
import { lineStats, unitInfoMap } from './review/helpers';
import { IssuesPanel } from './review/IssuesPanel';
import { LineDetail, playUnit } from './review/LineDetail';
import { LineList, type LineFilter } from './review/LineList';
import { INTRO } from '@/lib/guide';
import { FirstTip, HelpButton } from '@/components/guide/Help';

export function ReviewPage() {
  const listWidth = useApp((s) => s.reviewListWidth);
  const project = useProject()!;
  const view = useView()!;
  const result = useResult();
  const selLineId = useApp((s) => s.selLineId);
  const selUnitId = useApp((s) => s.selUnitId);
  const filter = useApp((s) => s.reviewFilter);
  const setFilter = (f: LineFilter) => useApp.setState({ reviewFilter: f });
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState('units');
  const rerunBusy = useJobRunning('align');

  const info = useMemo(() => unitInfoMap(project), [project]);
  const stats = useMemo(() => (result ? lineStats(project, result) : []), [project, result]);
  const lineIndex = useMemo(() => new Map(project.lyrics.lines.map((l, i) => [l.id, i])), [project]);
  const lineTexts = useMemo(() => new Map(project.lyrics.lines.map((l) => [l.id, l.text])), [project]);

  // default selection: first line with a problem, else the first line
  useEffect(() => {
    if (!stats.length) return;
    if (!selLineId || !stats.some((s) => s.line.id === selLineId)) {
      const first = stats.find((s) => s.failed || s.issues.length) ?? stats[0];
      useApp.setState({ selLineId: first.line.id });
    }
  }, [stats, selLineId]);

  const selStat = stats.find((s) => s.line.id === selLineId) ?? null;

  // ↑/↓ move the unit selection, Enter plays it
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Enter / arrows on a focused control (button, radio, slider …) or in a dialog belong to it;
      // arrows may repeat (holding ↓ walks the units)
      if (ignoreShortcut(e, { allowRepeat: e.key !== 'Enter' })) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (!result) return;
      const all = stats.flatMap((s) => s.units);
      if (!all.length) return;
      const i = all.findIndex((u) => u.unit_id === selUnitId);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const next = all[Math.max(0, Math.min(all.length - 1, i < 0 ? 0 : i + (e.key === 'ArrowDown' ? 1 : -1)))];
        useApp.setState({ selUnitId: next.unit_id, selLineId: next.line_id });
        if (next.start_ms !== null && next.end_ms !== null) revealOnWaveform(next.start_ms, next.end_ms);
      } else if (e.key === 'Enter' && i >= 0) {
        e.preventDefault();
        playUnit(all[i]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [stats, selUnitId, result]);

  if (!result) {
    return (
      <>
        <PageHeader eyebrow="第 6 步" title="人工检查" />
        <EmptyState
          icon={<ListChecks className="size-5" />}
          title="还没有对齐结果"
          description="先运行一次对齐，结果会出现在这里供检查、修正与锁定。"
          action={<Button variant="primary" onClick={() => setStep('align')} icon={<ArrowRight className="size-4" />}>去对齐</Button>}
        />
      </>
    );
  }

  const failed = result.units.filter((u) => u.status !== 'ok' && !u.manual).length;
  const manual = result.units.filter((u) => u.manual).length;
  const nIssues = result.issues.filter((i) => i.severity !== 'info').length;
  const rerunIds = checked.size ? [...checked] : selLineId ? [selLineId] : [];
  // local reruns are compared inside their parent; one opened directly (e.g. “查看” on the align page) is listed too
  const parentSummaries = view.results.filter((r) => !(r.parent_result_id && !r.coverage.full) || r.id === result.id);

  const selectLine = (lid: string) => {
    useApp.setState({ selLineId: lid, selUnitId: null, candidateId: null });
    const s = stats.find((x) => x.line.id === lid);
    if (s?.start !== null && s?.start !== undefined && s.end !== null) revealOnWaveform(s.start, s.end);
  };

  const toggleCheck = (lid: string) => setChecked((prev) => {
    const n = new Set(prev);
    if (n.has(lid)) n.delete(lid);
    else n.add(lid);
    return n;
  });

  const rerun = () => {
    if (!rerunIds.length) return toast('info', '请先在左侧勾选要重跑的行');
    void run(async () => {
      const job = await api.post<Job>(ppath('/align'), { line_ids: rerunIds });
      trackJob(job, {
        label: '局部重跑',
        onDone: async (j) => {
          if (!isOpenProject(j.project_id)) return;  // another project is open now
          await refreshProject();
          if (j.status === 'succeeded' && j.output?.result_id && isOpenProject(j.project_id)
            && useApp.getState().pv?.project.results.some((r) => r.id === j.output.result_id)) {
            useApp.setState({ compareWithId: j.output.result_id, candidateId: null });
            setTab('reruns');
          }
        },
      });
      toast('info', `已开始局部重跑 ${rerunIds.length} 行`, '完成后可在“局部重跑”中对比并按行采用');
    }, '无法开始局部重跑');
  };

  const jumpToIssue = (i: Issue) => {
    if (i.line_id) useApp.setState({ selLineId: i.line_id });
    const u = i.unit_id ? result.units.find((x) => x.unit_id === i.unit_id) : null;
    if (u) {
      useApp.setState({ selUnitId: u.unit_id });
      if (u.start_ms !== null && u.end_ms !== null) revealOnWaveform(u.start_ms, u.end_ms);
    } else if (i.line_id) {
      const s = stats.find((x) => x.line.id === i.line_id);
      if (s && s.start !== null && s.end !== null) revealOnWaveform(s.start, s.end);
    }
    setTab('units');
  };

  const nCandLine = selStat ? result.candidates.filter((c) => c.line_id === selStat.line.id).length : 0;
  const nReruns = view.results.filter((r) => r.parent_result_id === result.id && !r.coverage.full).length;

  return (
    <>
      <PageHeader
        eyebrow="第 6 步"
        title="人工检查"
        help={<HelpButton k="review.page" />}
        description={INTRO.review}
        actions={
          <>
            <Select
              className="w-80"
              value={result.id}
              onChange={(e) => { selectResult(e.target.value); setChecked(new Set()); }}
              aria-label="选择结果"
            >
              {parentSummaries.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.id === project.active_result_id ? '● ' : ''}{fmtRelative(r.created)} · {ROLE_LABEL[r.audio_role] ?? r.audio_role} · {r.mode === 'lrc' ? 'LRC' : '普通'}
                  {r.stale ? ' · 已过期' : ''}{r.coverage.full ? '' : ' · 局部'}
                </option>
              ))}
            </Select>
            <Button onClick={() => setStep('export')}>导出</Button>
            <Button variant="primary" onClick={() => setStep('karaoke')} icon={<ArrowRight className="size-4" />}>下一步：卡拉OK字幕</Button>
          </>
        }
      />

      <div className="space-y-6">
        <FirstTip k="review.first" />
        {result.stale && (
          <Callout tone="warn" title="该结果已过期" actions={<Button size="sm" onClick={() => setStep('align')}>重新对齐</Button>}>
            {result.stale_reason ?? '输入已修改'}。仍可查看与试听，但不能修改时间；重新对齐时人工锁定的单元会被保留。
          </Callout>
        )}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="发音单元" value={result.units.length} hint={`${stats.length} 行`} />
          <Stat label="无时间" value={failed} tone={failed ? 'danger' : 'ok'} hint="没对上的单元，需要处理" />
          <Stat label="提示" value={nIssues} tone={nIssues ? 'warn' : 'ok'} hint="可能需要看一眼的地方" />
          <Stat label="人工修改" value={manual} tone={manual ? 'accent' : undefined} hint="已锁定，重跑保留" />
          <Stat label="覆盖" value={result.coverage.full ? '完整' : '局部'} hint={`${Math.round((1 - failed / Math.max(1, result.units.length)) * 100)}% 单元有时间`} />
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
          <span>后端 <b className="font-medium text-fg">{result.backend.name}</b></span>
          <span className="font-mono">{result.backend.model_id}{result.backend.model_revision ? `@${result.backend.model_revision.slice(0, 10)}` : ''}</span>
          <span>转写 {result.backend.profile}</span>
          <span>音频 {ROLE_LABEL[result.snapshot.audio_role] ?? result.snapshot.audio_role}</span>
          {result.backend.license && <Badge tone="warn">许可 {result.backend.license}</Badge>}
          <Legend />
        </div>

        <div className="grid gap-6 lg:flex lg:items-start lg:gap-2">
          <Card
            className="flex min-h-0 flex-col overflow-hidden lg:sticky lg:top-4 lg:w-[var(--list-w)] lg:shrink-0 lg:self-start"
            style={{ '--list-w': `${listWidth}px` } as React.CSSProperties}
          >
            <LineList
              stats={stats}
              selected={selLineId}
              onSelect={selectLine}
              checked={checked}
              onToggleCheck={toggleCheck}
              filter={filter}
              onFilter={setFilter}
            />
            <div className="flex items-center gap-2 border-t border-line px-3 py-2.5">
              <Button size="sm" className="w-full" wrapperClassName="flex-1" icon={<RefreshCw className="size-3.5" />} loading={rerunBusy}
                title="只重新对齐所选行（未勾选时为当前行），生成局部结果供对比；锁定的单元不受影响"
                disabled={!rerunIds.length || result.stale} onClick={rerun}
                disabledReason={result.stale ? '该结果已过期：请先重新对齐' : '先在列表中选择要重跑的行'}>
                局部重跑{checked.size ? `所选 ${checked.size} 行` : '当前行'}
              </Button>
              {checked.size > 0 && <Button size="sm" variant="ghost" onClick={() => setChecked(new Set())}>清除</Button>}
            </div>
          </Card>

          <ResizeHandle
            axis="x" label="调整歌词列表宽度" className="hidden lg:flex"
            value={listWidth} onChange={(v) => setLayoutSize('reviewListWidth', v)}
            min={REVIEW_LIST_WIDTH.min} max={REVIEW_LIST_WIDTH.max} defaultValue={REVIEW_LIST_WIDTH.default}
          />

          <Card className="min-w-0 lg:flex-1">
            <CardBody>
              <Tabs
                value={tab}
                onChange={setTab}
                extra={<HelpButton k="review.detail" />}
                tabs={[
                  {
                    value: 'units',
                    label: <><ListChecks className="size-3.5" />单元</>,
                    content: selStat ? (
                      <LineDetail
                        stat={selStat}
                        result={result}
                        info={info}
                        selUnitId={selUnitId}
                        onRerun={rerun}
                        rerunBusy={rerunBusy}
                        contextTexts={lineTexts}
                      />
                    ) : <EmptyState title="选择左侧的一行" />,
                  },
                  {
                    value: 'issues',
                    label: <><AlertTriangle className="size-3.5" />提示 {result.issues.length > 0 && <Badge tone="warn">{result.issues.length}</Badge>}</>,
                    content: <IssuesPanel issues={result.issues} lineIndex={lineIndex} onJump={jumpToIssue} />,
                  },
                  {
                    value: 'candidates',
                    label: <><Sparkles className="size-3.5" />候选 {nCandLine > 0 && <Badge tone="accent">{nCandLine}</Badge>}</>,
                    content: selStat ? <CandidatesPanel result={result} lineId={selStat.line.id} /> : <EmptyState title="选择左侧的一行" />,
                  },
                  {
                    value: 'reruns',
                    label: <><Wand2 className="size-3.5" />局部重跑 {nReruns > 0 && <Badge>{nReruns}</Badge>}</>,
                    content: <RerunsPanel result={result} lineId={selLineId} />,
                  },
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader title="快捷操作" description="焦点不在输入框时可用" />
          <CardBody className="flex flex-wrap gap-x-6 gap-y-2 text-[13px] text-muted">
            <span><Kbd>Space</Kbd> 播放 / 暂停</span>
            <span><Kbd>↑</Kbd> <Kbd>↓</Kbd> 切换单元</span>
            <span><Kbd>Enter</Kbd> 循环试听所选单元</span>
            <span><Kbd>L</Kbd> 循环开关</span>
            <span><Kbd>{MOD_KEY}</Kbd>+<Kbd>Z</Kbd> 撤销 · <Kbd>{MOD_KEY}</Kbd>+<Kbd>{SHIFT_KEY}</Kbd>+<Kbd>Z</Kbd> 重做（只针对时间修改）</span>
            <span>慢速试听时游标仍是原音频时间 <Button size="xs" variant="ghost" onClick={() => player.setRate(player.rate === 1 ? 0.5 : 1)}>切换 0.5×</Button></span>
          </CardBody>
        </Card>
      </div>
    </>
  );
}

function Legend() {
  const items: [string, keyof typeof STATUS_COLORS][] = [
    ['正常', 'ok'], ['人工', 'manual'], ['失败', 'failed'], ['未对齐', 'unaligned'], ['候选', 'candidate'], ['重跑对比', 'compare'],
  ];
  return (
    <span className="ml-auto flex flex-wrap items-center gap-3">
      {items.map(([label, key]) => (
        <span key={key} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: STATUS_COLORS[key] }} />
          {label}
        </span>
      ))}
    </span>
  );
}
