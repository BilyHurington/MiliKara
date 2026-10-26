// Fifth review round (frontend): player loop and seeks, lazy decoding and
// project switches, shortcut / IME filtering, <Field> groups, restored job
// outputs, project-switch resets, colour-template request ordering, undo
// robustness, readable errors and the waveform's on-demand drawing.

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { player } from '@/audio/player';
import { Waveform, type Overlays } from '@/audio/waveform';
import { errorText, readableError, useConnection, validationMessage } from '@/lib/api';
import { ignoreShortcut, isEnter } from '@/lib/keys';
import type { Job, KaraokeStyle, ProjectView, ThemePreview } from '@/lib/types';
import {
  adoptProject, closeProject, patchResult, resultFrom, selectResult, setPV, toast, trackJob, useApp, type UndoEntry,
} from '@/store/app';
import { redo, setUnitTimes, undo } from '@/store/edits';
import { Field, Segmented } from '@/components/ui';
import { mergeThemeColors, StylePanel } from '@/components/karaoke/StylePanel';
import { ConnectionBanner } from '@/App';
import { fixturePV, mockApi, renderUI, seedStore, tick } from '@/test/helpers';
import { builtinSaved, plainStyle } from '@/test/style';
import { detectLyrics } from './simple/SimpleHome';
import { HomePage } from './Home';

const PID = 'proj';
const wav = () => new Response(new ArrayBuffer(8), { status: 200 });

/** Player with the fixture project's assets; returns the fetch mock. */
async function loadPlayer(pv = fixturePV()) {
  const api = mockApi({ 'GET /api/projects/': () => wav() });
  await act(async () => { await player.syncAssets(pv.project.id, pv); });
  return api;
}

/** Pretend `ms` of audio have played since the last start. */
function elapse(ms: number) {
  (player.ctx as any).currentTime = player.t0 + ms / 1000 / player.rate;
}

beforeEach(() => {
  player.reset();
  useConnection.setState({ down: false, since: null });
});
afterEach(() => { vi.unstubAllGlobals(); });

// ------------------------------------------------------------------ player

describe('player loop (F-H1 / F-H2)', () => {
  it('turning the loop off keeps the position inside the loop', async () => {
    await loadPlayer();
    player.setLoop(1000, 2000);
    player.play(1500);
    elapse(2200);  // 1500 + 2200 = 3700 → wrapped to 1700
    expect(Math.round(player.positionMs())).toBe(1700);
    player.toggleLoop(false);
    expect(player.loop.on).toBe(false);
    expect(Math.round(player.positionMs())).toBe(1700);  // not 3700, not 0
  });

  it('clearing the loop range keeps the position as well', async () => {
    await loadPlayer();
    player.setLoop(1000, 2000);
    player.play(1200);
    elapse(1300);  // → 1500
    player.setLoop(null, null);
    expect(player.loop).toEqual({ on: false, start: null, end: null });
    expect(Math.round(player.positionMs())).toBe(1500);
  });

  it('explicit seeks and plays outside the loop turn it off; inside they keep it', async () => {
    await loadPlayer();
    player.setLoop(1000, 2000);
    player.seek(1500);
    expect(player.loop.on).toBe(true);
    player.seek(8000);
    expect(player.loop.on).toBe(false);
    expect(player.loop.start).toBe(1000);  // the range stays for L
    expect(player.toggleLoop(true)).toBe(true);
    player.play(9000);
    expect(player.loop.on).toBe(false);
    expect(player.offsetMs).toBe(9000);
    // only playRange sets a loop
    player.playRange(3000, 4000);
    expect(player.loop).toEqual({ on: true, start: 3000, end: 4000 });
  });
});

