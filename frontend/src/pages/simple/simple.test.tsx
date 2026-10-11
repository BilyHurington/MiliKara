import { act, cleanup as cleanupRender, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import type { AppSettings, KaraokeStyle, PipelineTask } from '@/lib/types';
import { useApp } from '@/store/app';
import { loadTasks, useSimple } from '@/store/simple';
import { useLibrary } from '@/store/styles';

const useLibraryReset = () => useLibrary.setState({ saved: null });
import { fixtureInfo, fixturePV, mockApi, renderUI } from '@/test/helpers';
import { builtinSaved, defaultStyle } from '@/test/style';
import { detectLyrics, SimpleHome } from './SimpleHome';
import { SimpleSettings } from './SimpleSettings';
import { SimpleApp } from './SimpleApp';

const STYLE: KaraokeStyle = defaultStyle();

const SETTINGS: AppSettings = {
  version: 1,
  ai: { enabled: false, provider: 'manual', model: '', base_url: 'https://api.openai.com/v1', api_key_env: 'OPENAI_API_KEY', timeout_s: 600, has_api_key: false, env_key_present: false },
  simple: {
    default_mode: 'lrc', separate: true, separation_preset: 'melband-roformer', separation_device: 'auto', calibration: 'manual',
    karaoke: STYLE, auto_export: true, video_audio: ['original'],
    vocal_keep_pct: 20, quality: 'standard',
    task_style: { source: 'default', template: 'glow', color: '#FF8A1E', secondary: '', saved_id: '', translation: null, song_info: null, ruby: 'style', video_audio: null },
  },
};

const stages = (upto: number, running = false) => ['import', 'lyrics', 'readings', 'separate', 'calibrate', 'align', 'export'].map((key, i) => ({
  key, label: key, progress: i < upto ? 1 : 0.4, message: '',
  status: (i < upto ? 'done' : i === upto && running ? 'running' : 'pending') as PipelineTask['stages'][number]['status'],
}));

const task = (over: Partial<PipelineTask>): PipelineTask => ({
  id: 't1', created: '2026-09-24T10:00:00+00:00', finished: null, name: '初恋', mode: 'lrc', media_filename: 'a.mp4',
  lyrics_kind: 'link', lyrics_input: 'https://music.163.com/song?id=1', status: 'queued', project_id: null,
  stages: stages(0), progress: 0, message: '', error: null, detail: null, warnings: [], outputs: {}, ...over,
});

function seed(settings = SETTINGS) {
  useSimple.setState({ ui: 'simple', page: 'home', settings: structuredClone(settings), providers: null, tasks: [] });
  useApp.setState({ info: fixtureInfo(), pid: null, pv: null, jobs: {}, toasts: [] });
}

beforeEach(() => localStorage.clear());

describe('lyrics detection', () => {
  it('tells links, LRC and plain text apart', () => {
    expect(detectLyrics('').kind).toBe('empty');
    expect(detectLyrics('https://music.163.com/song?id=423314091&uct2=x')).toMatchObject({ kind: 'link', label: expect.stringContaining('网易云') });
    expect(detectLyrics('分享歌曲 https://y.qq.com/n/ryqq/songDetail/abc').label).toContain('QQ');
    expect(detectLyrics('[00:01.00]きみと\n[00:02.00]あるいた')).toMatchObject({ kind: 'lrc', label: 'LRC 歌词 · 2 行带时间' });
    expect(detectLyrics('君と\n歩いた')).toMatchObject({ kind: 'text' });
    // an album / playlist is refused as it is pasted (not after the task has started)
    for (const t of ['https://music.163.com/#/album?id=123', 'https://music.163.com/playlist?id=9', 'https://y.qq.com/n/ryqq/albumDetail/abc',
      'https://y.qq.com/n/ryqq/playlist/123', 'netease:album:123']) {
      expect(detectLyrics(t)).toMatchObject({ kind: 'badlink', label: expect.stringContaining('专辑或歌单') });
    }
    expect(detectLyrics('https://music.163.com/#/song?id=1').kind).toBe('link');
    expect(detectLyrics('https://y.qq.com/n/ryqq/songDetail/abc').kind).toBe('link');
  });
});

describe('simple mode home', () => {
  it('adds a task from a video and a music link', async () => {
    seed();
    const api = mockApi({
      'GET /api/tasks': () => [],
      'GET /api/karaoke/styles': () => [builtinSaved()],
      'POST /api/karaoke/theme': (c) => ({ palette: {}, style: { ...STYLE, text: { ...STYLE.text, color_sung: c.body.color }, glow: { ...STYLE.glow, enabled: c.body.template === 'glow' } } }),
      'POST /api/tasks': () => task({ status: 'queued' }),
      'PUT /api/settings': (c) => {  // like the server: every update merges into what is stored
        stored = { ...stored, simple: { ...stored.simple, ...c.body.simple } };
        return stored;
      },
    });
    let stored = structuredClone(SETTINGS);
    const { container } = renderUI(<SimpleHome />);
    const start = screen.getByRole('button', { name: /开始制作/ });
    expect(start).toBeDisabled();
    const input = container.querySelector('input[type=file]') as HTMLInputElement;
    await userEvent.upload(input, new File(['x'], '初恋.mp4', { type: 'video/mp4' }));
    expect(screen.getByText('初恋.mp4')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '音乐链接或歌词' }), { target: { value: 'https://music.163.com/song?id=1' } });
    expect(screen.getByText(/网易云音乐链接/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: /普通/ }));
    await waitFor(() => expect(api.find('PUT', '/api/settings')[0]?.body).toEqual({ simple: { default_mode: 'plain' } }));
    // step 4: this song's subtitle look — the glow template, blue with a pink second colour
    await userEvent.click(screen.getByRole('radio', { name: '模版配色' }));
    await userEvent.click(screen.getByRole('radio', { name: '荧光' }));
    await userEvent.click(screen.getByRole('button', { name: '主色 #2F80ED' }));
    await userEvent.click(screen.getByRole('button', { name: /加一个辅色/ }));
    await userEvent.click(screen.getByRole('button', { name: '辅色 #ED35B3' }));
    await userEvent.click(screen.getByRole('switch', { name: '开头和结尾显示歌曲信息' }));
    expect(screen.queryByRole('textbox', { name: '歌词字号（输入数值）' })).toBeNull();  // size lives in the style, not here
    // ruby: one of none / hiragana / katakana / romaji, then whether only kanji get it
    await userEvent.click(screen.getByRole('radio', { name: '罗马音' }));
    await userEvent.click(screen.getByRole('switch', { name: '仅汉字' }));
    await userEvent.click(screen.getByRole('radio', { name: '无' }));
    expect(screen.queryByRole('switch', { name: '仅汉字' })).toBeNull();
    await userEvent.click(screen.getByRole('radio', { name: '罗马音' }));
    // video sound: several at once, one video each; "reduce vocals" needs separation (on in these
    // settings) and shows its own level
    expect(screen.getByRole('button', { name: '原唱', pressed: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '降低人声', pressed: false })).toBeEnabled();
    expect(screen.queryByRole('textbox', { name: '人声保留（输入数值）' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '降低人声' }));
    expect(screen.getByText('每种声音各生成一个视频')).toBeInTheDocument();
    const level = screen.getByRole('textbox', { name: '人声保留（输入数值）' });
    expect(level).toHaveValue('20');
    await userEvent.click(level);
    await new Promise((r) => setTimeout(r, 30)); // the field selects its text on the next frame
    await userEvent.keyboard('35{Enter}');
    await waitFor(() => expect(api.find('POST', '/api/karaoke/theme').at(-1)?.body).toEqual({ template: 'glow', color: '#2F80ED', secondary: '#ED35B3' }));
    expect(screen.getByRole('img', { name: '字幕示意' })).toBeInTheDocument();
    const want = { source: 'template', template: 'glow', color: '#2F80ED', secondary: '#ED35B3', saved_id: '', translation: null, song_info: true,
      ruby: 'romaji', ruby_target: 'kanji', video_audio: ['original', 'mix'], vocal_keep_pct: 35, effects: null,
      countdown_intro: null, countdown_interlude: null };
    // remembered right away (the next song starts from the same choices)
    await waitFor(() => expect(api.find('PUT', '/api/settings').at(-1)?.body).toEqual({ simple: { task_style: want } }), { timeout: 2000 });
    await userEvent.click(start);
    await waitFor(() => expect(api.find('POST', '/api/tasks')).toHaveLength(1));
    const fd = api.find('POST', '/api/tasks')[0].body as FormData;
    expect(fd.get('lyrics')).toBe('https://music.163.com/song?id=1');
    expect(fd.get('mode')).toBe('plain');
    expect((fd.get('file') as File).name).toBe('初恋.mp4');
    expect(JSON.parse(fd.get('style') as string)).toEqual(want);  // bound to this task
    // the form is ready for the next song, keeping the subtitle choices
    expect(screen.getByRole('button', { name: /开始制作/ })).toBeDisabled();
    expect(screen.getByRole('radio', { name: '荧光' })).toBeChecked();
    // a saved style must be picked before starting
    await userEvent.click(screen.getByRole('radio', { name: '保存的预设' }));
    expect(await screen.findByRole('option', { name: '默认（内置）' })).toBeInTheDocument();
  });

  it('shows the queue with progress and opens a finished task in the detailed mode', async () => {
    seed();
    const pv = fixturePV();
    const running = task({ id: 't2', name: '夜に駆ける', status: 'running', progress: 0.42, stages: stages(3, true), message: '人声分离 · 42%', project_id: pv.project.id });
    const done = task({ id: 't1', status: 'succeeded', progress: 1, stages: stages(7), project_id: pv.project.id,
      outputs: { video: { filename: 'a-karaoke.mp4', url: '/api/projects/p/exports/a-karaoke.mp4' } }, warnings: ['有 1 处建议检查（在“人工检查”的“有问题”里查看）'] });
    mockApi({
      'GET /api/tasks': () => [running, done],
      [`GET /api/projects/${pv.project.id}/jobs`]: () => [],
      [`GET /api/projects/${pv.project.id}`]: () => pv,
    });
    await act(async () => { await loadTasks(); });  // the app polls the queue (App.tsx), not this page
    renderUI(<SimpleHome />);
    const rows = await screen.findAllByRole('listitem', { name: undefined });
    expect(await screen.findByText('人声分离 · 42%')).toBeInTheDocument();
    expect(screen.getByText('42%')).toBeInTheDocument();
    const warn = screen.getByRole('button', { name: /有 1 处建议检查/ });
    expect(screen.getByRole('link', { name: /下载视频/ })).toHaveAttribute('href', '/api/projects/p/exports/a-karaoke.mp4');
    expect(rows.length).toBeGreaterThan(2);
    const doneRow = screen.getByRole('button', { name: '初恋' }).closest('li')!;
    await userEvent.click(within(doneRow).getByRole('button', { name: /详细模式/ }));
    await waitFor(() => expect(useSimple.getState().ui).toBe('pro'));
    expect(useApp.getState().pid).toBe(pv.project.id);
    expect(useApp.getState().step).toBe('karaoke');
    // the warning opens the review on the lines with problems
    act(() => useSimple.setState({ ui: 'simple' }));
    await userEvent.click(warn);
    await waitFor(() => expect(useApp.getState().step).toBe('review'));
    expect(useApp.getState().reviewFilter).toBe('issues');
  });
});

