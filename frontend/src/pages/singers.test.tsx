// The 演唱者 page: selecting lines / words, number keys (1 + 2 = one undo step), undo, the singer list.

import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { player } from '@/audio/player';
import type { KaraokeStyle, ProjectView } from '@/lib/types';
import { useApp } from '@/store/app';
import { undo } from '@/store/edits';
import { flushSingers } from '@/store/singers';
import { stepStatus } from '@/store/steps';
import { fixturePV, mockApi, renderUI, seedStore } from '@/test/helpers';
import { plainStyle } from '@/test/style';
import { SingersPage } from './Singers';

const PID = 'proj';

function withSingers(): KaraokeStyle {
  const st = plainStyle();
  st.singers = {
    members: [
      { name: 'Ann', key: '1', color: '#ED35B3', color_unsung: '', color_sung: '', outline_color: '', glow_unsung: '', glow_sung: '' },
      { name: 'Bo', key: '2', color: '#2F80ED', color_unsung: '', color_sung: '', outline_color: '', glow_unsung: '', glow_sung: '' },
    ],
    mix: 'split', direction: 'vertical',
  };
  return st;
}

/** A server that stores what is sent (the project view follows every assignment). */
function server() {
  const pv: ProjectView = fixturePV();
  pv.project.karaoke = withSingers();
  const api = mockApi({
    [`GET /api/projects/${PID}/karaoke`]: () => pv.project.karaoke,
    [`PUT /api/projects/${PID}/karaoke/singers`]: (c) => { pv.project.karaoke = { ...pv.project.karaoke!, singers: c.body }; return pv.project.karaoke; },
    [`DELETE /api/projects/${PID}/karaoke/singers/`]: () => {
      pv.project.karaoke!.singers!.members.shift();
      return { ...structuredClone(pv), changed: 0 };
    },
    [`PUT /api/projects/${PID}/singers`]: (c) => {
      for (const x of c.body.lines) {
        const l = pv.project.lyrics.lines.find((y) => y.id === x.line_id)!;
        l.singers = x.singers;
        l.singer_spans = x.spans;
      }
      return structuredClone(pv);
    },
    'POST /api/karaoke/singer-colors': () => [],
    'GET /api/karaoke/singer-presets': () => [],
    [`POST /api/projects/${PID}/karaoke/preview`]: () => new Response(new Blob(['png']), { status: 200 }),
  });
  return { api, pv };
}

const press = (key: string, opts: Partial<KeyboardEventInit> = {}) => act(() => { fireEvent.keyDown(window, { key, ...opts }); });

beforeEach(() => {
  player.reset();
});

