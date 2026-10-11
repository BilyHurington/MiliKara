"""Application service: every user operation, shared by the CLI and the WebUI.

Neither interface implements algorithms itself; they call these functions,
which operate on a :class:`ProjectHandle` (project + its directory).
"""

from __future__ import annotations

import copy
import os
import shutil
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np

from .interfaces import CancelToken, Emission
from .models import (
    AiRoundtrip, AlignConfig, AlignmentResult, AudioAsset, AudioSource, Issue, LineAnchor, LyricsDoc,
    BackgroundAsset, MixSettings, Project, SourceSnapshot, VideoAsset, new_id, stable_hash,
)
from .project import store
from .project.store import ProjectError
from .storage import drop_replaced


class ServiceError(ValueError):
    """User-facing error (bad input, missing prerequisite)."""


class LrcTimesError(ServiceError):
    """The LRC line times cannot be used for alignment (missing, or anchors outside the audio);
    aligning in plain mode is the way out."""


# ---------------------------------------------------------------------------
# workspace / handles
# ---------------------------------------------------------------------------


class _Previews(dict):
    """Parse previews / AI reports kept for a later apply: only the most recent few (they were
    never removed when not applied, so a long session kept every one of them)."""

    MAX = 32

    def __setitem__(self, key, value) -> None:
        super().pop(key, None)
        super().__setitem__(key, value)
        while len(self) > self.MAX:
            super().pop(next(iter(self)))


# AI round trips keep the model's raw reply for inspection: only the last few, and capped
MAX_RAW_REPLIES = 5
MAX_RAW_REPLY_CHARS = 200_000


@dataclass
class ProjectHandle:
    dir: Path
    project: Project
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    previews: dict[str, Any] = field(default_factory=_Previews, repr=False)
    deleted: bool = False

    def save(self) -> None:
        with self.lock:
            if self.deleted:  # a request still holding the handle must not bring the folder back
                raise ServiceError("这个项目已被删除")
            self._trim()
            try:
                store.save_project(self.project, self.dir)
            except ProjectError:
                # invalid data was refused: it must not stay in memory either, or every later save of
                # this project would be refused too — go back to what is on disk
                self._restore()
                raise

    def _trim(self) -> None:
        rts = self.project.ai_roundtrips
        for i, rt in enumerate(rts):
            if rt.response_raw is None:
                continue
            if i < len(rts) - MAX_RAW_REPLIES:
                rt.response_raw = None
            elif len(rt.response_raw) > MAX_RAW_REPLY_CHARS:
                rt.response_raw = rt.response_raw[:MAX_RAW_REPLY_CHARS]

    def _restore(self) -> None:
        if not (self.dir / store.PROJECT_FILE).exists():
            return  # never saved: nothing to go back to
        try:
            saved = store.load_project(self.dir)
        except ProjectError:
            return
        # in place: code holding ``h.project`` keeps a valid object; the id stays the workspace's
        for name in type(self.project).model_fields:
            if name != "id":
                setattr(self.project, name, getattr(saved, name))

    @property
    def assets_dir(self) -> Path:
        return self.dir / "assets"