describe('player decoding and project switches (F-M7 / F-M10)', () => {
  it('decodes only the current source; the mix needs both stems', async () => {
    const pv = fixturePV();
    const api = await loadPlayer(pv);
    const id = (role: string) => pv.project.audio.find((a) => a.role === role)!.id;
    expect(api.find('GET', 'playback.wav').map((c) => c.url)).toEqual([`/api/projects/${PID}/audio/${id('original')}/playback.wav`]);
    expect(player.availableSources()).toEqual(['original', 'vocals', 'instrumental', 'mix']);  // known without decoding
    await act(async () => { player.setSource('mix'); await tick(20); });
    const urls = api.find('GET', 'playback.wav').map((c) => c.url);
    expect(urls).toContain(`/api/projects/${PID}/audio/${id('vocals')}/playback.wav`);
    expect(urls).toContain(`/api/projects/${PID}/audio/${id('instrumental')}/playback.wav`);
    expect(Object.keys(player.buffers).sort()).toEqual(['instrumental', 'vocals']);  // the original was freed
  });

  it('another project stops playback and resets position and loop; a replaced original too', async () => {
    const pv = fixturePV();
    await loadPlayer(pv);
    player.setLoop(1000, 2000);
    player.play(1500);
    expect(player.playing).toBe(true);
    const other = fixturePV();
    other.project.id = 'other';
    await act(async () => { await player.syncAssets('other', other); });
    expect(player.playing).toBe(false);
    expect(player.offsetMs).toBe(0);
    expect(player.loop.on).toBe(false);

    player.seek(5000);
    player.setLoop(1000, 2000);
    const replaced = structuredClone(other);
    replaced.project.audio.find((a) => a.role === 'original')!.id = 'new-original';
    await act(async () => { await player.syncAssets('other', replaced); });
    expect(player.offsetMs).toBe(0);
    expect(player.loop).toEqual({ on: false, start: null, end: null });
  });

  it('outdated stems are not offered', async () => {
    const pv = fixturePV();
    pv.view.audio.vocals = { ...pv.view.audio.vocals!, available: false, outdated: true };
    await loadPlayer(pv);
    expect(player.availableSources()).toEqual(['original', 'instrumental']);
  });
});

// ------------------------------------------------------------------ keyboard

function key(target: Element | Window, init: KeyboardEventInit) {
  const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  Object.defineProperty(e, 'target', { value: target });
  return e;
}

describe('shortcut filtering (F-M1) and IME Enter (F-M2)', () => {
  it('leaves Space / Enter to focused controls, arrows to radio groups, and everything in dialogs or repeats', () => {
    document.body.innerHTML = `<button id="b">x</button><div role="radiogroup"><button role="radio" id="r">a</button></div>
      <div role="switch" id="s" tabindex="0"></div><input id="i" /><div id="plain"></div>`;
    const el = (id: string) => document.getElementById(id)!;
    expect(ignoreShortcut(key(el('b'), { key: ' ', code: 'Space' }))).toBe(true);
    expect(ignoreShortcut(key(el('s'), { key: 'Enter' }))).toBe(true);
    expect(ignoreShortcut(key(el('plain'), { key: ' ', code: 'Space' }))).toBe(false);
    expect(ignoreShortcut(key(document.body, { key: ' ', code: 'Space' }))).toBe(false);
    // letters have no meaning on a button: L / M still work there, not while typing
    expect(ignoreShortcut(key(el('b'), { key: 'l' }))).toBe(false);
    expect(ignoreShortcut(key(el('i'), { key: 'l' }))).toBe(true);
    // arrows move inside radio groups
    expect(ignoreShortcut(key(el('r'), { key: 'ArrowDown' }))).toBe(true);
    expect(ignoreShortcut(key(el('b'), { key: 'ArrowDown' }))).toBe(false);
    // held keys and IME composition
    expect(ignoreShortcut(key(document.body, { key: 'm', repeat: true }))).toBe(true);
    expect(ignoreShortcut(key(document.body, { key: 'ArrowDown', repeat: true }), { allowRepeat: true })).toBe(false);
    expect(ignoreShortcut(key(document.body, { key: 'Enter', isComposing: true }))).toBe(true);
    // an open dialog
    document.body.innerHTML += '<div role="dialog" data-state="open"><span id="in-dialog"></span></div>';
    expect(ignoreShortcut(key(document.body, { key: ' ', code: 'Space' }))).toBe(true);
    expect(ignoreShortcut(key(el('in-dialog'), { key: 'l' }))).toBe(true);
    document.body.innerHTML = '';
  });

  it('isEnter ignores the Enter that commits an IME composition', () => {
    expect(isEnter({ key: 'Enter' })).toBe(true);
    expect(isEnter({ key: 'Enter', nativeEvent: { isComposing: true } as KeyboardEvent })).toBe(false);
    expect(isEnter({ key: 'Enter', keyCode: 229 })).toBe(false);
  });

  it('new project: Enter while composing does nothing; Enter twice creates one project', async () => {
    seedStore('mode');
    useApp.setState({ pid: null, pv: null });
    let resolve!: (v: unknown) => void;
    const created = fixturePV();
    const api = mockApi({
      'POST /api/projects': () => new Promise((r) => { resolve = r; }).then(() => created),
      'GET /api/projects/': () => [],
      'GET /api/projects': () => [],
    });
    renderUI(<HomePage />);
    const input = screen.getByRole('textbox', { name: '歌曲名' });
    fireEvent.change(input, { target: { value: 'きみ' } });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229, isComposing: true });
    expect(api.find('POST', '/api/projects')).toHaveLength(0);
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.keyDown(input, { key: 'Enter' });
    await userEvent.click(screen.getByRole('button', { name: /创建并开始/ }));
    expect(api.find('POST', '/api/projects')).toHaveLength(1);
    await act(async () => { resolve(null); await tick(10); });
    expect(useApp.getState().pid).toBe(created.project.id);
    expect(useApp.getState().step).toBe('input');
  });
});

