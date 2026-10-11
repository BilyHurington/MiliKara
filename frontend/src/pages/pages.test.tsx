// Every page renders against a real project fixture; key interactions send
// the expected API requests (mocked fetch).

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { player } from '@/audio/player';
import { StudioDock } from '@/components/shell/StudioDock';
import { Sidebar } from '@/components/shell/Sidebar';
import { currentResult, useApp, WAVE_HEIGHT } from '@/store/app';
import { fixturePV, mockApi, renderUI, seedStore } from '@/test/helpers';
import { builtinSaved, plainStyle } from '@/test/style';
import { AlignPage } from './Align';
import { CalibratePage } from './Calibrate';
import { EnhancePage } from './Enhance';
import { ExportPage } from './Export';
import { HomePage } from './Home';
import { InputPage } from './Input';
import { ModePage } from './Mode';
import { ReviewPage } from './Review';

const PID = 'proj';

/** Routes that behave like the server for the fixture project. */
function serverLike() {
  const pv = fixturePV();
  const viewResp = () => fixturePV();
  return mockApi({
    [`GET /api/projects/${PID}/results/`]: (c) => pv.project.results.find((r) => c.url.includes(r.id)),
    [`GET /api/projects/${PID}/export/`]: (c) => ({ filename: 'aligned-unit.lrc', media_type: 'text/plain', content: `[00:02.00]<00:02.00>君 ${c.url}`, warnings: ['增强 LRC 以片段为单位合并'] }),
    [`GET /api/projects/${PID}/audio/`]: () => ({ per_second: 200, mins: [0], maxs: [0], duration_ms: 16000, sample_rate: 44100 }),
    [`GET /api/projects/${PID}`]: viewResp,
    'GET /api/projects': () => [{ id: PID, name: 'proj', mode: 'lrc', updated: pv.project.updated }],
    [`PUT /api/projects/${PID}/results/`]: (c) => {
      const r = currentResult()!;
      const u = structuredClone(r.units.find((x) => c.url.includes(x.unit_id))!);
      u.manual = { start_ms: c.body.start_ms, end_ms: c.body.end_ms, locked: true, at: '', note: '' };
      u.start_ms = c.body.start_ms;
      u.end_ms = c.body.end_ms;
      return u;
    },
    [`POST /api/projects/${PID}/results/`]: (c) => {  // .../lines/{lid}/retime: the whole line moved
      const r = currentResult()!;
      const lid = c.url.split('/lines/')[1].split('/')[0];
      const units = r.units.filter((x) => x.line_id === lid && x.start_ms !== null && x.end_ms !== null);
      const d = c.body.start_ms - Math.min(...units.map((x) => x.start_ms!));
      return { units: units.map((x) => ({ ...structuredClone(x), start_ms: x.start_ms! + d, end_ms: x.end_ms! + d,
        manual: { start_ms: x.start_ms! + d, end_ms: x.end_ms! + d, locked: true, at: '', note: '整行平移' } })) };
    },
    [`POST /api/projects/${PID}/align`]: () => ({ id: 'job1', kind: 'align', project_id: PID, status: 'queued', progress: 0, message: '', error: null, created: 'z', finished: null, output: null }),
    [`POST /api/projects/${PID}/mix/preview-gain`]: () => ({ bus_gain: 1, peak_before: 0.5 }),
    [`POST /api/projects/${PID}/lyrics/parse`]: () => ({ preview_id: 'pv1', detected: 'lrc', warnings: ['示例警告'], error: null, doc: fixturePV().project.lyrics, extra_tracks: {} }),
    [`POST /api/projects/${PID}/ai/prompt`]: () => ({ prompt: 'PROMPT TEXT', snapshot_id: 'snap1', roundtrip_id: 'ai1' }),
    [`POST /api/projects/${PID}/ai/validate`]: () => ({ report_id: 'rep1', report: { ok: true, snapshot: 'snap1', roundtrip_id: 'ai1', warnings: [], errors: [], missing_line_ids: [], lines: [] } }),
    [`POST /api/projects/${PID}/calibration/`]: viewResp,
    [`POST /api/projects/${PID}/`]: viewResp,
    [`PATCH /api/projects/${PID}`]: viewResp,
    'GET /api/jobs/': () => ({ id: 'job1', kind: 'align', project_id: PID, status: 'running', progress: 0.4, message: '解码', error: null, created: 'z', finished: null, output: null }),
  });
}

