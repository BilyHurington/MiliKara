// The two modes share projects: the simple mode's queue is watched in the detailed mode, the open
// project follows a task working on it, and nothing the user did gets lost on the way.
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TaskBusyBanner } from '@/App';
import type { AppSettings, PipelineTask } from '@/lib/types';
import { trackJob, useApp } from '@/store/app';
import { loadTasks, setSimpleDefault, useSimple } from '@/store/simple';
import { fixturePV, mockApi, renderUI, seedStore } from '@/test/helpers';
import { builtinSaved, defaultStyle, plainStyle } from '@/test/style';

const PID = fixturePV().project.id;
const stages = (done: number) => ['import', 'lyrics', 'calibrate', 'readings', 'separate', 'align', 'export'].map((key, i) => ({
  key, label: key, progress: i < done ? 1 : 0, message: '', status: (i < done ? 'done' : i === done ? 'running' : 'pending') as PipelineTask['stages'][number]['status'],
}));
const task = (over: Partial<PipelineTask>): PipelineTask => ({
  id: 't1', created: 'z', finished: null, name: '初恋', mode: 'lrc', media_filename: 'a.mp4', lyrics_kind: 'link', lyrics_input: 'x',
  status: 'running', project_id: PID, stages: stages(4), progress: 0.5, message: '人声分离 · 40%', error: null, detail: null, warnings: [], outputs: {}, ...over,
});