// ------------------------------------------------------------------ Field

describe('<Field> (F-M3)', () => {
  it('around segmented buttons it is a labelled group, and clicking its text changes nothing', async () => {
    const onChange = vi.fn();
    renderUI(
      <Field group label="尾音策略" hint="说明">
        <Segmented value="off" onChange={onChange} options={[{ value: 'trim', label: '裁短' }, { value: 'off', label: '关闭' }]} />
      </Field>,
    );
    const group = screen.getByRole('group', { name: '尾音策略' });
    expect(group.tagName).toBe('DIV');
    expect(document.querySelector('label')).toBeNull();
    await userEvent.click(screen.getByText('尾音策略'));
    await userEvent.click(screen.getByText('说明'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('around one input it stays a label', () => {
    renderUI(<Field label="模型"><input /></Field>);
    expect(screen.getByLabelText('模型').tagName).toBe('INPUT');
  });

  it('segmented options: one tab stop, arrow keys move and choose', async () => {
    function Demo() {
      const [v, setV] = useState('a');
      return <Segmented value={v} onChange={setV} label="demo" options={[{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }, { value: 'c', label: 'C', disabled: true }]} />;
    }
    renderUI(<Demo />);
    const [a, b] = screen.getAllByRole('radio');
    expect(a).toHaveAttribute('tabindex', '0');
    expect(b).toHaveAttribute('tabindex', '-1');
    a.focus();
    fireEvent.keyDown(a, { key: 'ArrowRight' });
    expect(b).toHaveAttribute('aria-checked', 'true');
    fireEvent.keyDown(b, { key: 'ArrowRight' });  // skips the disabled one and wraps
    expect(a).toHaveAttribute('aria-checked', 'true');
  });
});

// ------------------------------------------------------------------ job outputs

const job = (over: Partial<Job>): Job => ({
  id: 'j1', kind: 'burn', project_id: PID, status: 'succeeded', progress: 1, message: '完成', error: null,
  created: '2026-09-26T01:00:00+00:00', finished: '2026-09-26T01:02:00+00:00', output: null, ...over,
});

describe('job outputs survive leaving the page (F-H3)', () => {
  function karaokeRoutes(jobs: Job[], exports: { filename: string; url: string; size: number; modified: string }[] = []) {
    return {
      [`GET /api/projects/${PID}/jobs`]: () => jobs,
      [`GET /api/projects/${PID}/exports`]: () => exports,
      [`GET /api/projects/${PID}/karaoke/info`]: () => ({ fields: {}, labels: {}, text: null }),
      [`GET /api/projects/${PID}/karaoke`]: () => plainStyle(),
      [`POST /api/projects/${PID}/karaoke/preview`]: () => new Response(new Blob(['png']), { status: 200 }),
      [`PUT /api/projects/${PID}/karaoke`]: () => plainStyle(),
      'GET /api/fonts': () => ({ default: 'Hiragino Sans', families: [] }),
      'GET /api/karaoke/styles': () => [builtinSaved()],
      [`GET /api/projects/${PID}`]: () => fixturePV(),
    };
  }

  it('the karaoke page shows the last video of this project with its download link', async () => {
    seedStore('karaoke');
    (URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = vi.fn();
    mockApi(karaokeRoutes([
      job({ output: { filename: 'song-karaoke.mp4', url: `/api/projects/${PID}/exports/song-karaoke.mp4`, warnings: [] } }),
      job({ id: 'other', project_id: 'someone-else', output: { filename: 'x.mp4', url: '/x', warnings: [] } }),
    ]));
    const { KaraokePage } = await import('./Karaoke');
    renderUI(<KaraokePage />);
    expect((await screen.findAllByText('song-karaoke.mp4')).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /下载视频/ })).toBeInTheDocument();
    expect(screen.queryByText('x.mp4')).toBeNull();
  });

  it('below the burn: the videos made before, the newest three, the rest folded', async () => {
    seedStore('karaoke');
    (URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = vi.fn();
    const file = (name: string, day: number) => ({ filename: name, url: `/api/projects/${PID}/exports/${name}`, size: 50 * 1024 * 1024,
      modified: `2026-09-${String(day).padStart(2, '0')}T10:00:00Z` });
    mockApi(karaokeRoutes([], [file('a-karaoke-5.mp4', 25), file('a-karaoke-4.mp4', 24), file('mix.wav', 23),
      file('a-karaoke-3.mp4', 22), file('a-karaoke-2.mp4', 21), file('a-karaoke-1.mp4', 20)]));
    const { KaraokePage } = await import('./Karaoke');
    renderUI(<KaraokePage />);
    const card = (await screen.findByText('导出过的视频')).closest('[class*="radius-card"]') as HTMLElement;
    expect(within(card).getByText('a-karaoke-5.mp4')).toBeInTheDocument();
    expect(within(card).getByText('a-karaoke-3.mp4')).toBeInTheDocument();
    expect(within(card).queryByText('a-karaoke-2.mp4')).toBeNull();
    expect(within(card).queryByText('mix.wav')).toBeNull();  // only the subtitled videos here
    fireEvent.click(within(card).getByRole('button', { name: '显示更多（还有 2 个）' }));
    expect(within(card).getByText('a-karaoke-1.mp4')).toBeInTheDocument();
    expect(within(card).getAllByRole('button', { name: /下载/ })).toHaveLength(5);
  });

  it('the export page restores the mix / video links and lists recent exports', async () => {
    const pv = fixturePV();
    seedStore('export', pv);
    mockApi({
      [`GET /api/projects/${PID}/jobs`]: () => [
        job({ id: 'm', kind: 'mix', output: { filename: 'mix.wav', url: `/api/projects/${PID}/exports/mix.wav`, report: { bus_gain: 1, peak_before: 0.5, peak_after: 0.5, clipped_samples: 0 } } }),
      ],
      [`POST /api/projects/${PID}/mix/preview-gain`]: () => ({ bus_gain: 1, peak_before: 0.5 }),
      [`GET /api/projects/${PID}`]: () => fixturePV(),
    });
    const { ExportPage } = await import('./Export');
    renderUI(<ExportPage />);
    expect(await screen.findByRole('heading', { name: '最近导出' })).toBeInTheDocument();
    expect(screen.getAllByText('mix.wav').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /下载 WAV/ })).toBeInTheDocument();
  });

  it('the calibration page restores the automatic offset suggestion', async () => {
    seedStore('calibrate');
    mockApi({
      [`GET /api/projects/${PID}/jobs`]: () => [job({ id: 'c', kind: 'calibrate', output: { shift_ms: 420, agree: 0.9, lines_checked: 10, audio_role: 'vocals', vocal_onset_ms: 1200 } })],
      [`GET /api/projects/${PID}`]: () => fixturePV(),
    });
    const { CalibratePage } = await import('./Calibrate');
    renderUI(<CalibratePage />);
    expect(await screen.findByText('+420 ms')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '应用建议' })).toBeInTheDocument();
  });

  it('a job finishing after another project was opened does not change the selection', async () => {
    const pv = seedStore('align');
    const doneJob = job({ id: 'a1', kind: 'align', output: { result_id: pv.project.results[0].id } });
    mockApi({ 'GET /api/jobs/': () => doneJob, 'GET /api/projects/': () => fixturePV() });
    const onDone = vi.fn();
    trackJob({ ...doneJob, status: 'running' }, { label: '对齐', onDone });
    const other = fixturePV();
    other.project.id = 'other';
    adoptProject(other);
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    // selecting a result of the old project is refused
    expect(selectResult(pv.project.results[0].id + 'x')).toBe(false);
    expect(useApp.getState().pid).toBe('other');
  });
});

