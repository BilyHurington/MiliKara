"""Fifth review round: backend security, data integrity and orchestration."""

import io
import json
import os
import stat
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from kara_align import pipeline as P
from kara_align import service as S
from kara_align import settings as AS
from kara_align.interfaces import CancelToken, Cancelled
from kara_align.models import AiRoundtrip, AudioAsset, MixSettings, Project
from kara_align.project import store
from kara_align.project.jobs import Job, JobManager
from kara_align.reading import llm


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    llm._detect_cache.clear()


def _wav(path, seconds=2.0, sr=22050):
    rng = np.random.default_rng(0)
    sf.write(path, (rng.standard_normal(int(seconds * sr)) * 0.05).astype(np.float32), sr)
    return path


def _app(tmp_path, monkeypatch=None, *, idle=True):
    from kara_align.web.server import create_app

    if monkeypatch is not None and idle:
        monkeypatch.setattr(P.TaskQueue, "_worker", lambda self: None)  # nothing runs
        monkeypatch.setattr(P.TaskQueue, "_prepare", lambda self, t: None)
    return TestClient(create_app(tmp_path / "projects"))


def _project_json(**over) -> str:
    p = Project(name="imported").model_dump(mode="json")
    p.update(over)
    return json.dumps(p)


def _stages():
    return [P.Stage(key=k, label=k) for k, _, _ in P.STAGES]


# ------------------------------------------------------------------ S1 separation preset


def test_separation_preset_must_be_a_known_one(tmp_path, monkeypatch):
    from kara_align.audio import separation as sep
    from kara_align.cli import build_parser

    with pytest.raises(sep.SeparationError):
        sep.get_preset("../../evil")
    assert sep.get_preset("melband-roformer").name == "melband-roformer"
    h = S.create_dir(tmp_path / "p", "x", "plain")
    S.add_audio(h, _wav(tmp_path / "a.wav"), "original")
    called = []
    monkeypatch.setattr(sep, "separate", lambda *a, **k: called.append(a))
    victim = tmp_path / "home" / "victim"
    victim.mkdir(parents=True)
    with pytest.raises(S.ServiceError, match="未知的分离预设"):
        S.run_separation(h, "../../victim")
    assert victim.exists() and not called
    with pytest.raises(ValueError):
        AS.update({"simple": {"separation_preset": "../x"}})
    with pytest.raises(SystemExit):
        build_parser().parse_args(["separate", "proj", "--preset", "../x"])
    client = _app(tmp_path, monkeypatch)
    pid = client.post("/api/projects", json={"name": "x"}).json()["project"]["id"]
    r = client.post(f"/api/projects/{pid}/separate", json={"preset": "../../x"})
    assert r.status_code == 400 and "未知的分离预设" in r.json()["detail"]


def test_separation_cache_folder_is_a_hash_and_has_a_timeout(tmp_path, monkeypatch):
    from kara_align.audio import separation as sep

    h = S.create_dir(tmp_path / "p", "x", "plain")
    S.add_audio(h, _wav(tmp_path / "a.wav", seconds=2.0), "original")
    seen = {}

    def fake(src, out_dir, preset, cancel=None, progress=None, device="auto", stall_s=None):
        seen.update(out_dir=Path(out_dir), stall_s=stall_s)
        raise sep.SeparationError("stop here")

    monkeypatch.setattr(sep, "separate", fake)
    with pytest.raises(sep.SeparationError):
        S.run_separation(h, "mdx-fast")
    root = store.cache_dir("separation")
    assert seen["out_dir"].parent == root and len(seen["out_dir"].name) == 32
    assert not seen["out_dir"].exists()  # a failed run leaves nothing behind
    assert seen["stall_s"] == S.SEPARATION_STALL_S  # stopped only when it stops making progress


# ------------------------------------------------------------------ S2 / S3 / S10 imports