beforeEach(() => {
  player.reset();
});

describe('pages render without crashing', () => {
  const pages = [
    ['mode', ModePage, '选择对齐模式'],
    ['input', InputPage, '音频与歌词'],
    ['enhance', EnhancePage, '注音与人声分离'],
    ['calibrate', CalibratePage, '首音校准'],
    ['align', AlignPage, '对齐'],
    ['review', ReviewPage, '人工检查'],
    ['export', ExportPage, '导出'],
  ] as const;
  for (const [step, Page, title] of pages) {
    it(step, async () => {
      seedStore(step);
      serverLike();
      renderUI(<Page />);
      expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument();
    });
  }

  it('home (no project)', () => {
    useApp.setState({ pid: null, pv: null, projects: [] });
    serverLike();
    renderUI(<HomePage />);
    expect(screen.getByText('新建项目')).toBeInTheDocument();
  });

  it('plain-mode calibrate explains it is not needed', () => {
    const pv = fixturePV();
    pv.project.mode = 'plain';
    seedStore('calibrate', pv);
    serverLike();
    renderUI(<CalibratePage />);
    expect(screen.getAllByText(/普通模式/).length).toBeGreaterThan(0);
  });

  it('review without results shows an empty state', () => {
    const pv = fixturePV();
    pv.project.results = [];
    pv.project.active_result_id = null;
    pv.view.results = [];
    seedStore('review', pv);
    serverLike();
    renderUI(<ReviewPage />);
    expect(screen.getAllByRole('button', { name: /对齐/ }).length).toBeGreaterThan(0);
  });
});

