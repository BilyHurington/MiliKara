import { useEffect, useState } from 'react';
import { WifiOff } from 'lucide-react';
import { player } from '@/audio/player';
import { useConnection } from '@/lib/api';
import { ignoreShortcut } from '@/lib/keys';
import { loadInfo, loadProjects, openProject, refreshProject, run, toast, useApp } from '@/store/app';
import { redo, undo } from '@/store/edits';
import { loadUpdate } from '@/store/update';
import { Sidebar } from '@/components/shell/Sidebar';
import { StudioDock } from '@/components/shell/StudioDock';
import { ErrorBoundary } from '@/components/shell/ErrorBoundary';
import { Toaster } from '@/components/shell/Toaster';
import { Topbar } from '@/components/shell/Topbar';
import { setShortcutsOpen, ShortcutsDialog } from '@/components/guide/Shortcuts';
import { TooltipProvider } from '@/components/ui';
import { HomePage } from '@/pages/Home';
import { ModePage } from '@/pages/Mode';
import { InputPage } from '@/pages/Input';
import { EnhancePage } from '@/pages/Enhance';
import { CalibratePage } from '@/pages/Calibrate';
import { AlignPage } from '@/pages/Align';
import { ReviewPage } from '@/pages/Review';
import { KaraokePage } from '@/pages/Karaoke';
import { SingersPage } from '@/pages/Singers';
import { ExportPage } from '@/pages/Export';
import { SimpleApp } from '@/pages/simple/SimpleApp';
import { loadSettings, loadTasks, setUi, startTaskPolling, taskOnProject, useSimple } from '@/store/simple';
import { Button, Callout } from '@/components/ui';

const PAGES = {
  mode: ModePage,
  input: InputPage,
  enhance: EnhancePage,
  calibrate: CalibratePage,
  align: AlignPage,
  review: ReviewPage,
  singers: SingersPage,
  karaoke: KaraokePage,
  export: ExportPage,
};

/** First load: app info, projects and the project open last time. */
async function startup() {
  await Promise.all([loadInfo(), loadProjects()]);
  let last: string | null = null;
  try { last = localStorage.getItem('kara.pid'); } catch { /* ignore */ }
  if (last && !useApp.getState().pid && useApp.getState().projects.some((p) => p.id === last)) await openProject(last);
}

export default function App() {
  const pid = useApp((s) => s.pid);
  const step = useApp((s) => s.step);
  const ui = useSimple((s) => s.ui);

  // the simple mode's queue is watched in both modes (notifications, open project refresh)
  useEffect(() => { startTaskPolling(); }, []);

  useEffect(() => {
    void run(startup, '无法连接本地服务');
    void loadUpdate().catch(() => undefined);  // (quietly: offline, or the check turned off)
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (useSimple.getState().ui === 'simple' || !useApp.getState().pid) return;
      // Space / Enter on a focused control, arrows in radio groups, anything in a dialog or an IME: not ours
      if (ignoreShortcut(e)) return;
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        void (e.shiftKey ? redo() : undo());
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '?' || (e.code === 'Slash' && e.shiftKey)) {  // (some layouts report / with Shift)
        e.preventDefault();
        setShortcutsOpen(true);
        return;
      }
      if (e.code === 'Space' || e.key === ' ') {
        e.preventDefault();
        player.toggle();
      } else if (e.key.toLowerCase() === 'l' && !e.shiftKey) {
        if (!player.toggleLoop()) toast('info', '请先在波形上拖选循环区间');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const Page = pid ? PAGES[step] : HomePage;

  if (ui === 'simple') {
    return (
      <TooltipProvider>
        <div className="flex h-full flex-col">
          <ConnectionBanner />
          <div className="min-h-0 flex-1"><SimpleApp /></div>
        </div>
        <Toaster />
      </TooltipProvider>
    );
  }

  return (
    <TooltipProvider>
      <div className="flex h-full flex-col">
        <ConnectionBanner />
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <Topbar />
            {/* keyed: another step / project starts at the top of the page, not where the last one was scrolled to */}
            <main key={`${pid}-${step}`} className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto max-w-6xl animate-slide-up px-6 py-8 xl:px-10">
                <ErrorBoundary resetKey={`${pid}-${step}`}>
                  <TaskBusyBanner />
                  <Page />
                </ErrorBoundary>
              </div>
            </main>
            <ErrorBoundary resetKey={pid ?? ''}>
              <StudioDock />
            </ErrorBoundary>
          </div>
        </div>
      </div>
      <ShortcutsDialog />
      <Toaster />
    </TooltipProvider>
  );
}

/** The local server does not answer: say so until it does, with a retry. */
export function ConnectionBanner() {
  const down = useConnection((s) => s.down);
  const [busy, setBusy] = useState(false);
  if (!down) return null;
  const retry = async () => {
    setBusy(true);
    try {
      const s = useApp.getState();
      if (!s.info || !s.projects.length) await startup();
      else await Promise.all([loadProjects(), refreshProject()]);
      await loadTasks().catch(() => undefined);
      if (!useSimple.getState().settings) await loadSettings().catch(() => undefined);
      toast('ok', '已重新连接本地服务');
    } catch {
      // still down: the banner stays
    } finally {
      setBusy(false);
    }
  };
  return (
    <div role="alert" className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-danger/40 bg-danger-soft px-4 py-2 text-[13px] text-fg">
      <WifiOff className="size-4 shrink-0 text-danger" />
      <span className="min-w-0 flex-1">
        <b className="font-semibold">无法连接本地服务。</b>
        请确认运行 <code className="rounded bg-surface px-1 font-mono text-xs">milikara serve</code> 的终端仍在运行；恢复后会自动继续，也可以手动重试。
      </span>
      <Button size="xs" variant="secondary" loading={busy} onClick={() => void retry()}>重试连接</Button>
    </div>
  );
}

/** A simple-mode task is still working on the open project: say so, and what waits for it. */
export function TaskBusyBanner() {
  const pid = useApp((s) => s.pid);
  const task = taskOnProject(useSimple((s) => s.tasks), pid);
  if (!task) return null;
  const doing = { waiting: '等待确认开头位置', running: task.message || '处理中', preparing: '读取视频和歌词', queued: '排队中' }[
    task.status as 'waiting' | 'running' | 'preparing' | 'queued'] ?? '处理中';
  return (
    <Callout tone="warn" className="mb-6" title={`极简模式任务「${task.name || task.media_filename}」正在处理这个项目（${doing}）`}
      actions={<Button size="sm" variant="secondary" onClick={() => setUi('simple')}>查看任务队列</Button>}>
      完成前这里不能对齐、人声分离、AI 注音或生成视频；任务每完成一步，这里会自动更新。
    </Callout>
  );
}