// ------------------------------------------------------------------ project switches

describe('project switches reset selection and undo (F-M6 / F-M5)', () => {
  const entry = (rid: string): UndoEntry => ({ rid, uid: 'u', before: null, after: null, label: 'x' });

  it('closing, creating or opening another project starts clean', () => {
    const pv = seedStore('review');
    const rid = pv.project.results[0].id;
    useApp.setState({ undo: [entry(rid)], redo: [entry(rid)], selLineId: 'L0001', selUnitId: 'u', candidateId: 'c', compareWithId: rid });
    closeProject();
    let s = useApp.getState();
    expect([s.pid, s.pv, s.undo.length, s.redo.length, s.selLineId, s.selUnitId, s.candidateId, s.compareWithId, s.resultId])
      .toEqual([null, null, 0, 0, null, null, null, null, null]);

    seedStore('review');
    useApp.setState({ undo: [entry(rid)], selLineId: 'L0001' });
    const other = fixturePV();
    other.project.id = 'new';
    adoptProject(other, 'input');
    s = useApp.getState();
    expect([s.pid, s.undo.length, s.selLineId, s.step]).toEqual(['new', 0, null, 'input']);
  });

  it('an answer about another project does not switch back to it', () => {
    seedStore('review');
    const stale = fixturePV();
    stale.project.id = 'old';
    setPV(stale);
    expect(useApp.getState().pid).toBe(PID);
  });

  it('selectResult only accepts results of the open project', () => {
    const pv = seedStore('review');
    expect(selectResult('not-here')).toBe(false);
    expect(selectResult(pv.project.results[0].id)).toBe(true);
    expect(useApp.getState().resultId).toBe(pv.project.results[0].id);
  });

  it('summary counts follow manual edits (F-L2)', () => {
    const pv = seedStore('review');
    const r = resultFrom(useApp.getState().pv, pv.project.results[0].id)!;
    const units = r.units.map((u, i) => (i === 0 ? { ...u, manual: { start_ms: 1, end_ms: 2, locked: true, at: '', note: '' } } : { ...u, manual: null }));
    patchResult({ ...r, units });
    expect(useApp.getState().pv!.view.results.find((x) => x.id === r.id)!.n_manual).toBe(1);
  });
});

