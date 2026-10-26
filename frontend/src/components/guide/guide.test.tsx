// The in-app guide: the "?" popovers, first-time tips, the steps overview and the shortcuts sheet.

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { act, fireEvent, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HELP, STEP_GUIDE, TUTORIAL } from '@/lib/guide';
import { STEPS, useApp } from '@/store/app';
import { renderUI } from '@/test/helpers';
import { FirstTip, HelpButton, resetTips } from './Help';
import { isShortcutsKey, setShortcutsOpen, ShortcutsDialog } from './Shortcuts';
import { StepsHelp, StepsOverviewCard } from './StepsOverview';

beforeEach(() => {
  localStorage.removeItem('kara.guide.dismissed');
  resetTips();
});

describe('guide', () => {
  it('a "?" shows its points and the part of the tutorial', () => {
    renderUI(<HelpButton k="enhance.readings" />);
    fireEvent.click(screen.getByRole('button', { name: '说明：读音' }));
    const pop = screen.getByRole('dialog', { name: '读音' });
    expect(pop).toHaveTextContent('显示“？”的词还没有读音');
    expect(screen.getByRole('link', { name: /查看教程里的完整说明/ })).toHaveAttribute('href', `${TUTORIAL}#注音与分离`);
  });

  it('every link goes to a heading the tutorial has', () => {
    const md = readFileSync(resolve(process.cwd(), '../docs/tutorial.md'), 'utf8')  // (tests run in frontend/);
    // GitHub's anchors: lower case, punctuation dropped, spaces to hyphens
    const anchors = new Set(md.split('\n').filter((l) => /^#{1,4} /.test(l))
      .map((l) => `#${l.replace(/^#+ /, '').trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/g, '-')}`));
    for (const [k, h] of Object.entries(HELP)) {
      if ('doc' in h && h.doc) expect(anchors, k).toContain(h.doc);
    }
  });

  it('a tip stays away once dismissed, and comes back when tips are shown again', () => {
    const { unmount } = renderUI(<FirstTip k="review.first" />);
    expect(screen.getByRole('note', { name: '怎么检查' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /知道了/ }));
    expect(screen.queryByRole('note')).toBeNull();
    unmount();
    renderUI(<FirstTip k="review.first" />);
    expect(screen.queryByRole('note')).toBeNull();
    expect(localStorage.getItem('kara.guide.dismissed')).toContain('review.first');
    act(() => resetTips());
    expect(screen.getByRole('note', { name: '怎么检查' })).toBeInTheDocument();
  });

  it('the steps overview: every step, what it needs; a step can be opened from it', () => {
    expect(Object.keys(STEP_GUIDE).sort()).toEqual(STEPS.map((s) => s.id).sort());
    renderUI(<><StepsOverviewCard /><StepsHelp /></>);
    expect(screen.getByText('第一次用详细模式？')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '知道了' }));
    expect(screen.queryByText('第一次用详细模式？')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '每一步做什么' }));
    fireEvent.click(screen.getByRole('button', { name: /演唱者多人演唱时/ }));
    expect(useApp.getState().step).toBe('singers');
    expect(screen.queryByRole('dialog', { name: '每一步做什么' })).toBeNull();  // closed once a step is picked
  });

  it('a dismissed tip stays dismissed after the page is loaded again', async () => {
    localStorage.setItem('kara.guide.dismissed', JSON.stringify(['simple.welcome']));
    vi.resetModules();
    const fresh = await import('./Help');
    renderUI(<><fresh.FirstTip k="simple.welcome" /><fresh.FirstTip k="review.first" /></>);
    expect(screen.queryByRole('note', { name: '第一次使用？' })).toBeNull();
    expect(screen.getByRole('note', { name: '怎么检查' })).toBeInTheDocument();
  });

  it('"?" opens the sheet; not with a modifier', () => {
    expect(isShortcutsKey(new KeyboardEvent('keydown', { key: '?', shiftKey: true }))).toBe(true);
    expect(isShortcutsKey(new KeyboardEvent('keydown', { key: '?', ctrlKey: true }))).toBe(false);
    expect(isShortcutsKey(new KeyboardEvent('keydown', { key: '/', shiftKey: true, code: 'Slash' }))).toBe(false);
  });

  it('the shortcuts sheet shows the keys of the page with the common ones', () => {
    useApp.setState({ step: 'review' });
    renderUI(<ShortcutsDialog />);
    act(() => setShortcutsOpen(true));
    const sheet = screen.getByRole('dialog', { name: '快捷键' });
    expect(sheet).toHaveTextContent('人工检查');
    expect(sheet).toHaveTextContent('循环试听所选单元');
    expect(sheet).toHaveTextContent('所有页面');
    expect(sheet).not.toHaveTextContent('标记所选这一句的开头');
    act(() => setShortcutsOpen(false));
  });
});