describe('a long task list', () => {
  it('a task that made several videos has a download for each sound', async () => {
    const v = (label: string, name: string) => ({ label, filename: name, url: `/api/projects/p/exports/${name}`, version: 'original' as const });
    const done = task({ id: 't1', status: 'succeeded', progress: 1, stages: stages(7), project_id: 'p',
      outputs: { video: v('原唱', 'a-karaoke.mp4'), videos: [v('原唱', 'a-karaoke.mp4'), v('伴唱', 'a-karaoke-vocal0.mp4')] } });
    mockApi({ 'GET /api/tasks': () => [done] });
    await act(async () => { await loadTasks(); });
    renderUI(<SimpleHome />);
    expect(await screen.findByRole('link', { name: /下载原唱/ })).toHaveAttribute('href', '/api/projects/p/exports/a-karaoke.mp4');
    expect(screen.getByRole('link', { name: /下载伴唱/ })).toHaveAttribute('href', '/api/projects/p/exports/a-karaoke-vocal0.mp4');
    expect(screen.queryByRole('link', { name: /下载视频/ })).toBeNull();
  });

  it('shows the newest five with their steps folded; older ones on request', async () => {
    seed();
    const done = Array.from({ length: 7 }, (_, i) => task({ id: `t${i}`, name: `歌 ${i}`, status: 'succeeded', project_id: `p${i}`, stages: stages(7),
      warnings: i === 0 ? ['歌词里有 2 行没有翻译', '有 3 处建议检查（在“人工检查”的“有问题”里查看）'] : [] }));
    const running = task({ id: 'tr', name: '进行中的歌', status: 'running', stages: stages(3, true) });
    useSimple.setState({ tasks: [running, ...done] });
    mockApi({ 'GET /api/tasks': () => useSimple.getState().tasks });
    renderUI(<SimpleHome />);
    expect(screen.getByText('进行中的歌')).toBeInTheDocument();
    expect(screen.getByText('歌 3')).toBeInTheDocument();
    expect(screen.queryByText('歌 4')).toBeNull();
    // a finished task: what needs a look stays, the steps and other notes fold
    const row = screen.getByText('歌 0').closest('li')!;
    expect(within(row).getByRole('button', { name: /有 3 处建议检查/ })).toBeInTheDocument();
    expect(within(row).queryByText('歌词里有 2 行没有翻译')).toBeNull();
    expect(within(row).queryByRole('list', { name: '处理步骤' })).toBeNull();
    await userEvent.click(within(row).getByRole('button', { name: /处理详情（7 步完成 · 1 条说明）/ }));
    expect(within(row).getByText('歌词里有 2 行没有翻译')).toBeInTheDocument();
    expect(within(row).getByRole('list', { name: '处理步骤' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '显示更早的 3 个任务' }));
    expect(screen.getByText('歌 6')).toBeInTheDocument();
  });
});

