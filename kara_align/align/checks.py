"""Diagnostic checks on alignment results (design §4.6).

All thresholds come from :class:`CheckConfig`.  Checks only produce issues and
flags; they never claim a confidence probability.
"""

from __future__ import annotations

from typing import Iterable, Optional

import numpy as np

from ..models import CheckConfig, Issue, LineTiming, UnitTiming
from .activity import VocalActivity


def _flag(obj, code: str) -> None:
    if code not in obj.flags:
        obj.flags.append(code)


def check_units(units: list[UnitTiming], cfg: CheckConfig) -> list[Issue]:
    issues: list[Issue] = []
    for u in units:
        if u.start_ms is None or u.end_ms is None:
            if (u.start_ms is None) != (u.end_ms is None):
                issues.append(Issue(code="illegal_interval", severity="error", line_id=u.line_id, unit_id=u.unit_id,
                                    message="单元只有起点或终点之一"))
            continue
        d = u.end_ms - u.start_ms
        if d <= 0:
            _flag(u, "illegal_interval")
            issues.append(Issue(code="illegal_interval", severity="error", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"终点 {u.end_ms} ≤ 起点 {u.start_ms}"))
        elif d < cfg.min_unit_ms and "consonant" not in u.flags:
            _flag(u, "short_unit")
            issues.append(Issue(code="short_unit", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"单元「{u.reading}」只有 {d} ms", data={"duration_ms": d}))
        else:
            # both can hold for one unit: a token gap does not hide an overlong unit
            if "token_gap" in u.flags:
                issues.append(Issue(code="token_gap", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                    message=f"单元「{u.reading}」的 token 之间有长停顿（共 {d} ms）；"
                                            "读音可能与演唱不符", data={"duration_ms": d}))
            if d > cfg.max_unit_ms:
                _flag(u, "long_unit")
                issues.append(Issue(code="long_unit", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                    message=f"单元「{u.reading}」持续 {d} ms", data={"duration_ms": d}))
    return issues