// ------------------------------------------------------------------ undo

describe('undo / redo (F-L1)', () => {
  it('a failed undo keeps its step; steps run in order', async () => {
    const pv = seedStore('review');
    const r = pv.project.results.find((x) => x.id === pv.project.active_result_id)!;
    const u = r.units[0];
    let fail = true;
    const order: string[] = [];
    mockApi({
      [`PUT /api/projects/${PID}/results/`]: async (c) => {
        await tick(20);
        order.push(`put ${c.body.start_ms}`);
        return { ...u, start_ms: c.body.start_ms, end_ms: c.body.end_ms, manual: { start_ms: c.body.start_ms, end_ms: c.body.end_ms, locked: true, at: '', note: '' } };
      },
      [`POST /api/projects/${PID}/results/`]: (c) => {
        order.push('restore');
        if (fail) return new Response(JSON.stringify({ detail: 'ValueError: 单元不存在' }), { status: 400 });
        return { ...u, manual: c.body.manual };
      },
    });
    const a = setUnitTimes(u.unit_id, 100, 200, r.id);
    const b = setUnitTimes(u.unit_id, 300, 400, r.id);
    await Promise.all([a, b]);
    expect(order).toEqual(['put 100', 'put 300']);
    expect(useApp.getState().undo).toHaveLength(2);
    await undo();
    expect(useApp.getState().undo).toHaveLength(2);  // put back after the failure
    expect(useApp.getState().toasts.at(-1)?.body).toBe('单元不存在');  // no “ValueError:”
    fail = false;
    await undo();
    expect(useApp.getState().undo).toHaveLength(1);
    expect(useApp.getState().redo).toHaveLength(1);
    await redo();
    expect(useApp.getState().undo).toHaveLength(2);
  });

  it('steps of a stale result are dropped with a message instead of changing it', async () => {
    const pv = seedStore('review');
    const rid = pv.project.active_result_id!;
    const stalePv: ProjectView = structuredClone(pv);
    stalePv.view.results = stalePv.view.results.map((x) => (x.id === rid ? { ...x, stale: true, stale_reason: '歌词已修改' } : x));
    setPV(stalePv);
    const uid = pv.project.results.find((x) => x.id === rid)!.units[0].unit_id;
    useApp.setState({ undo: [{ rid, uid, before: null, after: null, label: '修改 き' }] });
    const api = mockApi({});
    await undo();
    expect(api.calls).toHaveLength(0);
    expect(useApp.getState().undo).toHaveLength(0);
    expect(useApp.getState().toasts.at(-1)?.title).toMatch(/无法撤销/);
  });
});