class Workspace:
    """Projects living under one root directory (WebUI); CLI uses open_dir()."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else store.projects_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, ProjectHandle] = {}
        self._lock = threading.Lock()

    def _dir(self, pid: str) -> Path:
        """The folder of a project id: only ids that stay a plain folder of the workspace."""
        if not store.is_project_id(pid):
            raise ProjectError("非法项目 ID")
        d = self.root / pid
        if not store.inside(self.root, d):
            raise ProjectError("非法项目 ID")
        return d

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            if store.is_project_id(d.name) and (d / store.PROJECT_FILE).exists():
                try:
                    h = self.get(d.name)
                except ProjectError:  # an unreadable project is left out, never a failed listing
                    continue
                p = h.project
                out.append({"id": d.name, "name": p.name, "mode": p.mode, "updated": p.updated})
        return sorted(out, key=lambda x: x["updated"], reverse=True)

    def create(self, name: str, mode: str = "plain") -> ProjectHandle:
        p = Project(name=name or "untitled", mode=mode)  # type: ignore[arg-type]
        h = ProjectHandle(self._dir(p.id), p)
        h.save()
        with self._lock:
            self._handles[p.id] = h
        return h

    def get(self, pid: str) -> ProjectHandle:
        d = self._dir(pid)
        with self._lock:
            h = self._handles.get(pid)
            if h is None:
                h = ProjectHandle(d, store.load_project(d))
                # the directory name is the project's identity inside a workspace
                h.project.id = pid
                self._handles[pid] = h
            return h

    def delete(self, pid: str) -> None:
        """Remove a project with everything in it (audio, stems, exports)."""
        h = self.get(pid)  # validates the id
        trash = self.root / f".deleted-{pid}-{new_id()}"
        with h.lock:
            # the folder is first moved out of the way (one step), while the handle is marked deleted and
            # dropped from the cache: a concurrent get() finds the deleted handle or no project, and never
            # loads it again; a failed move leaves the project as it was
            with self._lock:
                try:
                    h.dir.rename(trash)
                except OSError as e:
                    raise ServiceError(f"无法删除项目：{e.strerror or e}") from e
                h.deleted = True
                self._handles.pop(pid, None)
        shutil.rmtree(trash, ignore_errors=True)  # anything left over stays hidden, outside the project list

    def import_file(self, path: Path, filename: str) -> ProjectHandle:
        """Import a project.json or a .kara.zip package as a new project.

        The project always gets a fresh id (= its folder): an id from the file is never used as a
        path.  A failed import leaves nothing behind."""
        pid = new_id("p")
        dest = self._dir(pid)
        try:
            if filename.endswith(".zip"):
                store.import_package(path, dest, project_id=pid)
            else:
                # read_project_file checks the size before reading anything
                store.save_project(store.read_project_file(Path(path), pid), dest)
            h = ProjectHandle(dest, store.load_project(dest))
            h.project.id = pid
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        with self._lock:
            self._handles[pid] = h
        return h


def open_dir(project_dir: Path) -> ProjectHandle:
    return ProjectHandle(Path(project_dir), store.load_project(project_dir))


def create_dir(project_dir: Path, name: str, mode: str = "plain") -> ProjectHandle:
    project_dir = Path(project_dir)
    if (project_dir / store.PROJECT_FILE).exists():
        raise ServiceError(f"{project_dir} 已经是项目目录")
    h = ProjectHandle(project_dir, Project(name=name, mode=mode))  # type: ignore[arg-type]
    h.save()
    return h


# ---------------------------------------------------------------------------
# view / staleness
# ---------------------------------------------------------------------------


def config_hash(config: AlignConfig) -> str:
    return stable_hash(config.model_dump(mode="json"))


def current_calibration_hash(p: Project) -> Optional[str]:
    from .align.calibration import calibration_hash

    return calibration_hash(p.lyrics, p.calibration) if p.mode == "lrc" else None


def staleness(p: Project, r: AlignmentResult) -> Optional[str]:
    """Reason why ``r`` no longer matches the current inputs (None = current)."""
    s = r.snapshot
    reasons = []
    if s.mode != p.mode:
        reasons.append("对齐模式已切换")
    if s.lyrics_text_revision != p.lyrics.text_revision():
        reasons.append("歌词文本已修改")
    elif s.lyrics_reading_revision != p.lyrics.reading_revision():
        reasons.append("读音或发音单元已修改")
    elif r.stats.get("detail_revision") and s.mode == p.mode \
            and r.stats["detail_revision"] != p.lyrics.detail_revision(p.mode):
        # recorded since round 5; older results are only compared by the revisions above
        reasons.append("声部、发音标记、片段语言、锚点容差或结束标记已修改")
    if p.mode == "lrc" and s.mode == "lrc" and s.calibration_hash != current_calibration_hash(p):
        reasons.append("校准或锚点已修改")
    asset = next((a for a in p.audio if a.id == s.audio_asset_id), None)
    orig = p.asset("original")
    if asset is None or asset.sha256 != s.audio_sha256:
        reasons.append("对齐所用音频已更换")
    elif asset.role != "original" and orig is not None and asset.source.parent_sha256 \
            and asset.source.parent_sha256 != orig.sha256:
        reasons.append("原曲已更换，对齐所用的分轨来自旧原曲")
    return "；".join(reasons) or None


def refresh_staleness(p: Project) -> None:
    for r in p.results:
        reason = staleness(p, r)
        r.stale = reason is not None
        r.stale_reason = reason


def backend_languages(p: Project) -> list[str]:
    from .align.backends import BACKENDS

    return list(BACKENDS.get(p.config.backend, {}).get("languages", ["ja"]))


def project_view(h: ProjectHandle) -> dict:
    from .align.calibration import check_issues, effective_line_starts, validate_anchors
    from .reading.prepare import capability_warnings

    p = h.project
    with h.lock:
        refresh_staleness(p)
        original = p.asset("original")
        duration = original.duration_ms if original else None
        eff = effective_line_starts(p.lyrics, p.calibration)
        cal_issues: list[Issue] = []
        if p.mode == "lrc":
            cal_issues = validate_anchors(p.lyrics, p.calibration, duration) + check_issues(p.calibration, doc=p.lyrics)
        mode_notice = None
        if p.mode == "plain" and any(ln.imported_start_ms is not None for ln in p.lyrics.lines):
            mode_notice = "普通模式：歌词中的 LRC 时间不会作为锚点使用"
        if p.mode == "lrc" and not eff:
            mode_notice = "LRC 增强模式需要带行时间的歌词：请补充时间或切换到普通模式"
        from .project.edits import manual_count

        results = [{
            "id": r.id, "created": r.created, "mode": r.mode, "stale": r.stale, "stale_reason": r.stale_reason,
            "coverage": r.coverage.model_dump(mode="json"), "parent_result_id": r.parent_result_id,
            "n_units": len(r.units), "n_failed": sum(1 for u in r.units if u.status != "ok"),
            "n_issues": len(r.issues), "n_manual": manual_count(r),
            "audio_role": r.snapshot.audio_role, "backend": r.backend.name,
        } for r in p.results]
        stems_ok = stems_current(p)
        # a stem separated from a replaced original is listed but not usable
        audio = {a.role: {"asset_id": a.id, "duration_ms": a.duration_ms, "sample_rate": a.sample_rate,
                          "available": a.path is not None and (a.role == "original" or stems_ok),
                          "outdated": a.role in ("vocals", "instrumental") and not stems_ok}
                 for a in p.audio}
        try:
            cap = capability_warnings(p.lyrics, backend_languages(p))
        except Exception:
            cap = []
        return {
            "project": p.model_dump(mode="json"),
            "view": {
                "effective_starts": {k: {"ms": v[0], "kind": v[1]} for k, v in eff.items()},
                "calibration_issues": [i.model_dump(mode="json") for i in cal_issues],
                "mode_notice": mode_notice,
                "results": results,
                "capability_warnings": cap,
                "audio": audio,
                "picture": picture(h),
                "cover": song_source(p) is not None,
                "singer_markers": _marker_count(p),
            },
        }


def _marker_count(p) -> int:
    """How many lines start with singer names (the 演唱者 page can assign and remove them)."""
    from .lyrics.singers import detect_markers

    try:
        return len(detect_markers(p.lyrics))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# project settings
# ---------------------------------------------------------------------------


def update_settings(h: ProjectHandle, *, name: Optional[str] = None, mode: Optional[str] = None,
                    config: Optional[dict] = None, mix: Optional[dict] = None) -> None:
    """Mode switches keep all inputs and manual edits; results get staleness markers.

    Everything is checked before anything changes: a refused value never reaches the project."""
    if name is not None and not isinstance(name, str):
        raise ServiceError("项目名称必须是文字")
    if mode is not None and mode not in ("plain", "lrc"):
        raise ServiceError("模式只能是 plain 或 lrc")
    _check_finite(config, "对齐设置")
    with h.lock:
        p = h.project
        new_config = None
        if config:
            try:
                new_config = AlignConfig.model_validate(_deep_merge(p.config.model_dump(mode="json"), config))
            except ValueError as e:
                raise ServiceError(f"对齐设置无效：{e}") from e
        new_mix = mix_settings(p.mix, mix) if mix else None
        if name is not None:
            p.name = name
        if mode is not None:
            p.mode = mode  # type: ignore[assignment]
        if new_config is not None:
            p.config = new_config
        if new_mix is not None:
            p.mix = new_mix
        h.save()


def _check_finite(obj: Any, what: str) -> None:
    """NaN / infinity (which JSON bodies may carry) are refused: they cannot be saved."""
    if isinstance(obj, float) and not np.isfinite(obj):
        raise ServiceError(f"{what}中有无效的数值（NaN / 无穷大）")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_finite(v, what)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _check_finite(v, what)


def mix_settings(base: MixSettings, patch: Optional[dict]) -> MixSettings:
    """``base`` with the changes in ``patch``, every value checked (numbers within range, finite)."""
    from .models import MIX_LIMITS

    if patch is not None and not isinstance(patch, dict):
        raise ServiceError("混音设置必须是对象")
    patch = dict(patch or {})
    for k, (lo, hi) in MIX_LIMITS.items():
        if k not in patch:
            continue
        v = patch[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or not lo <= v <= hi:
            unit = "%" if k.endswith("_pct") else ""
            raise ServiceError(f"混音设置 {k} 必须是 {lo:g}–{hi:g}{unit} 之间的数字")
    if "limiter" in patch and patch["limiter"] not in ("none", "normalize_peak"):
        raise ServiceError("防削波方式只能是 none 或 normalize_peak")
    return MixSettings.model_validate({**base.model_dump(), **patch})


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# lyrics input
# ---------------------------------------------------------------------------


def parse_lyrics(h: ProjectHandle, text: str, *, origin: str = "paste", filename: Optional[str] = None,
                 mode: Optional[str] = None) -> dict:
    """Parse text into a preview; nothing in the project changes until apply."""
    from .lyrics.parse import LyricsFormatError, LyricsModeError, detect_format, parse_lyrics_text

    mode = mode or h.project.mode
    preview_id = new_id("pv")
    detected = detect_format(text, filename)
    if detected == "json-prepared":
        return _parse_prepared(h, text, origin, filename, mode)
    if detected in ("json-project", "json-alignment", "json-reading-patch"):
        where = {"json-project": "请使用“导入项目”", "json-alignment": "请使用“导入结果”",
                 "json-reading-patch": "请在“粘贴 AI 结果”处使用"}[detected]
        return {"preview_id": None, "detected": detected, "warnings": [], "doc": None, "extra_tracks": {},
                "error": f"这是 {detected} 文件，不是歌词；{where}", "route": detected}
    try:
        res = parse_lyrics_text(text, mode=mode, origin=origin, filename=filename)  # type: ignore[arg-type]
    except LyricsModeError as e:
        detected = detect_format(text, filename)
        return {"preview_id": None, "detected": detected, "warnings": [], "error": str(e), "doc": None,
                "extra_tracks": {}}
    except LyricsFormatError as e:
        return {"preview_id": None, "detected": "unknown", "warnings": [], "error": str(e), "doc": None,
                "extra_tracks": {}}
    h.previews[preview_id] = res
    return {"preview_id": preview_id, "detected": res.detected, "warnings": res.warnings, "error": None,
            "doc": res.doc.model_dump(mode="json"), "extra_tracks": {}}


def _parse_prepared(h: ProjectHandle, text: str, origin: str, filename: Optional[str], mode: str) -> dict:
    """``prepared.json`` (lyrics + readings) → preview; times kept only in LRC mode."""
    import hashlib
    import json

    from .lyrics.parse import ParseResult

    data = json.loads(text)
    try:
        doc = LyricsDoc.model_validate({k: data[k] for k in ("language", "meta", "lines", "embedded_offset_raw",
                                                            "embedded_shift_ms", "embedded_offset_note")
                                        if k in data})
    except Exception as e:
        return {"preview_id": None, "detected": "json-prepared", "warnings": [], "doc": None, "extra_tracks": {},
                "error": f"prepared.json 校验失败: {e}"}
    warnings = []
    has_times = any(ln.imported_start_ms is not None for ln in doc.lines)
    if mode == "plain" and has_times:
        for ln in doc.lines:
            ln.imported_start_ms = ln.imported_end_ms = None
        warnings.append("普通模式：只导入正文与读音，已忽略其中的行时间")
    if mode == "lrc" and not has_times:
        return {"preview_id": None, "detected": "json-prepared", "warnings": [], "doc": None, "extra_tracks": {},
                "error": "LRC 增强模式需要带行时间的歌词：请补充时间或切换到普通模式"}
    snap = SourceSnapshot(origin=origin, kind="readings", filename=filename, text=text,  # type: ignore[arg-type]
                          sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    for ln in doc.lines:
        ln.source.source_id = snap.id
    res = ParseResult(doc=doc, warnings=warnings, detected="json-prepared", snapshot=snap)  # type: ignore[arg-type]
    preview_id = new_id("pv")
    h.previews[preview_id] = res
    return {"preview_id": preview_id, "detected": "json-prepared", "warnings": warnings, "error": None,
            "doc": doc.model_dump(mode="json"), "extra_tracks": {}}


def import_result_json(h: ProjectHandle, text: str) -> AlignmentResult:
    """Import an ``alignment.json`` (e.g. shared by someone) as a non-active result.

    Only accepted when its units refer to the current lyrics; staleness is
    recomputed so a result made from different inputs is clearly marked.
    """
    try:
        r = AlignmentResult.model_validate_json(text)
    except Exception as e:
        raise ServiceError(f"对齐结果 JSON 校验失败: {e}") from e
    known = {u.id for ln in h.project.lyrics.lines for u in ln.units()}
    unknown = [u.unit_id for u in r.units if u.unit_id not in known]
    if unknown:
        raise ServiceError(f"结果中有 {len(unknown)} 个单元不属于当前歌词（读音分组或歌词不同），无法导入")
    with h.lock:
        if any(x.id == r.id for x in h.project.results):
            r.id = new_id("r")
        r.stats["imported"] = True
        h.project.results.append(r)
        refresh_staleness(h.project)
        h.save()
    return r


def apply_lyrics(h: ProjectHandle, preview_id: str, *, prepare: bool = True) -> list[str]:
    res = h.previews.pop(preview_id, None)
    if res is None:
        raise ServiceError("预览已失效，请重新解析")
    with h.lock:
        p = h.project
        doc: LyricsDoc = res.doc
        if res.snapshot is not None:
            p.sources.append(res.snapshot)
        extra = getattr(res, "extra_tracks", None) or {}
        old_doc = p.lyrics
        p.lyrics = doc
        messages: list[str] = []
        if prepare:
            messages += prepare_readings_locked(p)
        messages += _calibration_for_new_lyrics(p, old_doc)
        h.save()
        return messages + [f"可配对的附加歌词轨: {', '.join(extra)}"] if extra else messages


def _timed_signature(doc: LyricsDoc) -> list[tuple]:
    return [(ln.text, ln.imported_start_ms) for ln in doc.lines if ln.imported_start_ms is not None]


def _calibration_for_new_lyrics(p: Project, old: LyricsDoc) -> list[str]:
    """The calibration was made for the old lyrics' times.  Line ids are positional (L0001 …),
    so a line only keeps meaning the same thing when its text *and* imported time are unchanged.

    * identical timed lines (and [offset]): nothing changes;
    * otherwise the calibration is no longer confirmed; the reference mark and check marks stay
      only on lines that are still the same; the shift is kept when the reference line is still
      the same or most timed lines are unchanged (e.g. one typo fixed), else reset to 0
      (a different LRC); the old state stays in the history (undo).
    """
    from .align import calibration as C

    cal = p.calibration
    new = p.lyrics
    if _timed_signature(old) == _timed_signature(new) and old.embedded_shift_ms == new.embedded_shift_ms:
        C.recheck(cal, new)
        return []
    if cal.user_shift_ms == 0 and not cal.confirmed and not cal.checks and cal.reference_line_id is None:
        return []

    def same(line_id: Optional[str]) -> bool:
        if line_id is None:
            return False
        try:
            a, b = old.line(line_id), new.line(line_id)
        except KeyError:
            return False
        return (a.text, a.imported_start_ms) == (b.text, b.imported_start_ms)

    old_sig = _timed_signature(old)
    new_sig = _timed_signature(new)
    kept = sum(1 for x in new_sig if x in set(old_sig))
    similar = bool(new_sig) and kept / len(new_sig) >= 0.8 and old.embedded_shift_ms == new.embedded_shift_ms
    newcal = C._with_history(cal, "lyrics_changed")
    newcal.confirmed = False
    msgs = []
    if not same(cal.reference_line_id):
        newcal.reference_line_id = None
        newcal.marked_ms = None
    if cal.user_shift_ms and not (same(cal.reference_line_id) or similar):
        newcal.user_shift_ms = 0
        msgs.append(f"歌词已更换：原来的整体偏移（{cal.user_shift_ms:+d} ms）属于旧歌词，已重置为 0；请重新校准首音"
                    "（可撤销）")
    elif cal.confirmed:
        msgs.append("歌词已更换：请重新确认首音校准")
    newcal.checks = [c for c in cal.checks if same(c.line_id)]
    p.calibration = C.recheck(newcal, new)
    return msgs


def prepare_readings_locked(p: Project, overwrite_rule: bool = True) -> list[str]:
    from .reading.prepare import prepare_doc

    rep = prepare_doc(p.lyrics, overwrite_rule=overwrite_rule)
    return list(rep.messages)


def prepare_readings(h: ProjectHandle, overwrite_rule: bool = True) -> dict:
    from .reading.prepare import prepare_doc

    with h.lock:
        rep = prepare_doc(h.project.lyrics, overwrite_rule=overwrite_rule)
        h.save()
        return asdict(rep)


def fetch_translation(h: ProjectHandle) -> int:
    """Fetch the translation track from the platform the lyrics came from and pair it."""
    from .lyrics.fetch import fetch_song as _fetch

    src = next((x for x in reversed(h.project.sources) if x.origin in ("netease", "qq") and x.platform_song_id), None)
    if src is None:
        raise ServiceError("歌词不是从网易云 / QQ 音乐获取的；请在“音频与歌词”页粘贴翻译进行配对")
    song = _fetch(src.origin, src.platform_song_id)
    text = song.tracks.get("translation")
    if not text or not text.strip():
        raise ServiceError("平台没有提供这首歌的翻译")
    prev = preview_track(h, text, "translation")
    pairs = [{"line_id": x["line_id"], "text": x["text"]} for x in prev["pairs"] if (x.get("text") or "").strip()]
    if not pairs:
        raise ServiceError("翻译和歌词对不上")
    apply_track(h, "translation", pairs)
    return len(pairs)


def preview_track(h: ProjectHandle, text: str, kind: str) -> dict:
    from .lyrics.pairing import pair_track

    prev = pair_track(h.project.lyrics, text, kind=kind)  # type: ignore[arg-type]
    return {
        "kind": kind,
        "pairs": [asdict(x) for x in prev.pairs],
        "unmatched_line_ids": prev.unmatched_line_ids,
        "unmatched": [t.text for t in prev.unmatched_texts],
    }


def apply_track(h: ProjectHandle, kind: str, pairs: list[dict]) -> None:
    from .lyrics.pairing import apply_pairs

    with h.lock:
        h.project.lyrics = apply_pairs(h.project.lyrics, [(x["line_id"], x["text"]) for x in pairs], kind=kind)  # type: ignore[arg-type]
        h.save()


def fetch_link(text: str) -> dict:
    from .lyrics.fetch import fetch_lyrics_from_link
    from .lyrics.fetch.types import CollectionListing

    out = fetch_lyrics_from_link(text)
    if isinstance(out, CollectionListing):
        return {"kind": "collection", "platform": out.platform, "title": out.title,
                "songs": [asdict(s) for s in out.songs]}
    return {"kind": "song", "song": _song_dict(out)}


def fetch_song(platform: str, song_id: str) -> dict:
    from .lyrics.fetch import fetch_song as _fetch

    return {"kind": "song", "song": _song_dict(_fetch(platform, song_id))}


def _song_dict(song) -> dict:
    d = asdict(song)
    return d


def parse_from_song(h: ProjectHandle, platform: str, song_id: str, *, mode: Optional[str] = None,
                    song=None) -> dict:
    """Fetch lyrics of one song and parse its original track into a preview.

    ``mode`` parses for another mode than the project's (nothing in the project changes);
    ``song`` reuses a song already fetched."""
    from .lyrics.fetch import fetch_song as _fetch
    from .lyrics.parse import LyricsModeError, parse_lyrics_text

    song = song or _fetch(platform, song_id)
    original = song.tracks.get("original")
    if not original:
        return {"preview_id": None, "detected": "unknown", "warnings": [], "doc": None, "extra_tracks": {},
                "error": "该歌曲没有可用的原文歌词，请手动输入"}
    snap = song.to_snapshot("original")
    try:
        res = parse_lyrics_text(original, mode=mode or h.project.mode, origin=platform, source_id=snap.id)
    except LyricsModeError as e:
        return {"preview_id": None, "detected": "plain", "warnings": [], "doc": None, "error": str(e),
                "extra_tracks": {k: v for k, v in song.tracks.items() if k != "original"}}
    res.snapshot = snap
    doc = res.doc
    doc.meta.title = doc.meta.title or song.title
    doc.meta.artist = doc.meta.artist or ", ".join(song.artists) or None
    doc.meta.album = doc.meta.album or song.album
    doc.meta.duration_ms = doc.meta.duration_ms or song.duration_ms
    extra = {k: v for k, v in song.tracks.items() if k != "original" and v}
    res.extra_tracks = extra  # type: ignore[attr-defined]
    preview_id = new_id("pv")
    h.previews[preview_id] = res
    warnings = list(res.warnings) + list(song.notes)
    if not song.has_timestamps.get("original"):
        warnings.append("平台只提供了无时间歌词；不会生成伪 LRC")
    return {"preview_id": preview_id, "detected": res.detected, "warnings": warnings, "error": None,
            "doc": doc.model_dump(mode="json"), "extra_tracks": extra,
            "song": {k: v for k, v in _song_dict(song).items() if k != "tracks"}}


# ---------------------------------------------------------------------------
# line editing
# ---------------------------------------------------------------------------


def update_line(h: ProjectHandle, line_id: str, **fields: Any) -> None:
    from .lyrics.parse import normalize_text
    from .reading.prepare import prepare_line

    with h.lock:
        ln = _line(h, line_id)
        if fields.get("text") is not None:
            # same canonical kana as imported text (composed, full width); one line only
            fields["text"] = normalize_text(str(fields["text"])).replace("\n", " ").strip()
        text_changed = "text" in fields and fields["text"] is not None and fields["text"] != ln.text
        old_text = ln.text
        for k in ("text", "sing", "kind", "translation", "voice"):
            if k in fields and fields[k] is not None:
                setattr(ln, k, fields[k])
        if text_changed:
            from .lyrics.singers import remap

            remap(ln, old_text)  # who sings which characters follows the edit
        if "countdown" in fields and fields["countdown"] is not None:
            # karaoke display only: "auto" = the style's rules, "on" / "off" = this line always / never
            v = fields["countdown"]
            if v not in ("auto", "on", "off"):
                raise ServiceError("countdown 只能是 auto / on / off")
            ln.countdown = None if v == "auto" else v == "on"
        if text_changed or (ln.kind == "lyric" and ln.sing and not ln.units()):
            # a line switched back to a sung lyric (or edited) is prepared now, in the project:
            # an alignment must never refer to units the project does not have
            prepare_line(ln, h.project.lyrics.language)
        h.save()


def _line(h: ProjectHandle, line_id: str):
    try:
        return h.project.lyrics.line(line_id)
    except KeyError:
        raise ServiceError(f"没有歌词行 {line_id}") from None


def merge_lines(h: ProjectHandle, line_ids: list[str]) -> None:
    from .lyrics.pairing import merge_lines as _merge
    from .reading.prepare import prepare_doc

    with h.lock:
        h.project.lyrics = _merge(h.project.lyrics, line_ids)
        prepare_doc(h.project.lyrics, overwrite_rule=False)
        h.save()


def split_line(h: ProjectHandle, line_id: str, at: int) -> None:
    from .lyrics.pairing import split_line as _split
    from .reading.prepare import prepare_doc

    with h.lock:
        h.project.lyrics = _split(h.project.lyrics, line_id, at)
        prepare_doc(h.project.lyrics, overwrite_rule=False)
        h.save()


def set_line_anchor(h: ProjectHandle, line_id: str, abs_ms: Optional[int], hard: bool = True,
                    tolerance_ms: int = 80) -> None:
    with h.lock:
        ln = _line(h, line_id)
        if abs_ms is None:
            ln.anchor = None
        else:
            if abs_ms < 0:
                raise ServiceError("锚点不能为负")
            orig = h.project.asset("original")
            if orig and abs_ms > orig.duration_ms:
                raise ServiceError("锚点超出音频长度")
            ln.anchor = LineAnchor(abs_ms=int(abs_ms), hard=hard, tolerance_ms=int(tolerance_ms))
        h.save()


def set_segment_reading(h: ProjectHandle, line_id: str, segment_id: str, reading: str,
                        units: Optional[list[str]] = None, confirm: bool = True) -> None:
    from .reading.prepare import set_segment_reading as _set

    with h.lock:
        ln = _line(h, line_id)
        try:
            _set(ln, segment_id, reading, units, source="manual", confirm=confirm)
        except (KeyError, ValueError) as e:
            raise ServiceError(str(e)) from e
        h.save()


def set_segment_hidden(h: ProjectHandle, line_id: str, segment_id: str, hidden: bool) -> None:
    """Show (or hide) a segment without a reading: brackets the AI took for a reading (宿敵(ライバル))."""
    with h.lock:
        ln = _line(h, line_id)
        seg = next((s for s in ln.segments if s.id == segment_id), None)
        if seg is None:
            raise ServiceError(f"没有这个片段：{segment_id}")
        if hidden and seg.units:
            raise ServiceError("有读音的片段是要唱的，不能隐藏")
        seg.hidden = hidden
        h.save()


# ---------------------------------------------------------------------------
# AI round trip
# ---------------------------------------------------------------------------


def ai_prompt(h: ProjectHandle, line_ids: Optional[list[str]] = None) -> dict:
    from .reading.ai import build_prompt

    with h.lock:
        bundle = build_prompt(h.project.lyrics, line_ids, lang=h.project.lyrics.language)
        h.project.ai_roundtrips.append(bundle.roundtrip)
        h.save()
        return {"prompt": bundle.prompt, "snapshot_id": bundle.snapshot_id, "roundtrip_id": bundle.roundtrip.id}


def ai_validate(h: ProjectHandle, text: str) -> dict:
    from .reading.ai import PatchParseError, extract_json, validate_patch

    try:
        obj = extract_json(text)
    except PatchParseError as e:
        raise ServiceError(f"无法解析 AI 结果: {e}") from e
    with h.lock:
        report = validate_patch(h.project.lyrics, obj, h.project.ai_roundtrips)
        rt = _roundtrip_for(h.project, obj)
        if rt is not None:
            rt.response_raw = text[:500_000]
            rt.status = "validated"
            # the prompt's per-line reading hashes stay: a later validation of the same snapshot
            # must still see readings changed since the prompt
            rt.report = {**{k: v for k, v in rt.report.items() if k in ("line_hashes", "line_texts")},
                         "validation": report.to_dict()}
        report_id = new_id("rep")
        h.previews[report_id] = (report, rt.id if rt else None)
        h.save()
        return {"report_id": report_id, "report": report.to_dict()}


def ai_auto(h: ProjectHandle, line_ids: Optional[list[str]] = None, *, cfg=None,
            cancel: Optional[CancelToken] = None,
            progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """AI readings without copy/paste: the same prompt is sent as one message to
    the configured CLI / API and the reply goes through the same validation.

    Nothing is applied here; the caller previews and applies the report like a
    pasted reply.  One retry is made when the reply is unusable, quoting the
    problems found.
    """
    from . import settings as app_settings
    from .reading.ai import PatchParseError, extract_json
    from .reading.llm import LlmError, ask

    cfg = cfg or app_settings.load().ai
    prog = progress or (lambda f, m="": None)
    out = ai_prompt(h, line_ids)
    prompt = out["prompt"]
    label = {"claude": "Claude Code", "codex": "Codex", "openai": "API"}.get(cfg.provider, cfg.provider)
    attempts: list[dict] = []
    result: Optional[dict] = None
    for attempt in range(2):
        prog(0.05 + 0.45 * attempt, f"等待 {label} 回复…")

        def waiting(s: float, a=attempt) -> None:
            prog(min(0.45 + 0.45 * a, 0.05 + 0.45 * a + s / 400), f"等待 {label} 回复 · {int(s)} 秒")

        try:
            reply = ask(cfg, prompt, cancel=cancel, on_wait=waiting)
        except LlmError as e:
            raise ServiceError(str(e)) from e
        attempts.append({"provider": reply.provider, "model": reply.model, "elapsed_s": reply.elapsed_s,
                         "cost_usd": reply.cost_usd})
        problems: list[str] = []
        try:
            extract_json(reply.text)
            result = ai_validate(h, reply.text)
            rep = result["report"]
            problems = list(rep.get("errors") or [])
            bad = [lr for lr in rep.get("lines", []) if lr.get("status") in ("invalid", "unknown_line", "duplicate")]
            problems += [f"{lr['line_id']}: {'; '.join(lr.get('reasons') or [])}" for lr in bad]
            if rep.get("missing_line_ids"):
                problems.append("缺少这些行：" + ", ".join(rep["missing_line_ids"]))
        except (PatchParseError, ServiceError) as e:
            problems = [f"回复不是可解析的 JSON：{e}"]
        if not problems or attempt == 1:
            break
        prompt = (out["prompt"] + "\n\n上一次的回复有以下问题，请修正后重新输出完整的 JSON（所有行，不要省略）：\n"
                  + "\n".join(f"- {p}" for p in problems[:30]))
    if result is None:
        raise ServiceError("AI 两次回复都无法解析为注音 JSON，请改用网页聊天或检查模型")
    prog(1.0, "完成")
    result["meta"] = {"provider": cfg.provider, "attempts": attempts,
                      "cost_usd": round(sum(a["cost_usd"] or 0 for a in attempts), 4)
                      if any(a["cost_usd"] is not None for a in attempts) else None}
    return result


def _roundtrip_for(p: Project, obj: Any) -> Optional[AiRoundtrip]:
    snap = obj.get("snapshot") if isinstance(obj, dict) else None
    for rt in reversed(p.ai_roundtrips):
        if snap and rt.snapshot_id == snap:
            return rt
    return None


def ai_apply(h: ProjectHandle, report_id: str, line_ids: Optional[list[str]] = None) -> dict:
    from .reading.ai import apply_patch

    item = h.previews.get(report_id)
    if item is None:
        raise ServiceError("校验报告已失效，请重新粘贴 AI 结果")
    report, rt_id = item
    with h.lock:
        new_doc, summary = apply_patch(h.project.lyrics, report, include_line_ids=line_ids)
        h.project.lyrics = new_doc
        for rt in h.project.ai_roundtrips:
            if rt.id == rt_id:
                rt.status = "applied"
                from .models import utcnow

                rt.applied_at = utcnow()
        h.save()
        return summary if isinstance(summary, dict) else {"summary": summary}


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


def add_audio(h: ProjectHandle, src_path: Path, role: str, filename: Optional[str] = None,
              source_kind: str = "upload") -> AudioAsset:
    """Add original audio or an existing stem (stems get a sync check)."""
    from .audio.io import import_asset

    if role not in ("original", "vocals", "instrumental"):
        raise ServiceError("音轨角色只能是 original / vocals / instrumental")
    src = AudioSource(kind=source_kind, filename=filename or Path(src_path).name)  # type: ignore[arg-type]
    asset = import_asset(src_path, role, h.assets_dir, src, project_dir=h.dir)  # type: ignore[arg-type]
    # the sync check decodes whole files: done before taking the project lock, which views and edits
    # of this project wait for
    checked = None
    if role != "original":
        with h.lock:
            orig0 = h.project.asset("original")
        if orig0 is not None:
            checked = (orig0.sha256, _sync_report(h, orig0, asset))
    with h.lock:
        p = h.project
        if role != "original":
            orig = p.asset("original")
            if orig is None:
                asset.source.notes.append("导入时没有原曲，无法检查同步")
            else:
                asset.sync_report = checked[1] if checked and checked[0] == orig.sha256 \
                    else _sync_report(h, orig, asset)  # the original changed meanwhile: check again
                asset.sync_checked = True
                asset.source.parent_sha256 = orig.sha256
        # a new original invalidates stems derived from another original
        replaced = [a.path for a in p.audio if a.role == role]
        p.audio = [a for a in p.audio if a.role != role]
        if role == "original":
            for a in p.audio:
                if a.source.parent_sha256 and a.source.parent_sha256 != asset.sha256:
                    a.source.notes.append("原曲已更换：此音轨对应旧原曲")
        p.audio.append(asset)
        h.save()
        drop_replaced(h, replaced)
    return asset


def add_media(h: ProjectHandle, src_path: Path, role: str, filename: Optional[str] = None,
              source_kind: str = "upload") -> AudioAsset:
    """Add audio, or a video whose first audio track is extracted and used.

    A video uploaded as the original is kept (content-addressed) so a
    reduced-vocal version can be muxed later; any other upload clears it.
    """
    import hashlib
    import tempfile

    from .audio.io import file_sha256
    from .audio.video import audio_offset_s, extract_audio, is_video, probe_media

    src_path = Path(src_path)
    name = filename or src_path.name
    if not is_video(src_path):
        asset = add_audio(h, src_path, role, filename=name, source_kind=source_kind)
        if role == "original" and h.project.video is not None:
            with h.lock:
                old = h.project.video.path
                h.project.video = None
                h.save()
                drop_replaced(h, [old])
        return asset

    info = probe_media(src_path)
    with tempfile.TemporaryDirectory() as td:
        flac = extract_audio(src_path, Path(td) / (Path(name).stem or "audio"))
        asset = add_audio(h, flac, role, filename=f"{Path(name).stem}（视频音轨）.flac", source_kind=source_kind)
    asset.source.notes.append(f"从视频 {name} 提取的音轨（{info.get('audio_codec')}）")
    video = None
    if role == "original":
        sha = file_sha256(src_path)
        ext = src_path.suffix.lower() or ".mp4"
        dest = h.assets_dir / f"{sha}{ext}"
        if not dest.exists():
            h.assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_path, dest)
        video = VideoAsset(
            sha256=sha, path=f"assets/{dest.name}", filename=name, container=ext,
            duration_ms=info["duration_ms"], width=info.get("width"), height=info.get("height"),
            fps=info.get("fps"), video_codec=info.get("video_codec"), audio_codec=info.get("audio_codec"),
            audio_offset_s=audio_offset_s(info), audio_sha256=asset.sha256, upright=True,
        )
    with h.lock:
        old = h.project.video.path if role == "original" and h.project.video is not None else None
        if role == "original":
            h.project.video = video
        h.save()
        drop_replaced(h, [old])
    return asset


def export_video(h: ProjectHandle, settings: dict, cancel: Optional[CancelToken] = None) -> dict:
    """Mux the reduced-vocal mix under the original video's picture."""
    import tempfile

    from .audio.video import mux_audio

    video = h.project.video
    orig = h.project.asset("original")
    if video is None:
        raise ServiceError("项目中没有视频：请在“音频与歌词”中上传视频作为原曲")
    if orig is None or orig.sha256 != video.audio_sha256:
        raise ServiceError("当前原曲不是从该视频提取的，无法合成视频")
    vpath = store.asset_abspath(h.dir, video.path)
    if vpath is None or not vpath.exists():
        raise ServiceError("视频文件缺失，请重新上传视频")
    with tempfile.TemporaryDirectory() as td:
        mix = export_mix(h, settings, Path(td) / "mix.wav", cancel=cancel)
        s = h.project.mix
        stem = Path(video.filename or "video").stem
        # the extension is added by mux_audio (a name with dots, "My.Song", stays whole)
        named = export_path(h, f"{stem}-vocal{int(round(s.vocal_keep_pct))}", video.container or ".mp4")
        base = named.with_name(named.name[: -len(video.container or ".mp4")])
        out = mux_audio(vpath, Path(mix["path"]), base, offset_s=video.audio_offset_s, container=video.container,
                        cancel=cancel)
    return {"filename": out.name, "report": {**mix["report"], "video": {"container": out.suffix,
            "audio_offset_s": video.audio_offset_s, "video_codec": video.video_codec, "copied_video": True}}}