describe('interactions', () => {
  it('review: editing a start time saves it (PUT) and is undoable', async () => {
    seedStore('review');
    const api = serverLike();
    renderUI(<ReviewPage />);
    const input = within(await screen.findByRole('table')).getAllByRole('spinbutton')[0] as HTMLInputElement;
    const orig = Number(input.value);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: String(orig - 15) } });
    fireEvent.blur(input);
    await waitFor(() => expect(api.find('PUT', '/units/')).toHaveLength(1));
    expect(api.find('PUT', '/units/')[0].body.start_ms).toBe(orig - 15);
    await waitFor(() => expect(useApp.getState().undo).toHaveLength(1));
  });

  it('review: a new line start moves the whole line in one call, undone as one step', async () => {
    seedStore('review');
    const api = serverLike();
    renderUI(<ReviewPage />);
    const start = await screen.findByRole('spinbutton', { name: '行首' }) as HTMLInputElement;
    const orig = Number(start.value);
    fireEvent.focus(start);
    fireEvent.change(start, { target: { value: String(orig - 100) } });
    fireEvent.blur(start);
    await waitFor(() => expect(api.find('POST', '/retime')).toHaveLength(1));
    expect(api.find('POST', '/retime')[0].body).toEqual({ start_ms: orig - 100, end_ms: null });
    await waitFor(() => expect(useApp.getState().undo).toHaveLength(1));
    const step = useApp.getState().undo[0];
    expect(step.label).toBe('整行平移');
    expect(step.items!.length).toBeGreaterThan(1);
    expect(api.find('PUT', '/units/')).toHaveLength(0);  // not unit by unit
  });

  it('review: end before start is rejected locally', async () => {
    seedStore('review');
    const api = serverLike();
    renderUI(<ReviewPage />);
    const inputs = within(await screen.findByRole('table')).getAllByRole('spinbutton');
    fireEvent.change(inputs[1], { target: { value: '1' } });
    fireEvent.blur(inputs[1]);
    await new Promise((r) => setTimeout(r, 20));
    expect(api.find('PUT', '/units/')).toHaveLength(0);
    expect(useApp.getState().toasts.at(-1)?.kind).toBe('error');
  });

  it('calibrate: M before audio is loaded asks to load audio', async () => {
    seedStore('calibrate');
    const api = serverLike();
    renderUI(<CalibratePage />);
    await screen.findByRole('heading', { level: 1, name: '首音校准' });
    fireEvent.keyDown(document.body, { key: 'm', code: 'KeyM' });
    expect(api.find('POST', '/calibration/mark')).toHaveLength(0);
    expect(useApp.getState().toasts.at(-1)?.title).toMatch(/音频/);
  });

  it('calibrate: M marks the playhead position', async () => {
    seedStore('calibrate');
    const api = serverLike();
    renderUI(<CalibratePage />);
    await screen.findByRole('heading', { level: 1, name: '首音校准' });
    player.durationMs = 16000; // audio loaded
    player.offsetMs = 2097;
    fireEvent.keyDown(document.body, { key: 'm', code: 'KeyM' });
    await waitFor(() => expect(api.find('POST', '/calibration/mark')).toHaveLength(1));
    expect(api.find('POST', '/calibration/mark')[0].body).toMatchObject({ marked_ms: 2097 });
  });

  it('align: start sends a job request and tracks it', async () => {
    seedStore('align');
    const api = serverLike();
    renderUI(<AlignPage />);
    await userEvent.click(await screen.findByRole('button', { name: /开始对齐/ }));
    await waitFor(() => expect(api.find('POST', '/align')).toHaveLength(1));
    await waitFor(() => expect(Object.keys(useApp.getState().jobs)).toContain('job1'));
  });

  it('export: preview opens a dialog with the content and loss warnings', async () => {
    seedStore('export');
    serverLike();
    renderUI(<ExportPage />);
    const buttons = await screen.findAllByRole('button', { name: /预览/ });
    await userEvent.click(buttons[0]);
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/\[00:02.00\]/)).toBeInTheDocument();
  });

  it('input: paste → parse → preview → apply', async () => {
    seedStore('input');
    const api = serverLike();
    renderUI(<InputPage />);
    const ta = await screen.findByPlaceholderText(/每行一句歌词/);
    fireEvent.change(ta, { target: { value: '[00:01.00]きみと' } });
    await userEvent.click(screen.getByRole('button', { name: /解析预览/ }));
    await waitFor(() => expect(api.find('POST', '/lyrics/parse')).toHaveLength(1));
    expect(api.find('POST', '/lyrics/parse')[0].body).toMatchObject({ text: '[00:01.00]きみと', origin: 'paste' });
    await userEvent.click(await screen.findByRole('button', { name: /应用到项目/ }));
    await waitFor(() => expect(api.find('POST', '/lyrics/apply')[0]?.body).toEqual({ preview_id: 'pv1' }));
  });

  it('enhance: prompt → validate', async () => {
    seedStore('enhance');
    const api = serverLike();
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => {}) } });
    renderUI(<EnhancePage />);
    await userEvent.click(await screen.findByRole('tab', { name: /AI 注音/ }));
    await userEvent.click(await screen.findByRole('button', { name: /复制 AI 提示词/ }));
    await waitFor(() => expect(api.find('POST', '/ai/prompt')).toHaveLength(1));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('PROMPT TEXT');
    const reply = screen.getByPlaceholderText(/网页聊天的回复/);
    fireEvent.change(reply, { target: { value: '{"format":"kara-align/reading-patch"}' } });
    await userEvent.click(screen.getByRole('button', { name: /^校验$/ }));
    await waitFor(() => expect(api.find('POST', '/ai/validate')).toHaveLength(1));
  });

  it('enhance: a word without a reading (digits) can be given one; punctuation stays text', async () => {
    localStorage.removeItem('kara.enhanceTab');
    const pv = fixturePV();
    const line = pv.project.lyrics.lines[0];
    const seg = (surface: string) => ({ ...line.segments[0], id: `s-${surface}`, surface, reading: null, units: [],
      reading_source: 'none' as const, uncertain: surface === '3', confirmed: false, candidates: [] });
    line.text = `3、${line.text}`;
    line.segments = [seg('3'), seg('、'), ...line.segments];
    seedStore('enhance', pv);
    serverLike();
    renderUI(<EnhancePage />);
    const digit = await screen.findByRole('button', { name: /^3\s*没有读音/ });
    expect(screen.queryByRole('button', { name: /^、/ })).toBeNull();
    await userEvent.click(digit);
    expect(await screen.findByRole('dialog', { name: /修改读音：3/ })).toBeInTheDocument();
  });

  it('enhance: the three tasks are tabs with their status, and a draft survives switching', async () => {
    localStorage.removeItem('kara.enhanceTab');
    seedStore('enhance');
    serverLike();
    renderUI(<EnhancePage />);
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    // separation is reachable without scrolling past the readings, and shows its state
    const sep = screen.getByRole('tab', { name: /人声分离/ });
    expect(sep).toHaveTextContent(/已有 人声 \+ 伴奏/);
    expect(screen.queryByRole('button', { name: /开始分离/ })).toBeNull();
    await userEvent.click(sep);
    expect(screen.getByRole('button', { name: /开始分离/ })).toBeInTheDocument();
    expect(localStorage.getItem('kara.enhanceTab')).toBe('separation');
    // a pasted AI reply is still there after looking at the readings
    await userEvent.click(screen.getByRole('tab', { name: /AI 注音/ }));
    fireEvent.change(screen.getByPlaceholderText(/网页聊天的回复/), { target: { value: 'draft' } });
    await userEvent.click(screen.getByRole('tab', { name: /读音与发音单元/ }));
    await userEvent.click(screen.getByRole('tab', { name: /AI 注音/ }));
    expect(screen.getByPlaceholderText(/网页聊天的回复/)).toHaveValue('draft');
  });

  it('mode: switching sends PATCH', async () => {
    seedStore('mode');
    const api = serverLike();
    renderUI(<ModePage />);
    await userEvent.click(screen.getByRole('radio', { name: /普通模式/ }));
    await waitFor(() => expect(api.find('PATCH', `/projects/${PID}`)[0].body).toEqual({ mode: 'plain' }));
  });

  it('sidebar navigates between steps', async () => {
    seedStore('mode');
    serverLike();
    renderUI(<Sidebar />);
    await userEvent.click(screen.getByRole('button', { name: /导出/ }));
    expect(useApp.getState().step).toBe('export');
  });
});