// ------------------------------------------------------------------ colour templates

describe('colour template requests (F-M12)', () => {
  it('are debounced, only the latest answer applies, and edits made meanwhile survive', async () => {
    const base = plainStyle();
    const pending: { body: any; resolve: (v: ThemePreview) => void }[] = [];
    const api = mockApi({
      'GET /api/karaoke/styles': () => [builtinSaved()],
      'POST /api/karaoke/theme': (c) => new Promise((r) => pending.push({ body: c.body, resolve: r as any })),
    });
    let latest: KaraokeStyle = base;
    function Host() {
      const [s, setS] = useState(base);
      latest = s;
      return <StylePanel style={s} onChange={setS} fonts={[]} defaultFont="" />;
    }
    renderUI(<Host />);
    // a quick series of clicks: one request
    await userEvent.click(screen.getByRole('button', { name: '主色 #2F80ED' }));
    await userEvent.click(screen.getByRole('button', { name: '主色 #3CC46A' }));
    await tick(300);
    expect(api.find('POST', '/api/karaoke/theme')).toHaveLength(1);
    expect(pending[0].body.color).toBe('#3CC46A');
    // a newer choice while the first answer is on its way
    await userEvent.click(screen.getByRole('button', { name: '主色 #FF4D6D' }));
    await tick(300);
    expect(pending).toHaveLength(2);
    // meanwhile the lyric size changes
    await userEvent.click(screen.getByRole('tab', { name: '歌词' }));
    const size = screen.getByRole('textbox', { name: '字号（输入数值）' });
    fireEvent.focus(size);
    fireEvent.change(size, { target: { value: '120' } });
    fireEvent.blur(size);
    expect(latest.text.size).toBe(120);
    const themed = (color: string): ThemePreview => ({
      palette: {}, style: { ...base, text: { ...base.text, color_sung: color, size: 88 }, theme: { template: 'plain', color, secondary: '' } },
    });
    await act(async () => { pending[1].resolve(themed('#FF4D6D')); await tick(10); });
    await act(async () => { pending[0].resolve(themed('#3CC46A')); await tick(10); });  // late, older: ignored
    expect(latest.text.color_sung).toBe('#FF4D6D');
    expect(latest.theme).toEqual({ template: 'plain', color: '#FF4D6D', secondary: '' });
    expect(latest.text.size).toBe(120);  // not reset by the theme answer
  });

  it('in a panel that scrolls on its own, another category starts at its top', async () => {
    mockApi({ 'GET /api/karaoke/styles': () => [builtinSaved()] });
    renderUI(<StylePanel style={plainStyle()} onChange={() => undefined} fonts={[]} defaultFont="" fill />);
    const box = screen.getAllByRole('tabpanel', { hidden: true })[0].parentElement!.parentElement!;
    await userEvent.click(screen.getByRole('tab', { name: '布局' }));
    box.scrollTop = 400;
    await userEvent.click(screen.getByRole('tab', { name: '时间' }));
    await waitFor(() => expect(box.scrollTop).toBe(0));
  });

  it('the outline once sung: its own colour, or the same as before', async () => {
    mockApi({ 'GET /api/karaoke/styles': () => [builtinSaved()] });
    const base = plainStyle();
    let latest: KaraokeStyle = base;
    function Host() {
      const [s, setS] = useState(base);
      latest = s;
      return <StylePanel style={s} onChange={setS} fonts={[]} defaultFont="" />;
    }
    renderUI(<Host />);
    fireEvent.change(screen.getByLabelText('已唱描边（同描边）'), { target: { value: '#ff0000' } });
    expect(latest.text.outline_color_sung).toBe('#FF0000');
    fireEvent.change(screen.getByLabelText('已唱描边'), { target: { value: base.text.outline_color.toLowerCase() } });
    expect(latest.text.outline_color_sung).toBe('');  // the outline's own colour again: no second colour
  });

  it('symbols at the start / end of every line', async () => {
    mockApi({ 'GET /api/karaoke/styles': () => [builtinSaved()] });
    const base = plainStyle();
    let latest: KaraokeStyle = base;
    function Host() {
      const [s, setS] = useState(base);
      latest = s;
      return <StylePanel style={s} onChange={setS} fonts={[]} defaultFont="" defaultSection="text" />;
    }
    renderUI(<Host />);
    expect(screen.queryByLabelText('文字颜色（同歌词未唱）')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '每句结尾的符号' }), { target: { value: '★' } });
    expect(latest.decor).toEqual({ start: '', end: '★', color: '', size_pct: 80, start_picture: '', end_picture: '' });
    fireEvent.change(screen.getByLabelText('文字颜色（同歌词未唱）'), { target: { value: '#ff0000' } });
    expect(latest.decor!.color).toBe('#FF0000');
  });

  it('a picture at the start of every line: uploaded, shown, removed', async () => {
    const api = mockApi({ 'GET /api/karaoke/styles': () => [builtinSaved()], 'POST /api/karaoke/pictures': () => ({ id: '0123456789abcdef', width: 40, height: 40 }) });
    let latest: KaraokeStyle = plainStyle();
    function Host() {
      const [s, setS] = useState(latest);
      latest = s;
      return <StylePanel style={s} onChange={setS} fonts={[]} defaultFont="" defaultSection="text" />;
    }
    const { container } = renderUI(<Host />);
    const file = new File([new Uint8Array([137, 80, 78, 71])], 'a.png', { type: 'image/png' });
    fireEvent.change(container.querySelectorAll('input[type=file]')[0], { target: { files: [file] } });
    await waitFor(() => expect(latest.decor?.start_picture).toBe('0123456789abcdef'));
    expect(api.find('POST', '/api/karaoke/pictures')[0].body).toBeInstanceOf(FormData);
    expect(screen.getByRole('button', { name: '更换每句开头的图片' }).querySelector('img')).toHaveAttribute('src', '/api/karaoke/pictures/0123456789abcdef');
    fireEvent.click(screen.getByRole('button', { name: '移除每句开头的图片' }));
    expect(latest.decor?.start_picture).toBe('');
  });

  it('mergeThemeColors takes only what a template decides', () => {
    const cur = plainStyle();
    cur.layout.margin_v = 333;
    const themed = plainStyle();
    themed.text.color_unsung = '#123456';
    themed.layout.margin_v = 1;
    themed.glow.enabled = true;
    const out = mergeThemeColors(cur, themed);
    expect(out.text.color_unsung).toBe('#123456');
    expect(out.glow.enabled).toBe(true);
    expect(out.layout.margin_v).toBe(333);
  });
});