describe('detailed mode ↔ simple-mode tasks', () => {
  it('shows a task working on the open project, and reloads the project as the task moves on', async () => {
    seedStore('review');
    useSimple.setState({ tasks: [], ui: 'pro' });
    let current = task({});
    const api = mockApi({
      'GET /api/tasks': () => [current],
      [`GET /api/projects/${PID}`]: () => fixturePV(),
    });
    await act(async () => { await loadTasks(); });
    renderUI(<TaskBusyBanner />);
    expect(screen.getByText(/极简模式任务「初恋」正在处理这个项目（人声分离 · 40%）/)).toBeInTheDocument();
    expect(screen.getByText(/完成前这里不能对齐/)).toBeInTheDocument();
    const reloads = () => api.find('GET', `/api/projects/${PID}`).filter((c) => c.url.endsWith(PID)).length;
    const before = reloads();
    await act(async () => { await loadTasks(); });  // nothing changed: no reload
    expect(reloads()).toBe(before);
    current = task({ stages: stages(5), message: '对齐' });  // separation finished
    await act(async () => { await loadTasks(); });
    expect(reloads()).toBe(before + 1);
    current = task({ status: 'succeeded', stages: stages(7), message: '' });
    await act(async () => { await loadTasks(); });
    expect(screen.queryByText(/正在处理这个项目/)).toBeNull();
  });

  it('"设为极简默认" makes the next tasks use the style as a whole', async () => {
    const style = { ...plainStyle(), preset: '' };
    const settings = { version: 1, ai: {} as AppSettings['ai'], simple: { karaoke: defaultStyle(), task_style: {
      source: 'template', template: 'glow', color: '#FF8A1E', secondary: '#F5C400', saved_id: '', translation: true, song_info: true,
      ruby: 'hiragana', ruby_target: 'kanji', video_audio: ['mix'], vocal_keep_pct: 35 } } } as unknown as AppSettings;
    useSimple.setState({ settings });
    const api = mockApi({ 'PUT /api/settings': (c) => ({ ...settings, simple: { ...settings.simple, ...c.body.simple } }) });
    await setSimpleDefault(style);
    const body = api.find('PUT', '/api/settings')[0].body.simple;
    expect(body.karaoke).toEqual(style);
    // step ④ switches to the settings' style, its switches follow the style; the video choices stay
    expect(body.task_style).toEqual({ ...settings.simple.task_style, source: 'default', translation: null, song_info: null, ruby: 'style', ruby_target: null });
  });

  it('a job the restarted server no longer knows is shown as interrupted instead of running forever', async () => {
    seedStore('align');
    mockApi({ 'GET /api/jobs/': () => new Response(JSON.stringify({ detail: '没有这个任务' }), { status: 404 }) });
    const onDone = vi.fn();
    trackJob({ id: 'gone', kind: 'align', project_id: PID, status: 'running', progress: 0.3, message: '', error: null, created: 'z', finished: null, output: null },
      { label: '对齐', onDone });
    await waitFor(() => expect(useApp.getState().jobs.gone?.status).toBe('failed'));
    expect(useApp.getState().jobs.gone?.error).toContain('本地服务已重启');
    expect(onDone).toHaveBeenCalled();
  });

  it('a style edit still waiting to be saved is saved when the page is left, and before burning', async () => {
    seedStore('karaoke');
    const style = plainStyle();
    const api = mockApi({
      'GET /api/fonts': () => ({ default: 'Hiragino Sans', families: [] }),
      'GET /api/karaoke/styles': () => [builtinSaved()],
      [`GET /api/projects/${PID}/karaoke/info`]: () => ({ fields: {}, labels: {}, text: null }),
      [`GET /api/projects/${PID}/karaoke`]: () => style,
      [`PUT /api/projects/${PID}/karaoke`]: (c) => c.body,
      [`POST /api/projects/${PID}/karaoke/preview`]: () => new Response(new Blob(['png']), { status: 200 }),
      [`POST /api/projects/${PID}/karaoke/burn`]: () => ({ id: 'jb', kind: 'burn', project_id: PID, status: 'queued', progress: 0, message: '', error: null, created: 'z', finished: null, output: null }),
      'GET /api/jobs/': () => ({ id: 'jb', kind: 'burn', project_id: PID, status: 'running', progress: 0.1, message: '', error: null, created: 'z', finished: null, output: null }),
    });
    (URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = vi.fn();
    const { KaraokePage } = await import('./Karaoke');
    const view = renderUI(<KaraokePage />);
    await userEvent.click(await screen.findByRole('tab', { name: '歌词' }));
    await userEvent.click(screen.getAllByRole('switch', { name: '粗体' })[0]);
    // burning right away: the edit is saved before the burn starts
    await userEvent.click(screen.getByRole('button', { name: /一键烧录/ }));
    await waitFor(() => expect(api.find('POST', '/karaoke/burn')).toHaveLength(1));
    const order = api.calls.filter((c) => c.url.includes('/karaoke') && ['PUT', 'POST'].includes(c.method) && !c.url.endsWith('/preview')).map((c) => c.method);
    expect(order.slice(0, 2)).toEqual(['PUT', 'POST']);
    expect(api.find('PUT', `/api/projects/${PID}/karaoke`)[0].body.text.bold).toBe(!style.text.bold);
    // another edit, then leaving the page before the autosave fires
    await userEvent.click(screen.getAllByRole('switch', { name: '粗体' })[0]);
    view.unmount();
    await waitFor(() => expect(api.find('PUT', `/api/projects/${PID}/karaoke`)).toHaveLength(2));
    expect(api.find('PUT', `/api/projects/${PID}/karaoke`)[1].body.text.bold).toBe(style.text.bold);
  });
});

describe('cleaning up', () => {
  it('deletes a project only after confirming, and says what goes with it', async () => {
    seedStore('mode');
    useApp.setState({ pid: null, pv: null, projects: [{ id: 'p9', name: '旧歌', mode: 'plain', updated: '2026-09-01T00:00:00+00:00' }] });
    let list = [{ id: 'p9', name: '旧歌', mode: 'plain', updated: '2026-09-01T00:00:00+00:00' }];
    const api = mockApi({ 'DELETE /api/projects/p9': () => { list = []; return { ok: true }; }, 'GET /api/projects': () => list });
    const { HomePage } = await import('./Home');
    renderUI(<HomePage />);
    await userEvent.click(screen.getByRole('button', { name: '删除项目 旧歌' }));
    expect(screen.getByText(/导出的视频都会一起删除，无法恢复/)).toBeInTheDocument();
    expect(api.find('DELETE', '/api/projects/p9')).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(api.find('DELETE', '/api/projects/p9')).toHaveLength(1));
    await waitFor(() => expect(useApp.getState().projects).toEqual([]));
  });

  it('removing a finished task asks first and says the project stays', async () => {
    const done = task({ id: 't5', status: 'succeeded', stages: stages(7), progress: 1, message: '' });
    useSimple.setState({ ui: 'simple', page: 'home', tasks: [done], settings: null });
    const api = mockApi({ 'GET /api/tasks': () => [], 'DELETE /api/tasks/t5': () => ({ ok: true }) });
    const { SimpleHome } = await import('./simple/SimpleHome');
    renderUI(<SimpleHome />);
    await userEvent.click(screen.getByRole('button', { name: '移除任务' }));
    expect(screen.getByText(/项目和视频仍保留在详细模式/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '移除' }));
    await waitFor(() => expect(api.find('DELETE', '/api/tasks/t5')).toHaveLength(1));
  });

  it('a saved style that no longer exists is cleared from the task form', async () => {
    const { TaskStyleStep } = await import('./simple/TaskStyleStep');
    const { useLibrary } = await import('@/store/styles');
    useLibrary.setState({ saved: [builtinSaved()] });
    mockApi({});
    const onChange = vi.fn();
    const simple = { karaoke: defaultStyle(), auto_export: true, separate: true, video_audio: ['original'], vocal_keep_pct: 20 } as unknown as AppSettings['simple'];
    renderUI(<TaskStyleStep value={{ source: 'saved', template: 'glow', color: '#FF8A1E', secondary: '', saved_id: 'st_gone', translation: null,
      song_info: null, ruby: 'style', video_audio: null }} onChange={onChange} settings={simple} />);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ saved_id: '' })));
  });
});