describe('studio dock', () => {
  it('renders the long mix sliders and a draggable divider', async () => {
    seedStore('review');
    serverLike();
    renderUI(<StudioDock />);
    const sep = await screen.findByRole('separator', { name: /波形高度/ });
    const h0 = useApp.getState().waveHeight;
    // keyboard resizing (the handle sits above the dock: ArrowUp grows it)
    act(() => { fireEvent.keyDown(sep, { key: 'ArrowUp' }); });
    expect(useApp.getState().waveHeight).toBe(Math.min(WAVE_HEIGHT.max, h0 + 10));
    act(() => { fireEvent.doubleClick(sep); });
    expect(useApp.getState().waveHeight).toBe(WAVE_HEIGHT.default);
    // pointer dragging
    act(() => { fireEvent.pointerDown(sep, { clientY: 500 }); });
    act(() => { window.dispatchEvent(new MouseEvent('pointermove', { clientY: 440 }) as any); });
    act(() => { window.dispatchEvent(new MouseEvent('pointerup') as any); });
    expect(useApp.getState().waveHeight).toBe(WAVE_HEIGHT.default + 60);
  });
});

describe('video in / reduced-vocal video out', () => {
  const withVideo = () => {
    const pv = fixturePV();
    const orig = pv.project.audio.find((a) => a.role === 'original')!;
    pv.project.video = {
      id: 'v1', sha256: 'abc', path: 'assets/abc.mp4', filename: 'clip.mp4', container: '.mp4', duration_ms: 16000,
      width: 1920, height: 1080, fps: 29.97, video_codec: 'h264', audio_codec: 'aac', audio_offset_s: 0, audio_sha256: orig.sha256,
    };
    return pv;
  };

  it('the original upload accepts video files', async () => {
    seedStore('input');
    serverLike();
    const { container } = renderUI(<InputPage />);
    const inputs = [...container.querySelectorAll('input[type=file]')] as HTMLInputElement[];
    expect(inputs.some((i) => i.accept.includes('.mp4') && i.accept.includes('video/*'))).toBe(true);
  });

  it('shows where the original audio came from', () => {
    seedStore('input', withVideo());
    serverLike();
    renderUI(<InputPage />);
    expect(screen.getByText(/来自视频 clip.mp4/)).toBeInTheDocument();
  });

  it('export offers the video button only with a video, and posts the mix settings', async () => {
    seedStore('export');
    serverLike();
    const first = renderUI(<ExportPage />);
    expect(screen.queryByRole('button', { name: /导出降低人声的视频/ })).toBeNull();
    first.unmount();

    const pv = withVideo();
    pv.view.audio.vocals = { asset_id: 'x', available: true, duration_ms: 16000, sample_rate: 44100 };
    pv.view.audio.instrumental = { asset_id: 'y', available: true, duration_ms: 16000, sample_rate: 44100 };
    seedStore('export', pv);
    const api = serverLike();
    renderUI(<ExportPage />);
    const btn = await screen.findByRole('button', { name: /导出降低人声的视频/ });
    await userEvent.click(btn);
    await waitFor(() => expect(api.find('POST', '/video/export')).toHaveLength(1));
    expect(api.find('POST', '/video/export')[0].body).toMatchObject({ vocal_keep_pct: pv.project.mix.vocal_keep_pct });
  });

  it('the dock keeps only a volume control (no vocal / instrumental sliders)', () => {
    seedStore('review');
    serverLike();
    renderUI(<StudioDock />);
    expect(screen.queryByRole('textbox', { name: /人声保留/ })).toBeNull();
    expect(screen.queryByRole('textbox', { name: /伴奏/ })).toBeNull();
    expect(screen.getByRole('textbox', { name: /音量/ })).toBeInTheDocument();
  });
});