// ------------------------------------------------------------------ errors / connection

describe('readable errors (F-L16) and the connection banner (F-M13)', () => {
  it('drops Python class names and localises 422 lists', () => {
    expect(readableError('ValueError: 歌词为空')).toBe('歌词为空');
    expect(readableError('kara_align.service.ServiceError: 不行')).toBe('不行');
    expect(readableError("KeyError: 'line_id'")).toBe("缺少 'line_id'");
    expect(validationMessage([{ loc: ['body', 'start_ms'], msg: 'Input should be a valid integer', type: 'int_parsing' }]))
      .toBe('请求内容不符合要求（start_ms：应为整数）');
    expect(errorText(500, 'Internal Server Error')).toMatch(/本地服务内部错误/);
    expect(errorText(404, { detail: 'Not Found' })).toMatch(/找不到/);
  });

  it('shows a banner while the server is unreachable, with a retry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    renderUI(<ConnectionBanner />);
    expect(screen.queryByRole('alert')).toBeNull();
    const { api } = await import('@/lib/api');
    await api.get('/api/info').catch(() => undefined);
    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地服务');
    mockApi({ 'GET /api/info': () => ({ version: 'x', backends: [], separation_presets: [], separation_available: false, export_formats: {} }), 'GET /api/projects': () => [], 'GET /api/tasks': () => [], 'GET /api/settings': () => null });
    await userEvent.click(screen.getByRole('button', { name: '重试连接' }));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });

  it('error toasts are alerts', async () => {
    const { Toaster } = await import('@/components/shell/Toaster');
    useApp.setState({ toasts: [] });
    renderUI(<Toaster />);
    act(() => toast('error', '失败了', '原因'));
    expect(screen.getByRole('alert')).toHaveTextContent('失败了');
  });
});

