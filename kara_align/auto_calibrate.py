"""Automatic LRC offset (detailed mode "自动匹配", simple mode "自动检测").

A plain-mode alignment on a throw-away copy of the project gives each line's
sung start, without looking at the LRC times.  Each timed line then votes for
an offset (sung start − LRC time).  The offset is the densest cluster of votes
(the most lines within ±0.3 s of each other), refined as the median of that
cluster, so lines the trial got wrong (a chorus sung twice, an ad-lib) cannot
move it.  No reference recording is needed.

The estimate is *confident* (``confident``: the simple mode then uses it
without asking) only when the evidence is clear:

- enough timed lines (``MIN_LINES``);
- most of them in the cluster (``MIN_TIGHT`` within ±``TIGHT_MS``);
- the cluster spans the song (lines from its first and its last quarter);
- no drift along the song (``MAX_DRIFT_MS``): a slope means another tempo or
  version, which one global offset cannot fix.

Otherwise ``reason`` says why, and the user marks the first onset as before
(with the estimate as the starting point).  Stems are only used while they
belong to the current original (``service.stems_current``); otherwise the
trial runs on the original and no vocal onset is given.  Nothing is saved.
"""

from __future__ import annotations

import statistics
from typing import Callable, Optional

from . import service as S
from .interfaces import CancelToken, Cancelled


def _audio_role(h: "S.ProjectHandle") -> str:
    # stems of a replaced original would place every line on another recording's timeline
    return "vocals" if S.stems_current(h.project) else "original"


MIN_LINES = 4
TIGHT_MS = 300  # a line "agrees" with the offset within this (LRC times are often this far off)
MIN_TIGHT = 0.6
MAX_DRIFT_MS = 400  # offset change from the first to the last agreeing line


