"""Local WebUI server (see docs/api.md).  All logic lives in :mod:`kara_align.service`."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal, Optional, Union
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .. import __version__
from .. import service as S
from ..models import LineKind, SourceOrigin
from ..project.jobs import Job, JobManager, progress_setter
from ..project.store import ProjectError

# the server only answers requests addressed to this computer by name (DNS rebinding: a web page
# whose domain points at 127.0.0.1 would otherwise reach the API); "testserver" is FastAPI's TestClient
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})

STATIC_DIR = Path(__file__).parent / "static"
MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024 * 1024  # videos can be large


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------


class CreateBody(BaseModel):
    name: str = "untitled"
    mode: str = "plain"


class PatchProjectBody(BaseModel):
    name: Optional[str] = None
    mode: Optional[str] = None
    config: Optional[dict] = None
    mix: Optional[dict] = None


class TextBody(BaseModel):
    text: str
    origin: SourceOrigin = "paste"
    filename: Optional[str] = None
    kind: Optional[str] = None


class PreviewBody(BaseModel):
    preview_id: str


class TrackApplyBody(BaseModel):
    kind: str
    pairs: list[dict]


class LinkBody(BaseModel):
    text: str


class SongBody(BaseModel):
    platform: str
    song_id: str


class LinePatch(BaseModel):
    text: Optional[str] = None
    sing: Optional[bool] = None
    kind: Optional[LineKind] = None
    translation: Optional[str] = None
    voice: Optional[str] = None
    countdown: Optional[Literal["auto", "on", "off"]] = None  # countdown dots before this line (karaoke)


class SpanBody(BaseModel):
    start: int
    end: int
    singers: list[int] = []


class LineSingersItem(BaseModel):
    line_id: str
    singers: list[int] = []
    spans: list[SpanBody] = []
    text: Optional[str] = None  # the text the spans were made for (refused if the line changed since)


class LineSingersBody(BaseModel):
    lines: list[LineSingersItem]


class MarkersBody(BaseModel):
    names: Optional[list[str]] = None
    strip: bool = True


class LineIdsBody(BaseModel):
    line_ids: Optional[list[str]] = None


class SplitBody(BaseModel):
    at: int


class AnchorBody(BaseModel):
    abs_ms: Optional[int] = None
    hard: bool = True
    tolerance_ms: int = 80


class PrepareBody(BaseModel):
    overwrite_rule: bool = True


class SegmentBody(BaseModel):
    reading: str
    units: Optional[list[str]] = None
    confirm: bool = True


class HiddenBody(BaseModel):
    hidden: bool


class ReportApplyBody(BaseModel):
    report_id: str
    line_ids: Optional[list[str]] = None


class SeparateBody(BaseModel):
    preset: str = "melband-roformer"  # one of the known presets (checked in the endpoint)
    device: Literal["auto", "cpu"] = "auto"


class MarkBody(BaseModel):
    line_id: str
    marked_ms: int


class ShiftBody(BaseModel):
    user_shift_ms: int


class AlignBody(BaseModel):
    line_ids: Optional[list[str]] = None
    audio_role: Optional[str] = None
    config: Optional[dict] = None


class UnitBody(BaseModel):
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    locked: bool = True


class RetimeBody(BaseModel):
    start_ms: Optional[int] = None  # the line's new start (alone: shift the whole line)
    end_ms: Optional[int] = None  # with a start: stretch the line onto [start_ms, end_ms)


class RetimeUnitsBody(RetimeBody):
    unit_ids: list[str]  # the units moved together (a selection on the waveform; may span lines)


class LockBody(BaseModel):
    locked: bool


class RestoreBody(BaseModel):
    manual: Optional[dict] = None


class CleanProjectBody(BaseModel):
    exports: Union[bool, list[str]] = False  # all exported files, or the ones named
    stems: bool = False


class CleanBody(BaseModel):
    cache: bool = False
    leftovers: bool = False


class AdoptBody(BaseModel):
    from_result_id: Optional[str] = None
    candidate_id: Optional[str] = None
    line_ids: list[str] = []


# ---------------------------------------------------------------------------


def create_app(root: Optional[Path] = None, jobs: Optional[JobManager] = None,
               allowed_hosts: Optional[set[str]] = None) -> FastAPI:
    """``allowed_hosts``: host names accepted besides this computer's own (``serve --allow-host``)."""
    from contextlib import asynccontextmanager

    from ..audio.mix import MixError
    from ..lyrics.fetch.types import FetchError
    from ..pipeline import TaskConflict, TaskQueue

    hosts = LOCAL_HOSTS | {h.lower().strip("[]") for h in (allowed_hosts or set())}
    ws = S.Workspace(root)
    jm = jobs or JobManager()
    tq = TaskQueue(ws)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # stopping the server: cancel running work (separation / AI / ffmpeg subprocesses stop with it)
        tq.shutdown()
        jm.shutdown()

    app = FastAPI(title="MiliKara", version=__version__, lifespan=lifespan)
    app.state.workspace = ws
    app.state.jobs = jm
    app.state.tasks = tq

    @app.exception_handler(S.ServiceError)
    async def _service_error(_req: Request, exc: S.ServiceError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ProjectError)
    async def _project_error(_req: Request, exc: ProjectError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(TaskConflict)
    async def _task_conflict(_req: Request, exc: TaskConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(MixError)
    async def _mix_error(_req: Request, exc: MixError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(FetchError)
    async def _fetch_error(_req: Request, exc: FetchError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.middleware("http")
    async def _local_only(request: Request, call_next):
        """Only requests to this computer by name, and changes only from the app's own pages."""
        host = request.headers.get("host", "")
        name = (urlsplit(f"//{host}").hostname or "") if host else ""
        if name.lower() not in hosts:
            return JSONResponse(status_code=403, content={"detail": f"不接受发往 {host or '（无 Host）'} 的请求："
                                                                     "请用 http://127.0.0.1 或 http://localhost 打开"})
        origin = request.headers.get("origin")
        if origin is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
            o = urlsplit(origin)
            # a page of another site (CSRF) may send requests here but must not change anything
            if o.scheme not in ("http", "https") or o.netloc.lower() != host.lower():
                return JSONResponse(status_code=403, content={"detail": "拒绝来自其他网页的请求"})
        return await call_next(request)

    def handle(pid: str) -> S.ProjectHandle:
        try:
            return ws.get(pid)
        except ProjectError as e:
            raise HTTPException(404, str(e)) from e

    def view(h: S.ProjectHandle, **extra: Any) -> dict:
        v = S.project_view(h)
        v.update(extra)
        return v

    # detailed-mode jobs that change the project (readings, stems, results); burns, exports and the
    # offset suggestion only read it
    CHANGING_JOBS = ("align", "separate", "ai")

    def no_jobs(pid: str, doing: str) -> None:
        """A task must not continue (retry / confirm) while the detailed mode changes the same project:
        the task would work on inputs that change underneath it.  Jobs that only read the project do
        not hold it up."""
        busy = [j for j in jm.list(pid) if j.status in ("queued", "running") and j.kind in CHANGING_JOBS]
        if busy:
            kind = {"align": "对齐", "separate": "人声分离", "burn": "字幕烧录", "ai": "AI 注音",
                    "calibrate": "自动匹配偏移"}.get(busy[0].kind, busy[0].kind)
            raise HTTPException(409, f"详细模式正在对这个项目进行{kind}；请等它完成后再{doing}")

    def not_busy(pid: str) -> None:
        """Heavy jobs in the detailed mode wait until the simple-mode task on this project is done."""
        t = tq.active_for_project(pid)
        if t is not None:
            raise HTTPException(409, f"极简模式任务「{t.name or t.media_filename}」正在处理这个项目；"
                                     "请等它完成，或在极简模式的任务队列里取消后再操作")

    def result_or_404(h: S.ProjectHandle, rid: str) -> None:
        if h.project.result(rid) is None:
            raise HTTPException(404, f"没有对齐结果 {rid}")

    def download_url(pid: str, filename: str) -> str:
        from ..pipeline import export_url

        return export_url(pid, filename)

    def guard(fn, *args, **kw):
        """Map module-level validation errors to 400 with a readable message."""
        try:
            return fn(*args, **kw)
        except (S.ServiceError, ProjectError, HTTPException):
            raise
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e

    # ------------------------------------------------------------------ info / jobs

    @app.get("/api/info")
    def info():
        from ..align.backends import list_backends
        from ..audio.separation import preset_dicts
        from ..project.exports import EXPORT_FORMATS

        try:
            from ..audio.separation import ensure_available

            ensure_available()
            sep_ok = True
        except Exception:
            sep_ok = False
        return {
            "version": __version__,
            "backends": list_backends(),
            "separation_presets": preset_dicts(),
            "separation_available": sep_ok,
            # another server process on this workspace runs the simple-mode queue: tasks are shown, not run
            "tasks_elsewhere": tq.passive,
            "export_formats": {k: {"filename": v[0], "description": v[2]} for k, v in EXPORT_FORMATS.items()},
        }

    # ------------------------------------------------------------------ app settings / AI providers

    @app.get("/api/settings")
    def get_settings():
        from .. import settings as app_settings

        return app_settings.public(app_settings.load())

    @app.put("/api/settings")
    def put_settings(body: dict):
        from .. import settings as app_settings

        try:
            out = app_settings.public(app_settings.update(body or {}))
        except ValueError as e:
            raise HTTPException(400, f"设置无效：{e}") from e
        if isinstance((body or {}).get("ai"), dict) and {"claude_cli", "codex_cli"} & set(body["ai"]):
            from ..reading.llm import _detect_cache

            _detect_cache.clear()  # the CLI in use changed: detected again on the next look
        return out

    @app.get("/api/update")
    def update_check(refresh: int = 0):
        """A newer version?  Only asks GitHub when the settings allow it (or when asked to now)."""
        from .. import settings as app_settings
        from .. import updates

        if not refresh and not app_settings.load().check_updates:
            return {"enabled": False, "current": updates.__version__}
        return {"enabled": True, **updates.check(force=bool(refresh))}

    @app.get("/api/ai/providers")
    def ai_providers(refresh: int = 0):
        from ..reading.llm import detect_all

        return detect_all(refresh=bool(refresh))

    @app.post("/api/ai/test")
    def ai_test(body: dict):
        """Send a tiny message with the given (or saved) AI settings."""
        from .. import settings as app_settings
        from ..reading.llm import LlmError, ask

        saved = app_settings.load().ai
        patch = {k: v for k, v in (body or {}).items() if k != "api_key" or v}
        # the saved key (or the key variable) only ever goes to the saved address: testing another
        # address needs its key in the same request
        try:
            cfg = app_settings.AiSettings.model_validate({**saved.model_dump(), **patch, "timeout_s": 120})
        except ValueError as e:
            raise HTTPException(400, f"设置无效：{e}") from e
        if cfg.base_url.rstrip("/") != saved.base_url.rstrip("/"):
            if not patch.get("api_key"):
                return {"ok": False, "error": "测试其他 API 地址时请同时填写该地址的 API Key（已保存的 Key 只发往已保存的地址）"}
            cfg = cfg.model_copy(update={"api_key_env": "NO_SAVED_KEY"})  # never the key variable either
        try:
            r = ask(cfg, "只回复两个字母：OK")
        except LlmError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "reply": r.text.strip()[:200], "model": r.model, "elapsed_s": r.elapsed_s,
                "cost_usd": r.cost_usd}

    # ------------------------------------------------------------------ simple mode: task queue

    def task_or_404(task_id: str):
        try:
            return tq.get(task_id)
        except KeyError:
            raise HTTPException(404, "没有该任务") from None

    @app.get("/api/tasks")
    def list_tasks():
        return tq.list()

    @app.get("/api/diagnostics")
    def diagnostics(task: str = "", job: str = ""):
        """A report to copy into a bug report: the system, and a failed task's / job's details."""
        from ..diagnostics import report

        t = j = None
        if task:
            try:
                t = tq.get(task).model_dump(mode="json", exclude={"karaoke"})
            except KeyError:
                raise HTTPException(404, "没有该任务") from None
        if job:
            jj = job_or_404(job)
            j = {**jj.to_dict(), "detail": jj.detail}
        return {"text": report(task=t, job=j)}

    @app.post("/api/tasks")
    async def add_task(file: UploadFile = File(...), lyrics: str = Form(...), mode: str = Form("lrc"),
                       name: str = Form(""), style: str = Form(""), background: Optional[UploadFile] = File(None)):
        from ..audio.io import AudioError, validate_upload
        from ..pipeline import QueueElsewhere

        if tq.passive:
            raise QueueElsewhere()
        _check_text(lyrics)
        fname = _upload_name(file.filename, "media")
        td = tempfile.mkdtemp(prefix="kara-task-")
        try:
            tmp = Path(td) / fname
            size = await _save_upload(file, tmp, MAX_AUDIO_BYTES)
            try:
                with open(tmp, "rb") as f:
                    validate_upload(fname, f.read(64), size, MAX_AUDIO_BYTES)
            except AudioError as e:
                raise HTTPException(400, _clean(str(e), td, fname)) from e
            try:
                opts = json.loads(style) if style.strip() else None
            except ValueError as e:
                raise HTTPException(400, "style 必须是 JSON") from e
            bg_tmp, bg_name = None, ""
            if background is not None and (background.filename or "").strip():
                bg_name = _upload_name(background.filename, "background")
                (Path(td) / "bg").mkdir()
                bg_tmp = Path(td) / "bg" / bg_name
                await _save_upload(background, bg_tmp, MAX_AUDIO_BYTES)
            # moving the upload and resolving the style run in a thread: the server keeps answering
            try:
                t = await run_in_threadpool(tq.add, media=tmp, filename=fname, lyrics=lyrics, mode=mode, name=name,
                                            style=opts, background=bg_tmp, background_filename=bg_name)
            except S.ServiceError as e:
                raise HTTPException(400, _clean(str(e), td, bg_name or fname)) from e
        finally:
            shutil.rmtree(td, ignore_errors=True)
        return t.model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        task_or_404(task_id)
        return tq.cancel(task_id).model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/retry")
    def retry_task(task_id: str):
        task_or_404(task_id)
        t = tq.get(task_id)
        if t.project_id and not t.project_deleted:
            no_jobs(t.project_id, "重试")
        return tq.retry(task_id).model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/calibration")
    def confirm_task_calibration(task_id: str, body: dict):
        task_or_404(task_id)
        t = tq.get(task_id)
        if t.project_id and not t.project_deleted:
            no_jobs(t.project_id, "确认")
        marked = (body or {}).get("marked_ms")
        if marked is not None and (not isinstance(marked, (int, float)) or marked < 0):
            raise HTTPException(400, "marked_ms 必须是非负的毫秒数")
        t = tq.confirm_calibration(task_id, marked_ms=None if marked is None else int(marked),
                                   plain=bool((body or {}).get("plain")))
        return t.model_dump(mode="json")

    @app.get("/api/tasks/{task_id}/readings/prompt")
    def task_readings_prompt(task_id: str):
        """AI readings by hand: the prompt to copy into a web chat."""
        task_or_404(task_id)
        return tq.readings_prompt(task_id)

    @app.post("/api/tasks/{task_id}/readings")
    def submit_task_readings(task_id: str, body: dict):
        """AI readings by hand: {text} = the chat's reply (checked like a pasted reply; applied lines
        that pass), or {skip: true} = go on with the rule readings.  400 when no line is usable."""
        task_or_404(task_id)
        t = tq.get(task_id)
        if t.project_id and not t.project_deleted:
            no_jobs(t.project_id, "提交 AI 注音")
        text = (body or {}).get("text")
        if text is not None and not isinstance(text, str):
            raise HTTPException(400, "text 必须是字符串")
        if text:
            _check_text(text)
        return tq.submit_readings(task_id, text=text, skip=bool((body or {}).get("skip"))).model_dump(mode="json")

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: str):
        task_or_404(task_id)
        tq.remove(task_id)
        return {"ok": True}

    def job_or_404(job_id: str) -> Job:
        job = jm.get(job_id)
        if job is None:
            raise HTTPException(404, "没有该任务")
        return job

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return job_or_404(job_id).to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = job_or_404(job_id)
        jm.cancel(job_id)
        return job.to_dict()

    @app.get("/api/projects/{pid}/jobs")
    def project_jobs(pid: str):
        return [j.to_dict() for j in jm.list(pid)]

    # ------------------------------------------------------------------ projects

    @app.get("/api/projects")
    def list_projects():
        from ..storage import size_of

        return [{**p, "size": size_of(Path(ws.root) / p["id"])} for p in ws.list()]

    @app.post("/api/projects")
    def create_project(body: CreateBody):
        if body.mode not in ("plain", "lrc"):
            raise HTTPException(400, "模式只能是 plain 或 lrc")
        return view(ws.create(body.name, body.mode))

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return view(handle(pid))

    def project_busy(pid: str, doing: str = "删除") -> None:
        t = tq.active_for_project(pid)
        if t is not None:
            raise HTTPException(409, f"极简模式任务「{t.name or t.media_filename}」正在处理这个项目，请先取消该任务")
        if any(j.status in ("queued", "running") for j in jm.list(pid)):
            raise HTTPException(409, f"这个项目还有正在进行的操作，请等它完成或取消后再{doing}")

    def audio_shas(skip: str = "") -> set[str]:
        out: set[str] = set()
        for p in ws.list():
            if p["id"] != skip:
                try:
                    out |= {a.sha256 for a in ws.get(p["id"]).project.audio}
                except ProjectError:
                    pass
        return out

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        from .. import storage

        h = handle(pid)
        project_busy(pid)
        shas = {a.sha256 for a in h.project.audio}
        ws.delete(pid)
        tq.forget_project(pid)  # its tasks stay listed, without links to the deleted project
        storage.drop_cache_of(shas, audio_shas())  # its decoded audio and waveforms, unless another project has them
        return {"ok": True}

    # ------------------------------------------------------------------ storage

    def busy_projects() -> set[str]:
        busy = {j.project_id for j in jm.list() if j.status in ("queued", "running") and j.project_id}
        busy |= {t.project_id for t in tq.snapshot() if t.project_id and t.status in ("preparing", "waiting", "queued", "running")}
        return busy

    def storage_view() -> dict:
        from .. import storage

        busy = busy_projects()
        projects = []
        for p in ws.list():
            try:
                u = storage.project_usage(ws.get(p["id"]))
            except ProjectError:
                continue
            projects.append({**p, **u, "busy": p["id"] in busy})
        projects.sort(key=lambda p: p["size"], reverse=True)
        left = storage.leftovers(ws, tq, busy)
        kinds: dict[str, int] = {}
        for i in left:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + i["size"]
        return {
            "root": str(ws.root), "disk": storage.disk(), "projects": projects,
            "projects_size": sum(p["size"] for p in projects),
            "cache": storage.cache_usage(), "models": storage.models_usage(),
            "leftovers": {"size": sum(kinds.values()), "parts": kinds},
            "working": bool(busy) or any(j.status in ("queued", "running") for j in jm.list()),
        }

    @app.get("/api/storage")
    def get_storage():
        return storage_view()

    @app.post("/api/storage/clean")
    def clean_storage(body: CleanBody):
        """The cache (made again when needed) and / or leftovers (never needed again)."""
        from .. import storage

        freed = 0
        if body.cache:
            if any(j.status in ("queued", "running") for j in jm.list()) or \
                    any(t.status in ("preparing", "running") for t in tq.snapshot()):
                raise HTTPException(409, "有任务或操作正在进行，它们会用到缓存；请等它们完成后再清理缓存")
            freed += storage.clear_cache()
        if body.leftovers:
            freed += storage.clean_leftovers(storage.leftovers(ws, tq, busy_projects()))
        return {"freed": freed, **storage_view()}

    @app.post("/api/projects/{pid}/storage/clean")
    def clean_project_storage(pid: str, body: CleanProjectBody):
        """Exported files (all or some) and / or the separated stems (made again by separating)."""
        from .. import storage

        h = handle(pid)
        project_busy(pid, "清理")
        freed = 0
        if body.exports:
            freed += storage.drop_exports(h, None if body.exports is True else body.exports)
        if body.stems:
            freed += storage.drop_stems(h)
        return {"freed": freed, **storage_view()}

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: PatchProjectBody):
        h = handle(pid)
        guard(S.update_settings, h, name=body.name, mode=body.mode, config=body.config, mix=body.mix)
        return view(h)

    @app.post("/api/projects/import")
    async def import_project(file: UploadFile = File(...)):
        from ..project.store import MAX_PROJECT_JSON_BYTES

        name = _upload_name(file.filename, "project.json")
        is_zip = name.endswith(".zip")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / ("upload.zip" if is_zip else "project.json")
            # a project.json is never larger than a project file may be; a package may carry audio
            await _save_upload(file, tmp, 4 * 1024**3 if is_zip else MAX_PROJECT_JSON_BYTES)
            h = await run_in_threadpool(ws.import_file, tmp, "upload.zip" if is_zip else "project.json")
        return view(h)

    @app.get("/api/projects/{pid}/package")
    def package(pid: str, include_audio: int = 1):
        from ..project.store import export_package

        h = handle(pid)
        # built in a temporary folder and removed once sent: packages never pile up in the project
        td = tempfile.mkdtemp(prefix="kara-package-")
        out = Path(td) / f"{h.project.id}.kara.zip"
        try:
            with h.lock:
                snapshot = h.project.model_copy(deep=True)
            export_package(snapshot, h.dir, out, include_audio=bool(include_audio))
        except BaseException:
            shutil.rmtree(td, ignore_errors=True)
            raise
        return FileResponse(out, filename=out.name, media_type="application/zip",
                            background=BackgroundTask(shutil.rmtree, td, ignore_errors=True))

    # ------------------------------------------------------------------ lyrics

    @app.post("/api/projects/{pid}/lyrics/parse")
    def lyrics_parse(pid: str, body: TextBody):
        _check_text(body.text)
        return S.parse_lyrics(handle(pid), body.text, origin=body.origin, filename=body.filename)

    @app.post("/api/projects/{pid}/lyrics/apply")
    def lyrics_apply(pid: str, body: PreviewBody):
        h = handle(pid)
        messages = S.apply_lyrics(h, body.preview_id)
        return view(h, messages=messages)

    @app.post("/api/projects/{pid}/lyrics/track/preview")
    def track_preview(pid: str, body: TextBody):
        _check_text(body.text)
        kind = body.kind or "translation"
        return guard(S.preview_track, handle(pid), body.text, kind)

    @app.post("/api/projects/{pid}/lyrics/track/apply")
    def track_apply(pid: str, body: TrackApplyBody):
        h = handle(pid)
        guard(S.apply_track, h, body.kind, body.pairs)
        return view(h)

    @app.post("/api/lyrics/link")
    def lyrics_link(body: LinkBody):
        from ..lyrics.fetch.types import FetchError

        try:
            return S.fetch_link(body.text)
        except FetchError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/lyrics/song")
    def lyrics_song(body: SongBody):
        from ..lyrics.fetch.types import FetchError

        try:
            return S.fetch_song(body.platform, body.song_id)
        except FetchError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/projects/{pid}/lyrics/from-song")
    def lyrics_from_song(pid: str, body: SongBody):
        from ..lyrics.fetch.types import FetchError

        try:
            return S.parse_from_song(handle(pid), body.platform, body.song_id)
        except FetchError as e:
            raise HTTPException(400, str(e)) from e

    # ------------------------------------------------------------------ lines / readings

    @app.patch("/api/projects/{pid}/lines/{line_id}")
    def patch_line(pid: str, line_id: str, body: LinePatch):
        h = handle(pid)
        guard(S.update_line, h, line_id, **body.model_dump(exclude_none=True))
        return view(h)

    @app.post("/api/projects/{pid}/lines/merge")
    def merge(pid: str, body: LineIdsBody):
        h = handle(pid)
        guard(S.merge_lines, h, body.line_ids or [])
        return view(h)

    @app.post("/api/projects/{pid}/lines/{line_id}/split")
    def split(pid: str, line_id: str, body: SplitBody):
        h = handle(pid)
        guard(S.split_line, h, line_id, body.at)
        return view(h)

    @app.put("/api/projects/{pid}/lines/{line_id}/anchor")
    def anchor(pid: str, line_id: str, body: AnchorBody):
        h = handle(pid)
        guard(S.set_line_anchor, h, line_id, body.abs_ms, body.hard, body.tolerance_ms)
        return view(h)

    @app.post("/api/projects/{pid}/readings/prepare")
    def readings_prepare(pid: str, body: PrepareBody):
        h = handle(pid)
        report = S.prepare_readings(h, body.overwrite_rule)
        return view(h, report=report)

    @app.put("/api/projects/{pid}/lines/{line_id}/segments/{segment_id}")
    def segment_reading(pid: str, line_id: str, segment_id: str, body: SegmentBody):
        h = handle(pid)
        guard(S.set_segment_reading, h, line_id, segment_id, body.reading, body.units, body.confirm)
        return view(h)

    @app.put("/api/projects/{pid}/lines/{line_id}/segments/{segment_id}/hidden")
    def segment_hidden(pid: str, line_id: str, segment_id: str, body: HiddenBody):
        h = handle(pid)
        guard(S.set_segment_hidden, h, line_id, segment_id, body.hidden)
        return view(h)

    @app.post("/api/projects/{pid}/ai/prompt")
    def ai_prompt(pid: str, body: LineIdsBody):
        return guard(S.ai_prompt, handle(pid), body.line_ids)

    @app.post("/api/projects/{pid}/ai/validate")
    def ai_validate(pid: str, body: TextBody):
        _check_text(body.text)
        return S.ai_validate(handle(pid), body.text)

    @app.post("/api/projects/{pid}/ai/auto")
    def ai_auto(pid: str, body: LineIdsBody):
        from .. import settings as app_settings

        h = handle(pid)
        not_busy(pid)
        cfg = app_settings.load().ai
        if cfg.provider == "manual":
            raise HTTPException(400, "当前设置为手动网页聊天往返：请用下面的“复制提示词 → 粘贴回复”；"
                                     "要一键注音请在设置中选择 Claude Code、Codex 或 API")

        def run(job: Job):
            return S.ai_auto(h, body.line_ids, cfg=cfg, cancel=job.cancel_token, progress=progress_setter(job))

        return jm.submit("ai", run, project_id=pid, heavy=False).to_dict()

    @app.post("/api/projects/{pid}/ai/apply")
    def ai_apply(pid: str, body: ReportApplyBody):
        h = handle(pid)
        try:
            summary = S.ai_apply(h, body.report_id, body.line_ids)
        except KeyError:  # a line of the report no longer exists (merged / split since)
            h.previews.pop(body.report_id, None)
            raise HTTPException(409, "报告已过期：歌词在校验后改动过，请重新粘贴或重新获取 AI 结果") from None
        h.previews.pop(body.report_id, None)  # applied: the report is not kept around
        return view(h, summary=summary)

    # ------------------------------------------------------------------ audio

    @app.post("/api/projects/{pid}/audio")
    async def upload_audio(pid: str, file: UploadFile = File(...), role: str = Form("original")):
        from ..audio.io import AudioError, validate_upload

        h = handle(pid)
        not_busy(pid)  # a task working on the project must not have its audio replaced underneath
        name = _upload_name(file.filename, "audio")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / name
            size = await _save_upload(file, tmp, MAX_AUDIO_BYTES)
            try:
                with open(tmp, "rb") as f:
                    validate_upload(name, f.read(64), size, MAX_AUDIO_BYTES)
                await run_in_threadpool(S.add_media, h, tmp, role, filename=name,
                                        source_kind="upload" if role == "original" else "import")
            except AudioError as e:  # (ffmpeg's messages name the temporary copy: shown as the file name)
                raise HTTPException(400, _clean(str(e), td, name)) from e
        return view(h)

    @app.put("/api/projects/{pid}/background")
    async def upload_background(pid: str, file: UploadFile = File(...)):
        """A picture or a video (looped) shown behind the subtitles instead of the video / black."""
        from ..karaoke.background import MAX_BACKGROUND_BYTES

        h = handle(pid)
        not_busy(pid)
        name = _upload_name(file.filename, "background")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / name
            await _save_upload(file, tmp, MAX_BACKGROUND_BYTES)
            try:
                await run_in_threadpool(S.set_background, h, tmp, filename=name)
            except S.ServiceError as e:
                raise HTTPException(400, _clean(str(e), td, name)) from e
        return view(h)

    @app.post("/api/projects/{pid}/background/cover")
    def background_from_cover(pid: str):
        """The song's cover (from the music link of the lyrics), blurred, as the picture."""
        h = handle(pid)
        guard(S.cover_background, h)
        return view(h)

    @app.delete("/api/projects/{pid}/background")
    def delete_background(pid: str):
        h = handle(pid)
        not_busy(pid)
        S.clear_background(h)
        return view(h)

    @app.get("/api/projects/{pid}/background/file")
    def background_file(pid: str):
        from ..project import store

        h = handle(pid)
        b = h.project.background
        p = store.asset_abspath(h.dir, b.path) if b is not None else None
        if p is None or not p.exists():
            raise HTTPException(404, "没有背景")
        return FileResponse(p, filename=b.filename or p.name)

    @app.get("/api/projects/{pid}/audio/{asset_id}/playback.wav")
    def playback(pid: str, asset_id: str):
        h = handle(pid)
        asset = S.get_asset(h, asset_id)
        path = S.playback_wav(h, asset)
        return FileResponse(path, media_type="audio/wav", filename=f"{asset.role}.wav")

    @app.get("/api/projects/{pid}/audio/{asset_id}/peaks")
    def peaks(pid: str, asset_id: str, per_second: int = 200):
        h = handle(pid)
        return S.peaks(h, S.get_asset(h, asset_id), per_second)

    @app.post("/api/projects/{pid}/separate")
    def separate(pid: str, body: SeparateBody):
        from ..audio.separation import PRESET_NAMES, is_known_preset

        h = handle(pid)
        if not is_known_preset(body.preset):
            raise HTTPException(400, f"未知的分离预设（可选 {', '.join(PRESET_NAMES)}）")
        not_busy(pid)
        if h.project.asset("original") is None:
            raise HTTPException(400, "请先上传原曲")

        def run(job: Job):
            return S.run_separation(h, body.preset, cancel=job.cancel_token, progress=progress_setter(job),
                                    device=body.device)

        return jm.submit("separate", run, project_id=pid).to_dict()

    @app.post("/api/projects/{pid}/mix/preview-gain")
    def mix_gain(pid: str, body: dict):
        return S.mix_bus_gain(handle(pid), body or {})

    @app.post("/api/projects/{pid}/mix/export")
    def mix_export(pid: str, body: dict):
        h = handle(pid)
        S.require_stems(h, "导出混音")

        settings = body or {}
        S.mix_settings(h.project.mix, settings)  # bad values: 400 now, not a failed job

        def run(job: Job):
            out = S.export_mix(h, settings, cancel=job.cancel_token)
            return {"filename": out["filename"], "report": out["report"], "url": download_url(pid, out["filename"])}

        return jm.submit("mix", run, project_id=pid, heavy=False).to_dict()

    # ------------------------------------------------------------------ karaoke subtitles

    @app.get("/api/fonts")
    def fonts():
        from ..karaoke.fonts import default_family, families

        return {"default": default_family(), "families": families()}

    # ---- saved subtitle styles (预设), shared by all projects and the simple mode

    @app.get("/api/karaoke/themes")
    def list_themes():
        from ..karaoke.themes import SWATCHES, TEMPLATES

        return {"templates": [{"id": k, "label": v} for k, v in TEMPLATES.items()], "swatches": SWATCHES}

    @app.post("/api/karaoke/theme")
    def theme_preview(body: dict):
        """The palette (and the whole style, on top of the simple mode's default) for a template + colours."""
        from .. import settings as app_settings
        from ..karaoke.themes import palette, theme_style

        from ..models import KaraokeStyle

        b = body or {}
        try:
            secondary = b.get("secondary") or None
            pal = palette(b.get("color") or "", secondary)
            # on top of the given style (the editor's current one), else the simple mode's default
            base = KaraokeStyle.model_validate(b["base"]) if b.get("base") else app_settings.load().simple.karaoke
            st = theme_style(b.get("template") or "plain", b.get("color") or "", base, secondary)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"palette": pal, "style": st.model_dump(mode="json")}

    @app.get("/api/karaoke/styles")
    def list_styles():
        from ..karaoke.styles import list_styles as _list

        return _list()

    @app.post("/api/karaoke/styles")
    def save_style(body: dict):
        from ..karaoke.styles import StyleError, save_style as _save

        try:
            return _save((body or {}).get("name", ""), (body or {}).get("style") or {}, (body or {}).get("id"))
        except StyleError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/karaoke/styles/{style_id}")
    def delete_style(style_id: str):
        from ..karaoke.styles import StyleError, delete_style as _delete

        try:
            _delete(style_id)
        except StyleError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/projects/{pid}/lyrics/fetch-translation")
    def fetch_translation(pid: str):
        """Pair the translation the lyrics' music platform provides (NetEase / QQ)."""
        h = handle(pid)
        n = S.fetch_translation(h)
        return view(h, paired=n)

    @app.get("/api/projects/{pid}/karaoke")
    def get_karaoke(pid: str):
        return handle(pid).project.karaoke.model_dump(mode="json")

    @app.put("/api/projects/{pid}/karaoke")
    def put_karaoke(pid: str, body: dict):
        h = handle(pid)
        S.set_karaoke_style(h, body)
        return h.project.karaoke.model_dump(mode="json")

    @app.put("/api/projects/{pid}/singers")
    def put_line_singers(pid: str, body: LineSingersBody):
        h = handle(pid)
        guard(S.set_line_singers, h, [x.model_dump() for x in body.lines])
        return view(h)

    @app.get("/api/projects/{pid}/singers/markers")
    def get_singer_markers(pid: str):
        return S.singer_markers(handle(pid))

    @app.post("/api/projects/{pid}/singers/markers")
    def apply_markers(pid: str, body: MarkersBody):
        h = handle(pid)
        messages = guard(S.apply_singer_markers, h, body.names, body.strip)
        return view(h, messages=messages)

    @app.put("/api/projects/{pid}/karaoke/singers")
    def put_singers(pid: str, body: dict):
        h = handle(pid)
        guard(S.set_singers, h, body)
        return h.project.karaoke.model_dump(mode="json")

    @app.post("/api/projects/{pid}/karaoke/singers/preset")
    def use_singer_preset(pid: str, body: dict):
        """Use a saved set of singers ({id}); parts already assigned stay with the same person."""
        from ..karaoke.singer_presets import PresetError, get_preset

        h = handle(pid)
        try:
            preset = get_preset(str((body or {}).get("id") or ""))
        except PresetError as e:
            raise HTTPException(404, str(e)) from e
        out = guard(S.apply_singer_preset, h, preset["singers"])
        return view(h, **out)

    @app.get("/api/karaoke/singer-presets")
    def list_singer_presets():
        from ..karaoke.singer_presets import list_presets

        return list_presets()

    @app.post("/api/karaoke/singer-presets")
    def save_singer_preset(body: dict):
        from ..karaoke.singer_presets import PresetError, save_preset

        b = body or {}
        try:
            return save_preset(b.get("name", ""), b.get("singers") or {}, b.get("id"))
        except PresetError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/karaoke/singer-presets/{preset_id}")
    def delete_singer_preset(preset_id: str):
        from ..karaoke.singer_presets import PresetError, delete_preset

        try:
            delete_preset(preset_id)
        except PresetError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.delete("/api/projects/{pid}/karaoke/singers/{number}")
    def delete_singer(pid: str, number: int):
        h = handle(pid)
        changed = guard(S.remove_singer, h, number)
        return view(h, changed=changed)

    @app.post("/api/karaoke/singer-colors")
    def singer_colors(body: dict):
        """Every colour of these singers, the derived ones filled in (for the editor)."""
        from ..karaoke.themes import singer_colors as colors
        from ..models import KaraokeSinger

        out = []
        for m in (body or {}).get("members") or []:
            try:
                out.append(colors(KaraokeSinger.model_validate(m)))
            except Exception as e:
                raise HTTPException(400, f"演唱者颜色无效：{e}") from e
        return out

    @app.get("/api/projects/{pid}/karaoke/info")
    def get_song_info(pid: str):
        return S.song_info(handle(pid))

    @app.put("/api/projects/{pid}/karaoke/info")
    def put_song_info(pid: str, body: dict):
        h = handle(pid)
        S.set_song_info_text(h, body.get("text"))
        return S.song_info(h)

    @app.post("/api/projects/{pid}/karaoke/preview")
    def karaoke_preview(pid: str, body: dict):
        from ..karaoke.render import RenderError

        try:
            png = S.karaoke_preview(handle(pid), (body or {}).get("t_ms", 0), (body or {}).get("style"),
                                    background=(body or {}).get("background", "auto"))
        except RenderError as e:
            raise HTTPException(400, str(e)) from e
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/projects/{pid}/karaoke/burn")
    def karaoke_burn(pid: str, body: dict):
        h = handle(pid)
        not_busy(pid)
        if h.project.result() is None:
            raise HTTPException(400, "还没有对齐结果：请先完成对齐")
        audio = body.get("audio", "original")
        if audio not in ("original", "mix", "none"):
            raise HTTPException(400, "audio 只能是 original / mix / none")
        pct = body.get("vocal_keep_pct")
        if pct is not None and (not isinstance(pct, (int, float)) or not 0 <= pct <= 100):
            raise HTTPException(400, "vocal_keep_pct 必须是 0–100 之间的数字")

        def run(job: Job):
            out = S.karaoke_burn(h, background=body.get("background", "auto"), audio=audio,
                                 quality=body.get("quality", "standard"), vocal_keep_pct=pct,
                                 cancel=job.cancel_token,
                                 progress=progress_setter(job))
            return {"filename": out["filename"], "warnings": out["warnings"], "url": download_url(pid, out["filename"])}

        return jm.submit("burn", run, project_id=pid).to_dict()

    @app.post("/api/projects/{pid}/video/export")
    def video_export(pid: str, body: dict):
        h = handle(pid)
        if h.project.video is None:
            raise HTTPException(400, "项目中没有视频：请在“音频与歌词”中上传视频作为原曲")
        S.require_stems(h, "降低人声")
        settings = body or {}
        S.mix_settings(h.project.mix, settings)

        def run(job: Job):
            job.message = "混音并合成视频"
            out = S.export_video(h, settings, cancel=job.cancel_token)
            return {"filename": out["filename"], "report": out["report"], "url": download_url(pid, out["filename"])}

        return jm.submit("video", run, project_id=pid, heavy=False).to_dict()

    @app.get("/api/projects/{pid}/exports")
    def list_exports(pid: str):
        """Files in the project's exports folder, newest first (partial ".…" files left out)."""
        from datetime import datetime, timezone

        from ..pipeline import export_url

        d = handle(pid).dir / "exports"
        files = [f for f in d.iterdir() if f.is_file() and not f.name.startswith(".")] if d.is_dir() else []
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return [{"filename": f.name, "url": export_url(pid, f.name), "size": f.stat().st_size,
                 "modified": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()} for f in files]

    @app.get("/api/projects/{pid}/exports/{filename}")
    def exported_file(pid: str, filename: str):
        h = handle(pid)
        name = Path(filename).name
        path = h.dir / "exports" / name
        if name in ("", ".", "..") or name != filename or not path.is_file():
            raise HTTPException(404, "文件不存在")
        return FileResponse(path, filename=path.name)

    # ------------------------------------------------------------------ calibration

    @app.post("/api/projects/{pid}/calibration/suggest")
    def calibration_suggest(pid: str):
        """Automatic offset suggestion (a trial plain alignment); nothing is changed."""
        from ..auto_calibrate import suggest_calibration

        h = handle(pid)
        if h.project.mode != "lrc":
            raise HTTPException(400, "只有 LRC 增强模式需要校准")
        if h.project.asset("original") is None:
            raise HTTPException(400, "请先上传原曲")
        not_busy(pid)  # a trial alignment: heavy, like the other jobs a running task holds off

        def run(job: Job):
            return suggest_calibration(h, cancel=job.cancel_token, progress=progress_setter(job))

        return jm.submit("calibrate", run, project_id=pid).to_dict()

    @app.post("/api/projects/{pid}/calibration/mark")
    def cal_mark(pid: str, body: MarkBody):
        h = handle(pid)
        guard(S.calibration_op, h, "mark", line_id=body.line_id, marked_ms=body.marked_ms)
        return view(h)

    @app.post("/api/projects/{pid}/calibration/shift")
    def cal_shift(pid: str, body: ShiftBody):
        h = handle(pid)
        guard(S.calibration_op, h, "shift", user_shift_ms=body.user_shift_ms)
        return view(h)

    @app.post("/api/projects/{pid}/calibration/confirm-zero")
    def cal_zero(pid: str):
        h = handle(pid)
        guard(S.calibration_op, h, "confirm-zero")
        return view(h)

    @app.post("/api/projects/{pid}/calibration/check")
    def cal_check(pid: str, body: MarkBody):
        h = handle(pid)
        guard(S.calibration_op, h, "check", line_id=body.line_id, marked_ms=body.marked_ms)
        return view(h)

    @app.post("/api/projects/{pid}/calibration/undo")
    def cal_undo(pid: str):
        h = handle(pid)
        guard(S.calibration_op, h, "undo")
        return view(h)

    # ------------------------------------------------------------------ alignment / results

    @app.post("/api/projects/{pid}/align")
    def align(pid: str, body: AlignBody):
        h = handle(pid)
        not_busy(pid)

        def run(job: Job):
            r = S.run_align(h, line_ids=body.line_ids, audio_role=body.audio_role, config=body.config,
                            cancel=job.cancel_token, progress=progress_setter(job))
            return {"result_id": r.id}

        return jm.submit("align", run, project_id=pid).to_dict()

    @app.get("/api/projects/{pid}/results/{rid}")
    def get_result(pid: str, rid: str):
        h = handle(pid)
        result_or_404(h, rid)
        return S.get_result(h, rid).model_dump(mode="json")

    @app.post("/api/projects/{pid}/results/import")
    def import_result(pid: str, body: TextBody):
        _check_text(body.text)
        h = handle(pid)
        r = S.import_result_json(h, body.text)
        return view(h, result_id=r.id)

    @app.post("/api/projects/{pid}/results/{rid}/activate")
    def activate(pid: str, rid: str):
        h = handle(pid)
        result_or_404(h, rid)
        S.activate_result(h, rid)
        return view(h)

    def unit_op(pid: str, rid: str, fn, *args, **kw):
        from ..project.edits import EditError

        h = handle(pid)
        result_or_404(h, rid)
        with h.lock:
            r = S.get_result(h, rid)
            try:
                ut = fn(r, *args, **kw)
            except EditError as e:
                raise HTTPException(400, str(e)) from e
            h.save()
            return ut.model_dump(mode="json")

    @app.put("/api/projects/{pid}/results/{rid}/units/{uid}")
    def set_unit(pid: str, rid: str, uid: str, body: UnitBody):
        from ..project.edits import set_manual

        h = handle(pid)
        orig = h.project.asset("original")
        return unit_op(pid, rid, set_manual, uid, body.start_ms, body.end_ms, locked=body.locked,
                       duration_ms=orig.duration_ms if orig else None)

    @app.delete("/api/projects/{pid}/results/{rid}/units/{uid}/manual")
    def clear_unit(pid: str, rid: str, uid: str):
        from ..project.edits import clear_manual

        return unit_op(pid, rid, clear_manual, uid)

    @app.post("/api/projects/{pid}/results/{rid}/units/{uid}/lock")
    def lock_unit(pid: str, rid: str, uid: str, body: LockBody):
        from ..project.edits import set_lock

        return unit_op(pid, rid, set_lock, uid, body.locked)

    @app.post("/api/projects/{pid}/results/{rid}/units/{uid}/restore")
    def restore_unit(pid: str, rid: str, uid: str, body: RestoreBody):
        from ..project.edits import restore_manual

        return unit_op(pid, rid, restore_manual, uid, body.manual)

    @app.post("/api/projects/{pid}/results/{rid}/lines/{lid}/retime")
    def retime_line(pid: str, rid: str, lid: str, body: RetimeBody):
        """Shift (start only) or stretch (start and end) every unit of a line; they become locked manual edits."""
        from ..project.edits import EditError, retime_line as retime

        h = handle(pid)
        result_or_404(h, rid)
        orig = h.project.asset("original")
        with h.lock:
            r = S.get_result(h, rid)
            try:
                units = retime(r, lid, body.start_ms, body.end_ms, duration_ms=orig.duration_ms if orig else None)
            except EditError as e:
                raise HTTPException(400, str(e)) from e
            h.save()
            return {"units": [u.model_dump(mode="json") for u in units]}

    @app.post("/api/projects/{pid}/results/{rid}/units/retime")
    def retime_units(pid: str, rid: str, body: RetimeUnitsBody):
        """Shift (start only) or stretch (start and end) several units together; they become locked manual edits."""
        from ..project.edits import EditError, retime_units as retime

        h = handle(pid)
        result_or_404(h, rid)
        orig = h.project.asset("original")
        with h.lock:
            r = S.get_result(h, rid)
            try:
                units = retime(r, body.unit_ids, body.start_ms, body.end_ms,
                               duration_ms=orig.duration_ms if orig else None)
            except EditError as e:
                raise HTTPException(400, str(e)) from e
            h.save()
            return {"units": [u.model_dump(mode="json") for u in units]}

    @app.post("/api/projects/{pid}/results/{rid}/adopt")
    def adopt(pid: str, rid: str, body: AdoptBody):
        h = handle(pid)
        result_or_404(h, rid)
        r = S.adopt_lines(h, rid, body.line_ids, from_result_id=body.from_result_id,
                          candidate_id=body.candidate_id)
        return r.model_dump(mode="json")

    # ------------------------------------------------------------------ export

    @app.get("/api/projects/{pid}/export/{fmt}")
    def export(pid: str, fmt: str, result_id: Optional[str] = None, download: int = 0):
        h = handle(pid)
        if result_id:
            result_or_404(h, result_id)
        out = S.export(h, fmt, result_id)
        if download:
            ascii_name = out.filename.encode("ascii", "replace").decode().replace('"', "_")
            headers = {"Content-Disposition": f'attachment; filename="{ascii_name}"; '
                                              f"filename*=UTF-8''{quote(out.filename, safe='')}"}
            if out.warnings:
                headers["X-Export-Warnings"] = str(len(out.warnings))
            return Response(out.content.encode("utf-8"), media_type=f"{out.media_type}; charset=utf-8",
                            headers=headers)
        return {"filename": out.filename, "media_type": out.media_type, "content": out.content,
                "warnings": out.warnings}

    # ------------------------------------------------------------------ static UI

    @app.middleware("http")
    async def _no_stale_ui(request: Request, call_next):
        # UI files are small and local: always revalidate so an upgrade is picked up
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


def _upload_name(filename: Optional[str], default: str) -> str:
    """The plain file name of an upload ("/", ".." and empty names become ``default``)."""
    name = Path((filename or "").replace("\\", "/")).name
    return default if name in ("", ".", "..") else name


def _clean(message: str, tmp_dir: str, name: str) -> str:
    """An error message without the server's temporary paths (ffmpeg quotes the file it read)."""
    for d in {str(tmp_dir), str(Path(tmp_dir).resolve())}:
        message = message.replace(str(Path(d) / name), name).replace(d, "")
    return message


def _check_text(text: str) -> None:
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(413, "文本过大")


async def _save_upload(file: UploadFile, dest: Path, max_bytes: int) -> int:
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, "上传文件过大")
            f.write(chunk)
    return size