describe('singers page', () => {
  it('whole lines by number keys; 1 + 2 is one undo step; undo sends what was there before', async () => {
    const pv = seedStore('singers');
    const { api } = server();
    renderUI(<SingersPage />);
    expect(await screen.findByRole('heading', { level: 1, name: '演唱者' })).toBeInTheDocument();
    expect(await screen.findByDisplayValue('Ann')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '选择第 1 行' }));
    expect(screen.getByRole('status')).toHaveTextContent('已选 1 行');
    press('1');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(1));
    expect(api.find('PUT', `${PID}/singers`)[0].body.lines).toEqual([{ line_id: 'L0001', text: pv.project.lyrics.lines[0].text, singers: [1], spans: [] }]);
    press('+');
    press('2');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(2));
    expect(api.find('PUT', `${PID}/singers`)[1].body.lines[0].singers).toEqual([1, 2]);
    expect(useApp.getState().undo).toHaveLength(1);
    expect(useApp.getState().undo[0].label).toBe('指定 Ann + Bo');
    expect(useApp.getState().pv!.project.lyrics.lines[0].singers).toEqual([1, 2]);

    await act(() => undo());
    const last = api.find('PUT', `${PID}/singers`).at(-1)!;
    expect(last.body.lines).toEqual([{ line_id: 'L0001', text: pv.project.lyrics.lines[0].text, singers: [], spans: [] }]);
    expect(useApp.getState().redo).toHaveLength(1);
  });

  it('words across lines (Shift), a singer the style lacks, clearing', async () => {
    seedStore('singers');
    const { api } = server();
    const { container } = renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    const word = (li: number, wi: number) => container.querySelector(`[data-word="${li}:${wi}"]`)!;
    // 道 (the last word of line 1) … 夜空 (the first of line 2)
    fireEvent.mouseDown(word(0, 4), { button: 0 });
    fireEvent.mouseUp(window);
    fireEvent.mouseDown(word(1, 0), { button: 0, shiftKey: true });
    expect(screen.getByRole('status')).toHaveTextContent('已选 2 行中的部分歌词');
    press('3');  // only two singers
    press('2');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(1));
    const lines = api.find('PUT', `${PID}/singers`)[0].body.lines;
    expect(lines.map((l: any) => [l.line_id, l.singers, l.spans])).toEqual([
      ['L0001', [], [{ start: 5, end: 6, singers: [2] }]],
      ['L0002', [], [{ start: 0, end: 2, singers: [2] }]],
    ]);
    press('0');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(2));
    expect(api.find('PUT', `${PID}/singers`)[1].body.lines.every((l: any) => !l.spans.length)).toBe(true);
    expect(useApp.getState().undo).toHaveLength(2);
  });

  it('1 + 2 can be saved to key 3, which then assigns both', async () => {
    seedStore('singers');
    const { api } = server();
    renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    fireEvent.click(screen.getByRole('button', { name: '选择第 1 行' }));
    press('1');
    // "+" and "2" typed faster than the page re-renders
    act(() => { fireEvent.keyDown(window, { key: '+' }); fireEvent.keyDown(window, { key: '2' }); });
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(2));
    expect(api.find('PUT', `${PID}/singers`)[1].body.lines[0].singers).toEqual([1, 2]);
    fireEvent.click(await screen.findByRole('button', { name: /把 1\+2 存到 3/ }));
    await act(() => flushSingers());
    expect(api.find('PUT', '/karaoke/singers').at(-1)!.body.combos).toEqual([{ key: '3', singers: [1, 2] }]);
    fireEvent.click(screen.getByRole('button', { name: '选择第 2 行' }));
    press('3');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(3));
    expect(api.find('PUT', `${PID}/singers`)[2].body.lines[0]).toMatchObject({ line_id: 'L0002', singers: [1, 2] });
    expect(screen.getByRole('textbox', { name: '组合 1+2 的演唱者' })).toHaveValue('1+2');
  });

  it('a key can be changed: click it, press the new one (taken: they swap); letters work too', async () => {
    seedStore('singers');
    const { api } = server();
    renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    const annKey = screen.getByRole('button', { name: /Ann的快捷键：1/ });
    fireEvent.click(annKey);
    fireEvent.keyDown(annKey, { key: 'l' });  // loop: not usable, the button keeps listening
    fireEvent.keyDown(annKey, { key: 'A' });
    await act(() => flushSingers());
    expect(api.find('PUT', '/karaoke/singers').at(-1)!.body.members.map((m: { key: string }) => m.key)).toEqual(['a', '2']);
    // the page did not take that "A" as a shortcut (nothing selected, nothing assigned)
    expect(api.find('PUT', `${PID}/singers`)).toHaveLength(0);
    const boKey = screen.getByRole('button', { name: /Bo的快捷键：2/ });
    fireEvent.click(boKey);
    fireEvent.keyDown(boKey, { key: 'a' });
    await act(() => flushSingers());
    expect(api.find('PUT', '/karaoke/singers').at(-1)!.body.members.map((m: { key: string }) => m.key)).toEqual(['2', 'a']);
    // pressing a on the lyrics now assigns Bo
    fireEvent.click(screen.getByRole('button', { name: '选择第 1 行' }));
    press('a');
    await waitFor(() => expect(api.find('PUT', `${PID}/singers`)).toHaveLength(1));
    expect(api.find('PUT', `${PID}/singers`)[0].body.lines[0].singers).toEqual([2]);
    // more than nine singers
    for (let i = 0; i < 9; i++) fireEvent.click(screen.getByRole('button', { name: '添加演唱者' }));
    await act(() => flushSingers());
    expect(api.find('PUT', '/karaoke/singers').at(-1)!.body.members.map((m: { key: string }) => m.key).join(''))
      .toBe('2a13456789b');
  });

  it('a combination gets its own look; the reading colour is a setting of all parts sung together', async () => {
    seedStore('singers');
    const { api, pv } = server();
    pv.project.karaoke!.singers!.combos = [{ key: '3', singers: [1, 2] }];
    renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    const look = screen.getByRole('combobox', { name: '组合 1+2 的效果' });
    expect(look).toHaveValue('');  // follows the default
    expect(screen.getByRole('option', { name: '默认（上下分色）' })).toBeInTheDocument();
    fireEvent.change(look, { target: { value: 'gradient-horizontal' } });
    fireEvent.click(screen.getByRole('radio', { name: '跟着分色' }));
    await act(() => flushSingers());
    const sent = api.find('PUT', '/karaoke/singers').at(-1)!.body;
    expect(sent.combos).toEqual([{ key: '3', singers: [1, 2], mix: 'gradient', direction: 'horizontal' }]);
    expect(sent.ruby).toBe('split');
    fireEvent.change(look, { target: { value: '' } });
    await act(() => flushSingers());
    expect(api.find('PUT', '/karaoke/singers').at(-1)!.body.combos[0]).toMatchObject({ mix: null, direction: null });
    // a new combination takes two singers without one yet (here: 2 on top of 1); the same singers twice are refused
    fireEvent.click(screen.getByRole('button', { name: '添加组合' }));
    const second = screen.getByRole('textbox', { name: '组合 2+1 的演唱者' });
    fireEvent.change(second, { target: { value: '1+2' } });
    fireEvent.blur(second);
    expect(second).toHaveValue('2+1');
    await waitFor(() => expect(useApp.getState().toasts.map((t) => t.title)).toContain('组合 1+2 已经有了'));
    fireEvent.click(screen.getByRole('button', { name: '添加组合' }));
    await waitFor(() => expect(useApp.getState().toasts.map((t) => t.title)).toContain('每两位演唱者都已经有组合了'));
  });

  it('short names, a name for a combination, the legend and who sings each line', async () => {
    seedStore('singers');
    const { api, pv } = server();
    pv.project.karaoke!.singers!.combos = [{ key: '3', singers: [1, 2] }];
    renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    fireEvent.change(screen.getByRole('textbox', { name: 'Ann的简称' }), { target: { value: 'A' } });
    fireEvent.change(screen.getByRole('textbox', { name: '组合 1+2 的名字' }), { target: { value: '一起' } });
    fireEvent.click(screen.getByRole('radio', { name: '句首' }));
    fireEvent.click(screen.getByRole('switch', { name: '开头显示分色列表' }));
    await act(() => flushSingers());
    const sent = api.find('PUT', '/karaoke/singers').at(-1)!.body;
    expect(sent.members[0].label).toBe('A');
    expect(sent.combos[0].label).toBe('一起');
    expect(sent.labels).toBe('start');
    expect(sent.legend).toBe(false);
    expect(screen.getByText(/两人一起唱分上下两小行/)).toBeInTheDocument();
  });

  it('a saved set of singers is saved and loaded', async () => {
    seedStore('singers');
    const { pv } = server();
    const presets: unknown[] = [];
    const routes = mockApi({  // (in place of the server's routes: presets too)
      [`GET /api/projects/${PID}/karaoke`]: () => pv.project.karaoke,
      'GET /api/karaoke/singer-presets': () => presets,
      'POST /api/karaoke/singer-presets': (c) => { const p = { id: 'sp1', name: c.body.name, updated: 'z', singers: c.body.singers }; presets.push(p); return p; },
      [`POST /api/projects/${PID}/karaoke/singers/preset`]: () => ({ ...structuredClone(pv), lines: 3, kept: [] }),
      'POST /api/karaoke/singer-colors': () => [],
      [`POST /api/projects/${PID}/karaoke/preview`]: () => new Response(new Blob(['png']), { status: 200 }),
    });
    renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    fireEvent.click(screen.getByRole('button', { name: '存为预设' }));
    expect(screen.getByRole('textbox', { name: '演唱者预设名称' })).toHaveValue('Ann、Bo');
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(routes.find('POST', '/api/karaoke/singer-presets')[0]?.body.name).toBe('Ann、Bo'));
    expect(routes.find('POST', '/api/karaoke/singer-presets')[0].body.singers.members[0].key).toBe('1');
    await waitFor(() => expect(screen.getByRole('combobox', { name: '演唱者预设' })).toHaveValue('sp1'));
    fireEvent.click(screen.getByRole('button', { name: '载入' }));
    fireEvent.click(screen.getByRole('button', { name: '载入' }));  // (asks first: there are singers already)
    await waitFor(() => expect(routes.find('POST', '/karaoke/singers/preset')[0]?.body).toEqual({ id: 'sp1' }));
    await waitFor(() => expect(useApp.getState().toasts.map((t) => t.title)).toContain('已使用演唱者预设「Ann、Bo」'));
  });

  it('double-clicking a word goes there; the row being sung is marked', async () => {
    seedStore('singers');
    server();
    const { container } = renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    // 星 in line 2 (夜空に星が光る) is sung from 6180 ms
    fireEvent.doubleClick(container.querySelector('[data-word="1:2"]')!);
    expect(player.positionMs()).toBe(6180);
    await waitFor(() => expect(container.querySelector('[data-row][data-playing]')?.getAttribute('data-row')).toBe('L0002'));
  });

  it('selecting with the mouse leaves no control focused, so Space plays', async () => {
    seedStore('singers');
    server();
    const { container } = renderUI(<SingersPage />);
    const name = await screen.findByDisplayValue('Ann');
    name.focus();
    const line = screen.getByRole('button', { name: '选择第 1 行' });
    fireEvent.mouseDown(line);
    fireEvent.click(line);
    expect(screen.getByRole('status')).toHaveTextContent('已选 1 行');
    expect(document.activeElement).toBe(document.body);
    line.focus();
    fireEvent.mouseDown(container.querySelector('[data-word="1:2"]')!);
    expect(document.activeElement).toBe(document.body);
  });

  it('a space cannot be selected', async () => {
    const pv = fixturePV();
    const l = pv.project.lyrics.lines[0];
    l.text = '君と for';
    l.segments = [l.segments[0], l.segments[1], { ...l.segments[1], id: 'sp', surface: ' ', units: [] },
      { ...l.segments[1], id: 'en', surface: 'for', units: [] }];
    seedStore('singers', pv);
    server();
    const { container } = renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    const space = [...container.querySelectorAll('[data-text="L0001"] span')].find((x) => x.textContent === ' ')!;
    expect(space.hasAttribute('data-word')).toBe(false);
    fireEvent.mouseDown(space, { button: 0 });
    fireEvent.mouseUp(window);
    expect(screen.getByRole('status')).toHaveTextContent('未选择');
    // a drag from と to "for" takes the space between, but a selection never starts or ends on one
    fireEvent.mouseDown(container.querySelector('[data-word="0:1"]')!, { button: 0 });
    fireEvent.mouseUp(window);
    fireEvent.mouseDown(container.querySelector('[data-word="0:3"]')!, { button: 0, shiftKey: true });
    expect(space.className).not.toContain('outline');
  });

  it('the preview shows the selection half-way sung, allowing for the lyrics shown ahead', async () => {
    seedStore('singers');
    const { api, pv } = server();
    pv.project.karaoke!.timing.advance_ms = 150;
    // (jsdom has no object URLs for the returned picture)
    URL.createObjectURL = () => 'blob:preview';
    URL.revokeObjectURL = () => undefined;
    const { container } = renderUI(<SingersPage />);
    await screen.findByDisplayValue('Ann');
    // 星 (line 2) is sung 6180–6440 ms: half-way at 6310, drawn in the frame at 6310 − 150
    fireEvent.mouseDown(container.querySelector('[data-word="1:2"]')!, { button: 0 });
    fireEvent.mouseUp(window);
    await waitFor(() => expect(api.find('POST', '/karaoke/preview').length).toBeGreaterThan(0), { timeout: 2000 });
    expect(api.find('POST', '/karaoke/preview').at(-1)!.body.t_ms).toBe(6160);
    fireEvent.click(screen.getByRole('radio', { name: '唱完' }));
    await waitFor(() => expect(api.find('POST', '/karaoke/preview').at(-1)!.body.t_ms).toBe(6290), { timeout: 2000 });
  });

  it('the singer list is saved; the step shows who sings', async () => {
    seedStore('singers');
    const { api, pv } = server();
    renderUI(<SingersPage />);
    const name = await screen.findByDisplayValue('Bo');
    fireEvent.change(name, { target: { value: 'Bob' } });
    await act(() => flushSingers());
    const put = api.find('PUT', '/karaoke/singers');
    expect(put.at(-1)!.body.members[1].name).toBe('Bob');
    pv.project.lyrics.lines[0].singers = [2];
    expect(stepStatus('singers', pv, new Set())).toEqual({ state: 'done', note: '2 位演唱者 · 1 行' });
    fireEvent.click(screen.getByRole('button', { name: '添加演唱者' }));
    await act(() => flushSingers());
    expect(api.find('PUT', '/karaoke/singers').at(-1)!.body.members).toHaveLength(3);
  });

  it('lyrics that name their singers: pick the real names, assign and take them out', async () => {
    const pv = fixturePV();
    pv.view.singer_markers = 2;
    seedStore('singers', pv);
    const routes = mockApi({
      [`GET /api/projects/${PID}/karaoke`]: () => withSingers(),
      'POST /api/karaoke/singer-colors': () => [],
    'GET /api/karaoke/singer-presets': () => [],
      [`GET /api/projects/${PID}/singers/markers`]: () => ({
        lines: [
          { line_id: 'L0001', text: 'A：君と歩いた道', prefix: 'A：', names: ['A'], everyone: false },
          { line_id: 'L0002', text: 'Hey：夜空に星が光る', prefix: 'Hey：', names: ['Hey'], everyone: false },
        ],
        names: ['A', 'Hey'], existing: [],
      }),
      [`POST /api/projects/${PID}/singers/markers`]: () => ({ ...fixturePV(), messages: ['已按标记给 1 行指定演唱者：A'] }),
    });
    renderUI(<SingersPage />);
    fireEvent.click(await screen.findByRole('button', { name: '识别并指定…' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Hey' }));
    fireEvent.click(screen.getByRole('button', { name: /指定 2 行/ }));
    await waitFor(() => expect(routes.find('POST', '/singers/markers')).toHaveLength(1));
    expect(routes.find('POST', '/singers/markers')[0].body).toEqual({ names: ['A'], strip: true });
  });
});