describe('simple mode settings', () => {
  it('saves each change and never shows the API key', async () => {
    seed();
    const api = mockApi({
      'GET /api/karaoke/styles': () => [builtinSaved()],
      'GET /api/fonts': () => ({ default: '', families: [] }),
      'GET /api/ai/providers': () => [
        { id: 'claude', label: 'Claude Code', available: true, version: '2.1 (Claude Code)', detail: '/bin/claude' },
        { id: 'codex', label: 'Codex', available: false, version: null, detail: '' },
        { id: 'openai', label: 'OpenAI 兼容 API', available: true, version: null, detail: '' },
      ],
      'PUT /api/settings': (c) => {
        const s = structuredClone(useSimple.getState().settings!);
        Object.assign(s.ai, c.body.ai ?? {});
        Object.assign(s.simple, c.body.simple ?? {});
        if (c.body.ai?.api_key) { s.ai.has_api_key = true; delete (s.ai as any).api_key; }
        return s;
      },
    });
    renderUI(<SimpleSettings />);
    // off: no choices; switched on, the four ways to reach an AI
    expect(screen.queryByRole('radio', { name: /Claude Code/ })).toBeNull();
    await userEvent.click(screen.getByRole('switch', { name: '使用 AI 注音' }));
    await waitFor(() => expect(useSimple.getState().settings!.ai.enabled).toBe(true));
    expect(screen.getByRole('radio', { name: /手动（网页聊天）/ })).toHaveAttribute('aria-checked', 'true');
    const claude = await screen.findByRole('radio', { name: /Claude Code/ });
    expect(within(claude).getByText('已安装')).toBeInTheDocument();
    expect(within(screen.getByRole('radio', { name: /Codex/ })).getByText('未找到')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: /OpenAI 兼容 API/ }));
    await waitFor(() => expect(useSimple.getState().settings!.ai.provider).toBe('openai'));
    const key = screen.getByPlaceholderText('sk-…');
    await userEvent.type(key, 'sk-test{Enter}');
    await waitFor(() => expect(api.find('PUT', '/api/settings').some((c) => c.body.ai?.api_key === 'sk-test')).toBe(true));
    expect(key).toHaveValue('');
    expect(await screen.findByPlaceholderText('••••••••（已保存）')).toBeInTheDocument();
    // one category at a time; the video's sound is chosen per song on the task form (one place), not here
    expect(screen.queryByText(/每首歌在“制作”页第 4 步选择/)).toBeNull();
    await userEvent.click(screen.getByRole('tab', { name: '输出视频' }));
    expect(screen.queryByPlaceholderText('sk-…')).toBeNull();
    expect(screen.queryByRole('button', { name: /降低人声/ })).toBeNull();
    expect(screen.getByText(/每首歌在“制作”页第 4 步选择/)).toBeInTheDocument();
    // the category is remembered
    cleanupRender();
    renderUI(<SimpleSettings />);
    expect(screen.getByRole('tab', { name: '输出视频' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('where Claude Code / Codex run from', () => {
  it('found automatically, chosen from the places found, or a typed path', async () => {
    seed({ ...SETTINGS, ai: { ...SETTINGS.ai, enabled: true, provider: 'claude' } });
    const loc = (source: 'path' | 'app' | 'wsl', program: string, distro = '') => ({
      source, program, distro, version: '2.1.284 (Claude Code)', where: source === 'wsl' ? `wsl:${distro}` : source,
      label: source === 'wsl' ? `WSL（${distro}）` : source === 'app' ? '桌面应用自带' : '命令行（PATH）' });
    let providerCalls = 0;
    const api = mockApi({
      'GET /api/karaoke/styles': () => [builtinSaved()],
      'GET /api/fonts': () => ({ default: '', families: [] }),
      'GET /api/ai/providers': () => {
        providerCalls++;
        return [{ id: 'claude', label: 'Claude Code', available: true, version: '2.1.284 (Claude Code)', detail: '',
          locations: [loc('app', 'C:/Users/u/AppData/Roaming/Claude/claude-code/2.1.284/claude.exe'), loc('wsl', '/home/u/.nvm/bin/claude', 'Ubuntu')],
          where: 'auto', chosen: loc('app', 'C:/Users/u/AppData/Roaming/Claude/claude-code/2.1.284/claude.exe') },
        { id: 'codex', label: 'Codex', available: false, version: null, detail: '没有找到 codex', locations: [], where: 'auto', chosen: null }];
      },
      'PUT /api/settings': (c) => {
        const s = structuredClone(useSimple.getState().settings!);
        Object.assign(s.ai, c.body.ai ?? {});
        return s;
      },
    });
    renderUI(<SimpleSettings />);
    const where = await screen.findByRole('combobox', { name: '运行位置' });
    expect(screen.getByText(/正在使用：桌面应用自带 · 2.1.284/)).toBeInTheDocument();
    expect(within(where).getByRole('option', { name: /WSL（Ubuntu）/ })).toBeInTheDocument();
    await userEvent.selectOptions(where, 'wsl:Ubuntu');
    await waitFor(() => expect(api.find('PUT', '/api/settings').at(-1)?.body.ai.claude_cli).toEqual({ where: 'wsl:Ubuntu', path: '' }));
    await waitFor(() => expect(providerCalls).toBeGreaterThan(1));  // detected again for the new choice
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '运行位置' }), 'custom');
    const path = await screen.findByRole('textbox', { name: '程序路径' });
    await userEvent.type(path, '/opt/claude/bin/claude{Enter}');
    await waitFor(() => expect(api.find('PUT', '/api/settings').at(-1)?.body.ai.claude_cli).toEqual({ where: 'custom', path: '/opt/claude/bin/claude' }));
  });
});

describe('subtitle style panel in the simple-mode settings', () => {
  const settingsServer = (extra: Record<string, (c: any) => unknown> = {}) => mockApi({
    'GET /api/karaoke/styles': () => [builtinSaved(), ...savedExtra],
    'POST /api/karaoke/styles': (c) => { const x = { id: 'st1', name: c.body.name, builtin: false, updated: 'z', style: { ...c.body.style, preset: c.body.name } }; savedExtra = [x]; return x; },
    'GET /api/fonts': () => ({ default: 'Hiragino Sans', families: [] }),
    'GET /api/ai/providers': () => [],
    'GET /api/projects/p1/karaoke': () => ({ ...defaultStyle(), ruby: { ...defaultStyle().ruby, script: 'katakana' } }),
    'PUT /api/settings': (c) => {
      const st = structuredClone(useSimple.getState().settings!);
      Object.assign(st.simple, c.body.simple ?? {});
      return st;
    },
    ...extra,
  });
  let savedExtra: any[] = [];
  beforeEach(() => { savedExtra = []; useLibraryReset(); });

  it('edits the full style, saves it as a preset and copies from a project', async () => {
    seed();
    useApp.setState({ projects: [{ id: 'p1', name: '初恋组曲 Karaoke', mode: 'lrc', updated: 'z' }] });
    const api = settingsServer();
    renderUI(<SimpleSettings />);
    await userEvent.click(screen.getByRole('tab', { name: '字幕样式' }));
    // the built-in 默认 preset is selected; the colours are shown, the other categories are tabs
    expect(await screen.findByRole('combobox', { name: '预设' })).toHaveValue('default');
    expect(screen.getByRole('tab', { name: '配色' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '特效' })).toHaveAttribute('aria-selected', 'false');
    // the glow edge is part of the lyric style; effects fire around each sung syllable
    await userEvent.click(screen.getByRole('tab', { name: '歌词' }));
    await userEvent.click(screen.getByRole('switch', { name: /荧光边缘/ }));
    expect(screen.getByLabelText('荧光大小（输入数值）')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: '特效' }));
    expect(screen.queryByRole('switch', { name: /荧光边缘/ })).toBeNull(); // it lives in 歌词 only
    await userEvent.click(screen.getByRole('radio', { name: '花瓣飘落' }));
    // particles sit behind the text unless asked otherwise
    expect(screen.getByRole('switch', { name: /放在字幕后面/ })).toBeChecked();
    await userEvent.click(screen.getByRole('switch', { name: /放在字幕后面/ }));
    expect(screen.getByText(/使用荧光边缘（唱过后）的颜色/)).toBeInTheDocument();
    await waitFor(() => {
      const k = api.find('PUT', '/api/settings').at(-1)?.body.simple.karaoke as KaraokeStyle | undefined;
      expect(k?.glow.enabled).toBe(true);
      expect(k?.effects.kind).toBe('petals');
      expect(k?.effects.behind).toBe(false);
    }, { timeout: 2000 });
    // ruby swept together with the lyric below it
    await userEvent.click(screen.getByRole('tab', { name: '注音' }));
    await userEvent.click(screen.getByRole('radio', { name: '与歌词对齐' }));
    expect(screen.getByText(/上下一条竖线扫过/)).toBeInTheDocument();
    await waitFor(() => expect((api.find('PUT', '/api/settings').at(-1)?.body.simple.karaoke as KaraokeStyle).ruby.sweep).toBe('base'), { timeout: 2000 });
    // song info card: a switch and the lines to show (no free text without a song)
    await userEvent.click(screen.getByRole('tab', { name: '歌曲信息' }));
    await userEvent.click(screen.getByRole('switch', { name: '显示歌曲信息（开头，以及结尾）' }));
    await userEvent.click(screen.getByRole('checkbox', { name: '显示作词' }));
    await userEvent.click(screen.getByRole('radio', { name: '右上角' }));
    expect(screen.queryByRole('textbox', { name: '歌曲信息文字' })).toBeNull();
    await waitFor(() => expect((api.find('PUT', '/api/settings').at(-1)?.body.simple.karaoke as KaraokeStyle).info)
      .toMatchObject({ enabled: true, position: 'top-right', fields: ['title', 'artist', 'lyricist'] }), { timeout: 2000 });
    expect(screen.getByText('已修改')).toBeInTheDocument();
    // save as a named preset
    await userEvent.click(screen.getByRole('button', { name: '另存为' }));
    await userEvent.type(screen.getByRole('textbox', { name: '预设名称' }), '樱花荧光{Enter}');
    await waitFor(() => expect(api.find('POST', '/api/karaoke/styles')[0]?.body.name).toBe('樱花荧光'));
    expect(api.find('POST', '/api/karaoke/styles')[0].body.style.glow.enabled).toBe(true);
    await waitFor(() => expect(screen.getByRole('combobox', { name: '预设' })).toHaveValue('st1'));
    // switching back to 默认 restores it
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '预设' }), 'default');
    await waitFor(() => expect((api.find('PUT', '/api/settings').at(-1)!.body.simple.karaoke as KaraokeStyle).glow.enabled).toBe(false), { timeout: 2000 });
    // take a project's style
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '从项目复制样式' }), 'p1');
    await waitFor(() => expect((api.find('PUT', '/api/settings').at(-1)!.body.simple.karaoke as KaraokeStyle).ruby.script).toBe('katakana'), { timeout: 2000 });
  });

  it('translation has its own style and the display advance / fades live under 时间', async () => {
    seed();
    const api = settingsServer();
    renderUI(<SimpleSettings />);
    await userEvent.click(screen.getByRole('tab', { name: '字幕样式' }));
    await userEvent.click(await screen.findByRole('tab', { name: '翻译' }));
    await userEvent.click(screen.getByRole('switch', { name: /显示翻译字幕/ }));
    await userEvent.click(screen.getByRole('radio', { name: '歌词旁' }));
    await waitFor(() => {
      const k = api.find('PUT', '/api/settings').at(-1)?.body.simple.karaoke as KaraokeStyle | undefined;
      expect(k?.translation).toMatchObject({ enabled: true, position: 'block', size_pct: 60 });
    }, { timeout: 2000 });
    expect(screen.getByRole('combobox', { name: '翻译字体' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: '时间' }));
    expect(screen.getByRole('textbox', { name: '淡入（输入数值）' })).toHaveValue('200');
    await userEvent.click(screen.getByRole('switch', { name: /歌词提前显示/ }));
    await waitFor(() => expect((api.find('PUT', '/api/settings').at(-1)!.body.simple.karaoke as KaraokeStyle).timing.advance_ms).toBe(150), { timeout: 2000 });
  });
});