# ---------------------------------------------------------------------------
# karaoke subtitles
# ---------------------------------------------------------------------------


def _export_stem(h: ProjectHandle) -> str:
    """The song's name as the start of an export's file name (no path separators)."""
    import re

    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (h.project.name or "").strip()).strip(". ")
    return name[:80] or "song"


def set_karaoke_style(h: ProjectHandle, style: dict) -> None:
    from .models import KaraokeStyle

    try:
        # strict: a colour / number out of range is refused here (stored styles are only clamped on load)
        k = KaraokeStyle.model_validate(style, context={"strict": True})
    except Exception as e:
        raise ServiceError(f"字幕样式无效：{e}") from e
    with h.lock:
        h.project.karaoke = k
        h.save()


# ---------------------------------------------------------------------------
# singers (多人演唱分色)
# ---------------------------------------------------------------------------


def set_line_singers(h: ProjectHandle, items: list[dict]) -> None:
    """Who sings each of these lines: ``{line_id, singers, spans: [{start, end, singers}], text?}``
    (``text``: the line's text the spans were made for; refused when the line has changed since)."""
    from .lyrics.singers import clean_ids, normalize
    from .models import SingerSpan

    with h.lock:
        todo = []
        for it in items:
            if not isinstance(it, dict):
                raise ServiceError("每一项必须是对象")
            ln = _line(h, str(it.get("line_id")))
            if it.get("text") is not None and it["text"] != ln.text:
                raise ServiceError(f"歌词「{ln.text}」已修改，请刷新后重试")
            spans = []
            for sp in it.get("spans") or []:
                try:
                    a, b = int(sp["start"]), int(sp["end"])
                except (KeyError, TypeError, ValueError):
                    raise ServiceError("分段必须有 start / end") from None
                if not 0 <= a < b <= len(ln.text):
                    raise ServiceError(f"分段 {a}–{b} 超出了歌词「{ln.text}」")
                spans.append(SingerSpan(start=a, end=b, singers=clean_ids(sp.get("singers"))))
            todo.append((ln, clean_ids(it.get("singers")), spans))
        for ln, ids, spans in todo:
            ln.singers, ln.singer_spans = ids, spans
            normalize(ln)
        h.save()


