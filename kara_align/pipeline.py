"""Simple mode: one task per song, run start to finish in a queue.

A task turns (video or audio, music link or pasted lyrics, mode) into a karaoke
video with the app settings::

    import → lyrics → LRC offset (confirmed by the user) → AI readings → separation → align → video

Each stage reuses the service functions of the detailed mode, so a finished
(or failed) task is an ordinary project that can be opened and refined there.
Tasks run one at a time; heavy stages share :data:`HEAVY_LOCK` with the
detailed mode's jobs.  The queue is saved in ``<workspace>/.tasks/tasks.json``;
after a restart, queued tasks continue and an interrupted one can be retried
from the stage where it stopped.

Stages that only improve the result (AI readings, separation) never fail a
task: they are skipped with a warning shown on the task.

The first stages (import, lyrics, offset) are quick and run at once in a
separate preparation lane, even while another task holds the heavy worker.
In LRC mode the task then stops for the user to mark where the first line is
sung (the audio of a video is often not the recording the LRC was timed on),
so everything that needs a person happens right after the task is added; the
rest (AI readings, separation, alignment, video) runs unattended in order.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import Field, field_validator

from . import audio_versions
from . import service as S
from . import settings as app_settings
from .audio_versions import AudioVersion
from .interfaces import CancelToken, Cancelled
from .models import KaraokeStyle, _Base, new_id, utcnow
from .project.jobs import run_heavy
from .project.store import atomic_write_text, timestamped

try:  # an exclusive lock on the queue's folder: flock on POSIX, msvcrt.locking on Windows
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

TaskStatus = Literal["preparing", "queued", "running", "waiting", "succeeded", "failed", "cancelled", "interrupted"]
StageStatus = Literal["pending", "running", "waiting", "done", "skipped", "failed"]


class WaitForUser(Exception):
    """A stage needs a decision from the user; the task waits without failing."""


class TaskConflict(S.ServiceError):
    """The task is not in a state that allows this (e.g. removing a running task)."""


class QueueElsewhere(TaskConflict):
    """Another server process on the same workspace runs the task queue."""

    def __init__(self) -> None:
        super().__init__("另一个 MiliKara 服务进程正在使用这个工作区的任务队列；请在那个进程打开的页面中操作，"
                         "或关闭它后重启本服务")

STAGES: list[tuple[str, str, float]] = [  # key, label, share of the progress bar
    ("import", "导入视频", 0.05),
    ("lyrics", "获取歌词", 0.03),
    ("calibrate", "确认偏移", 0.02),
    ("readings", "AI 注音", 0.12),
    ("separate", "人声分离", 0.38),
    ("align", "对齐", 0.20),
    ("export", "生成视频", 0.20),
]
PREP_STAGES = ("import", "lyrics", "calibrate")  # quick; run as soon as the task is added
AUTO_CALIBRATE_LABEL = "检测偏移"
SKIPPED_ON_ERROR = "skipped:error"  # a stage outcome: optional step skipped because it went wrong
_LABEL = {k: label for k, label, _ in STAGES}
_WEIGHT = {k: w for k, _, w in STAGES}


class Stage(_Base):
    key: str
    label: str
    status: StageStatus = "pending"
    progress: float = 0.0
    message: str = ""
    # skipped because something went wrong (an optional step: AI readings, separation) — a retry runs
    # it again; skipped because it is switched off / not set up is kept as it is
    failed_soft: bool = False


class TaskVideo(_Base):
    """The video settings of one task, fixed when it is added."""

    auto_export: bool = True
    video_audio: list[AudioVersion] = Field(default_factory=lambda: ["original"])  # one video each
    vocal_keep_pct: float = 20.0
    quality: Literal["standard", "high"] = "standard"

    @field_validator("video_audio", mode="before")
    @classmethod
    def _audio_versions(cls, v: Any) -> Any:  # (one string in tasks from before v1.2.0)
        return audio_versions.normalize(v)


class TaskProcessing(_Base):
    """How the task is processed, fixed when it is added (AI readings, vocal separation)."""

    ai_provider: Optional[str] = None  # None: tasks from before this was recorded (today's setting)
    # (ai_readings: AI readings on — the settings' ai.enabled when the task was added)
    ai_model: str = ""
    ai_readings: bool = True
    separate: bool = True
    separation_preset: str = "melband-roformer"
    separation_device: Literal["auto", "cpu"] = "auto"
    # LRC offset: "manual" = the user marks the first line right after adding; "auto" = detected from a
    # trial alignment after separation (asks only when unsure)
    calibration: Literal["manual", "auto"] = "manual"


def task_stages(calibration: str) -> list[Stage]:
    """A new task's stages, in the order they run: the automatic offset needs the separated vocals,
    so it comes after separation instead of right after the lyrics."""
    order = [(k, label) for k, label, _ in STAGES]
    if calibration == "auto":
        cal = next(x for x in order if x[0] == "calibrate")
        order.remove(cal)
        order.insert(next(i for i, x in enumerate(order) if x[0] == "align"), ("calibrate", AUTO_CALIBRATE_LABEL))
    return [Stage(key=k, label=label) for k, label in order]


class PipelineTask(_Base):
    id: str = Field(default_factory=lambda: new_id("t"))
    created: str = Field(default_factory=utcnow)
    finished: Optional[str] = None
    name: str = ""
    mode: Literal["plain", "lrc"] = "lrc"
    media_filename: str = ""
    # a picture / video (looped) shown behind the subtitles instead of the media's own picture
    background_filename: str = ""
    lyrics_kind: Literal["link", "text"] = "text"
    lyrics_input: str = ""
    status: TaskStatus = "queued"
    project_id: Optional[str] = None
    stages: list[Stage] = Field(default_factory=list)
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None
    detail: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    # LRC offset to confirm: the suggestion shown to the user (see stage_calibrate)
    calibration: Optional[dict[str, Any]] = None
    calibration_confirmed: bool = False
    # AI readings by hand (provider "manual"): the prompt to copy is the project's roundtrip
    # {roundtrip_id, snapshot_id, lines, chars}; see submit_readings
    readings_request: Optional[dict[str, Any]] = None
    # subtitle style and video settings, fixed when the task is added (queued tasks never pick up
    # later changes to the settings); None on tasks from before this existed
    karaoke: Optional[KaraokeStyle] = None
    video: Optional[TaskVideo] = None
    processing: Optional[TaskProcessing] = None
    style_label: str = ""
    style_colors: list[str] = Field(default_factory=list)
    style_applied: bool = False
    warning_stage: dict[str, str] = Field(default_factory=dict)  # warning text -> stage that raised it
    current_stage: str = ""
    project_deleted: bool = False  # the project was deleted in the detailed mode
    name_auto: bool = False  # the name is the media file's (nobody gave one)

    def stage(self, key: str) -> Stage:
        return next(s for s in self.stages if s.key == key)


def export_url(project_id: str, filename: str) -> str:
    """Download URL of an exported file (the name may contain #, ?, %, spaces …)."""
    from urllib.parse import quote

    return f"/api/projects/{quote(project_id, safe='')}/exports/{quote(filename, safe='')}"


def is_music_link(text: str) -> bool:
    """A pasted music link / share text, as opposed to lyrics."""
    from .lyrics.fetch.links import _EXPLICIT, extract_urls

    t = text.strip()
    if _EXPLICIT.match(t):
        return True
    lines = [ln for ln in t.splitlines() if ln.strip()]
    return bool(extract_urls(t)) and len(lines) <= 3 and not re.search(r"^\s*\[\d+:\d+", t, re.M)


# ------------------------------------------------------------------------------------------ queue


class TaskQueue:
    """Runs tasks one after another in a worker thread; state is saved to disk.

    One server process per workspace runs the queue: it holds an exclusive lock on
    ``<workspace>/.tasks/lock``.  A second process on the same workspace (e.g. a second
    ``milikara serve``) starts *passive*: it shows the tasks as saved by the first one and
    refuses every change (:class:`QueueElsewhere`), so tasks never run twice and tasks.json is
    never written by two processes.
    """

    def __init__(self, ws: "S.Workspace") -> None:
        self.ws = ws
        self.dir = Path(ws.root) / ".tasks"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._cancel: dict[str, CancelToken] = {}
        self._stop = False
        self.save_error: Optional[str] = None  # the last failure to write tasks.json (disk full …)
        self._lock_file = self._acquire()
        self.passive = self._lock_file is None
        self._loaded_mtime: Optional[float] = None
        self.tasks: list[PipelineTask] = self._load()
        from concurrent.futures import ThreadPoolExecutor

        self._prep_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kara-prep")
        self._thread = threading.Thread(target=self._worker, name="kara-tasks", daemon=True)
        if self.passive:
            log.warning("another process runs the task queue of %s; this one only shows it", ws.root)
            return
        self._thread.start()
        for t in self.tasks:
            if t.status == "preparing":
                self._submit_prep(t)

    # ---- one queue per workspace
    def _acquire(self):
        """The exclusive queue lock, or None when another process holds it."""
        if fcntl is None and msvcrt is None:
            return True  # no locking available: behave as before
        # held for the life of the process (released by shutdown, or by the OS when the process ends)
        fd = os.open(self.dir / "lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # Windows: a byte-range lock on the first byte (non-blocking)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(fd)
            return None
        return fd

    def _release(self) -> None:
        fd, self._lock_file = self._lock_file, None
        if isinstance(fd, int) and not isinstance(fd, bool):
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            finally:
                os.close(fd)

    def _require_owner(self) -> None:
        if self.passive:
            raise QueueElsewhere()

    def _refresh(self) -> None:
        """Passive: pick up what the process running the queue saved since."""
        if not self.passive:
            return
        try:
            mtime = self._file().stat().st_mtime
        except OSError:
            return
        if mtime != self._loaded_mtime:
            tasks = self._load()
            with self._lock:
                self.tasks = tasks

    # ---- persistence
    def _file(self) -> Path:
        return self.dir / "tasks.json"

    def _load(self) -> list[PipelineTask]:
        try:
            self._loaded_mtime = self._file().stat().st_mtime
            raw = json.loads(self._file().read_text(encoding="utf-8"))
            tasks = [PipelineTask.model_validate(t) for t in raw]
        except FileNotFoundError:
            return []
        except Exception:  # unreadable (e.g. written by another version): keep it for inspection, start empty
            if not self.passive:
                try:  # a new name each time: an earlier broken copy is never overwritten
                    self._file().replace(timestamped(self._file()))
                except OSError:
                    pass
            return []
        if self.passive:  # the other process's tasks, as they are (they may be running there right now)
            return tasks
        for t in tasks:
            if t.status == "running" and _first_open_stage(t) not in prep_keys(t):  # stopped while it ran
                t.status = "interrupted"
                t.message = "应用重启时中断，可以重试"
                for s in t.stages:
                    if s.status == "running":
                        s.status = "pending"
            elif t.status == "running":  # was still preparing: simply prepare again
                t.status = "preparing"
                for s in t.stages:
                    if s.status == "running":
                        s.status = "pending"
        return tasks

    def _save(self) -> bool:
        """Write tasks.json; False (and ``save_error`` set) when it cannot be written.

        Never raises for a disk problem: a full disk must not stop the queue thread (the tasks then
        stayed "queued" forever); the task being run gets a warning instead (see :meth:`_run`)."""
        if self.passive:
            return True  # never written by a process that does not run the queue
        # one saver at a time, each writing what is current then: an older snapshot never lands last
        try:
            with self._save_lock:
                with self._lock:
                    data = [t.model_dump(mode="json") for t in self.tasks]
                atomic_write_text(self._file(), json.dumps(data, ensure_ascii=False))
        except OSError as e:
            self.save_error = f"{e.strerror or e}"
            log.error("cannot write %s: %s", self._file(), e)
            return False
        self.save_error = None
        return True

    def media_path(self, task: PipelineTask) -> Path:
        return self.dir / task.id / task.media_filename

    def background_path(self, task: PipelineTask) -> Optional[Path]:
        return self.dir / task.id / "background" / task.background_filename if task.background_filename else None

    # ---- public API
    def list(self) -> list[dict]:
        self._refresh()
        with self._lock:
            tasks = list(reversed(self.tasks))
        out = []
        for t in tasks:
            # polled every second: the full style snapshot and tracebacks stay on the server
            d = t.model_dump(mode="json", exclude={"karaoke", "detail", "warning_stage"})
            if t.status == "waiting" and t.calibration and t.project_id:
                try:  # the project's current offset, if one was set meanwhile (detailed mode)
                    shift = self.ws.get(t.project_id).project.calibration.user_shift_ms
                except Exception:
                    shift = 0
                d["calibration"]["current_ms"] = t.calibration["lrc_ms"] + shift if shift else None
            out.append(d)
        return out

    def forget_project(self, pid: str) -> None:
        """The project was deleted: its tasks keep their history but lose links to it."""
        if self.passive:
            return
        with self._lock:
            for t in self.tasks:
                if t.project_id == pid:
                    t.project_deleted = True
                    t.outputs = {}
                    t.message = "项目已删除"
        self._save()

    def get(self, task_id: str) -> PipelineTask:
        self._refresh()
        with self._lock:
            for t in self.tasks:
                if t.id == task_id:
                    return t
        raise KeyError(task_id)

    def add(self, *, media: Path, filename: str, lyrics: str, mode: str, name: str = "",
            style: Optional[dict] = None, background: Optional[Path] = None,
            background_filename: str = "") -> PipelineTask:
        """``style``: the task's subtitle choices (TaskStyleOptions); None = the last ones used.
        ``background``: a picture or a video (looped) to show behind the subtitles (the media is then
        usually just the song's audio); checked here, before the task is added."""
        self._require_owner()
        lyrics = lyrics.strip()
        if not lyrics:
            raise S.ServiceError("请粘贴音乐链接或歌词")
        if mode not in ("plain", "lrc"):
            raise S.ServiceError("模式只能是 plain 或 lrc")
        cfg = app_settings.load()
        try:
            opts = app_settings.TaskStyleOptions.model_validate(style) if style is not None else cfg.simple.task_style
        except Exception as e:
            raise S.ServiceError(f"字幕样式选项无效：{e}") from e
        karaoke, label, colors = resolve_task_style(cfg.simple, opts)
        video = TaskVideo(auto_export=cfg.simple.auto_export, video_audio=opts.video_audio or cfg.simple.video_audio,
                          vocal_keep_pct=cfg.simple.vocal_keep_pct if opts.vocal_keep_pct is None else opts.vocal_keep_pct,
                          quality=cfg.simple.quality)
        karaoke.output.vocal_keep_pct = video.vocal_keep_pct
        safe = Path(filename).name
        if safe in ("", ".", ".."):
            safe = "media"
        bg_name = ""
        if background is not None:
            from .karaoke.background import BackgroundError, probe_background, validate_background

            bg_name = Path(background_filename or Path(background).name).name
            if bg_name in ("", ".", ".."):
                bg_name = "background" + Path(background).suffix
            try:
                with open(background, "rb") as f:
                    kind = validate_background(bg_name, f.read(64), Path(background).stat().st_size)
                probe_background(Path(background), kind)
            except BackgroundError as e:
                raise S.ServiceError(str(e)) from e
        t = PipelineTask(name=name.strip(), mode=mode, media_filename=safe, background_filename=bg_name,  # type: ignore[arg-type]
                         lyrics_kind="link" if is_music_link(lyrics) else "text", lyrics_input=lyrics,
                         stages=task_stages(cfg.simple.calibration),
                         karaoke=karaoke, video=video, style_label=label, style_colors=colors,
                         processing=TaskProcessing(ai_provider=cfg.ai.provider, ai_model=cfg.ai.model,
                                                   ai_readings=cfg.ai.enabled, separate=cfg.simple.separate,
                                                   separation_preset=cfg.simple.separation_preset,
                                                   separation_device=cfg.simple.separation_device,
                                                   calibration=cfg.simple.calibration))
        if style is not None:  # the next task starts from these choices
            app_settings.update({"simple": {"task_style": opts.model_dump(mode="json")}})
        if not t.name and t.lyrics_kind == "text":
            t.name = Path(safe).stem
            t.name_auto = True
        dest = self.dir / t.id
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(media), dest / safe)
        if background is not None:
            (dest / "background").mkdir(exist_ok=True)  # its own folder: the names may be the same
            shutil.move(str(background), dest / "background" / bg_name)
        t.status, t.message = "preparing", "读取视频和歌词"
        with self._lock:
            self.tasks.append(t)
        self._save()
        self._submit_prep(t)
        return t

    def snapshot(self) -> list[PipelineTask]:
        """The tasks as they are now (read from tasks.json first when another server owns the queue)."""
        self._refresh()
        with self._lock:
            return list(self.tasks)

    def active_for_project(self, pid: str) -> Optional[PipelineTask]:
        """The unfinished task working on a project, if any (the detailed mode must not run heavy
        jobs on it meanwhile)."""
        self._refresh()
        with self._lock:
            return next((t for t in self.tasks if t.project_id == pid
                         and t.status in ("preparing", "waiting", "queued", "running")), None)

    def _submit_prep(self, task: PipelineTask) -> None:
        fut = self._prep_pool.submit(self._prepare, task)

        def done(f) -> None:  # an error there would otherwise vanish with the future
            exc = f.exception() if not f.cancelled() else None
            if exc is not None:
                log.error("preparing task %s failed", task.id, exc_info=exc)

        fut.add_done_callback(done)

    def _prepare(self, task: PipelineTask) -> None:
        """Quick stages right away; then wait for the user (LRC) or join the queue."""
        token = CancelToken()
        with self._lock:
            # (a task removed from the list before it came to this is never run)
            if task.status != "preparing" or not any(x is task for x in self.tasks) or self._stop:
                return
            self._cancel[task.id] = token
        self._run(task, token, prep_keys(task), done_status="queued")
        with self._lock:
            self._wake.notify_all()

    def cancel(self, task_id: str) -> PipelineTask:
        self._require_owner()
        t = self.get(task_id)
        with self._lock:
            token = self._cancel.get(task_id)
            if token is not None:  # picked up by a worker (even if it has not marked it running yet)
                token.cancel()
                t.message = "正在取消…"
            if t.status in ("queued", "waiting") or (t.status == "preparing" and token is None):
                t.status, t.message, t.finished = "cancelled", "已取消", utcnow()
                for s in t.stages:
                    if s.status == "waiting":
                        s.status = "pending"
        self._save()
        return t

    def retry(self, task_id: str) -> PipelineTask:
        self._require_owner()
        t = self.get(task_id)
        with self._lock:
            if t.status not in ("failed", "cancelled", "interrupted"):
                raise TaskConflict("只有失败、取消或中断的任务可以重试")
            if t.project_deleted:
                raise S.ServiceError("这个任务的项目已被删除，无法重试；请重新添加任务")
            for s in t.stages:
                # failed / stopped stages, and optional ones that went wrong, get another chance;
                # an optional step that is switched off (or AI not set up) stays skipped
                if s.status in ("failed", "running", "waiting") or (s.status == "skipped" and s.failed_soft):
                    s.status, s.progress, s.message, s.failed_soft = "pending", 0.0, "", False
            # every stage that runs again (also one a restart put back to pending) drops its warnings;
            # they are raised again if still true
            again = {s.key for s in t.stages if s.status == "pending"}
            t.warnings = [w for w in t.warnings if t.warning_stage.get(w) not in again]
            t.warning_stage = {w: k for w, k in t.warning_stage.items() if w in t.warnings}
            prep = _first_open_stage(t) in prep_keys(t)
            t.status = "preparing" if prep else "queued"
            t.error, t.detail, t.message, t.finished = None, None, "等待开始", None
            self._wake.notify_all()
        self._save()
        if prep:
            self._submit_prep(t)
        return t

    def confirm_calibration(self, task_id: str, *, marked_ms: Optional[int] = None, plain: bool = False) -> PipelineTask:
        """The user confirmed where the first line starts (or chose not to use the LRC times)."""
        self._require_owner()
        t = self.get(task_id)
        with self._lock:  # a cancel must not slip in between the check and the change
            if t.status != "waiting" or not t.calibration or not t.project_id \
                    or t.stage("calibrate").status != "waiting":
                raise S.ServiceError("这个任务现在不需要确认偏移")
            h = self.ws.get(t.project_id)
            st = t.stage("calibrate")
            if plain:
                S.update_settings(h, mode="plain")
                t.mode = "plain"
                st.message = "改用普通模式"
            else:
                if marked_ms is None:
                    raise S.ServiceError("请标记第一句开始唱的位置")
                line_id = t.calibration["line_id"]
                if not any(ln.id == line_id for ln in h.project.lyrics.lines):
                    # the lyrics were edited in the detailed mode meanwhile: ask about the new first line
                    t.calibration = calibration_request(h)
                    stale = True
                else:
                    stale = False
                    try:
                        S.calibration_op(h, "mark", line_id=line_id, marked_ms=int(marked_ms))
                    except ValueError as e:
                        raise S.ServiceError(str(e)) from e
                    shift = h.project.calibration.user_shift_ms
                    st.message = f"偏移 {shift:+d} ms（已确认）"
                    t.calibration["confirmed_ms"] = int(marked_ms)
            if plain or not stale:
                st.status, st.progress = "done", 1.0
                t.calibration_confirmed = True
                prep = self._continue(t)
        self._save()
        if not plain and stale:
            raise S.ServiceError("歌词在详细模式中改过：已重新选出要确认的第一句，请重新标记后确认")
        if prep:
            self._submit_prep(t)
        return t

    def _continue(self, t: PipelineTask) -> bool:
        """After the user answered (under the lock): back to the preparation lane when a quick stage is
        next (the readings prompt by hand), else into the queue.  Returns whether to submit it."""
        prep = _first_open_stage(t) in prep_keys(t)
        t.status, t.message = ("preparing", "继续准备") if prep else ("queued", "等待继续")
        if not prep:
            self._wake.notify_all()
        return prep

    def readings_prompt(self, task_id: str) -> dict:
        """The prompt to copy into a web chat (AI readings by hand)."""
        t = self.get(task_id)
        req = t.readings_request
        if not req or not t.project_id:
            raise S.ServiceError("这个任务没有等待粘贴的 AI 注音")
        h = self.ws.get(t.project_id)
        rt = next((x for x in h.project.ai_roundtrips if x.id == req.get("roundtrip_id")), None)
        if rt is None:
            raise S.ServiceError("提示词已不在项目里（项目可能在详细模式中改过），请重试这个任务")
        return {"prompt": rt.prompt, "lines": req.get("lines"), "snapshot_id": req.get("snapshot_id")}

    def submit_readings(self, task_id: str, *, text: Optional[str] = None, skip: bool = False) -> PipelineTask:
        """The web chat's reply for a task waiting for AI readings by hand (or: go on with the rule
        readings).  A reply with no usable line is refused and the task keeps waiting."""
        self._require_owner()
        t = self.get(task_id)
        with self._lock:
            if t.status != "waiting" or not t.readings_request or not t.project_id \
                    or t.stage("readings").status != "waiting":
                raise S.ServiceError("这个任务现在不需要粘贴 AI 注音结果")
            h = self.ws.get(t.project_id)
            st = t.stage("readings")
            if skip:
                st.status, st.message = "skipped", "已跳过（使用规则读音）"
            else:
                if not (text or "").strip():
                    raise S.ServiceError("请粘贴 AI 的回复")
                val = S.ai_validate(h, text or "")
                rep = val["report"]
                ok = [x for x in rep.get("lines", []) if x.get("status") == "ok"]
                if not ok:
                    h.previews.pop(val["report_id"], None)
                    why = (rep.get("errors") or [r for x in rep.get("lines", []) for r in x.get("reasons", [])] or ["没有可用的行"])
                    raise S.ServiceError("回复里没有可以采用的行：" + "；".join(why[:3]))
                try:
                    summary = S.ai_apply(h, val["report_id"], None)
                finally:
                    h.previews.pop(val["report_id"], None)
                bad = len(rep.get("lines", [])) - len(ok)
                if bad:
                    _warn(t, f"AI 注音有 {bad} 行未采用（保留规则读音）")
                applied = summary.get("applied", []) if isinstance(summary, dict) else []
                st.status, st.message = "done", f"更新 {len(applied)} 行（网页聊天）"
            st.progress = 1.0
            prep = self._continue(t)
        self._save()
        if prep:
            self._submit_prep(t)
        return t

    def remove(self, task_id: str) -> None:
        self._require_owner()
        with self._lock:
            # one step under the lock: a task picked up (or being picked up) by a worker is refused;
            # anything else leaves the list before a worker or the preparation lane can take it
            t = next((x for x in self.tasks if x.id == task_id), None)
            if t is None:
                raise KeyError(task_id)
            if task_id in self._cancel or t.status == "running":
                raise TaskConflict("任务正在运行，请先取消")
            self.tasks = [x for x in self.tasks if x.id != task_id]
            if t.status in ("preparing", "queued", "waiting"):
                t.status, t.message = "cancelled", "已移除"  # a stale reference never runs it
        shutil.rmtree(self.dir / task_id, ignore_errors=True)  # staged upload only; the project stays
        self._save()

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
            for c in self._cancel.values():
                c.cancel()
            self._wake.notify_all()
        self._prep_pool.shutdown(wait=False, cancel_futures=True)
        self._release()

    # ---- worker
    def _next(self) -> Optional[PipelineTask]:
        return next((t for t in self.tasks if t.status == "queued"), None)

    def _worker(self) -> None:
        while True:
            try:
                with self._lock:
                    while not self._stop and self._next() is None:
                        self._wake.wait(timeout=5)
                    if self._stop:
                        return
                    task = self._next()
                    assert task is not None
                    token = CancelToken()
                    self._cancel[task.id] = token
                    task.status = "running"  # taken: a cancel from now on goes through the token
                self._run(task, token, None, done_status="succeeded")
            except Exception:  # the queue thread must never die (every later task would wait forever)
                log.exception("task queue worker error")
                time.sleep(1.0)

    def _save_during(self, task: PipelineTask) -> None:
        """Save while a task runs; a failure to write is shown on that task."""
        if not self._save() and self.save_error:
            _warn(task, f"任务状态无法写入磁盘（{self.save_error}）；服务重启后这个任务可能需要重新添加")

    def _run(self, task: PipelineTask, token: CancelToken, keys: Optional[tuple[str, ...]], *,
             done_status: TaskStatus) -> None:
        with self._lock:
            dropped = task.status == "cancelled" or token.cancelled  # cancelled while being picked up
            if dropped:
                self._cancel.pop(task.id, None)
                if self._stop and task.status != "cancelled":
                    # stopped by the server shutting down before it began: not the user's cancel —
                    # it simply starts again after the restart
                    task.status, task.message = ("preparing", "读取视频和歌词") if keys else ("queued", "排队中")
                else:
                    task.status, task.message = "cancelled", "已取消"
                    task.finished = task.finished or utcnow()
            else:
                task.status, task.message = ("preparing" if keys else "running"), "开始"
        self._save_during(task)  # never while holding self._lock (the save lock is always taken first)
        if dropped:
            return
        try:
            run_task(self, task, token, keys)
            with self._lock:
                if token.cancelled:  # cancelled right as the last stage finished
                    raise Cancelled()
                task.status = done_status
                task.message = "完成" if done_status == "succeeded" else "排队中"
                if done_status == "succeeded":
                    task.progress = 1.0
        except WaitForUser as w:
            # (stages first, then the task: a reader never sees a finished task with a running stage)
            if token.cancelled:  # cancelled just as it came to ask: stays cancelled
                self._mark_running_stage(task, "pending")
                task.status, task.message = "cancelled", "已取消"
                return  # (finally still runs)
            for s in task.stages:
                if s.status == "running":
                    s.status, s.message = "waiting", str(w)
            task.status, task.message = "waiting", str(w)
        except Cancelled:
            if self._stop:  # the server is shutting down: not the user's cancel
                self._mark_running_stage(task, "pending")
                if keys:  # was preparing: prepared again on the next start
                    task.status, task.message = "preparing", "读取视频和歌词"
                else:
                    task.status, task.message = "interrupted", "服务关闭时中断，可以重试"
            else:
                self._mark_running_stage(task, "pending")
                task.status, task.message = "cancelled", "已取消"
        except Exception as e:  # report the real reason on the task
            error = str(e) if isinstance(e, S.ServiceError) else f"{type(e).__name__}: {e}"
            self._mark_running_stage(task, "failed", error)
            task.error = error
            task.detail = traceback.format_exc(limit=8)
            from .diagnostics import failure

            failure(f"任务 {task.id}（{task.name}）", error, task.detail)
            task.message = "失败"
            task.status = "failed"
        finally:
            if task.status in ("succeeded", "failed", "cancelled"):
                task.finished = utcnow()
            with self._lock:
                if self._cancel.get(task.id) is token:  # a retry may already have registered a new one
                    self._cancel.pop(task.id, None)
            self._save_during(task)

    @staticmethod
    def _mark_running_stage(task: PipelineTask, status: StageStatus, message: str = "") -> None:
        for s in task.stages:
            if s.status == "running":
                s.status = status
                if message:
                    s.message = message


# ------------------------------------------------------------------------------------------ stages


def _first_open_stage(t: PipelineTask) -> Optional[str]:
    return next((s.key for s in t.stages if s.status not in ("done", "skipped")), None)


def prep_keys(t: PipelineTask) -> tuple[str, ...]:
    """The quick stages at the start of this task (run as soon as it is added).  AI readings by hand
    are one of them: the prompt is ready at once and the user answers right after adding, not when
    the queue gets to the task."""
    quick = set(PREP_STAGES)
    if manual_readings(t):
        quick.add("readings")
    keys: list[str] = []
    for s in t.stages:
        if s.key not in quick:
            break
        keys.append(s.key)
    return tuple(keys)


def manual_readings(t: PipelineTask) -> bool:
    pr = t.processing
    return bool(pr and pr.ai_readings and pr.ai_provider == "manual")


def run_task(q: TaskQueue, task: PipelineTask, cancel: CancelToken, keys: Optional[tuple[str, ...]] = None) -> None:
    cfg = app_settings.load()
    if task.processing is not None:  # the choices made when the task was added, not today's settings
        pr = task.processing
        cfg.simple = cfg.simple.model_copy(update=pr.model_dump(exclude={"ai_provider", "ai_model", "ai_readings"}))
        if pr.ai_provider is not None:  # keys / URLs stay today's (never copied into the task)
            off = pr.ai_provider == "none"  # (tasks from before the switch: "none" was off)
            cfg.ai = cfg.ai.model_copy(update={"provider": "manual" if off else pr.ai_provider, "model": pr.ai_model,
                                               "enabled": pr.ai_readings and not off})
        else:  # older still: today's provider, the task's own switch
            cfg.ai = cfg.ai.model_copy(update={"enabled": cfg.ai.enabled and pr.ai_readings})
    last_save = [0.0]

    def save(force: bool = False) -> None:
        now = time.time()
        if force or now - last_save[0] > 1.0:
            last_save[0] = now
            q._save_during(task)

    def overall() -> None:
        done = sum(_WEIGHT[s.key] * (1.0 if s.status in ("done", "skipped") else s.progress) for s in task.stages)
        task.progress = round(min(0.999, done / sum(_WEIGHT.values())), 4)

    for st in list(task.stages):  # the task's own order (see task_stages)
        key = st.key
        if st.status in ("done", "skipped"):
            continue
        if keys is not None and key not in keys:
            break  # (the preparation stops at the first stage that is not its own)
        cancel.check()
        st.status, st.progress, st.message = "running", 0.0, ""
        task.message = st.label
        task.current_stage = key
        overall()
        save(True)

        def progress(frac: float, message: str = "", st=st) -> None:
            st.progress = max(0.0, min(1.0, float(frac)))
            if message:
                st.message = message
                task.message = f"{st.label} · {message}"
            overall()
            save()
            cancel.check()

        outcome = STAGE_FUNCS[key](q, task, cfg, cancel, progress)
        st.failed_soft = outcome == SKIPPED_ON_ERROR
        st.status = "skipped" if outcome in ("skipped", SKIPPED_ON_ERROR) else "done"
        st.progress = 1.0
        # keep a result note ("48 行", "整体偏移 -350 ms"), drop the last progress text
        st.message = "未完成，已跳过" if st.failed_soft else \
            (outcome if isinstance(outcome, str) and outcome not in ("skipped", "done") else "")
        overall()
        save(True)


def _handle(q: TaskQueue, task: PipelineTask) -> "S.ProjectHandle":
    if not task.project_id:
        raise S.ServiceError("任务还没有项目")
    return q.ws.get(task.project_id)


def _holder(task: PipelineTask, what: str) -> str:
    return f"极简模式任务「{task.name or task.media_filename}」的{what}"


# the checks worth a look after simple mode's alignment (the app links a warning naming 人工检查 there)
REVIEW_CODES = ("unit_in_rest", "line_gap", "low_confidence")


def _warn(task: PipelineTask, text: str) -> None:
    if text not in task.warnings:
        task.warnings.append(text)
        task.warning_stage[text] = task.current_stage


def stage_import(q, task, cfg, cancel, progress):
    if task.project_id:
        try:
            h = q.ws.get(task.project_id)
            if h.project.asset("original") is not None:
                return "done"
        except Exception:
            task.project_id = None
    src = q.media_path(task)
    if not src.exists():
        raise S.ServiceError("上传的文件已不存在，请重新添加任务")
    h = q.ws.get(task.project_id) if task.project_id else q.ws.create(task.name or Path(src).stem, task.mode)
    task.project_id = h.project.id
    bg = q.background_path(task)
    if bg is not None:
        if not bg.exists():
            raise S.ServiceError("上传的背景已不存在，请重新添加任务")
        progress(0.1, "读取背景")
        S.set_background(h, bg, filename=task.background_filename)
    progress(0.2, "读取视频 / 音频")
    from .audio.io import validate_upload

    with open(src, "rb") as f:
        validate_upload(src.name, f.read(64), src.stat().st_size, 8 * 1024**3)
    S.add_media(h, src, "original", filename=task.media_filename)
    with h.lock:
        h.project.mode = task.mode
        h.save()
    apply_task_style(h, task)
    # the project keeps its own copy: drop the staged upload (a retry reads the project's assets)
    shutil.rmtree(q.dir / task.id, ignore_errors=True)
    return "done"


def stage_lyrics(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    if task.lyrics_kind == "link":
        progress(0.2, "从音乐平台获取歌词")
        try:
            got = S.fetch_link(task.lyrics_input)
        except Exception as e:
            raise S.ServiceError(f"无法从链接获取歌词：{e}") from e
        if got["kind"] != "song":
            raise S.ServiceError("这是专辑或歌单链接，请粘贴单曲链接")
        song = got["song"]
        from .lyrics.fetch import fetch_song

        try:  # fetched once; both parses below use it
            fetched = fetch_song(song["platform"], song["song_id"])
        except Exception as e:
            raise S.ServiceError(f"无法从链接获取歌词：{e}") from e
        pv = S.parse_from_song(h, song["platform"], song["song_id"], song=fetched)
        if pv.get("error") and task.mode == "lrc":
            # only lyrics without times fall back to plain mode (they parse as plain lyrics);
            # anything else (no lyrics at all, …) fails with the platform's own message
            plain = S.parse_from_song(h, song["platform"], song["song_id"], mode="plain", song=fetched)
            if plain.get("error") or not plain.get("preview_id"):
                raise S.ServiceError(pv["error"])
            _warn(task, f"{pv['error']}；已改用普通模式")
            S.update_settings(h, mode="plain")
            task.mode = "plain"
            pv = plain
        title = song.get("title") or ""
        artists = song.get("artists") or []
        if not task.name and title:
            task.name = title + (f" - {', '.join(artists)}" if artists else "")
            S.update_settings(h, name=task.name)
    else:
        pv = S.parse_lyrics(h, task.lyrics_input, origin="paste")
        if pv.get("error") and task.mode == "lrc":
            # only lyrics without time tags fall back to plain mode; other errors keep their own message
            plain = S.parse_lyrics(h, task.lyrics_input, origin="paste", mode="plain")
            if plain.get("error") or not plain.get("preview_id"):
                raise S.ServiceError(pv["error"])
            _warn(task, "粘贴的歌词没有时间标签；已改用普通模式")
            S.update_settings(h, mode="plain")
            task.mode = "plain"
            pv = plain
    if pv.get("error") or not pv.get("preview_id"):
        raise S.ServiceError(pv.get("error") or "无法解析歌词")
    for w in pv.get("warnings") or []:
        _warn(task, w)
    progress(0.7, "整理歌词与读音")
    S.apply_lyrics(h, pv["preview_id"])
    if task.lyrics_kind == "link" and h.project.video is None and h.project.background is None:
        # audio only, no picture of its own: the song's cover, blurred behind it
        progress(0.75, "用歌曲封面做背景")
        try:
            S.cover_background(h)
        except Exception as e:
            _warn(task, f"没能用歌曲封面做背景（{e}），视频会是纯黑背景")
    if not h.project.lyrics.sung_lines():
        raise S.ServiceError("歌词里没有可以演唱的行")
    n = len(h.project.lyrics.sung_lines())
    paired = pair_translation(h, (pv.get("extra_tracks") or {}).get("translation"))
    if task.karaoke is not None and task.karaoke.info.enabled and task.name_auto \
            and not (h.project.lyrics.meta.title or "").strip():
        # no real title (pasted lyrics without [ti:], no name typed): a title card would show the file name
        k = h.project.karaoke.model_copy(deep=True)
        k.info.enabled = False
        S.set_karaoke_style(h, k.model_dump(mode="json"))
        task.karaoke.info.enabled = False
        _warn(task, "没有歌名（歌词里没有 [ti:]，也没有填写歌名），开头不显示歌曲信息；可以在详细模式的“歌曲信息”里填写后开启")
    if task.karaoke is not None and task.karaoke.translation.enabled and not any(
            (ln.translation or "").strip() for ln in h.project.lyrics.sung_lines()):
        _warn(task, "音乐平台没有提供这首歌的翻译，视频里不会显示翻译" if task.lyrics_kind == "link"
              else "粘贴的歌词没有翻译，视频里不会显示翻译（粘贴网易云 / QQ 音乐链接会自动带上平台的翻译）")
    return f"{n} 行" + (f" · 翻译 {paired} 行" if paired else "")


def pair_translation(h: "S.ProjectHandle", text: Optional[str]) -> int:
    """Store the platform's translation on the lyric lines (shown only if the
    subtitle style turns translations on).  Returns the number of lines paired."""
    if not text or not text.strip():
        return 0
    try:
        prev = S.preview_track(h, text, "translation")
        pairs = [{"line_id": x["line_id"], "text": x["text"]} for x in prev["pairs"] if x.get("text", "").strip()]
        if pairs:
            S.apply_track(h, "translation", pairs)
        return len(pairs)
    except Exception:  # a translation is a bonus, never a reason to fail
        return 0


def stage_readings(q, task, cfg, cancel, progress):
    if not cfg.ai.enabled:
        return "skipped"
    h = _handle(q, task)
    if cfg.ai.provider == "manual":
        # the prompt goes to a web chat by hand: wait for the reply (submit_readings)
        if task.readings_request is None:
            out = S.ai_prompt(h, None)
            task.readings_request = {"roundtrip_id": out["roundtrip_id"], "snapshot_id": out["snapshot_id"],
                                     "lines": len(h.project.lyrics.sung_lines()), "chars": len(out["prompt"])}
        raise WaitForUser("等待粘贴 AI 注音结果")
    try:
        out = S.ai_auto(h, None, cfg=cfg.ai, cancel=cancel, progress=progress)
        try:
            summary = S.ai_apply(h, out["report_id"], None)
        finally:
            h.previews.pop(out["report_id"], None)  # applied (or not applicable): not kept around
    except Cancelled:
        raise
    except Exception as e:  # rule readings are still usable
        _warn(task, f"AI 注音失败，使用规则读音：{e}")
        return SKIPPED_ON_ERROR
    rep = out["report"]
    bad = [lr["line_id"] for lr in rep.get("lines", []) if lr.get("status") != "ok"]
    if bad:
        _warn(task, f"AI 注音有 {len(bad)} 行未采用（保留规则读音）")
    applied = summary.get("applied", []) if isinstance(summary, dict) else []
    return f"更新 {len(applied)} 行"


def stage_separate(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    if not cfg.simple.separate:
        return "skipped"
    if _stems_match(h):
        return "已有分轨"  # kept (a retry, or separated in the detailed mode)
    try:
        from .audio.separation import ensure_available

        ensure_available()
    except Exception:
        _warn(task, "未安装人声分离组件：使用原曲对齐，也无法生成降低人声的视频")
        return SKIPPED_ON_ERROR
    try:
        run_heavy(lambda: S.run_separation(h, cfg.simple.separation_preset, cancel=cancel, progress=progress,
                                           device=cfg.simple.separation_device),
                  lambda m: progress(0.0, m), cancel, holder=_holder(task, "人声分离"))
    except Cancelled:
        raise
    except Exception as e:
        _warn(task, f"人声分离失败，使用原曲对齐：{e}")
        return SKIPPED_ON_ERROR
    return "done"


def _stems_match(h) -> bool:
    """Vocals and instrumental exist and were separated from the current original."""
    orig, voc, inst = (h.project.asset(r) for r in ("original", "vocals", "instrumental"))
    return bool(orig and voc and inst and voc.source.parent_sha256 == orig.sha256
                and inst.source.parent_sha256 == orig.sha256)


def _audio_role(h) -> str:
    return "vocals" if _stems_match(h) else "original"


def stage_calibrate(q, task, cfg, cancel, progress):
    """LRC mode: the global offset of the LRC times.  "manual": wait for the user to mark where the
    first timed line is sung.  "auto": detect it from a trial alignment; ask only when unsure."""
    h = _handle(q, task)
    if h.project.mode != "lrc":
        return "skipped"
    if task.calibration_confirmed:
        return task.stage("calibrate").message or "已确认"
    try:
        task.calibration = calibration_request(h)
    except S.ServiceError as e:
        _warn(task, f"LRC 时间无法使用（{e}）；已改用普通模式")
        S.update_settings(h, mode="plain")
        task.mode = "plain"
        return "改用普通模式"
    if task.processing is not None and task.processing.calibration == "auto":  # (the task's stage order)
        cal = h.project.calibration
        if cal.confirmed:  # set in the detailed mode meanwhile: that one counts
            task.calibration_confirmed = True
            return f"偏移 {cal.user_shift_ms:+d} ms（详细模式中已设置）"
        done = _auto_calibrate(task, h, cancel, progress)
        if done is not None:
            return done
        raise WaitForUser("自动检测没有把握，请确认开头位置")
    raise WaitForUser("等待确认开头位置")


def _auto_calibrate(task: PipelineTask, h: "S.ProjectHandle", cancel: CancelToken, progress) -> Optional[str]:
    """Detect the offset (auto_calibrate); applied when confident.  Otherwise the estimate (if any)
    and the reason go with the confirmation request, and None is returned."""
    from .auto_calibrate import estimate_lrc_shift

    assert task.calibration is not None
    try:
        est = run_heavy(lambda: estimate_lrc_shift(h, cancel=cancel, progress=lambda f, m="": progress(f * 0.95, m)),
                        lambda m: progress(0.0, m), cancel, holder=_holder(task, "偏移检测"))
    except S.ServiceError as e:
        task.calibration["auto"] = {"reason": str(e)}
        return None
    auto = {"shift_ms": est["shift_ms"], "tight": round(est["tight"], 3), "lines": est["lines"],
            "tight_lines": est["tight_lines"], "drift_ms": est["drift_ms"], "reason": est["reason"],
            "confident": est["confident"]}
    task.calibration["auto"] = auto
    if not est["confident"]:
        return None
    S.calibration_op(h, "shift", user_shift_ms=est["shift_ms"])
    task.calibration_confirmed = True
    return f"自动 · 偏移 {est['shift_ms']:+d} ms（{est['tight_lines']}/{est['lines']} 行一致）"


def calibration_request(h: "S.ProjectHandle") -> dict:
    """What the confirmation dialog needs: the first timed line, its LRC time,
    a line from the middle to check the result by ear, and the audio to play.
    (No automatic guess here: the vocals are not separated yet.)"""
    from .align import calibration as C

    doc = h.project.lyrics
    timed = [ln for ln in doc.sung_lines() if C.base_ms(doc, ln) is not None and ln.anchor is None]
    if not timed:
        raise S.ServiceError("歌词没有可用的行时间")
    ref = timed[0]
    mid = timed[len(timed) // 2] if len(timed) > 2 else None
    audio = h.project.asset("original")
    # lines whose LRC time is after the end of the audio (a shortened video): said up front
    after = sum(1 for ln in timed if audio and audio.duration_ms and C.base_ms(doc, ln) >= audio.duration_ms)
    return {
        "line_id": ref.id, "line_text": ref.text, "lrc_ms": C.base_ms(doc, ref),
        "lines_after_audio": after, "lines_total": len(timed),
        "lines": [{"id": ln.id, "text": ln.text, "lrc_ms": C.base_ms(doc, ln)} for ln in timed[:3]],
        "check_line": {"id": mid.id, "text": mid.text, "lrc_ms": C.base_ms(doc, mid)} if mid else None,
        "asset_id": audio.id if audio else None, "duration_ms": audio.duration_ms if audio else None,
    }


def _align(q: TaskQueue, task: PipelineTask, h: "S.ProjectHandle", cancel: CancelToken, progress):
    role = _audio_role(h)  # passed to this alignment only; the project's own setting is left alone

    def align():
        try:
            return S.run_align(h, audio_role=role, cancel=cancel, progress=progress)
        except S.LrcTimesError as e:
            if h.project.mode != "lrc":
                raise
            # only unusable LRC times fall back to plain mode; any other error fails the task
            _warn(task, f"LRC 时间无法使用（{e}）；已改用普通模式")
            S.update_settings(h, mode="plain")
            task.mode = "plain"
            return S.run_align(h, audio_role=role, cancel=cancel, progress=progress)

    r = run_heavy(align, lambda m: progress(0.0, m), cancel, holder=_holder(task, "对齐"))
    warns = [i for i in r.issues if i.severity in ("warning", "error") and i.code in REVIEW_CODES]
    if warns:
        _warn(task, f"有 {len(warns)} 处建议检查（在“人工检查”的“有问题”里查看）")
    return r


def stage_align(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    r = h.project.result()
    wanted = h.project.asset(_audio_role(h))
    if r is not None and S.staleness(h.project, r) is None and wanted is not None \
            and r.snapshot.audio_asset_id == wanted.id:
        # a current alignment of the same audio (a retry, or aligned in the detailed mode): keep it and its edits
        return f"已有对齐结果 · {len(r.units)} 个发音单元"
    r = _align(q, task, h, cancel, progress)
    return f"{len(r.units)} 个发音单元"


def resolve_task_style(simple: "app_settings.SimpleSettings",
                       opts: "app_settings.TaskStyleOptions") -> tuple[KaraokeStyle, str, list[str]]:
    """The complete subtitle style for a task's choices: (style, short label, colours for the list)."""
    from .karaoke.styles import StyleError, get_style
    from .karaoke.themes import TEMPLATES, hex_to_rgb, theme_style

    base = simple.karaoke
    if opts.source == "template":
        for c in filter(None, (opts.color, opts.secondary)):
            try:
                hex_to_rgb(c)
            except ValueError as e:
                raise S.ServiceError(str(e)) from e
        style = theme_style(opts.template, opts.color, base, opts.secondary or None)
        label, colors = TEMPLATES[opts.template], [opts.color] + ([opts.secondary] if opts.secondary else [])
    elif opts.source == "saved":
        try:
            style = get_style(opts.saved_id)
        except StyleError as e:
            raise S.ServiceError("选择的预设已不存在，请重新选择字幕样式") from e
        label, colors = style.preset or "预设", [style.text.color_sung]
    else:
        style = base.model_copy(deep=True)
        label, colors = "默认样式", [style.text.color_sung]
    if opts.translation is not None:
        style.translation.enabled = opts.translation
    if opts.song_info is not None:
        style.info.enabled = opts.song_info
    if opts.ruby == "off":
        style.ruby.enabled = False
    elif opts.ruby != "style":
        style.ruby.enabled, style.ruby.script = True, opts.ruby
    if opts.ruby_target and style.ruby.enabled:
        style.ruby.target = opts.ruby_target
    if opts.effects is not None:
        style.effects.kind = opts.effects
    if opts.countdown_intro is not None:
        style.countdown.intro = opts.countdown_intro
    if opts.countdown_interlude is not None:
        style.countdown.interlude = opts.countdown_interlude
    return style, label, colors


def apply_task_style(h: "S.ProjectHandle", task: PipelineTask) -> None:
    """Give the project the task's own subtitle style, once (later edits in the detailed mode stay)."""
    if task.karaoke is not None and not task.style_applied:
        S.set_karaoke_style(h, task.karaoke.model_dump(mode="json"))
        task.style_applied = True


def apply_karaoke_settings(h: "S.ProjectHandle", simple: "app_settings.SimpleSettings") -> None:
    """The project gets the simple mode's complete subtitle style."""
    style = simple.karaoke.model_copy(deep=True)
    style.output.vocal_keep_pct = simple.vocal_keep_pct
    S.set_karaoke_style(h, style.model_dump(mode="json"))


def stage_export(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    if task.karaoke is not None:  # the task's own style (and any edits made to the project since)
        apply_task_style(h, task)
        video = task.video or TaskVideo()
    else:  # a task from before styles were bound to tasks: the settings' style, once
        if not task.style_applied:
            apply_karaoke_settings(h, cfg.simple)
            task.style_applied = True
        video = TaskVideo(auto_export=cfg.simple.auto_export, video_audio=cfg.simple.video_audio,
                          vocal_keep_pct=cfg.simple.vocal_keep_pct, quality=cfg.simple.quality)
    if not video.auto_export:
        return "skipped"
    r = h.project.result()
    if r is None or S.staleness(h.project, r) is not None:  # computed now, not the saved flag
        # e.g. a retry re-ran the AI readings, or the lyrics were edited in the detailed mode:
        # the video must not be burned from an alignment of different lyrics
        progress(0.0, "对齐结果已过期，重新对齐")
        _warn(task, "歌词或读音在对齐后有变化，已重新对齐后再生成视频")
        _align(q, task, h, cancel, lambda f, m="": progress(0.0, m))
    out = run_heavy(lambda: S.karaoke_burn_versions(h, video.video_audio, background="auto", quality=video.quality,
                                                    vocal_keep_pct=video.vocal_keep_pct,
                                                    cancel=cancel, progress=progress),
                    lambda m: progress(0.0, m), cancel, holder=_holder(task, "生成视频"))
    for w in out.get("warnings") or []:
        if "停顿" in w or "人声分轨" in w:
            _warn(task, w)
    videos = [{**v, "url": export_url(h.project.id, v["filename"])} for v in out["videos"]]
    task.outputs["video"] = videos[0]  # (the one download button of tasks from before several versions)
    task.outputs["videos"] = videos
    return "done"


STAGE_FUNCS: dict[str, Callable[..., Any]] = {
    "import": stage_import, "lyrics": stage_lyrics, "readings": stage_readings, "separate": stage_separate,
    "calibrate": stage_calibrate, "align": stage_align, "export": stage_export,
}