def test_imported_project_gets_a_fresh_id_inside_the_workspace(tmp_path):
    ws = S.Workspace(tmp_path / "projects")
    src = tmp_path / "project.json"
    src.write_text(_project_json(id="../../outside"), encoding="utf-8")
    h = ws.import_file(src, "project.json")
    assert h.project.id != "../../outside" and h.dir.parent == ws.root
    assert not (tmp_path / "outside").exists()
    assert [p["id"] for p in ws.list()] == [h.project.id]
    with pytest.raises(ValueError):
        Project(id="../x")
    for bad in ("..", ".", "a/b", ".tasks", ""):
        with pytest.raises(store.ProjectError):
            ws.get(bad)


def test_sha256_fields_are_checked_and_failed_imports_leave_nothing(tmp_path):
    with pytest.raises(ValueError):
        AudioAsset(role="original", sha256="../../x", duration_ms=1, sample_rate=1, channels=1, num_samples=1)
    ws = S.Workspace(tmp_path / "projects")
    bad = json.loads(_project_json())
    bad["audio"] = [{"role": "original", "sha256": "../../../etc/x", "duration_ms": 1, "sample_rate": 1,
                     "channels": 1, "num_samples": 1}]
    src = tmp_path / "project.json"
    src.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(store.ProjectError):
        ws.import_file(src, "project.json")
    # a package whose project.json is invalid: the extracted audio is removed again
    z = tmp_path / "p.kara.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("project.json", json.dumps(bad))
        zf.writestr("assets/big.wav", b"x" * 1000)
    with pytest.raises(store.ProjectError):
        ws.import_file(z, "p.kara.zip")
    src.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(store.ProjectError, match="UTF-8"):
        ws.import_file(src, "project.json")
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with pytest.raises(store.ProjectError):
        ws.import_file(tmp_path / "junk.zip", "junk.zip")
    assert [d.name for d in ws.root.iterdir()] == []


def test_unreadable_projects_are_skipped_and_a_damaged_file_falls_back_to_its_backup(tmp_path):
    ws = S.Workspace(tmp_path / "projects")
    good = ws.create("good")
    (ws.root / "p_broken").mkdir()
    (ws.root / "p_broken" / "project.json").write_bytes(b"\xff\xfe\x00")
    assert [p["name"] for p in ws.list()] == ["good"]
    # project.json cut off by a crash: the backup of the previous save is used, the damaged file kept aside
    good.project.name = "renamed"
    good.save()  # project.json.bak now holds the first save
    path = good.dir / "project.json"
    path.write_text(path.read_text()[:40], encoding="utf-8")
    p = store.load_project(good.dir)
    assert p.name == "good"
    assert list(good.dir.glob("project.broken-*.json")) and store.load_project(good.dir).name == "good"