def set_singers(h: ProjectHandle, data: dict) -> None:
    """The style's singers (list, colours, how parts sung together look); the rest of the style stays."""
    from .models import KaraokeSingers

    try:
        sg = KaraokeSingers.model_validate(data, context={"strict": True})
    except Exception as e:
        raise ServiceError(f"演唱者设置无效：{e}") from e
    with h.lock:
        h.project.karaoke.singers = sg
        h.save()


def apply_singer_preset(h: ProjectHandle, data: dict) -> dict:
    """Use a saved set of singers (演唱者预设) in this song.  Parts already assigned stay with the same
    person: a singer is matched by name, an unnamed one by number; one that is used in the lyrics
    but not in the preset is kept (added after the preset's)."""
    from .karaoke.themes import new_singer_color
    from .lyrics.singers import renumber, usage
    from .models import KaraokeSingers

    try:
        new = KaraokeSingers.model_validate(data, context={"strict": True})
    except Exception as e:
        raise ServiceError(f"演唱者预设无效：{e}") from e
    with h.lock:
        old = h.project.karaoke.singers.members
        used = usage(h.project.lyrics)
        mapping: dict[int, int] = {}
        taken: set[int] = set()
        names = {m.name.strip().casefold(): j for j, m in enumerate(new.members, 1) if m.name.strip()}
        for i, m in enumerate(old, 1):
            j = names.get(m.name.strip().casefold()) if m.name.strip() else None
            if j is not None and j not in taken:
                mapping[i] = j
                taken.add(j)
        for i, m in enumerate(old, 1):
            if i not in mapping and not m.name.strip() and i <= len(new.members) and i not in taken \
                    and not new.members[i - 1].name.strip():
                mapping[i] = i
                taken.add(i)
        kept = []
        for i, m in enumerate(old, 1):
            if i not in mapping and i in used:
                color = m.color if all(x.color.upper() != m.color.upper() for x in new.members) \
                    else new_singer_color({x.color for x in new.members}, len(new.members))
                new.members.append(m.model_copy(update={"color": color, "key": new.free_key()}))
                mapping[i] = len(new.members)
                kept.append(m.name.strip() or f"演唱者 {i}")
        changed = renumber(h.project.lyrics, mapping)
        h.project.karaoke.singers = new
        h.save()
    return {"lines": changed, "kept": kept}