describe('third review round', () => {
  it('a project refresh answered after another project was opened is dropped', async () => {
    seedStore('review');
    const pv = fixturePV();
    let answer: (v: Response) => void = () => {};
    mockApi({ [`GET /api/projects/${PID}`]: () => new Promise<Response>((r) => { answer = r; }) });
    const { refreshProject } = await import('@/store/app');
    const pending = refreshProject();
    useApp.setState({ pid: 'p_other' });  // the user opened another project meanwhile
    answer(new Response(JSON.stringify(pv), { status: 200 }));
    await pending;
    expect(useApp.getState().pid).toBe('p_other');
  });

  it('a task whose project was deleted shows it and offers no dead links', async () => {
    const gone = task({ id: 't7', status: 'succeeded', stages: stages(7), progress: 1, message: '项目已删除', project_deleted: true, outputs: {} });
    useSimple.setState({ ui: 'simple', page: 'home', tasks: [gone], settings: null });
    mockApi({ 'GET /api/tasks': () => [gone] });
    const { SimpleHome } = await import('./simple/SimpleHome');
    renderUI(<SimpleHome />);
    expect(screen.getByText('项目已删除')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /详细模式/ })).toBeNull();
    expect(screen.queryByRole('link', { name: /下载视频/ })).toBeNull();
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull();
  });

  it('the offset dialog starts from an offset already set in the detailed mode', async () => {
    const waiting = task({ id: 'tw', status: 'waiting', calibration: {
      line_id: 'L1', line_text: 'きみと', lrc_ms: 1500, lines: [{ id: 'L1', text: 'きみと', lrc_ms: 1500 }], check_line: null,
      asset_id: null, duration_ms: 7000, current_ms: 1200, lines_after_audio: 2, lines_total: 5 } });
    mockApi({});
    const { CalibrateDialog } = await import('./simple/CalibrateDialog');
    renderUI(<CalibrateDialog task={waiting} onClose={() => {}} />);
    expect(screen.getByText(/详细模式里已设置的偏移（-300 ms）/)).toBeInTheDocument();
    // a shortened video: said before anything runs
    expect(screen.getByText('有 2 行歌词的时间在音频结束之后')).toBeInTheDocument();
  });

  it('the banner says a task is still reading its video', () => {
    seedStore('review');
    useSimple.setState({ tasks: [task({ status: 'preparing', message: '' })] });
    renderUI(<TaskBusyBanner />);
    expect(screen.getByText(/（读取视频和歌词）/)).toBeInTheDocument();
  });
});