describe('simple mode shell', () => {
  it('switches between the pages and to the detailed mode', async () => {
    seed();
    mockApi({ 'GET /api/tasks': () => [], 'GET /api/karaoke/styles': () => [builtinSaved()], 'GET /api/fonts': () => ({ default: '', families: [] }), 'GET /api/ai/providers': () => [] });
    renderUI(<SimpleApp />);
    expect(await screen.findByText('做一首卡拉OK')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '设置' }));
    expect(await screen.findByRole('heading', { level: 1, name: '设置' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /详细模式/ }));
    expect(useSimple.getState().ui).toBe('pro');
    expect(localStorage.getItem('kara.ui')).toBe('pro');
  });

  it('each page keeps its own scroll position; another settings category starts at the top', async () => {
    seed();
    mockApi({ 'GET /api/tasks': () => [], 'GET /api/karaoke/styles': () => [builtinSaved()], 'GET /api/fonts': () => ({ default: '', families: [] }),
      'GET /api/ai/providers': () => [], 'GET /api/storage': () => new Response('{}', { status: 404 }) });
    const { container } = renderUI(<SimpleApp />);
    await screen.findByText('做一首卡拉OK');
    const main = container.querySelector('main')!;
    main.scrollTop = 900;
    await userEvent.click(screen.getByRole('button', { name: '设置' }));
    await screen.findByRole('heading', { level: 1, name: '设置' });
    expect(main.scrollTop).toBe(0);  // first visit: from the top
    main.scrollTop = 120;
    await userEvent.click(screen.getByRole('button', { name: '制作' }));
    await screen.findByText('做一首卡拉OK');
    expect(main.scrollTop).toBe(900);  // back where it was on 制作
    await userEvent.click(screen.getByRole('button', { name: '设置' }));
    expect(main.scrollTop).toBe(120);
    await userEvent.click(screen.getByRole('tab', { name: '字幕样式' }));
    container.querySelector('main')!.scrollTop = 700;
    await userEvent.click(screen.getByRole('tab', { name: '输出视频' }));
    expect(container.querySelector('main')!.scrollTop).toBe(0);
  });
});