def remove_singer(h: ProjectHandle, number: int) -> int:
    """Remove singer ``number`` (1-based): its parts go back to the other singers of the line (or the
    style's own colours) and the singers after it move up.  Returns how many lines changed."""
    from .lyrics.singers import shift_numbers

    with h.lock:
        members = h.project.karaoke.singers.members
        if not 1 <= number <= len(members):
            raise ServiceError(f"没有第 {number} 位演唱者")
        del members[number - 1]
        n = shift_numbers(h.project.lyrics, number)
        # combinations: without the singer, later ones renumbered; one left with fewer than two goes
        sg = h.project.karaoke.singers
        combos = []
        for c in sg.combos:
            ids = [i - 1 if i > number else i for i in c.singers if i != number]
            if len(ids) >= 2:
                combos.append(c.model_copy(update={"singers": ids}))
        sg.combos = combos
        h.save()
        return n


def singer_markers(h: ProjectHandle) -> dict:
    """Lines whose text starts with singer names ("A：…", "（XX）…", "【成员】…")."""
    from .lyrics.singers import detect_markers, is_all, marker_names

    with h.lock:
        found = detect_markers(h.project.lyrics)
        texts = {ln.id: ln.text for ln in h.project.lyrics.lines}
        existing = [m.name for m in h.project.karaoke.singers.members]
    return {
        "lines": [{"line_id": m.line_id, "text": texts.get(m.line_id, ""), "prefix": m.prefix, "names": m.names,
                   "everyone": all(is_all(n) for n in m.names)} for m in found],
        "names": marker_names(found),
        "existing": existing,
    }


def apply_singer_markers(h: ProjectHandle, names: Optional[list[str]] = None, strip: bool = True) -> list[str]:
    """Assign the lines that start with singer names to those singers (added to the style when new;
    the words for "everyone" mean every singer named in the lyrics); ``names``: only these (default:
    all found).  ``strip``: take the names out of the lyrics (only where every name was used)."""
    from .karaoke.themes import new_singer_color
    from .lyrics.singers import detect_markers, is_all, marker_names
    from .models import KaraokeSinger

    with h.lock:
        doc = h.project.lyrics
        found = detect_markers(doc)
        if not found:
            raise ServiceError("歌词里没有找到演唱者标记")
        wanted = marker_names(found) if names is None else [n for n in marker_names(found) if n in names]
        members = h.project.karaoke.singers.members
        number: dict[str, int] = {}
        messages: list[str] = []
        for name in wanted:
            hit = next((i for i, m in enumerate(members) if m.name.strip().casefold() == name.casefold()), None)
            if hit is None:
                color = new_singer_color({m.color for m in members}, len(members))
                members.append(KaraokeSinger(name=name, color=color, key=h.project.karaoke.singers.free_key()))
                hit = len(members) - 1
            number[name] = hit + 1
        everyone = sorted(set(number.values()))
        assigned = stripped = 0
        for mk in found:
            ln = doc.line(mk.line_id)
            ids: list[int] = []
            for n in mk.names:
                for i in (everyone if is_all(n) else [number[n]] if n in number else []):
                    if i not in ids:
                        ids.append(i)
            if not ids:
                continue
            ln.singers, ln.singer_spans = ids, []
            assigned += 1
            if strip and all(is_all(n) or n in number for n in mk.names) and ln.text.startswith(mk.prefix):
                _strip_prefix(ln, len(mk.prefix), doc.language)
                stripped += 1
        h.save()
    messages.insert(0, f"已按标记给 {assigned} 行指定演唱者：{'、'.join(number)}")
    if stripped:
        messages.append(f"已去掉 {stripped} 行开头的演唱者标记")
        if h.project.results:
            messages.append("歌词文本有改动，现有对齐结果已标为过期（其余部分的时间仍保留），建议重新对齐这些行")
    return messages


def _strip_prefix(ln, n: int, lang: str) -> None:
    """Take the first ``n`` characters out of a line.  Where they are whole segments, the other
    segments (and their units' times) are kept; otherwise the line is prepared again."""
    from .reading.prepare import prepare_line

    old = ln.text
    pos, cut = 0, 0
    for seg in ln.segments:
        if pos >= n:
            break
        pos += len(seg.surface)
        cut += 1
    ln.text = old[n:]
    if pos == n:
        ln.segments = ln.segments[cut:]
    else:
        ln.segments = []
        prepare_line(ln, lang)
    ln.singer_spans = [sp.model_copy(update={"start": max(0, sp.start - n), "end": sp.end - n})
                       for sp in ln.singer_spans if sp.end > n]


def song_info(h: ProjectHandle) -> dict:
    """The title card's data: every field the song data fills, and the project's own text (None = automatic)."""
    from .karaoke.info import LABELS, song_fields

    return {"fields": song_fields(h.project), "labels": LABELS, "text": h.project.song_info_text}


def set_song_info_text(h: ProjectHandle, text: Optional[str]) -> None:
    """The title card's own text (one line each, the first is the title); None goes back to the song data."""
    if text is not None and not isinstance(text, str):
        raise ServiceError("歌曲信息必须是文字")
    with h.lock:
        h.project.song_info_text = text[:2000] if text is not None else None
        h.save()


def _karaoke_inputs(h: ProjectHandle, style: Optional[dict]):
    from .models import KaraokeStyle

    r = h.project.result()
    if r is None:
        raise ServiceError("还没有对齐结果：请先完成对齐")
    try:
        k = KaraokeStyle.model_validate(style) if style is not None else h.project.karaoke
    except Exception as e:
        raise ServiceError(f"字幕样式无效：{e}") from e
    return r, k


def ensure_upright_video(h: ProjectHandle) -> None:
    """Videos imported before rotation was read keep a sideways size (a phone video stored as
    1920×1080 plays as 1080×1920): probe once more and store the displayed size."""
    v = h.project.video
    if v is None or v.upright:
        return
    p = store.asset_abspath(h.dir, v.path) if v.path else None
    if p is None or not p.exists():
        return
    try:
        from .audio.video import probe_media

        info = probe_media(p)
    except Exception:
        return
    with h.lock:
        v.width, v.height = info.get("width") or v.width, info.get("height") or v.height
        v.upright = True
        h.save()


def set_background(h: ProjectHandle, src_path: Path, filename: Optional[str] = None) -> BackgroundAsset:
    """Use a picture or a video (looped) behind the subtitles (kara_align.karaoke.background).
    Checked before anything changes; stored content-addressed like the other assets."""
    from .audio.io import file_sha256
    from .karaoke.background import BackgroundError, probe_background, validate_background

    src_path = Path(src_path)
    name = filename or src_path.name
    with open(src_path, "rb") as f:
        head = f.read(64)
    try:
        kind = validate_background(name, head, src_path.stat().st_size)
        info = probe_background(src_path, kind)
    except BackgroundError as e:
        raise ServiceError(str(e)) from e
    sha = file_sha256(src_path)
    ext = Path(name).suffix.lower()
    dest = h.assets_dir / f"{sha}{ext}"
    if not dest.exists():
        h.assets_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.part")
        shutil.copyfile(src_path, tmp)
        tmp.replace(dest)
    bg = BackgroundAsset(sha256=sha, path=f"assets/{dest.name}", filename=name, kind=kind, **info)
    with h.lock:
        old = h.project.background.path if h.project.background is not None else None
        h.project.background = bg
        h.save()
        drop_replaced(h, [old])
    return bg


def song_source(p: Project) -> Optional[tuple[str, str, Optional[str]]]:
    """(platform, song id, cover url) of the music link the lyrics came from (the latest one)."""
    for snap in reversed(p.sources):
        if snap.origin in ("netease", "qq") and snap.platform_song_id:
            return snap.origin, snap.platform_song_id, (snap.fetched_meta or {}).get("cover_url")
    return None


def cover_background(h: ProjectHandle) -> BackgroundAsset:
    """The song's cover, blurred behind the cover itself, as the picture behind the subtitles
    (karaoke/cover.py).  The cover comes from the music platform the lyrics were fetched from."""
    import tempfile

    from .karaoke.cover import CoverError, blurred_cover, fetch_cover

    with h.lock:
        src = song_source(h.project)
    if src is None:
        raise ServiceError("歌词不是从网易云音乐 / QQ 音乐链接获取的，没有封面")
    platform, song_id, url = src
    try:
        if not url:  # lyrics fetched before covers were kept: ask the platform again
            from .lyrics.fetch import fetch_song

            url = fetch_song(platform, song_id).cover_url
        if not url:
            raise ServiceError("音乐平台没有这首歌的封面")
        jpg = blurred_cover(fetch_cover(url))
    except CoverError as e:
        raise ServiceError(str(e)) from e
    except ServiceError:
        raise
    except Exception as e:
        raise ServiceError(f"无法获取封面：{e}") from e
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "cover.jpg"
        path.write_bytes(jpg)
        return set_background(h, path, filename="歌曲封面（模糊背景）.jpg")


def clear_background(h: ProjectHandle) -> None:
    """Back to the video (or black); the file is deleted."""
    with h.lock:
        old = h.project.background.path if h.project.background is not None else None
        h.project.background = None
        h.save()
        drop_replaced(h, [old])


def _background_file(h: ProjectHandle) -> Optional[tuple[BackgroundAsset, Path]]:
    b = h.project.background
    if b is None:
        return None
    p = store.asset_abspath(h.dir, b.path)
    return (b, p) if p and p.exists() else None


def picture(h: ProjectHandle) -> dict:
    """What a burned video shows by default: {source: background|video|black, width, height, kind?, filename?}."""
    from .karaoke.ass import resolution
    from .karaoke.render import frame_size

    bg = _background_file(h)
    if bg is not None:
        w, hh = resolution(h.project)
        return {"source": "background", "kind": bg[0].kind, "filename": bg[0].filename, "width": w, "height": hh}
    video = _video_file(h)
    if video is not None:
        w, hh = frame_size(video, resolution(h.project))
        return {"source": "video", "width": w, "height": hh, "filename": h.project.video.filename}
    w, hh = resolution(h.project)
    return {"source": "black", "width": w, "height": hh}