describe('dock track switching', () => {
  it('offers only original / vocals / instrumental (no custom mix)', () => {
    seedStore('review');
    serverLike();
    renderUI(<StudioDock />);
    expect(screen.queryByRole('radio', { name: '自定义混音' })).toBeNull();
  });

  it('the waveform loads the peaks of the selected track', async () => {
    const pv = seedStore('review');
    const api = serverLike();
    renderUI(<StudioDock />);
    const orig = pv.project.audio.find((a) => a.role === 'original')!;
    const voc = pv.project.audio.find((a) => a.role === 'vocals')!;
    await waitFor(() => expect(api.find('GET', `/audio/${orig.id}/peaks`).length).toBeGreaterThan(0));
    act(() => player.setSource('vocals'));
    await waitFor(() => expect(api.find('GET', `/audio/${voc.id}/peaks`).length).toBeGreaterThan(0));
    act(() => player.setSource('original'));
  });
});

describe('karaoke subtitles page', () => {
  const DEFAULT_STYLE = plainStyle();

  let info: { fields: Record<string, string>; labels: Record<string, string>; text: string | null };

  function karaokeServer() {
    info = {
      fields: { title: 'わたぐも', artist: '黒沢ともよ', album: 'STARLIGHT MASTER 13' },
      labels: { title: '歌名', artist: '歌手', album: '专辑', lyricist: '作词', composer: '作曲', arranger: '编曲' }, text: null,
    };
    const api = serverLike();
    const base = api.fn.getMockImplementation()!;
    api.fn.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      const json = (x: unknown) => new Response(JSON.stringify(x), { status: 200 });
      if (u === '/api/fonts') return json({ default: 'Hiragino Sans', families: [{ family: 'Hiragino Sans', names: ['Hiragino Sans'], bold: true }] });
      if (u === '/api/karaoke/styles') return json([builtinSaved()]);
      if (u.endsWith('/lyrics/fetch-translation')) {
        api.calls.push({ method: 'POST', url: u, body: null });
        return json({ ...fixturePV(), paired: 3 });
      }
      if (u === '/api/karaoke/theme') {
        const b = JSON.parse(String(init!.body));
        api.calls.push({ method: 'POST', url: u, body: b });
        return json({ palette: {}, style: { ...b.base, text: { ...b.base.text, color_sung: b.color }, glow: { ...b.base.glow, enabled: b.template === 'glow' },
          theme: { template: b.template, color: b.color, secondary: b.secondary ?? '' } } });
      }
      if (u.endsWith('/karaoke/info')) {
        if (init?.method === 'PUT') {
          const body = JSON.parse(String(init.body));
          api.calls.push({ method: 'PUT', url: u, body });
          info.text = body.text;
        }
        return json(info);
      }
      if (u.endsWith('/karaoke') && (!init || init.method === 'GET' || !init.method)) return json(DEFAULT_STYLE);
      if (u.endsWith('/karaoke/preview')) {
        api.calls.push({ method: 'POST', url: u, body: JSON.parse(String(init!.body)) });
        return new Response(new Blob(['png']), { status: 200, headers: { 'Content-Type': 'image/png' } });
      }
      if (u.endsWith('/karaoke/burn')) {
        api.calls.push({ method: 'POST', url: u, body: JSON.parse(String(init!.body)) });
        return json({ id: 'jb', kind: 'burn', project_id: PID, status: 'queued', progress: 0, message: '', error: null, created: 'z', finished: null, output: null });
      }
      return base(url, init);
    });
    return api;
  }

  it('renders, previews the selected line and saves preset changes', async () => {
    seedStore('karaoke');
    const api = karaokeServer();
    (URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = vi.fn();
    const { KaraokePage } = await import('./Karaoke');
    renderUI(<KaraokePage />);
    expect(await screen.findByRole('heading', { level: 1, name: '卡拉OK字幕' })).toBeInTheDocument();
    await waitFor(() => expect(api.calls.some((c) => c.url.endsWith('/karaoke/preview'))).toBe(true), { timeout: 2000 });
    const first = api.calls.find((c) => c.url.endsWith('/karaoke/preview'))!;
    expect(first.body.style.text.size).toBe(88);
    expect(first.body.t_ms).toBeGreaterThan(0);

    // turning on translations refreshes the preview (it used to look unchanged: no translation stored)
    const n0 = api.calls.filter((c) => c.url.endsWith('/karaoke/preview')).length;
    await userEvent.click(screen.getByRole('tab', { name: '翻译' }));
    await userEvent.click(screen.getByRole('switch', { name: /显示翻译字幕/ }));
    await waitFor(() => expect(api.calls.filter((c) => c.url.endsWith('/karaoke/preview')).length).toBeGreaterThan(n0), { timeout: 2000 });
    expect(api.calls.filter((c) => c.url.endsWith('/karaoke/preview')).at(-1)!.body.style.translation.enabled).toBe(true);
    await waitFor(() => expect(api.find('PUT', '/karaoke').at(-1)?.body.translation.enabled).toBe(true), { timeout: 2000 });
    // this project has no translations yet: say so, and offer to fetch them from the platform
    expect(screen.getByText(/还没有翻译/)).toBeInTheDocument()
  });

  it('colour template: re-derives the colours, and shows 自定义 after a colour is changed by hand', async () => {
    seedStore('karaoke');
    const api = karaokeServer();
    (URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = vi.fn();
    const { KaraokePage } = await import('./Karaoke');
    renderUI(<KaraokePage />);
    await screen.findByRole('heading', { level: 1, name: '卡拉OK字幕' });
    // a new project's colours were set by hand
    expect(await screen.findByText('自定义')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: '荧光' }));
    await waitFor(() => expect(api.find('POST', '/api/karaoke/theme').at(-1)?.body).toMatchObject({ template: 'glow', secondary: null }));
    expect(api.find('POST', '/api/karaoke/theme').at(-1)!.body.base.text.size).toBe(88);  // applied on top of this style
    await userEvent.click(screen.getByRole('button', { name: '主色 #2F80ED' }));
    await waitFor(() => expect(api.find('PUT', '/karaoke').at(-1)?.body.theme).toEqual({ template: 'glow', color: '#2F80ED', secondary: '' }), { timeout: 2000 });
    expect(screen.queryByText('自定义')).toBeNull();
    expect(screen.getByRole('radio', { name: '荧光' })).toBeChecked();
    // a layout change keeps the template …
    await userEvent.click(screen.getByRole('tab', { name: '歌词' }));
    await userEvent.click(screen.getAllByRole('switch', { name: '粗体' })[0]);
    expect(screen.queryByText('自定义')).toBeNull();
    // … a colour changed by hand does not
    await userEvent.click(screen.getByRole('tab', { name: '配色' }));
    fireEvent.change(screen.getByLabelText('已唱（扫光）'), { target: { value: '#00ff00' } });
    expect(await screen.findByText('自定义')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '荧光' })).not.toBeChecked();
    await waitFor(() => expect(api.find('PUT', '/karaoke').at(-1)?.body.theme).toBeNull(), { timeout: 2000 });
  });

  it('song info card: pick lines, then edit the text freely', async () => {
    seedStore('karaoke');
    const api = karaokeServer();
    (URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = vi.fn();
    const { KaraokePage } = await import('./Karaoke');
    renderUI(<KaraokePage />);
    await userEvent.click(await screen.findByRole('tab', { name: '歌曲信息' }));
    await userEvent.click(screen.getByRole('switch', { name: '显示歌曲信息（开头，以及结尾）' }));
    // the song data shows next to each line; the text follows the ticks
    expect(screen.getByText('STARLIGHT MASTER 13')).toBeInTheDocument();
    const box = screen.getByRole('textbox', { name: '歌曲信息文字' });
    expect(box).toHaveValue('わたぐも\n黒沢ともよ');
    await userEvent.click(screen.getByRole('checkbox', { name: '显示专辑' }));
    expect(box).toHaveValue('わたぐも\n黒沢ともよ\nSTARLIGHT MASTER 13');
    await waitFor(() => expect(api.find('PUT', '/karaoke').at(-1)?.body.info).toMatchObject({ enabled: true, fields: ['title', 'artist', 'album'] }), { timeout: 2000 });
    // jump the preview to the card
    await userEvent.click(screen.getByRole('button', { name: '看歌曲信息' }));
    await waitFor(() => expect(api.calls.filter((c) => c.url.endsWith('/karaoke/preview')).at(-1)!.body.t_ms).toBe(1700), { timeout: 2000 });
    // free text: saved to the project, the ticks stop applying until reset
    await userEvent.clear(box);
    await userEvent.type(box, 'わたぐも{Enter}赤城みりあ');
    await waitFor(() => expect(api.find('PUT', '/karaoke/info').at(-1)?.body.text).toBe('わたぐも\n赤城みりあ'), { timeout: 2000 });
    const infoSection = document.querySelector('section[data-section=info]') as HTMLElement;
    expect(await within(infoSection).findByText('自定义')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '显示专辑' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /恢复为按勾选自动生成/ }));
    await waitFor(() => expect(api.find('PUT', '/karaoke/info').at(-1)?.body.text).toBeNull());
    expect(screen.getByRole('textbox', { name: '歌曲信息文字' })).toHaveValue('わたぐも\n黒沢ともよ\nSTARLIGHT MASTER 13');
  });

  it('burn sends the chosen options', async () => {
    seedStore('karaoke');
    const api = karaokeServer();
    const { KaraokePage } = await import('./Karaoke');
    renderUI(<KaraokePage />);
    await userEvent.click(await screen.findByRole('button', { name: /一键烧录/ }));
    await waitFor(() => expect(api.calls.some((c) => c.url.endsWith('/karaoke/burn'))).toBe(true));
    expect(api.calls.find((c) => c.url.endsWith('/karaoke/burn'))!.body).toEqual({ background: 'auto', audio: 'original', quality: 'standard', vocal_keep_pct: 20 });
  });

  it('reduced vocals shows its own level control and burns with it', async () => {
    seedStore('karaoke');
    const api = karaokeServer();
    (URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = vi.fn();
    const { KaraokePage } = await import('./Karaoke');
    renderUI(<KaraokePage />);
    await screen.findByRole('button', { name: /一键烧录/ });
    expect(screen.queryByRole('textbox', { name: '人声保留（输入数值）' })).toBeNull();
    // the option no longer shows the Export page's mix level
    const mix = screen.getByRole('radio', { name: '降低人声' });
    await userEvent.click(mix);
    expect(screen.getByRole('textbox', { name: '人声保留（输入数值）' })).toHaveValue('20');
    await waitFor(() => expect(api.calls.filter((c) => c.url.endsWith('/karaoke/preview')).length).toBeGreaterThan(0), { timeout: 2000 });
    const previews = api.calls.filter((c) => c.url.endsWith('/karaoke/preview')).length;
    const box = screen.getByRole('textbox', { name: '人声保留（输入数值）' });
    await userEvent.click(box);
    await new Promise((r) => setTimeout(r, 30)); // the field selects its text on the next frame
    await userEvent.keyboard('35{Enter}');
    await waitFor(() => expect(api.find('PUT', '/karaoke').at(-1)?.body.output.vocal_keep_pct).toBe(35), { timeout: 2000 });
    await new Promise((r) => setTimeout(r, 500));
    // changing the audio level does not re-render the picture
    expect(api.calls.filter((c) => c.url.endsWith('/karaoke/preview')).length).toBe(previews);
    await userEvent.click(screen.getByRole('button', { name: /一键烧录/ }));
    await waitFor(() => expect(api.calls.some((c) => c.url.endsWith('/karaoke/burn'))).toBe(true));
    expect(api.calls.find((c) => c.url.endsWith('/karaoke/burn'))!.body).toMatchObject({ audio: 'mix', vocal_keep_pct: 35 });
  });
});