def suggest_calibration(h: "S.ProjectHandle", *, cancel: Optional[CancelToken] = None,
                        progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """{shift_ms, agree, tight, lines_checked, confident, reason, drift_ms, line_starts,
    vocal_onset_ms, audio_role}.

    ``shift_ms`` is the suggested global user shift: the first onset of any
    line is suggested at its LRC time + ``shift_ms``.
    """
    est = estimate_lrc_shift(h, cancel=cancel, progress=progress)
    return {
        "shift_ms": est["shift_ms"], "agree": round(est["agree"], 3), "tight": round(est["tight"], 3),
        "lines_checked": est["lines"], "confident": est["confident"], "reason": est["reason"],
        "drift_ms": est["drift_ms"], "line_starts": est["starts"], "audio_role": _audio_role(h),
        "vocal_onset_ms": _vocal_onset_ms(h) if S.stems_current(h.project) else None,
    }


def _vocal_onset_ms(h: "S.ProjectHandle") -> Optional[int]:
    """First moment the separated vocals are clearly singing for at least 0.3 s."""
    from .align.activity import detect_activity
    from .audio.analysis import rms_envelope_db
    from .audio.io import load_audio

    try:
        voc = h.project.asset("vocals")
        data, sr = load_audio(S.asset_path(h, voc), mono=True)
        # sample 0 of the stem lies at its origin on the original timeline
        origin_ms = voc.origin_offset_samples * 1000.0 / voc.sample_rate
        act = detect_activity(rms_envelope_db(data[0], sr, hop_ms=10.0), 10.0)
    except Exception:
        return None
    if act is None:
        return None
    singing = act.rest < 0.5
    run = int(300 / act.hop_ms)
    for i in range(len(singing) - run):
        if singing[i] and singing[i:i + run].all():
            return int(round(i * act.hop_ms + origin_ms))
    return None


def estimate_lrc_shift(h: "S.ProjectHandle", *, cancel: Optional[CancelToken] = None,
                       progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Estimate the LRC's global offset from a trial alignment (nothing is saved).

    {shift_ms, agree, tight, lines, confident, reason, drift_ms, starts}: ``agree`` / ``tight``
    are the shares of lines within 0.7 s / ``TIGHT_MS`` of the offset.
    """
    import copy

    from .align import calibration as C

    with h.lock:
        tmp = S.ProjectHandle(h.dir, copy.deepcopy(h.project))
    tmp.save = lambda: None  # type: ignore[method-assign]
    tmp.project.mode = "plain"
    tmp.project.results, tmp.project.active_result_id = [], None
    r = S.run_align(tmp, audio_role=_audio_role(h), cancel=cancel, progress=progress)
    starts = {lt.line_id: lt.start_ms for lt in r.lines if lt.start_ms is not None}
    votes: list[tuple[int, int]] = []  # (LRC time, sung start − LRC time), in song order
    doc = h.project.lyrics
    for ln in doc.sung_lines():
        b = C.base_ms(doc, ln)
        if b is not None and ln.anchor is None and ln.id in starts:
            votes.append((b, starts[ln.id] - b))
    if len(votes) < 3:
        raise S.ServiceError("带时间的行太少，无法估计偏移")
    fit = fit_offset(votes)
    # a trial start voted by a model that cannot hear this language is not evidence of the LRC's offset:
    # the errors are systematic per syllable, not a constant shift, so any cluster it finds is noise
    from .reading.prepare import LANG_NAMES, unsupported_languages

    langs = S.backend_languages(h.project)
    bad = unsupported_languages(doc, langs) if langs else []
    if bad and fit["confident"]:
        fit["confident"] = False
        fit["reason"] = (f"歌词含 {'、'.join(LANG_NAMES.get(x, x) for x in bad)} 片段，当前对齐模型（"
                         f"{h.project.config.backend}）不支持这些语言，自动偏移不可信；请手动校准或换后端")
    return fit | {"starts": starts}


def fit_offset(votes: list[tuple[int, int]]) -> dict:
    """The offset most lines agree on, and whether it can be trusted (see the module docstring).
    ``votes``: (LRC time, sung start − LRC time) per timed line, in song order."""
    diffs = [d for _, d in votes]
    # densest cluster: the vote with the most others within ±TIGHT_MS (ties: the tighter one)
    def density(d: int) -> tuple[int, float]:
        near = [x for x in diffs if abs(x - d) <= TIGHT_MS]
        return len(near), -statistics.pstdev(near)

    center = max(diffs, key=density)
    cluster = [i for i, d in enumerate(diffs) if abs(d - center) <= TIGHT_MS]
    shift = int(round(statistics.median(diffs[i] for i in cluster)))
    tight_ids = [i for i, d in enumerate(diffs) if abs(d - shift) <= TIGHT_MS]
    n = len(diffs)
    tight = len(tight_ids) / n
    agree = sum(1 for d in diffs if abs(d - shift) <= 700) / n
    drift = _drift_ms([votes[i] for i in tight_ids])

    reason = ""
    if n < MIN_LINES:
        reason = f"带时间的行太少（{n} 行）"
    elif tight < MIN_TIGHT:
        reason = f"只有 {len(tight_ids)}/{n} 行对得上同一个偏移，歌词可能是别的版本"
    elif not tight_ids or tight_ids[0] >= max(1, n // 4) or tight_ids[-1] < n - max(1, n // 4):
        reason = "对得上的行只集中在歌曲的一部分"
    elif abs(drift) > MAX_DRIFT_MS:
        reason = f"从头到尾偏移变化了 {drift / 1000:+.1f} 秒，视频可能是别的速度或剪辑过"
    return {"shift_ms": shift, "agree": agree, "tight": tight, "lines": n, "confident": not reason,
            "reason": reason, "drift_ms": drift, "tight_lines": len(tight_ids)}


def _drift_ms(pts: list[tuple[int, int]]) -> int:
    """How much the offset changes from the first to the last of these lines (least-squares slope)."""
    if len(pts) < 2:
        return 0
    ts = [t for t, _ in pts]
    mt, md = statistics.fmean(ts), statistics.fmean(d for _, d in pts)
    var = sum((t - mt) ** 2 for t in ts)
    if var <= 0:
        return 0
    slope = sum((t - mt) * (d - md) for t, d in pts) / var
    return int(round(slope * (max(ts) - min(ts))))