def _video_file(h: ProjectHandle) -> Optional[Path]:
    v = h.project.video
    orig = h.project.asset("original")
    if v is None or orig is None or orig.sha256 != v.audio_sha256:
        return None
    p = store.asset_abspath(h.dir, v.path)
    return p if p and p.exists() else None


def karaoke_ass(h: ProjectHandle, style: Optional[dict] = None, *, for_video: bool = True) -> tuple[str, list[str]]:
    """ASS text; on the video's timeline when the project has a video."""
    ensure_upright_video(h)
    from .karaoke.ass import build_ass

    from .karaoke.ass import resolution
    from .karaoke.render import frame_size

    r, k = _karaoke_inputs(h, style)
    # with a background the video's picture is not used: its frame, the audio's timeline
    video = _video_file(h) if for_video and _background_file(h) is None else None
    offset = h.project.video.audio_offset_s * 1000 if video else 0.0
    # laid out for the frame as the video shows it (non-square pixels applied)
    size = frame_size(video, resolution(h.project)) if video else None
    text, warnings = build_ass(h.project, r, k, time_offset_ms=offset, size=size)
    if r.stale:
        warnings.append(f"对齐结果已过期：{r.stale_reason}")
    if offset:
        warnings.append(f"时间已按视频中音轨的起点偏移 {offset:.0f} ms，可直接配合原视频使用")
    return text, warnings


def karaoke_preview(h: ProjectHandle, t_ms: int, style: Optional[dict] = None, background: str = "auto") -> bytes:
    try:
        t_ms = int(t_ms)
    except (TypeError, ValueError, OverflowError):
        raise ServiceError("t_ms 必须是毫秒数") from None
    t_ms = max(0, t_ms)
    if style is not None and not isinstance(style, dict):
        raise ServiceError("字幕样式必须是对象")
    if background not in ("auto", "black"):
        raise ServiceError("background 只能是 auto 或 black")
    ensure_upright_video(h)
    from .karaoke.ass import build_ass, resolution
    from .karaoke.render import preview_png

    from .karaoke.render import frame_size

    r, k = _karaoke_inputs(h, style)
    bg = _background_file(h) if background != "black" else None
    video = _video_file(h) if background != "black" and bg is None else None
    # the size the video is shown at (non-square pixels applied), like the burn: same layout, no squash
    size = frame_size(video, resolution(h.project)) if video else resolution(h.project)
    text, _ = build_ass(h.project, r, k, size=size)  # audio timeline; the frame is taken at t (+offset)
    off = h.project.video.audio_offset_s if video else 0.0
    return preview_png(text, int(t_ms), size, video=video, audio_offset_s=off,
                       background=(bg[1], bg[0].kind, bg[0].duration_ms) if bg else None)