def test_project_import_endpoint_limits_and_errors(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    r = client.post("/api/projects/import", files={"file": ("x.json", b"\xff\xfe", "application/json")})
    assert r.status_code == 400
    r = client.post("/api/projects/import",
                    files={"file": ("../../p.json", _project_json(id="../evil").encode(), "application/json")})
    assert r.status_code == 200 and r.json()["project"]["id"] != "../evil"
    assert sorted(d.name for d in (tmp_path / "projects").iterdir() if not d.name.startswith(".")) \
        == [r.json()["project"]["id"]]


# ------------------------------------------------------------------ S4 local server


def test_host_and_origin_checks(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/projects", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert client.get("/api/projects", headers={"Host": "[::1]:8765"}).status_code == 200
    assert client.get("/api/projects", headers={"Host": "localhost"}).status_code == 200
    r = client.get("/api/projects", headers={"Host": "evil.example:8765"})
    assert r.status_code == 403
    r = client.post("/api/projects", json={"name": "x"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/projects", json={"name": "x"}, headers={"Origin": "null"})
    assert r.status_code == 403
    r = client.post("/api/projects", json={"name": "x"},
                    headers={"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200
    assert client.get("/api/projects", headers={"Origin": "http://evil.example"}).status_code == 200  # reads: CORS


def test_ai_settings_validation_and_the_saved_key_stays_with_its_address(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        AS.AiSettings(base_url="file:///etc/passwd")
    with pytest.raises(ValueError):
        AS.AiSettings(api_key_env="PATH")
    assert AS.AiSettings(api_key_env="MY_TOKEN").api_key_env == "MY_TOKEN"
    AS.update({"ai": {"provider": "openai", "model": "m", "api_key": "sk-saved"}})
    sent = []
    monkeypatch.setattr(llm, "ask", lambda cfg, prompt, **k: sent.append(cfg) or llm.LlmReply("OK", "openai"))
    client = _app(tmp_path, monkeypatch)
    r = client.post("/api/ai/test", json={"base_url": "http://attacker.example/v1"}).json()
    assert r["ok"] is False and "API Key" in r["error"] and not sent
    r = client.post("/api/ai/test", json={"base_url": "http://other.example/v1", "api_key": "sk-other"}).json()
    assert r["ok"] and sent[-1].api_key == "sk-other"
    assert client.post("/api/ai/test", json={}).json()["ok"] and sent[-1].api_key == "sk-saved"
    assert client.post("/api/ai/test", json={"base_url": "ftp://x"}).status_code == 400


def test_settings_keep_valid_fields_and_broken_copies_are_never_overwritten():
    p = AS.settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "ai": {"api_key": "sk-keep", "api_key_env": "HOME",
                                                  "base_url": "file:///x", "model": "m"},
                             "simple": {"quality": "high", "separation_preset": "../x", "vocal_keep_pct": 500}}),
                 encoding="utf-8")
    s = AS.load()
    assert (s.ai.api_key, s.ai.model, s.simple.quality) == ("sk-keep", "m", "high")
    assert s.ai.api_key_env == "OPENAI_API_KEY" and s.ai.base_url.startswith("https://")
    assert s.simple.separation_preset == "melband-roformer" and s.simple.vocal_keep_pct == 20.0
    for _ in range(2):
        p.write_text("{ not json", encoding="utf-8")
        assert AS.load() == AS.AppSettings()
    assert len(list(p.parent.glob("settings.broken-*.json"))) == 2


# ------------------------------------------------------------------ S5 / B-L12 AI command line


def test_cli_is_killed_when_the_wait_callback_raises(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    pids = tmp_path / "pids.txt"
    exe = d / "claude"
    exe.write_text(f"#!{sys.executable}\nimport os, subprocess, sys, time\n"
                   f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
                   f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}} ' + ' '.join(sys.argv[1:]))\n"
                   "sys.stdin.read()\ntime.sleep(60)\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    if os.name == "nt":  # what npm installs there: a .cmd that starts the real program
        (d / "claude.cmd").write_text(f'@"{sys.executable}" "%~dp0claude" %*\r\n')
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")

    def on_wait(waited):
        if pids.exists() and len(pids.read_text().split()) >= 2:
            raise Cancelled()  # what a cancelled job's progress callback does

    t0 = time.time()
    with pytest.raises(Cancelled):
        llm.ask(AS.AiSettings(provider="claude", model="-weird"), "x", on_wait=on_wait)
    assert time.time() - t0 < 20
    parts = pids.read_text().split()
    assert "--model=-weird" in parts  # one argument: never read as an option
    for pid in map(int, parts[:2]):
        deadline = time.time() + 5
        while time.time() < deadline:
            if not _alive(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            pytest.fail(f"process {pid} still running")


def _alive(pid: int) -> bool:
    if os.name == "nt":  # (os.kill(pid, 0) would end the process there)
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# ------------------------------------------------------------------ S6 invalid input never poisons a project


def test_refused_values_never_stay_in_memory(tmp_path):
    h = S.create_dir(tmp_path / "p", "x", "plain")
    with pytest.raises(S.ServiceError):
        S.update_settings(h, mix={"vocal_keep_pct": float("nan")})
    with pytest.raises(S.ServiceError):
        S.update_settings(h, mix={"vocal_keep_pct": 150})
    with pytest.raises(S.ServiceError):
        S.update_settings(h, config={"decode": {"soft_sigma_ms": float("inf")}})
    assert h.project.mix == MixSettings()
    S.update_settings(h, name="still saves")
    # a bug assigning invalid data: the save is refused and the project goes back to what is on disk
    h.project.calibration = (h.project.calibration, [])
    with pytest.raises(store.ProjectError):
        h.save()
    h.project.name = "later"
    h.save()
    assert store.load_project(h.dir).name == "later"
    # a stored mix from before the ranges existed still loads
    data = json.loads((h.dir / "project.json").read_text())
    data["mix"]["vocal_keep_pct"] = 250
    assert store.parse_project_json(json.dumps(data)).mix.vocal_keep_pct == 100.0


def test_line_kind_and_mix_errors_are_client_errors(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    pid = client.post("/api/projects", json={"name": "x"}).json()["project"]["id"]
    assert client.patch(f"/api/projects/{pid}/lines/L1", json={"kind": "bogus"}).status_code == 422
    r = client.patch(f"/api/projects/{pid}", json={"mix": {"master": -1}})
    assert r.status_code == 400
    assert client.patch(f"/api/projects/{pid}", json={"name": "ok"}).status_code == 200
    r = client.post(f"/api/projects/{pid}/lyrics/parse", json={"text": "x", "origin": "../nope"})
    assert r.status_code == 422


# ------------------------------------------------------------------ S8 / S9 task queue


def test_queue_keeps_running_when_tasks_json_cannot_be_written(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "run_task", lambda *a, **k: None)
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(P, "atomic_write_text", full)
    tasks = [P.PipelineTask(name=f"T{i}", status="queued", stages=_stages()) for i in range(2)]
    with q._lock:
        q.tasks.extend(tasks)
        q._wake.notify_all()
    deadline = time.time() + 10
    while time.time() < deadline and any(t.status != "succeeded" for t in tasks):
        time.sleep(0.05)
    assert [t.status for t in tasks] == ["succeeded", "succeeded"]
    assert any("无法写入磁盘" in w for w in tasks[0].warnings) and q.save_error
    q.shutdown()


def test_one_queue_per_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "run_task", lambda *a, **k: None)
    ws_root = tmp_path / "projects"
    q1 = P.TaskQueue(S.Workspace(ws_root))
    assert not q1.passive
    t = P.PipelineTask(name="W", status="waiting", stages=_stages())
    q1.tasks.append(t)
    q1._save()
    q2 = P.TaskQueue(S.Workspace(ws_root))
    assert q2.passive and q2.get(t.id).status == "waiting"  # shown as saved, never changed
    with pytest.raises(P.QueueElsewhere):
        q2.cancel(t.id)
    with pytest.raises(P.QueueElsewhere):
        q2.add(media=tmp_path / "x.wav", filename="x.wav", lyrics="x", mode="plain")
    t.name = "W2"
    time.sleep(0.01)
    q1._save()
    assert q2.get(t.id).name == "W2"  # picks up what the queue's process saved
    # the server on the same workspace: task changes are refused with the reason
    from kara_align.web.server import create_app

    client = TestClient(create_app(ws_root))
    assert client.get("/api/info").json()["tasks_elsewhere"] is True
    assert [x["id"] for x in client.get("/api/tasks").json()] == [t.id]
    r = client.post(f"/api/tasks/{t.id}/cancel")
    assert r.status_code == 409 and "另一个 MiliKara 服务进程" in r.json()["detail"]
    q1.shutdown()
    q3 = P.TaskQueue(S.Workspace(ws_root))  # the lock is free again
    assert not q3.passive
    q3.shutdown()
    q2.shutdown()
    client.app.state.tasks.shutdown()


def test_remove_is_atomic_and_running_tasks_are_conflicts(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(P, "run_task", lambda q, task, *a, **k: ran.append(task.id))
    monkeypatch.setattr(P.TaskQueue, "_worker", lambda self: None)
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    t = P.PipelineTask(name="P", status="preparing", stages=_stages())
    q.tasks.append(t)
    q.remove(t.id)
    q._prepare(t)  # the preparation lane comes to it only now: it is not run
    assert not ran and t.status == "cancelled"
    r = P.PipelineTask(name="R", status="running", stages=_stages())
    q.tasks.append(r)
    with pytest.raises(P.TaskConflict):
        q.remove(r.id)
    with pytest.raises(P.TaskConflict):
        q.retry(r.id)
    q.shutdown()
    client = _app(tmp_path / "srv", monkeypatch)
    tq = client.app.state.tasks
    busy = P.PipelineTask(name="B", status="running", stages=_stages())
    tq.tasks.append(busy)
    assert client.delete(f"/api/tasks/{busy.id}").status_code == 409
    assert client.post(f"/api/tasks/{busy.id}/retry").status_code == 409
    tq.shutdown()


def test_shutdown_before_a_task_starts_does_not_cancel_it(tmp_path, monkeypatch):
    monkeypatch.setattr(P.TaskQueue, "_worker", lambda self: None)
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    a = P.PipelineTask(name="A", status="running", stages=_stages())
    b = P.PipelineTask(name="B", status="preparing", stages=_stages())
    q.tasks += [a, b]
    q.shutdown()
    tok = CancelToken()
    tok.cancel()
    q._run(a, tok, None, done_status="succeeded")
    q._run(b, tok, P.PREP_STAGES, done_status="queued")
    assert (a.status, b.status) == ("queued", "preparing")


def test_retry_drops_warnings_of_stages_a_restart_put_back(tmp_path, monkeypatch):
    monkeypatch.setattr(P.TaskQueue, "_worker", lambda self: None)
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    t = P.PipelineTask(name="A", status="interrupted", stages=_stages())
    for s in t.stages[:4]:
        s.status = "done"
    t.current_stage = "separate"  # was running when the app stopped; _load made it pending
    P._warn(t, "人声分离：旧的提示")
    t.current_stage = "readings"
    P._warn(t, "AI 注音：保留")
    q.tasks.append(t)
    q.retry(t.id)
    assert t.warnings == ["AI 注音：保留"]
    q.shutdown()


# ------------------------------------------------------------------ B-L7 jobs vs tasks


def test_suggestion_waits_for_a_task_and_reading_jobs_do_not_block_a_task(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tq, jm = client.app.state.tasks, client.app.state.jobs
    pid = client.post("/api/projects", json={"name": "x", "mode": "lrc"}).json()["project"]["id"]
    h = client.app.state.workspace.get(pid)
    S.add_audio(h, _wav(tmp_path / "a.wav"), "original")
    t = P.PipelineTask(name="T", project_id=pid, status="failed", stages=_stages())
    t.stage("export").status = "failed"
    tq.tasks.append(t)
    burn = Job(id="job_burn", kind="burn", project_id=pid, status="running")
    jm._jobs[burn.id] = burn
    assert client.post(f"/api/tasks/{t.id}/retry").status_code == 200  # a burn only reads the project
    r = client.post(f"/api/projects/{pid}/calibration/suggest")
    assert r.status_code == 409 and "极简模式任务「T」" in r.json()["detail"]
    t.status = "failed"
    ai = Job(id="job_ai", kind="ai", project_id=pid, status="running")
    jm._jobs[ai.id] = ai
    assert client.post(f"/api/tasks/{t.id}/retry").status_code == 409  # AI readings change it
    tq.shutdown()


def test_finished_jobs_are_forgotten_after_a_while(monkeypatch):
    from kara_align.project import jobs as J

    jm = JobManager()
    done = jm.submit("t", lambda job: 1, heavy=False)
    for _ in range(200):
        if done.status == "succeeded":
            break
        time.sleep(0.01)
    monkeypatch.setattr(J, "FINISHED_JOB_TTL_S", 0.0)
    time.sleep(0.01)
    jm.submit("t", lambda job: 2, heavy=False)
    assert jm.get(done.id) is None
    jm.shutdown()


# ------------------------------------------------------------------ B-L5 memory growth


def test_raw_ai_replies_and_previews_are_capped(tmp_path):
    h = S.create_dir(tmp_path / "p", "x", "plain")
    for i in range(S.MAX_RAW_REPLIES + 2):
        h.project.ai_roundtrips.append(AiRoundtrip(snapshot_id=f"s{i}", text_revision="a", reading_revision="b",
                                                   line_ids=[], prompt="p", response_raw="r" * 10))
    h.project.ai_roundtrips[-1].response_raw = "x" * (S.MAX_RAW_REPLY_CHARS + 10)
    h.save()
    raws = [rt.response_raw for rt in store.load_project(h.dir).ai_roundtrips]
    assert raws[:2] == [None, None] and len(raws[-1]) == S.MAX_RAW_REPLY_CHARS
    for i in range(100):
        h.previews[f"pv{i}"] = i
    assert len(h.previews) == 32 and "pv99" in h.previews and "pv0" not in h.previews


def test_stale_ai_report_is_a_conflict(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    pid = client.post("/api/projects", json={"name": "x"}).json()["project"]["id"]

    def gone(*a, **k):
        raise KeyError("L0002")

    monkeypatch.setattr(S, "ai_apply", gone)
    r = client.post(f"/api/projects/{pid}/ai/apply", json={"report_id": "rep1"})
    assert r.status_code == 409 and "报告已过期" in r.json()["detail"]


# ------------------------------------------------------------------ B-L1 other client errors


def test_bad_requests_are_4xx(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    pid = client.post("/api/projects", json={"name": "x"}).json()["project"]["id"]
    assert client.get(f"/api/projects/{pid}/exports/%2E%2E").status_code == 404
    assert client.get(f"/api/projects/{pid}/results/r_nope").status_code == 404
    assert client.post(f"/api/projects/{pid}/results/r_nope/activate").status_code == 404
    assert client.get(f"/api/projects/{pid}/export/csv?result_id=r_nope").status_code == 404
    r = client.post(f"/api/projects/{pid}/karaoke/preview", json={"t_ms": "abc"})
    assert r.status_code == 400
    for name in ("/", ".."):
        r = client.post(f"/api/projects/{pid}/audio", files={"file": (name, b"RIFF0000WAVE", "audio/wav")})
        assert r.status_code == 400, (name, r.text)
    # an upload that does not decode: the message names the file, not the server's temporary folder
    r = client.post(f"/api/projects/{pid}/audio", files={"file": ("song.mp3", b"ID3" + b"\x00" * 200, "audio/mpeg")})
    assert r.status_code == 400 and "kara" not in r.json()["detail"] and "/tmp" not in r.json()["detail"]


def test_platform_errors_are_fetch_errors(monkeypatch):
    from kara_align.lyrics import fetch
    from kara_align.lyrics.fetch import netease
    from kara_align.lyrics.fetch.types import FetchError

    with pytest.raises(FetchError, match="数字"):
        netease.get_song("abc")

    def weird(song_id, client=None):
        return {}.get("x").get("y")  # AttributeError from an unexpected answer

    monkeypatch.setattr(netease, "get_song", weird)
    with pytest.raises(FetchError, match="无法识别"):
        fetch.fetch_song("netease", "1")


# ------------------------------------------------------------------ B-M6 / B-M7 / B-M9 / B-L2 files


def test_download_urls_are_encoded():
    assert P.export_url("p1", "My #1 ?.mp4") == "/api/projects/p1/exports/My%20%231%20%3F.mp4"


def test_video_name_with_dots_keeps_its_whole_stem(tmp_path, monkeypatch):
    from kara_align.audio import video as V

    def fake(cmd, **k):
        Path(cmd[-1]).write_bytes(b"x" * 10)
        return 0, b"", ""

    monkeypatch.setattr(V, "run_tool", fake)
    monkeypatch.setattr(V, "ffmpeg_path", lambda: "ffmpeg")
    out = V.mux_audio(tmp_path / "v.mp4", tmp_path / "a.wav", tmp_path / "My.Song-vocal20", container=".mp4")
    assert out.name == "My.Song-vocal20.mp4" and out.exists()
    assert not list(tmp_path.glob("*.part*"))


def test_run_tool_timeout_and_cancel():
    from kara_align.audio.io import AudioError, run_tool

    t0 = time.time()
    with pytest.raises(AudioError, match="超过"):
        run_tool([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    tok = CancelToken()
    threading.Timer(0.3, tok.cancel).start()
    with pytest.raises(Cancelled):
        run_tool([sys.executable, "-c", "import time; time.sleep(30)"], timeout=60, cancel=tok)
    assert time.time() - t0 < 10
    code, out, err = run_tool([sys.executable, "-c", "import sys; sys.stdout.write('hi'); sys.stderr.write('e')"],
                              timeout=30)
    assert (code, out, err) == (0, b"hi", "e")


def test_damaged_peaks_cache_is_made_again(tmp_path):
    h = S.create_dir(tmp_path / "p", "x", "plain")
    a = S.add_audio(h, _wav(tmp_path / "a.wav"), "original")
    first = S.peaks(h, a, 100)
    cache = store.cache_dir("peaks") / f"{a.sha256}-100.npz"
    assert cache.exists() and not list(cache.parent.glob("*.tmp.npz"))
    cache.write_bytes(b"cut off")
    assert S.peaks(h, a, 100)["maxs"] == first["maxs"]
    a.sha256 = "../../x"  # (set directly; loading such a project is refused)
    with pytest.raises(S.ServiceError):
        S.peaks(h, a, 100)


# ------------------------------------------------------------------ B-L9 / B-L10 / B-L13


def test_only_lyrics_without_times_fall_back_to_plain(tmp_path, monkeypatch):
    h = S.create_dir(tmp_path / "p", "x", "lrc")
    ws = type("WS", (), {"get": lambda self, pid: h})()
    q = type("Q", (), {"ws": ws})()
    task = P.PipelineTask(mode="lrc", project_id="p", lyrics_kind="text", lyrics_input="whatever")
    monkeypatch.setattr(S, "parse_lyrics", lambda *a, **k: {"preview_id": None, "error": "不是歌词：格式无法识别"})
    with pytest.raises(S.ServiceError, match="格式无法识别"):
        P.stage_lyrics(q, task, AS.load(), CancelToken(), lambda *a: None)
    assert task.mode == "lrc" and h.project.mode == "lrc" and not task.warnings


def test_delete_moves_the_folder_away_first(tmp_path, monkeypatch):
    ws = S.Workspace(tmp_path / "projects")
    h = ws.create("x")
    pid = h.project.id
    real = Path.rename

    def fail(self, target):
        raise OSError(16, "Resource busy")

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(S.ServiceError):
        ws.delete(pid)
    assert not h.deleted and ws.get(pid) is h  # nothing changed
    monkeypatch.setattr(Path, "rename", real)
    ws.delete(pid)
    assert h.deleted and not h.dir.exists() and list(ws.root.iterdir()) == []
    with pytest.raises(store.ProjectError):
        ws.get(pid)
    with pytest.raises(S.ServiceError):
        h.save()


def test_cli_flags():
    from kara_align.cli import build_parser

    ap = build_parser()
    assert ap.parse_args(["readings", "p"]).overwrite_rule is True
    assert ap.parse_args(["readings", "p", "--no-overwrite-rule"]).overwrite_rule is False
    a = ap.parse_args(["burn", "p", "--audio", "mix", "--vocal", "30"])
    assert (a.audio, a.vocal) == ("mix", 30.0)


def test_package_download_leaves_nothing_in_the_project(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    pid = client.post("/api/projects", json={"name": "x"}).json()["project"]["id"]
    r = client.get(f"/api/projects/{pid}/package")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "project.json" in zf.namelist()
    assert not list((tmp_path / "projects" / pid).rglob("*.kara.zip"))


def test_exports_folder_listing(tmp_path):
    from fastapi.testclient import TestClient

    from kara_align.web.server import create_app

    client = TestClient(create_app(tmp_path / "projects"))
    pid = client.post("/api/projects", json={"name": "x", "mode": "plain"}).json()["project"]["id"]
    assert client.get(f"/api/projects/{pid}/exports").json() == []
    d = tmp_path / "projects" / pid / "exports"
    d.mkdir(parents=True, exist_ok=True)
    (d / "My Song #2-karaoke.mp4").write_bytes(b"123")
    (d / ".My Song-karaoke.part.mp4").write_bytes(b"partial")
    listed = client.get(f"/api/projects/{pid}/exports").json()
    assert [x["filename"] for x in listed] == ["My Song #2-karaoke.mp4"] and listed[0]["size"] == 3
    assert "%23" in listed[0]["url"]
    assert client.get(listed[0]["url"]).content == b"123"
    client.app.state.tasks.shutdown()