def check_confidence(units: list[UnitTiming], min_units: int = 40, z_low: float = -4.0) -> list[Issue]:
    """Lines where the model heard something else than the reading: several units whose acoustic
    score (mean log-probability per frame) is far below this song's usual level (a robust z-score,
    median / MAD over the song, so a quieter or noisier recording does not matter).  Typical causes:
    English words, backing vocals in brackets, a wrong reading.  One issue per line (at least 2 of its
    units, and 30 % of them); units edited by hand do not count."""
    scored = [u for u in units if u.acoustic_score is not None and u.start_ms is not None and u.manual is None]
    if len(scored) < min_units:
        return []
    vals = sorted(u.acoustic_score for u in scored)  # type: ignore[misc]
    med = vals[len(vals) // 2]
    mad = sorted(abs(v - med) for v in vals)[len(vals) // 2] * 1.4826
    mad = max(mad, 0.05)
    per_line: dict[str, list[UnitTiming]] = {}
    for u in scored:
        per_line.setdefault(u.line_id, []).append(u)
    issues: list[Issue] = []
    for line_id, us in per_line.items():
        low = [u for u in us if (u.acoustic_score - med) / mad < z_low]  # type: ignore[operator]
        if len(low) < max(2, 0.3 * len(us)):
            continue
        for u in low:
            _flag(u, "low_confidence")
        worst = min(low, key=lambda u: u.acoustic_score)  # type: ignore[arg-type, return-value]
        readings = "".join(u.reading for u in low[:8])
        issues.append(Issue(code="low_confidence", severity="warning", line_id=line_id, unit_id=worst.unit_id,
                            message=f"这一行有 {len(low)}/{len(us)} 个单元和模型听到的发音差得较多（{readings}）："
                                    "常见于英文、括号里的和声或读音不对，建议试听检查",
                            data={"low_units": len(low), "units": len(us)}))
    return issues


# a unit whose frames the model scores this low (mean log-probability) was not heard there: on any
# audio, the vocals stem or the original with accompaniment (well matched units are around -0.3 … -1.5)
WEAK_UNIT_LOGP = -4.0


def weak_units(units: list[UnitTiming]) -> list[Issue]:
    """For comparing retry variants only (never reported): units the model clearly did not hear where
    a variant put them, so a variant that squeezes a few units somewhere wrong does not win just
    because it raised no other warning."""
    return [Issue(code="weak_unit", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                  message=f"单元「{u.reading}」的声学分数很低（{u.acoustic_score:.1f}）")
            for u in units if u.acoustic_score is not None and u.start_ms is not None and u.manual is None
            and u.acoustic_score < WEAK_UNIT_LOGP]


def check_line_gaps(units: list[UnitTiming], cfg: CheckConfig) -> list[Issue]:
    """A long pause between two consecutive units of the same line."""
    issues: list[Issue] = []
    prev: dict[str, UnitTiming] = {}
    for u in units:
        if u.start_ms is None or u.end_ms is None:
            continue
        p = prev.get(u.line_id)
        prev[u.line_id] = u
        if p is None or p.end_ms is None:
            continue
        gap = u.start_ms - p.end_ms
        if gap > cfg.max_line_gap_ms:
            _flag(u, "line_gap")
            issues.append(Issue(code="line_gap", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"「{p.reading}」与「{u.reading}」之间停顿 {gap / 1000:.1f} 秒；"
                                        "可能被放进了间奏", data={"gap_ms": gap}))
    return issues


def check_rest(units: list[UnitTiming], activity: Optional[VocalActivity]) -> list[Issue]:
    """A unit placed where the separated vocal stem is silent."""
    if activity is None:
        return []
    issues: list[Issue] = []
    for u in units:
        if u.start_ms is None or u.end_ms is None or u.end_ms <= u.start_ms:
            continue
        if activity.is_rest(u.start_ms, u.end_ms):
            _flag(u, "in_rest")
            issues.append(Issue(code="unit_in_rest", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"单元「{u.reading}」所在位置人声分轨几乎无声"
                                        f"（{activity.peak_db(u.start_ms, u.end_ms):.0f} dB，"
                                        f"演唱约 {activity.reference_db:.0f} dB）",
                                data={"peak_db": round(activity.peak_db(u.start_ms, u.end_ms), 1)}))
    return issues


# singing the lyrics have no line over: at least this long, this much (rest value) singing
UNCOVERED_MIN_MS = 2500
UNCOVERED_REST = 0.25  # rest at most this: clearly singing (see activity.py)
UNCOVERED_BRIDGE_MS = 600  # dips shorter than this (a breath, a consonant) do not split it
UNCOVERED_EDGE_MS = 400  # a line's own breath / held note just past its ends


def check_uncovered(lines: list[LineTiming], activity: Optional[VocalActivity],
                    order: Optional[list[str]] = None) -> list[Issue]:
    """Singing in the vocal stem that no lyric line is placed over: lyrics the song has but the text
    does not (a backing vocal, an ad-lib, a repeat written once), or a line put somewhere else (two
    lines of the same text, one placed over the other's place).  Reported on the line after it (the
    last line for singing after the end); a hint to listen, never changed by itself."""
    if activity is None or activity.rest.size == 0:
        return []
    timed = sorted((lt for lt in lines if lt.start_ms is not None and lt.end_ms is not None),
                   key=lambda lt: lt.start_ms)  # type: ignore[arg-type, return-value]
    if not timed:
        return []
    hop = activity.hop_ms
    singing = activity.rest <= UNCOVERED_REST
    covered = np.zeros(singing.size, dtype=bool)
    for lt in timed:
        a = max(0, int((lt.start_ms - UNCOVERED_EDGE_MS) / hop))  # type: ignore[operator]
        b = min(covered.size, int(np.ceil((lt.end_ms + UNCOVERED_EDGE_MS) / hop)))  # type: ignore[operator]
        covered[a:b] = True
    free = singing & ~covered
    # runs of free singing, bridging short dips
    runs: list[list[int]] = []
    idx = np.flatnonzero(free)
    bridge = int(UNCOVERED_BRIDGE_MS / hop)
    for i in idx:
        if runs and i - runs[-1][1] <= bridge and not covered[runs[-1][1]:i].any():
            runs[-1][1] = int(i)
        else:
            runs.append([int(i), int(i)])
    # one hint per line: the runs before the same line together
    found: dict[str, tuple[float, float, bool]] = {}
    for a, b in runs:
        a_ms, b_ms = a * hop, (b + 1) * hop
        if b_ms - a_ms < UNCOVERED_MIN_MS:
            continue
        after = next((lt for lt in timed if lt.start_ms >= b_ms), timed[-1])  # type: ignore[operator]
        lo, _, before = found.get(after.line_id, (a_ms, b_ms, after.start_ms >= b_ms))  # type: ignore[operator]
        found[after.line_id] = (lo, b_ms, before)
    issues: list[Issue] = []
    for lid, (a_ms, b_ms, before) in found.items():
        issues.append(Issue(code="uncovered_singing", severity="warning", line_id=lid,
                            message=f"{'这一句之前' if before else '这一句之后'} {_fmt(a_ms)}–{_fmt(b_ms)} 有人在唱，但没有对应的歌词："
                                    "可能是歌词里没写的部分（和声、即兴、重复段），也可能是相邻的句子被放错了位置，建议试听",
                            data={"from_ms": int(a_ms), "to_ms": int(b_ms)}))
    return issues


def _fmt(ms: float) -> str:
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:04.1f}"


def alignable(u: UnitTiming) -> bool:
    """Units without model tokens (``unaligned``: a っ at the end of a line, a character the model
    cannot spell) can never get a time; they are reported once when tokens are prepared and do not
    count as missing coverage (no incomplete line, no retries for them)."""
    return u.status != "unaligned" or u.start_ms is not None


def check_coverage(units: list[UnitTiming], lines: list[LineTiming], cfg: CheckConfig) -> tuple[list[Issue], float]:
    issues: list[Issue] = []
    units = [u for u in units if alignable(u)]
    total = len(units)
    timed = sum(1 for u in units if u.start_ms is not None and u.end_ms is not None)
    cov = timed / total if total else 1.0
    by_line: dict[str, list[UnitTiming]] = {}
    for u in units:
        by_line.setdefault(u.line_id, []).append(u)
    for lt in lines:
        us = by_line.get(lt.line_id, [])
        missing = [u for u in us if u.start_ms is None]
        if us and missing:
            _flag(lt, "incomplete")
            issues.append(Issue(code="line_incomplete", severity="error" if len(missing) == len(us) else "warning",
                                line_id=lt.line_id,
                                message=f"{len(missing)}/{len(us)} units have no time",
                                data={"unit_ids": [u.unit_id for u in missing]}))
    if cov < cfg.min_coverage:
        issues.append(Issue(code="low_coverage", severity="warning",
                            message=f"只有 {timed}/{total} 个单元得到时间", data={"coverage": cov}))
    return issues, cov


def check_lines(lines: list[LineTiming], cfg: CheckConfig, voices: Optional[dict[str, str]] = None,
                audio_end_ms: Optional[float] = None) -> list[Issue]:
    """Line-level checks.  ``audio_end_ms``: where the audio (the emission) ends; a window that
    ends there has no later lyrics behind its right edge."""
    issues: list[Issue] = []
    last: dict[str, LineTiming] = {}
    for lt in lines:
        if lt.anchor_residual_ms is not None and abs(lt.anchor_residual_ms) > cfg.anchor_deviation_ms:
            _flag(lt, "anchor_deviation")
            issues.append(Issue(code="anchor_deviation", severity="warning", line_id=lt.line_id,
                                message=f"句首与 LRC 锚点相差 {lt.anchor_residual_ms:+d} ms",
                                data={"residual_ms": lt.anchor_residual_ms}))
        if lt.window_ms is not None and lt.start_ms is not None and lt.end_ms is not None:
            w0, w1 = lt.window_ms
            if w0 > 0 and lt.start_ms - w0 < cfg.edge_crowd_ms:
                _flag(lt, "window_edge")
                issues.append(Issue(code="window_edge", severity="warning", line_id=lt.line_id,
                                    message="句首贴在解码窗口左边缘",
                                    data={"window_ms": [w0, w1]}))
            # the right edge is next anchor + margin, only a search bound: touching it
            # means the line ran into audio that belongs to later lines (not when the window
            # simply ends with the audio: a song cut right after its last note)
            at_audio_end = audio_end_ms is not None and w1 >= audio_end_ms - 1
            if lt.end_ms >= w1 - cfg.edge_crowd_ms and not at_audio_end:
                _flag(lt, "window_edge")
                issues.append(Issue(code="window_edge", severity="warning", line_id=lt.line_id,
                                    message="句尾贴在解码窗口右边缘",
                                    data={"window_ms": [w0, w1]}))
        v = (voices or {}).get(lt.line_id, "main")
        prev = last.get(v)
        if prev is not None and prev.end_ms is not None and lt.start_ms is not None:
            if lt.start_ms < prev.start_ms:  # type: ignore[operator]
                _flag(lt, "order_conflict")
                issues.append(Issue(code="order_conflict", severity="error", line_id=lt.line_id,
                                    message=f"句首早于上一行 {prev.line_id}",
                                    data={"previous_line_id": prev.line_id}))
            elif lt.start_ms < prev.end_ms:
                _flag(lt, "line_overlap")
                issues.append(Issue(code="line_overlap", severity="warning", line_id=lt.line_id,
                                    message=f"与上一行 {prev.line_id} 重叠 {prev.end_ms - lt.start_ms} ms",
                                    data={"previous_line_id": prev.line_id, "overlap_ms": prev.end_ms - lt.start_ms}))
        if lt.start_ms is not None:
            last[v] = lt
    return issues


def check_unit_order(units: list[UnitTiming], voices: Optional[dict[str, str]] = None) -> list[Issue]:
    issues = []
    last: dict[str, UnitTiming] = {}
    for u in units:
        if u.start_ms is None:
            continue
        v = (voices or {}).get(u.line_id, "main")
        p = last.get(v)
        if p is not None and p.end_ms is not None and u.start_ms < p.end_ms and p.line_id == u.line_id:
            _flag(u, "unit_overlap")
            issues.append(Issue(code="unit_overlap", severity="error", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"单元在前一单元结束前开始（{u.start_ms} < {p.end_ms}）"))
        last[v] = u
    return issues


def stability_issues(base: dict[str, Optional[int]], alt: dict[str, Optional[int]], tolerance_ms: int,
                     lines: Iterable[LineTiming] = ()) -> list[Issue]:
    """Compare line starts decoded with different context."""
    by_id = {lt.line_id: lt for lt in lines}
    issues = []
    for lid, b in base.items():
        a = alt.get(lid)
        if b is None or a is None:
            continue
        if abs(a - b) > tolerance_ms:
            if lid in by_id:
                _flag(by_id[lid], "unstable")
            issues.append(Issue(code="unstable_boundary", severity="warning", line_id=lid,
                                message=f"改变解码上下文后句首移动 {a - b:+d} ms",
                                data={"base_ms": b, "alt_ms": a}))
    return issues


def run_checks(units: list[UnitTiming], lines: list[LineTiming], cfg: CheckConfig,
               voices: Optional[dict[str, str]] = None,
               activity: Optional[VocalActivity] = None,
               audio_end_ms: Optional[float] = None) -> tuple[list[Issue], float]:
    cov_issues, cov = check_coverage(units, lines, cfg)
    issues = (check_units(units, cfg) + check_line_gaps(units, cfg) + check_rest(units, activity) + cov_issues
              + check_lines(lines, cfg, voices, audio_end_ms) + check_unit_order(units, voices)
              + check_confidence(units) + check_uncovered(lines, activity))
    return issues, cov


# codes produced by the checks above (recomputed when unit times change, e.g. adopting a rerun)
CHECK_CODES = {"illegal_interval", "short_unit", "token_gap", "long_unit", "line_gap", "unit_in_rest",
               "line_incomplete", "low_coverage", "anchor_deviation", "window_edge", "order_conflict",
               "line_overlap", "unit_overlap", "low_confidence"}


# issue codes that a retry could plausibly improve
RETRYABLE = {"short_unit", "token_gap", "long_unit", "anchor_deviation", "window_edge", "line_overlap", "order_conflict",
             "unstable_boundary", "line_incomplete", "decode_failed", "boundary_conflict", "line_gap",
             "unit_in_rest"}