def karaoke_burn(h: ProjectHandle, *, background: str = "auto", audio: str = "original", quality: str = "standard",
                 vocal_keep_pct: Optional[float] = None,
                 cancel: Optional[CancelToken] = None, progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Burn the karaoke subtitles into a video (the background, the source video, or black).

    ``audio="mix"`` keeps the vocals at ``vocal_keep_pct`` (default: the karaoke
    style's own setting) over the full instrumental; the Export page's mix
    settings are neither used nor changed.
    """
    ensure_upright_video(h)
    import tempfile

    from .karaoke.ass import build_ass, resolution
    from .karaoke.render import burn, even_size, frame_size

    r, k = _karaoke_inputs(h, None)
    orig = h.project.asset("original")
    if orig is None:
        raise ServiceError("请先上传原曲")
    # a background (picture / looped video) comes first, then the song's own video, then black
    bg = _background_file(h) if background != "black" else None
    video = _video_file(h) if background != "black" and bg is None else None
    offset_s = h.project.video.audio_offset_s if video else 0.0
    # the frame the subtitles are drawn on: the video as shown (square pixels), both sides even (yuv420p);
    # the ASS is laid out for exactly that frame
    size = even_size(frame_size(video, resolution(h.project)) if video else resolution(h.project))
    text, warnings = build_ass(h.project, r, k, time_offset_ms=offset_s * 1000, size=size)
    stem = Path(h.project.video.filename).stem if video and h.project.video.filename else _export_stem(h)
    pct = float(k.output.vocal_keep_pct if vocal_keep_pct is None else vocal_keep_pct)
    if not 0.0 <= pct <= 100.0:
        raise ServiceError("人声保留比例必须在 0–100% 之间")
    suffix = {"original": "", "mix": f"-vocal{int(round(pct))}", "none": "-noaudio"}[audio]
    # each burn its own file (export_path): an earlier video, a simple-mode task's too, is never overwritten
    out = export_path(h, f"{stem}-karaoke{suffix}", ".mp4")
    with tempfile.TemporaryDirectory() as td:
        audio_file: Optional[Path] = None
        use_video_audio = False
        if audio == "mix":
            require_stems(h, "降低人声")
            mix = MixSettings(vocal_keep_pct=pct, instrumental_pct=100.0).model_dump()
            audio_file = Path(export_mix(h, mix, Path(td) / "mix.wav", save=False)["path"])
        elif audio == "original":
            if video is not None:
                use_video_audio = True
            else:
                audio_file = asset_path(h, orig)
        burn(text, out, size, orig.duration_ms, video=video, audio=audio_file, audio_offset_s=offset_s,
             use_video_audio=use_video_audio, quality=quality, cancel=cancel, progress=progress,
             background=(bg[1], bg[0].kind) if bg else None)
    return {"filename": out.name, "warnings": warnings}


def karaoke_burn_versions(h: ProjectHandle, versions: Sequence[str], *, background: str = "auto",
                          quality: str = "standard", vocal_keep_pct: Optional[float] = None,
                          cancel: Optional[CancelToken] = None,
                          progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Several videos with the same subtitles and different sound (原唱, 伴唱, 人声 20 %, 无声; see
    :mod:`audio_versions`), one after another: ``{"videos": [{filename, label, version}], "warnings"}``.
    Versions that need separated stems are left out, with a warning, when there are none."""
    from . import audio_versions as AV

    _, k = _karaoke_inputs(h, None)
    pct = float(k.output.vocal_keep_pct if vocal_keep_pct is None else vocal_keep_pct)
    try:
        chosen = AV.normalize(list(versions))
    except ValueError as e:
        raise ServiceError(str(e)) from e
    todo, warnings = AV.plan(chosen, pct, stems_current(h.project))
    videos: list[dict] = []
    for i, v in enumerate(todo):
        tag = f"（{v['label']}，{i + 1}/{len(todo)}）" if len(todo) > 1 else ""

        def sub(frac: float, msg: str = "", i: int = i, tag: str = tag) -> None:
            if progress is not None:
                progress((i + frac) / len(todo), f"{msg}{tag}")

        out = karaoke_burn(h, background=background, audio=v["audio"], quality=quality,
                           vocal_keep_pct=v["vocal_keep_pct"], cancel=cancel, progress=sub)
        videos.append({"filename": out["filename"], "label": v["label"], "version": v["version"]})
        warnings += [w for w in out["warnings"] if w not in warnings]
    return {"videos": videos, "warnings": warnings}


def _sync_report(h: ProjectHandle, orig: AudioAsset, stem: AudioAsset) -> dict:
    from .audio.io import load_audio
    from .audio.sync import check_stem_sync

    sr = 16000
    o, _ = load_audio(asset_path(h, orig), target_sr=sr, mono=True)
    s, _ = load_audio(asset_path(h, stem), target_sr=sr, mono=True)
    other = None
    counterpart = h.project.asset("instrumental" if stem.role == "vocals" else "vocals")
    if counterpart is not None and counterpart.path:
        other, _ = load_audio(asset_path(h, counterpart), target_sr=sr, mono=True)
        other = other[0]
    rep = check_stem_sync(o[0], s[0], sr, stem.role, other_stem=other)
    return _jsonable(rep)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def asset_path(h: ProjectHandle, asset: AudioAsset) -> Path:
    p = store.asset_abspath(h.dir, asset.path)
    if p is None or not p.exists():
        raise ServiceError(f"音频 {asset.role} 缺失，请重新上传（sha256 {asset.sha256[:12]}…）")
    return p


def get_asset(h: ProjectHandle, asset_id: str) -> AudioAsset:
    for a in h.project.audio:
        if a.id == asset_id:
            return a
    raise ServiceError(f"没有音频 {asset_id}")


def _cache_file(kind: str, asset: AudioAsset, suffix: str) -> Path:
    """A cache file named after the asset's content hash — only a real sha256 (an imported project
    could carry anything there) and only inside the cache folder."""
    if not store.is_sha256(asset.sha256):
        raise ServiceError("音频的内容校验值无效，请重新上传")
    root = store.cache_dir(kind)
    out = root / f"{asset.sha256}{suffix}"
    if not store.inside(root, out):
        raise ServiceError("缓存路径无效")
    return out


def playback_wav(h: ProjectHandle, asset: AudioAsset) -> Path:
    """Decoded PCM WAV made by the same decoder the aligner uses (same origin)."""
    from .audio.io import load_audio, write_wav

    out = _cache_file("playback", asset, ".wav")
    if not out.exists():
        data, sr = load_audio(asset_path(h, asset))
        tmp = out.with_suffix(f".{new_id()}.tmp.wav")
        try:
            write_wav(tmp, data, sr)
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
    return out


def peaks(h: ProjectHandle, asset: AudioAsset, per_second: int = 200) -> dict:
    from .audio.analysis import waveform_peaks
    from .audio.io import load_audio

    per_second = max(10, min(int(per_second), 2000))
    cache = _cache_file("peaks", asset, f"-{per_second}.npz")
    got = None
    if cache.exists():
        try:
            with np.load(cache) as z:
                got = z["mins"], z["maxs"], int(z["sr"])
        except Exception:  # a damaged cache file (e.g. cut off): made again below
            got = None
    if got is None:
        data, sr = load_audio(asset_path(h, asset), mono=True)
        spp = max(1, int(round(sr / per_second)))
        mins, maxs = waveform_peaks(data[0], sr, spp)
        # written aside and moved into place: a reader never sees half a file
        tmp = cache.with_name(f"{cache.stem}.{new_id()}.tmp.npz")
        try:
            np.savez(tmp, mins=mins, maxs=maxs, sr=sr)
            os.replace(tmp, cache)
        except OSError:
            pass  # the cache is only a speed-up
        finally:
            tmp.unlink(missing_ok=True)
    else:
        mins, maxs, sr = got
    return {"sample_rate": sr, "duration_ms": asset.duration_ms, "per_second": per_second,
            "mins": np.round(mins, 4).tolist(), "maxs": np.round(maxs, 4).tolist()}


# a separation that takes longer than this has hung (e.g. the MPS backend): generous for slow CPUs
SEPARATION_TIMEOUT_FACTOR = 5.0
SEPARATION_TIMEOUT_EXTRA_S = 600.0


def run_separation(h: ProjectHandle, preset: str, cancel: Optional[CancelToken] = None,
                   progress: Optional[Callable[[float, str], None]] = None, device: str = "auto") -> dict:
    """Separate the original; failure raises (never a silent fallback)."""
    import hashlib

    from .audio.io import import_asset
    from .audio.separation import is_known_preset, separate

    if not is_known_preset(preset):
        raise ServiceError(f"未知的分离预设：{preset}")
    if device not in ("auto", "cpu"):
        raise ServiceError("分离设备只能是 auto 或 cpu")
    orig = h.project.asset("original")
    if orig is None:
        raise ServiceError("请先上传原曲")
    if not store.is_sha256(orig.sha256):
        raise ServiceError("原曲的内容校验值无效，请重新上传原曲")
    src = asset_path(h, orig)
    root = store.cache_dir("separation")
    # the folder name is a hash (never built from user input); it is removed below, so it must be in the cache
    out_dir = root / hashlib.sha256(f"{orig.sha256}:{preset}".encode("utf-8")).hexdigest()[:32]
    if not store.inside(root, out_dir):
        raise ServiceError("分离缓存路径无效")
    if out_dir.exists():
        shutil.rmtree(out_dir)  # never trust a possibly partial earlier run
    out_dir.mkdir(parents=True)
    timeout_s = orig.duration_ms / 1000.0 * SEPARATION_TIMEOUT_FACTOR + SEPARATION_TIMEOUT_EXTRA_S
    try:
        result = separate(src, out_dir, preset, cancel=cancel, progress=progress, device=device,
                          timeout_s=timeout_s)
        if cancel is not None:
            cancel.check()
        report = _jsonable(result.report)
        assets = []
        for role, path in (("vocals", result.vocals_path), ("instrumental", result.instrumental_path)):
            source = AudioSource(kind="separation", filename=Path(path).name, model=report.get("model_filename"),
                                 model_version=report.get("audio_separator_version"),
                                 config={"preset": preset, "device": device}, parent_sha256=orig.sha256)
            assets.append(import_asset(path, role, h.assets_dir, source, project_dir=h.dir))  # type: ignore[arg-type]
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    with h.lock:
        replaced = [a.path for a in h.project.audio if a.role in ("vocals", "instrumental")]
        h.project.audio = [a for a in h.project.audio if a.role not in ("vocals", "instrumental")]
        sync = report.get("sync") if isinstance(report.get("sync"), dict) else {}
        for a in assets:
            # the separator reports sync per stem: keep each stem's own report
            a.sync_report = sync.get(a.role) if isinstance(sync.get(a.role), dict) else None
            a.sync_checked = a.sync_report is not None
            h.project.audio.append(a)
        h.save()
        drop_replaced(h, replaced)
    return {"report": report, "assets": [a.id for a in assets]}


def stems_current(p: Project) -> bool:
    """Vocals and instrumental exist and belong to the current original (separated from it, or
    imported without a known source).  Stems of a replaced original must not be used."""
    orig, v, i = p.asset("original"), p.asset("vocals"), p.asset("instrumental")
    if v is None or i is None:
        return False
    return all(x.source.parent_sha256 in (None, orig.sha256 if orig else None) for x in (v, i))


def require_stems(h: ProjectHandle, what: str) -> tuple[AudioAsset, AudioAsset]:
    v, i = h.project.asset("vocals"), h.project.asset("instrumental")
    if v is None or i is None:
        raise ServiceError(f"{what}需要人声和伴奏两条分轨；请先进行人声分离")
    if not stems_current(h.project):
        raise ServiceError(f"{what}需要重新进行人声分离：现有分轨来自更换前的原曲")
    return v, i


def mix_bus_gain(h: ProjectHandle, settings: dict) -> dict:
    from .audio.io import load_audio
    from .audio.mix import mix_stems

    v, i = require_stems(h, "人声保留比例")
    s = mix_settings(h.project.mix, settings)
    vd, sr = load_audio(asset_path(h, v))
    idata, sr2 = load_audio(asset_path(h, i), target_sr=sr)
    _, rep = mix_stems(vd, idata, sr, s.vocal_keep_pct, s.instrumental_pct, s.master,
                       limiter="normalize_peak" if s.limiter == "normalize_peak" else "none")
    return {"bus_gain": rep.bus_gain, "peak_before": rep.peak_before}


def export_path(h: ProjectHandle, base: str, ext: str) -> Path:
    """A new file in the project's exports folder, never an earlier export's name: ``base`` (song and
    kind, e.g. "わたぐも-karaoke-vocal40") + the local time it was made + ``ext``; ``-2``, ``-3`` …
    when another one was made in the same second.  Sorted by name, they are sorted by time."""
    import datetime as dt

    folder = h.dir / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{base}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path, n = folder / f"{stem}{ext}", 1
    while path.exists() or path.with_name(f".{path.stem}.part{ext}").exists():
        n += 1
        path = folder / f"{stem}-{n}{ext}"
    return path


def export_mix(h: ProjectHandle, settings: dict, out_path: Optional[Path] = None, *, save: bool = True,
               cancel: Optional[CancelToken] = None) -> dict:
    from .audio.mix import export_mix_wav

    v, i = require_stems(h, "导出混音")
    s = mix_settings(h.project.mix, settings)
    orig = h.project.asset("original")
    out = Path(out_path) if out_path else export_path(
        h, f"{_export_stem(h)}-mix-v{int(round(s.vocal_keep_pct))}-i{int(round(s.instrumental_pct))}", ".wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    original_n = None
    if orig is not None and orig.sample_rate == v.sample_rate:
        original_n = orig.num_samples
    # written to a part file and moved into place (a download never gets half a file)
    rep = export_mix_wav(asset_path(h, v), asset_path(h, i), out, s.vocal_keep_pct, s.instrumental_pct,
                         s.master, limiter="normalize_peak" if s.limiter == "normalize_peak" else "none",
                         original_num_samples=original_n, cancel=cancel)
    if save:  # the Export page remembers its mix; other callers (burn-in) do not touch it
        with h.lock:
            h.project.mix = s
            h.save()
    rep_d = _jsonable(asdict(rep)) if hasattr(rep, "__dataclass_fields__") else _jsonable(rep)
    return {"filename": out.name, "path": str(out), "report": rep_d}


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


def calibration_op(h: ProjectHandle, op: str, **kw: Any) -> None:
    from .align import calibration as C

    with h.lock:
        p = h.project
        if op == "mark":
            _line(h, kw["line_id"])
            p.calibration = C.mark_first_onset(p.calibration, p.lyrics, kw["line_id"], int(kw["marked_ms"]))
        elif op == "shift":
            p.calibration = C.set_user_shift(p.calibration, int(kw["user_shift_ms"]), p.lyrics)
        elif op == "confirm-zero":
            p.calibration = C.confirm_zero(p.calibration, p.lyrics)
        elif op == "check":
            _line(h, kw["line_id"])
            # issues are recomputed for every view, only the calibration is stored
            p.calibration, _issues = C.add_check(p.calibration, p.lyrics, kw["line_id"], int(kw["marked_ms"]))
        elif op == "undo":
            p.calibration = C.undo(p.calibration, p.lyrics)
        else:
            raise ServiceError(f"未知校准操作 {op}")
        h.save()


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------


def _load_for_backend(h: ProjectHandle, asset: AudioAsset, sr: int) -> np.ndarray:
    from .audio.io import load_audio

    data, _ = load_audio(asset_path(h, asset), target_sr=sr, mono=True)
    return data[0]


def run_align(h: ProjectHandle, *, line_ids: Optional[list[str]] = None, audio_role: Optional[str] = None,
              config: Optional[dict] = None, cancel: Optional[CancelToken] = None,
              progress: Optional[Callable[[float, str], None]] = None, backend=None) -> AlignmentResult:
    """Run an alignment on a snapshot of the current inputs.

    The result is appended only when the run completes.  A full run becomes
    the active result; a local rerun (``line_ids``) is stored as a separate
    partial result with ``parent_result_id`` and never overwrites anything.
    """
    from .align.backends import get_backend
    from .align.emission_cache import EmissionCache, emission_cache_key
    from .align.runner import AlignInputs, run_alignment
    from .audio.analysis import rms_envelope_db
    from .reading.profiles import get_profile

    user_progress = progress or (lambda f, m="": None)
    last_progress = {"value": 0.0}

    def progress(frac: float, msg: str = "") -> None:
        # never move the bar backwards (retries may run extra passes)
        last_progress["value"] = max(last_progress["value"], frac)
        user_progress(last_progress["value"], msg)
    with h.lock:
        if any(not ln.units() for ln in h.project.lyrics.sung_lines()):
            # readings are prepared in the project itself (not only in the snapshot): the result
            # must refer to units the project has, or it would be outdated at once
            from .reading.prepare import prepare_doc

            prepare_doc(h.project.lyrics, overwrite_rule=False)
            h.save()
        snap: Project = copy.deepcopy(h.project)
    cfg = snap.config
    if config:
        cfg = AlignConfig.model_validate(_deep_merge(cfg.model_dump(mode="json"), config))
    if audio_role:
        cfg = cfg.model_copy(update={"audio_role": audio_role})
    elif line_ids and snap.result() is not None and snap.result().snapshot.audio_role in ("original", "vocals") \
            and (snap.result().snapshot.audio_role == "original" or stems_current(snap)):
        # a local rerun is compared with / adopted into the active result: same audio as that result
        cfg = cfg.model_copy(update={"audio_role": snap.result().snapshot.audio_role})
    if not snap.lyrics.sung_lines():
        raise ServiceError("没有参与对齐的歌词行")
    skip_line_ids: list[str] = []
    if snap.mode == "lrc":
        from .align.calibration import effective_line_starts, validate_anchors

        if not effective_line_starts(snap.lyrics, snap.calibration):
            raise LrcTimesError("LRC 增强模式需要有效的行时间；请补充时间或切换到普通模式")
        orig = snap.asset("original")
        found = validate_anchors(snap.lyrics, snap.calibration, orig.duration_ms if orig else None)
        # lines after the end of the audio (a shortened video, e.g. a TV-size cut with full lyrics):
        # left out of the alignment with a note, instead of giving up the LRC times of the whole song
        skip_line_ids = [i.line_id for i in found if i.code == "anchor_out_of_range" and i.line_id]
        errors = [i for i in found if i.severity == "error" and i.code != "anchor_out_of_range"]
        if errors:
            raise LrcTimesError("锚点需要修正: " + "; ".join(i.message for i in errors[:5]))
        if skip_line_ids and len(skip_line_ids) >= len(snap.lyrics.sung_lines()):
            raise LrcTimesError("所有歌词行的时间都在音频结束之后：歌词和音频可能不是同一首歌或同一版本")
    original = snap.asset("original")
    if original is None:
        raise ServiceError("请先上传原曲")
    # stems of a replaced original are never used (not as input, not for singing / rest detection)
    roles = ["original"] + (["vocals"] if stems_current(snap) else [])
    if cfg.audio_role not in roles:
        if snap.asset("vocals") is not None:
            raise ServiceError("选择了人声作为对齐输入，但现有人声分轨来自更换前的原曲；请重新进行人声分离")
        raise ServiceError("选择了人声作为对齐输入，但项目中没有人声分轨（请先分离或导入）")

    progress(0.02, "加载模型")
    backend = backend or get_backend(cfg)
    info = backend.info()
    profile = get_profile(info.profile)
    cache = EmissionCache(store.cache_dir("emissions"))
    sr = backend.sample_rate
    emissions: dict[str, Emission] = {}
    assets = {r: snap.asset(r) for r in roles}

    def emission_for(role: str) -> Emission:
        if role in emissions:
            return emissions[role]
        asset = assets[role]
        key = emission_cache_key(asset.sha256, role, asset.origin_offset_samples, info, cfg.chunk_s,
                                 cfg.context_s, f"resample_poly->{sr}")
        em = cache.get(key)
        if em is None:
            progress(0.05, f"声学推理（{role}）")
            if cancel is not None:
                cancel.check()
            audio = _load_for_backend(h, asset, sr)
            origin = int(round(asset.origin_offset_samples * sr / asset.sample_rate))

            first_pass = not emissions

            def sub_progress(frac: float, msg: str = "") -> None:
                if first_pass:
                    progress(0.05 + 0.6 * frac, msg or f"声学推理（{role}）")
                else:
                    # a retry needs another track: keep the bar where it is, update the text
                    progress(last_progress["value"], f"重试：声学推理（{role}）{int(frac * 100)}%")

            em = backend.emissions(audio, origin_samples=origin, cancel=cancel, progress=sub_progress)
            if cancel is not None:
                cancel.check()
            cache.put(key, em, info, complete=True)
            em.cache_key = key
        emissions[role] = em
        return em

    envelopes: dict[str, tuple[np.ndarray, float]] = {}

    def energy_for(role: str) -> tuple[np.ndarray, float]:
        if role not in envelopes:
            from .audio.io import load_audio

            asset = assets[role]
            data, asr = load_audio(asset_path(h, asset), mono=True)
            hop = 10.0
            env = rms_envelope_db(data[0], asr, hop_ms=hop)
            # index i must mean i*hop ms on the *original* timeline: a track whose sample 0 lies
            # later (origin offset) is padded with silence in front, an earlier one cut
            shift = int(round(asset.origin_offset_samples * 1000.0 / asset.sample_rate / hop))
            if shift > 0:
                env = np.concatenate([np.full(shift, float(env.min()) if env.size else -120.0, dtype=env.dtype), env])
            elif shift < 0:
                env = env[-shift:]
            envelopes[role] = (env, hop)
        return envelopes[role]

    # acoustic scores for the chosen input first, so progress stays monotonic
    emission_for(cfg.audio_role)
    previous = snap.result()
    inp = AlignInputs(
        # info again after the first inference: it knows the device used (and any GPU → CPU fallback)
        lyrics=snap.lyrics, mode=snap.mode, calibration=snap.calibration, config=cfg, backend_info=backend.info(),
        tokenize=backend.tokenize, profile=profile, emission_for=emission_for, available_roles=roles,
        audio_assets={r: a for r, a in assets.items() if a is not None}, audio_duration_ms=original.duration_ms,
        energy_for=energy_for, previous=previous, line_ids=line_ids,
        skip_line_ids=[x for x in skip_line_ids if line_ids is None or x in line_ids],
        original_sha256=original.sha256,
    )

    def run_progress(frac: float, msg: str = "") -> None:
        progress(0.65 + 0.35 * frac, msg or "解码")

    result = run_alignment(inp, cancel=cancel, progress=run_progress)
    if cancel is not None:
        cancel.check()
    if line_ids:
        result.parent_result_id = previous.id if previous else None
    with h.lock:
        h.project.results.append(result)
        if not line_ids:
            h.project.active_result_id = result.id
        refresh_staleness(h.project)
        h.save()
    return result


def get_result(h: ProjectHandle, result_id: str) -> AlignmentResult:
    r = h.project.result(result_id)
    if r is None:
        raise ServiceError(f"没有对齐结果 {result_id}")
    refresh_staleness(h.project)
    return r


def activate_result(h: ProjectHandle, result_id: str) -> None:
    with h.lock:
        get_result(h, result_id)
        h.project.active_result_id = result_id
        h.save()


def adopt_lines(h: ProjectHandle, result_id: str, line_ids: list[str], *, from_result_id: Optional[str] = None,
                candidate_id: Optional[str] = None) -> AlignmentResult:
    """Copy non-locked unit times of ``line_ids`` from a rerun / candidate into ``result_id``."""
    with h.lock:
        target = get_result(h, result_id)
        if candidate_id:
            cand = next((c for r in h.project.results for c in r.candidates if c.id == candidate_id), None)
            if cand is None:
                raise ServiceError("没有该候选")
            source_units = cand.units
            line_ids = line_ids or [cand.line_id]
            label = f"candidate:{cand.label}"
        elif from_result_id:
            src = get_result(h, from_result_id)
            source_units = src.units
            label = f"result:{from_result_id}"
        else:
            raise ServiceError("需要 from_result_id 或 candidate_id")
        by_id = {u.unit_id: u for u in source_units if u.line_id in set(line_ids)}
        target_ids = {u.unit_id for u in target.units if u.line_id in set(line_ids)}
        if set(by_id) != target_ids:
            raise ServiceError("单元不一致（读音分组已变化），无法直接采用；请完整重跑")
        adopted = 0
        for i, u in enumerate(target.units):
            if u.unit_id in by_id and not u.locked:
                newu = by_id[u.unit_id].model_copy(deep=True)
                newu.manual, newu.manual_history = u.manual, u.manual_history
                newu.flags = [f for f in newu.flags if f != "adopted"] + ["adopted"]
                target.units[i] = newu
                adopted += 1
        src_lines = {}
        src_issues: list[Issue] = []
        if from_result_id:
            src = get_result(h, from_result_id)
            src_lines = {lt.line_id: lt for lt in src.lines}
            src_issues = [i for i in src.issues if i.line_id in set(line_ids)]
        for i, lt in enumerate(target.lines):
            if lt.line_id in line_ids:
                if lt.line_id in src_lines:
                    target.lines[i] = src_lines[lt.line_id].model_copy(deep=True)
                units = [u for u in target.units if u.line_id == lt.line_id]
                st = [u.start_ms for u in units if u.start_ms is not None]
                en = [u.end_ms for u in units if u.end_ms is not None]
                target.lines[i].start_ms = min(st) if st else None
                target.lines[i].end_ms = max(en) if en else None
                target.lines[i].candidate = label
                if target.lines[i].anchor_ms is not None and target.lines[i].start_ms is not None:
                    target.lines[i].anchor_residual_ms = target.lines[i].start_ms - target.lines[i].anchor_ms
        _recheck_adopted(h.project, target, list(line_ids), src_issues)
        target.stats["adoptions"] = target.stats.get("adoptions", 0) + adopted
        h.save()
        return target


def _recheck_adopted(p: Project, target: AlignmentResult, line_ids: list[str], src_issues: list[Issue]) -> None:
    """Issues of adopted lines describe the adopted times: the checks are run again for them
    (and for the line after each, whose overlap / order with them may have changed); evidence
    the checks cannot recompute here (stability, singing in a rest, …) comes from the source."""
    from .align import checks as chk

    ids = set(line_ids)
    order = [lt.line_id for lt in target.lines]
    after = {order[k + 1] for k, lid in enumerate(order[:-1]) if lid in ids}
    keep = []
    for i in target.issues:
        if i.line_id in ids and (i.code in chk.CHECK_CODES or i.code in ("unstable_boundary", "decode_failed",
                                                                         "boundary_conflict", "tail_unresolved")):
            continue
        if i.line_id in after and i.code in ("order_conflict", "line_overlap"):
            continue
        keep.append(i)
    keep += [i for i in src_issues if i.code in ("unstable_boundary", "unit_in_rest", "boundary_conflict",
                                                 "tail_unresolved")]
    cfg = target.config.checks
    units = [u for u in target.units if u.line_id in ids]
    for u in units:
        u.flags = [f for f in u.flags if f not in ("short_unit", "long_unit", "illegal_interval", "line_gap",
                                                  "unit_overlap")]
    line_flags = ("incomplete", "anchor_deviation", "window_edge", "order_conflict", "line_overlap")
    lines_by_id = {lt.line_id: lt for lt in target.lines}
    for lid in ids:
        if lid in lines_by_id:
            lines_by_id[lid].flags = [f for f in lines_by_id[lid].flags if f not in line_flags]
    voices = {ln.id: ln.voice for ln in p.lyrics.lines}
    cov_issues, _ = chk.check_coverage(units, [lines_by_id[x] for x in line_ids if x in lines_by_id], cfg)
    new = (chk.check_units(units, cfg) + chk.check_line_gaps(units, cfg) + cov_issues
           + chk.check_unit_order(units, voices))
    # line checks look at neighbours too: run them on copies, take over what concerns these lines
    copies = [lt.model_copy(deep=True) for lt in target.lines]
    for c in copies:
        c.flags = [f for f in c.flags if f not in line_flags]
    orig = p.asset("original")
    line_issues = chk.check_lines(copies, cfg, voices, orig.duration_ms if orig else None)
    by_copy = {c.line_id: c for c in copies}
    for lid in ids | after:
        lt, c = lines_by_id.get(lid), by_copy.get(lid)
        if lt is None or c is None:
            continue
        take = line_flags[1:] if lid in ids else ("order_conflict", "line_overlap")
        lt.flags = [f for f in lt.flags if f not in take] + [f for f in c.flags if f in take and f not in lt.flags]
    new += [i for i in line_issues if i.line_id in ids or (i.line_id in after and i.code in ("order_conflict",
                                                                                             "line_overlap"))]
    target.issues = keep + [i for i in new if i.code != "low_coverage"]


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def export(h: ProjectHandle, fmt: str, result_id: Optional[str] = None):
    from .project.exports import export as _export

    refresh_staleness(h.project)
    if fmt == "karaoke-ass":
        from .project.exports import ExportOutput

        text, warnings = karaoke_ass(h)
        return ExportOutput("karaoke.ass", "text/plain", text, warnings)
    result = get_result(h, result_id) if result_id else h.project.result()
    advance = h.project.karaoke.timing.advance_ms
    if fmt in ("lrc-line", "lrc-unit") and advance and result is not None:
        # the same "show lyrics early" setting as the karaoke subtitles; data exports keep real times
        result = result.model_copy(deep=True)
        for u in result.units:
            if u.start_ms is not None:
                u.start_ms = max(0, u.start_ms - advance)
            if u.end_ms is not None:
                u.end_ms = max(0, u.end_ms - advance)
    try:
        out = _export(h.project, fmt, result)
    except ValueError as e:
        raise ServiceError(str(e)) from e
    if fmt in ("lrc-line", "lrc-unit") and advance:
        out.warnings.append(f"已按“歌词提前显示”把所有时间提前 {advance} ms（alignment.json / CSV 保持原始时间）")
    return out