describe('task form link detection (F-L13)', () => {
  it('only NetEase / QQ links count as music links', () => {
    expect(detectLyrics('https://example.com/song/1').kind).toBe('badlink');
    expect(detectLyrics('https://163cn.tv/abc').kind).toBe('link');
    expect(detectLyrics('qq:004Z8Ihr0JIu5s').kind).toBe('link');
  });
});

// ------------------------------------------------------------------ waveform

describe('waveform draws on demand (F-M9)', () => {
  it('no frames while idle or hidden; one frame per change', () => {
    const queue: FrameRequestCallback[] = [];
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { queue.push(cb); return queue.length; });
    vi.stubGlobal('cancelAnimationFrame', () => { queue.length = 0; });
    const flush = () => { const q = queue.splice(0); q.forEach((cb) => cb(0)); };
    const canvas = document.createElement('canvas');
    const overlays: Overlays = { units: [], candUnits: [], lineStarts: [], loop: { on: false, start: null, end: null }, selectedUnitId: null, marks: [] };
    let playing = false;
    const getOverlays = vi.fn(() => overlays);
    const wf = new Waveform(canvas, {
      onSeek: vi.fn(), onSelectUnit: vi.fn(), onEditUnit: vi.fn(), onLoop: vi.fn(), getOverlays,
      getPlayhead: () => ({ ms: 0, playing }),
    });
    flush();
    const f0 = wf.frames;
    flush();
    flush();
    expect(wf.frames).toBe(f0);  // idle: nothing drawn, nothing requested
    expect(queue).toHaveLength(0);
    wf.invalidate();
    wf.invalidate();
    flush();
    expect(wf.frames).toBe(f0 + 1);  // two changes, one frame
    wf.setActive(false);
    wf.invalidate();
    expect(queue).toHaveLength(0);  // hidden: no frames at all
    wf.setActive(true);
    playing = true;
    flush();
    flush();
    expect(wf.frames).toBe(f0 + 3);  // playing: every frame
    playing = false;
    flush();
    expect(queue).toHaveLength(0);  // stopped: the loop ends
    wf.dispose();
  });
});