describe('one-click AI readings in the detailed mode', () => {
  it('sends the prompt through the configured CLI and shows the validated preview', async () => {
    const { seedStore } = await import('@/test/helpers');
    const { AiRoundtripCard } = await import('@/pages/enhance/AiRoundtrip');
    const pv = seedStore('enhance');
    useSimple.setState({ settings: { ...structuredClone(SETTINGS), ai: { ...SETTINGS.ai, enabled: true, provider: 'claude', model: 'sonnet' } }, providers: [] });
    const line = pv.project.lyrics.lines.find((l) => l.sing)!;
    const report = { ok: true, snapshot: 'snap-1', roundtrip_id: 'rt', errors: [], warnings: [], missing_line_ids: [],
      lines: [{ line_id: line.id, status: 'ok', reasons: [], segments: [], diff: [{ surface: '君', old_reading: 'くん', new_reading: 'きみ', old_units: ['く', 'ん'], new_units: ['き', 'み'], changed: true, locked: false }] }] };
    const api = mockApi({
      [`POST /api/projects/${pv.project.id}/ai/auto`]: () => ({ id: 'jai', kind: 'ai', project_id: pv.project.id, status: 'running', progress: 0.1, message: '等待 Claude Code 回复…', error: null, created: 'z', finished: null, output: null }),
      'GET /api/jobs/jai': () => ({ id: 'jai', kind: 'ai', project_id: pv.project.id, status: 'succeeded', progress: 1, message: '完成', error: null, created: 'z', finished: 'z',
        output: { report_id: 'rep1', report, meta: { provider: 'claude', attempts: [{ provider: 'claude', model: 'sonnet', elapsed_s: 12.3, cost_usd: 0.02 }], cost_usd: 0.02 } } }),
    });
    renderUI(<AiRoundtripCard />);
    expect(screen.getByText('Claude Code · sonnet')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /开始 AI 注音/ }));
    await waitFor(() => expect(api.find('POST', '/ai/auto')).toHaveLength(1));
    expect(await screen.findByText(/已收到回复：12 秒 · sonnet · \$0\.020/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /应用所选 1 行/ })).toBeEnabled();
  });
});

