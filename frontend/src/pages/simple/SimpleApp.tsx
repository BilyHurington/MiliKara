// 极简模式外壳：只有“制作”和“设置”两页，没有工作流侧栏和播放器。
// 需要精细调整时切到详细模式（完成的任务点开也会进入详细模式）。

import { Moon, Settings2, SlidersHorizontal, Sparkles, Sun } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef } from 'react';
import { cn } from '@/lib/format';
import { run, setTheme, useApp } from '@/store/app';
import { loadSettings, setSimplePage, setUi, useSimple, type SimplePage } from '@/store/simple';
import { UpdateBadge } from '@/components/shell/UpdateBadge';
import { ErrorBoundary } from '@/components/shell/ErrorBoundary';
import { SimpleHome } from './SimpleHome';
import { SimpleSettings } from './SimpleSettings';
import { HelpButton } from '@/components/guide/Help';

export function SimpleApp() {
  const page = useSimple((s) => s.page);
  const settings = useSimple((s) => s.settings);
  const theme = useApp((s) => s.theme);
  const mainRef = useRef<HTMLElement>(null);
  const scrolls = useRef<Partial<Record<SimplePage, number>>>({});
  const shown = useRef(page);
  // the position is taken as the page changes (the old page is still on screen then), not from
  // scroll events, which a window in the background may not send
  useEffect(() => useSimple.subscribe((s, prev) => {
    if (s.page !== prev.page && mainRef.current) scrolls.current[prev.page] = mainRef.current.scrollTop;
  }), []);
  useLayoutEffect(() => {
    if (shown.current === page) return;
    shown.current = page;
    if (mainRef.current) mainRef.current.scrollTop = scrolls.current[page] ?? 0;
  }, [page]);

  useEffect(() => {
    if (!settings) void run(() => loadSettings(), '读取设置失败');
  }, [settings]);

  const nav: { id: SimplePage; label: string; icon: React.ReactNode }[] = [
    { id: 'home', label: '制作', icon: <Sparkles className="size-4" /> },
    { id: 'settings', label: '设置', icon: <Settings2 className="size-4" /> },
  ];

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-3 sm:px-6">
        <img src="/logo.png" alt="" aria-hidden className="size-9 shrink-0 object-contain" />
        <div className="mr-2 hidden sm:block">
          <div className="text-[15px] leading-5 font-semibold tracking-tight">MiliKara</div>
          <div className="text-[11px] text-muted">一键卡拉OK</div>
        </div>
        <nav className="flex items-center gap-1" aria-label="极简模式">
          {nav.map((n) => (
            <button key={n.id} onClick={() => setSimplePage(n.id)} aria-current={page === n.id ? 'page' : undefined}
              className={cn('focus-ring flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition',
                page === n.id ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-surface-2 hover:text-fg')}>
              {n.icon}{n.label}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          <UpdateBadge className="mr-1" />
          <button onClick={() => setUi('pro')} title="切换到详细模式：逐步操作、人工检查、精细调整字幕"
            className="focus-ring flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium text-muted transition hover:bg-surface-2 hover:text-fg">
            <SlidersHorizontal className="size-4" />详细模式
          </button>
          <HelpButton k="ui.modes" />
          <button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label="切换主题"
            className="focus-ring grid size-8 place-items-center rounded-lg text-muted transition hover:bg-surface-2 hover:text-fg">
            {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
        </div>
      </header>
      {/* each page keeps its own scroll position (going back to 制作 returns to where you were on it) */}
      <main ref={mainRef} className="min-h-0 flex-1 overflow-y-auto">
        <div key={page} className="mx-auto max-w-4xl animate-slide-up px-4 py-8 sm:px-6">
          <ErrorBoundary resetKey={page}>
            {page === 'home' ? <SimpleHome /> : <SimpleSettings />}
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