describe('the new-task form', () => {
  it('sends the audio with a background picture, and keeps the background for the next song', async () => {
    seed();
    const api = mockApi({ 'GET /api/tasks': () => [], 'POST /api/tasks': () => task({ id: 'tb', status: 'preparing' }) });
    const { container } = renderUI(<SimpleHome />);
    await userEvent.click(screen.getByRole('radio', { name: '音频 + 背景' }));
    const inputs = () => container.querySelectorAll('input[type=file]');
    await userEvent.upload(inputs()[0] as HTMLInputElement, new File(['a'], 'song.mp3', { type: 'audio/mpeg' }));
    await userEvent.upload(inputs()[0] as HTMLInputElement, new File(['p'], 'cover.png', { type: 'image/png' }));
    expect(screen.getByText(/^背景图片 ·/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '音乐链接或歌词' }), { target: { value: 'きみと' } });
    await userEvent.click(screen.getByRole('button', { name: /开始制作/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks')).toHaveLength(1));
    const fd = api.find('POST', '/api/tasks')[0].body as FormData;
    expect((fd.get('file') as File).name).toBe('song.mp3');
    expect((fd.get('background') as File).name).toBe('cover.png');
    await waitFor(() => expect(screen.queryByText('song.mp3')).not.toBeInTheDocument());
    expect(screen.getByText('cover.png')).toBeInTheDocument();  // the next song usually has the same one
    // back to "video": no background is sent
    await userEvent.click(screen.getByRole('radio', { name: '视频' }));
    await userEvent.upload(inputs()[0] as HTMLInputElement, new File(['v'], 'clip.mp4', { type: 'video/mp4' }));
    fireEvent.change(screen.getByRole('textbox', { name: '音乐链接或歌词' }), { target: { value: 'きみと' } });
    await userEvent.click(screen.getByRole('button', { name: /开始制作/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks')).toHaveLength(2));
    expect((api.find('POST', '/api/tasks')[1].body as FormData).get('background')).toBeNull();
    // leave the form as the other tests expect it
    await userEvent.click(screen.getByRole('radio', { name: '音频 + 背景' }));
    await userEvent.click(screen.getAllByRole('button', { name: /换一个/ }).at(-1)!);
    await userEvent.click(screen.getByRole('radio', { name: '视频' }));
    localStorage.clear();
  });

  it('keeps the chosen file, lyrics and name while the settings are open', async () => {
    seed();
    const api = mockApi({ 'GET /api/tasks': () => [], 'POST /api/tasks': () => task({ id: 'tn', status: 'preparing' }) });
    const first = renderUI(<SimpleHome />);
    await userEvent.upload(first.container.querySelector('input[type=file]') as HTMLInputElement, new File(['x'], 'song.mp4', { type: 'video/mp4' }));
    fireEvent.change(screen.getByRole('textbox', { name: '音乐链接或歌词' }), { target: { value: '[00:01.50]きみと' } });
    first.unmount();  // e.g. to the settings page and back
    renderUI(<SimpleHome />);
    expect(screen.getByText('song.mp4')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '音乐链接或歌词' })).toHaveValue('[00:01.50]きみと');
    // once the task is added the form starts empty, also after coming back
    await userEvent.click(screen.getByRole('button', { name: /开始制作/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks')).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole('textbox', { name: '音乐链接或歌词' })).toHaveValue(''));
  });
});

describe('AI readings by hand (web chat)', () => {
  const waitingReadings = () => task({ id: 'tr', status: 'waiting', project_id: 'p', mode: 'plain',
    readings_request: { roundtrip_id: 'rt1', snapshot_id: 'snap', lines: 12, chars: 3000 },
    stages: stages(2).map((s) => (s.key === 'readings' ? { ...s, status: 'waiting' as const } : s)) });

  it('copies the prompt, refuses an unusable reply and sends a usable one', async () => {
    seed();
    useSimple.setState({ tasks: [waitingReadings()] });
    let accept = false;
    const api = mockApi({
      'GET /api/tasks/tr/readings/prompt': () => ({ prompt: '你是日语歌词注音助手……', lines: 12, snapshot_id: 'snap' }),
      'GET /api/tasks': () => useSimple.getState().tasks,
      'POST /api/tasks/tr/readings': () => (accept ? { ...waitingReadings(), status: 'queued' }
        : new Response(JSON.stringify({ detail: '无法解析 AI 结果: 没有找到 JSON' }), { status: 400 })),
    });
    renderUI(<SimpleHome />);
    expect(await screen.findByText(/需要你把 AI 注音的提示词发给 AI 聊天网页/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /粘贴 AI 注音结果/ }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByRole('textbox', { name: '提示词' })).toHaveValue('你是日语歌词注音助手……');
    expect(within(dialog).getByRole('button', { name: '提交并继续' })).toBeDisabled();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'AI 的回复' }), { target: { value: '好的' } });
    await userEvent.click(within(dialog).getByRole('button', { name: '提交并继续' }));
    expect(await within(dialog).findByText(/没有找到 JSON/)).toBeInTheDocument();  // the dialog stays
    accept = true;
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'AI 的回复' }), { target: { value: '```json\n{}\n```' } });
    await userEvent.click(within(dialog).getByRole('button', { name: '提交并继续' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.find('POST', '/api/tasks/tr/readings').at(-1)!.body).toEqual({ text: '```json\n{}\n```' });
  });

  it('can be skipped', async () => {
    seed();
    useSimple.setState({ tasks: [waitingReadings()] });
    const api = mockApi({
      'GET /api/tasks/tr/readings/prompt': () => ({ prompt: 'p', lines: 12, snapshot_id: 'snap' }),
      'GET /api/tasks': () => useSimple.getState().tasks,
      'POST /api/tasks/tr/readings': () => ({ ...waitingReadings(), status: 'queued' }),
    });
    renderUI(<SimpleHome />);
    await userEvent.click(await screen.findByRole('button', { name: /粘贴 AI 注音结果/ }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /跳过/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks/tr/readings')).toHaveLength(1));
    expect(api.find('POST', '/api/tasks/tr/readings')[0].body).toEqual({ skip: true });
  });
});

describe('confirming the start before the task continues', () => {
  const cal = {
    line_id: 'L1', line_text: 'きみと', lrc_ms: 1500, lines: [{ id: 'L1', text: 'きみと', lrc_ms: 1500 }, { id: 'L2', text: 'あるいた', lrc_ms: 3500 }],
    check_line: { id: 'L2', text: 'あるいた', lrc_ms: 3500 }, asset_id: 'a1', duration_ms: 60000,
  };
  const waiting = () => task({ id: 'tw', status: 'waiting', project_id: 'p', calibration: cal,
    stages: stages(2).map((s) => (s.key === 'calibrate' ? { ...s, status: 'waiting' as const } : s)) });

  it('opens by itself for the task just added, and the marked position is sent', async () => {
    seed();
    const api = mockApi({
      'GET /api/tasks': () => useSimple.getState().tasks,
      'POST /api/tasks/tw/calibration': () => ({ ...waiting(), status: 'queued' }),
      'POST /api/tasks': () => task({ id: 'tw', status: 'preparing' }),
      'GET /api/projects/p/audio/a1/peaks': () => ({ per_second: 100, mins: [], maxs: [] }),
    });
    const { container } = renderUI(<SimpleHome />);
    await userEvent.upload(container.querySelector('input[type=file]') as HTMLInputElement, new File(['x'], 'a.mp4', { type: 'video/mp4' }));
    fireEvent.change(screen.getByRole('textbox', { name: '音乐链接或歌词' }), { target: { value: '[00:01.50]きみと' } });
    await userEvent.click(screen.getByRole('button', { name: /开始制作/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks')).toHaveLength(1));
    // import + lyrics are done: the task now waits for the user
    useSimple.setState({ tasks: [waiting()] });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('きみと')).toBeInTheDocument();
    expect(within(dialog).getByRole('textbox', { name: '标记时间' })).toHaveValue('0:01.500');
    expect(within(dialog).getByRole('button', { name: /从标记处播放/ })).toBeEnabled();
    expect(within(dialog).getByRole('button', { name: /标记前 2 秒开始/ })).toBeEnabled();
    expect(within(dialog).getByRole('button', { name: /试听中间一句「あるいた」/ })).toBeEnabled();
    await userEvent.click(within(dialog).getByRole('button', { name: '−100 ms' }));
    await userEvent.click(within(dialog).getByRole('button', { name: /确认并继续/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks/tw/calibration')).toHaveLength(1));
    expect(api.find('POST', '/api/tasks/tw/calibration')[0].body).toEqual({ marked_ms: 1400 });  // −100 ms nudge
  });

  it('after an unsure automatic detection the marker starts at its estimate and says why', async () => {
    seed();
    const unsure = () => ({ ...waiting(), calibration: { ...cal, auto: { shift_ms: -420, tight: 0.5, lines: 8, tight_lines: 4, reason: '只有 4/8 行对得上同一个偏移，歌词可能是别的版本', confident: false } } });
    useSimple.setState({ tasks: [unsure()] });
    const api = mockApi({
      'GET /api/tasks': () => [unsure()],
      'POST /api/tasks/tw/calibration': () => ({ ...unsure(), status: 'queued' }),
      'GET /api/projects/p/audio/a1/peaks': () => ({ per_second: 100, mins: [], maxs: [] }),
    });
    renderUI(<SimpleHome />);
    await userEvent.click(await screen.findByRole('button', { name: /确认开头位置/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('自动检测没有把握')).toBeInTheDocument();
    expect(within(dialog).getByText(/只有 4\/8 行对得上/)).toBeInTheDocument();
    expect(within(dialog).getByRole('textbox', { name: '标记时间' })).toHaveValue('0:01.080');
    await userEvent.click(within(dialog).getByRole('button', { name: /确认并继续/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks/tw/calibration')).toHaveLength(1));
    expect(api.find('POST', '/api/tasks/tw/calibration')[0].body).toEqual({ marked_ms: 1080 });
  });

  it('a waiting task shows the button; plain mode is one click', async () => {
    seed();
    useSimple.setState({ tasks: [waiting()] });
    const api = mockApi({
      'GET /api/tasks': () => [waiting()],
      'POST /api/tasks/tw/calibration': () => ({ ...waiting(), status: 'queued' }),
      'GET /api/projects/p/audio/a1/peaks': () => ({ per_second: 100, mins: [], maxs: [] }),
    });
    renderUI(<SimpleHome />);
    expect(await screen.findByText(/需要你确认第一句「きみと」/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /确认开头位置/ }));
    const dialog = await screen.findByRole('dialog');
    const input = within(dialog).getByRole('textbox', { name: '标记时间' });
    await userEvent.clear(input);
    await userEvent.type(input, '0:02.250{Enter}');
    expect(within(dialog).getByText('+750 ms')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /改普通模式/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks/tw/calibration')[0]?.body).toEqual({ plain: true }));
  });

  it('the marker moves with the arrow keys (Shift: 100 ms), not while typing the time', async () => {
    seed();
    useSimple.setState({ tasks: [waiting()] });
    const api = mockApi({
      'GET /api/tasks': () => [waiting()],
      'POST /api/tasks/tw/calibration': () => ({ ...waiting(), status: 'queued' }),
      'GET /api/projects/p/audio/a1/peaks': () => ({ per_second: 100, mins: [], maxs: [] }),
    });
    renderUI(<SimpleHome />);
    await userEvent.click(await screen.findByRole('button', { name: /确认开头位置/ }));
    const dialog = await screen.findByRole('dialog');
    const wave = within(dialog).getByRole('slider', { name: /波形/ });
    await waitFor(() => expect(wave).toHaveFocus());
    await userEvent.keyboard('{ArrowRight}{ArrowRight}{Shift>}{ArrowLeft}{/Shift}');
    expect(wave).toHaveAttribute('aria-valuenow', '1420');
    const input = within(dialog).getByRole('textbox', { name: '标记时间' });
    await userEvent.click(input);
    await userEvent.keyboard('{ArrowLeft}');
    expect(wave).toHaveAttribute('aria-valuenow', '1420');
    await userEvent.click(within(dialog).getByRole('button', { name: /确认并继续/ }));
    await waitFor(() => expect(api.find('POST', '/api/tasks/tw/calibration')[0]?.body).toEqual({ marked_ms: 1420 }));
  });
});

describe('detailed calibration page', () => {
  it('plays from the calibrated start and applies the automatic match only on request', async () => {
    const { seedStore } = await import('@/test/helpers');
    const { CalibratePage } = await import('@/pages/Calibrate');
    const { player } = await import('@/audio/player');
    const { vi } = await import('vitest');
    const pv = seedStore('calibrate');
    const play = vi.spyOn(player, 'play').mockImplementation(() => {});
    const line = pv.project.lyrics.lines.find((l) => pv.view.effective_starts[l.id])!;
    const eff = pv.view.effective_starts[line.id].ms;
    const base = line.imported_start_ms! + pv.project.lyrics.embedded_shift_ms;
    const api = mockApi({
      [`POST /api/projects/${pv.project.id}/calibration/suggest`]: () => ({ id: 'jc', kind: 'calibrate', project_id: pv.project.id, status: 'running', progress: 0, message: '', error: null, created: 'z', finished: null, output: null }),
      'GET /api/jobs/jc': () => ({ id: 'jc', kind: 'calibrate', project_id: pv.project.id, status: 'succeeded', progress: 1, message: '完成', error: null, created: 'z', finished: 'z',
        output: { shift_ms: -180, agree: 0.95, tight: 0.92, confident: true, reason: '', lines_checked: 48, audio_role: 'vocals', vocal_onset_ms: 12000, line_starts: {} } }),
      [`POST /api/projects/${pv.project.id}/calibration/shift`]: () => pv,
    });
    renderUI(<CalibratePage />);
    await userEvent.click(await screen.findByRole('button', { name: /从校准点播放/ }));
    expect(play).toHaveBeenLastCalledWith(eff);
    await userEvent.click(screen.getByRole('button', { name: /从有效句首前 2 秒播放/ }));
    expect(play).toHaveBeenLastCalledWith(Math.max(0, eff - 2000));
    await userEvent.click(screen.getByRole('button', { name: /自动匹配/ }));
    expect(await screen.findByText('44/48 行一致（±0.3 秒）', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(api.find('POST', '/calibration/shift')).toHaveLength(0);  // nothing changes by itself
    await userEvent.click(screen.getByRole('button', { name: /试听建议位置/ }));
    expect(play).toHaveBeenLastCalledWith(Math.max(0, base - 180));
    await userEvent.click(screen.getByRole('button', { name: '应用建议' }));
    await waitFor(() => expect(api.find('POST', '/calibration/shift')[0]?.body).toEqual({ user_shift_ms: -180 }));
    play.mockRestore();
  });
});

describe('project list', () => {
  it('entering the detailed mode reloads the project list', async () => {
    seed();
    const { setUi } = await import('@/store/simple');
    mockApi({ 'GET /api/projects': () => [{ id: 'pw', name: 'わたぐも - 黒沢ともよ', mode: 'lrc', updated: 'z' }] });
    setUi('pro');
    await waitFor(() => expect(useApp.getState().projects.map((p) => p.name)).toEqual(['わたぐも - 黒沢ともよ']));
  });
});
