"""Alignment orchestration: plan -> decode -> stitch -> check -> retry -> tail.

Emissions are computed once for the whole song by the caller (and cached);
decode windows are slices of it.  Times are converted to integer ms only when
building the public result.  Manual locks from a previous result are carried
over for unchanged unit ids (or, after a reading change, for the unit at the
same position of the same segment) and are never overwritten by new
predictions; they are applied *before* the checks, so the issues describe the
final times.  Locks are only carried while the audio timeline is the same one:
after the original was replaced they stay in the unit's history with a warning.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .. import ALGORITHM_VERSION
from ..interfaces import CancelToken, Emission, TokenizedUnit, TranslitProfile
from ..models import (
    AlignConfig,
    AlignMode,
    AlignmentResult,
    AudioAsset,
    BackendInfo,
    Calibration,
    Candidate,
    Coverage,
    InputSnapshot,
    Issue,
    LineTiming,
    LyricsDoc,
    UnitTiming,
    new_id,
    stable_hash,
    utcnow,
)
from . import checks as chk
from .activity import VocalActivity, detect_activity
from .calibration import calibration_hash, check_issues, validate_anchors
from .decoding import Prepared, TaskOutcome, decode_task, prepare, unit_timings_for_line
from .planning import Task, merge_tasks, plan_lrc, plan_plain, plan_plain_local
from .retry import RetryContext, retry_lines
from .tail import apply_tail

MAX_STABILITY_TASKS = 200


class AlignmentInputError(Exception):
    """Inputs must be corrected before aligning (never silently clipped)."""

    def __init__(self, issues: list[Issue]):
        super().__init__("; ".join(i.message for i in issues))
        self.issues = issues


@dataclass
class AlignInputs:
    lyrics: LyricsDoc
    mode: AlignMode
    calibration: Calibration
    config: AlignConfig
    backend_info: BackendInfo
    tokenize: Callable[[list[str], list[str]], list[TokenizedUnit]]
    profile: TranslitProfile
    emission_for: Callable[[str], Emission]
    available_roles: list[str]
    audio_assets: dict[str, AudioAsset]
    audio_duration_ms: int
    # (dB RMS envelope on the original timeline, hop in ms); index i ~ i*hop ms
    energy_for: Optional[Callable[[str], tuple[np.ndarray, float]]] = None
    previous: Optional[AlignmentResult] = None
    line_ids: Optional[list[str]] = None
    # LRC lines starting after the end of the audio (a shortened video): left out of this run
    skip_line_ids: list[str] = field(default_factory=list)
    supports_language: Optional[Callable[[str], bool]] = None
    extra_stats: dict = field(default_factory=dict)
    # sha256 of the original audio (the timeline all times refer to); default: the original asset's
    original_sha256: Optional[str] = None


def _noop(*_a, **_k) -> None:
    pass


def timeline_sha(assets: dict[str, AudioAsset], explicit: Optional[str] = None) -> Optional[str]:
    """The original recording the times refer to."""
    if explicit:
        return explicit
    orig = assets.get("original")
    if orig is not None:
        return orig.sha256
    for a in assets.values():
        if a.source.parent_sha256:
            return a.source.parent_sha256
    return None


def previous_timeline_sha(prev: AlignmentResult, assets: dict[str, AudioAsset]) -> Optional[str]:
    """The original recording a previous result was aligned on, when it can be known."""
    known = prev.stats.get("original_sha256")
    if known:
        return known
    if prev.snapshot.audio_role == "original":
        return prev.snapshot.audio_sha256
    for a in assets.values():
        if a.sha256 == prev.snapshot.audio_sha256:
            return a.sha256 if a.role == "original" else a.source.parent_sha256
    return None


# the candidate of the LRC-mode cross check (shown as is in the review page)
PLAIN_CHECK_LABEL = "普通模式（不用歌词时间）"

def run_alignment(inp: AlignInputs, cancel: Optional[CancelToken] = None,
                  progress: Optional[Callable[[float, str], None]] = None) -> AlignmentResult:
    t0 = time.time()
    prog = progress or _noop
    cfg = inp.config
    doc = inp.lyrics
    cal = inp.calibration

    def check_cancel() -> None:
        if cancel is not None:
            cancel.check()

    role = cfg.audio_role
    if role not in inp.available_roles or role not in inp.audio_assets:
        raise AlignmentInputError([Issue(code="audio_role_missing", severity="error",
                                         message=f"对齐输入音轨「{role}」不可用；"
                                                 f"可用：{inp.available_roles}")])
    issues: list[Issue] = []
    skip = set(inp.skip_line_ids)
    if inp.mode == "lrc":
        v = [i for i in validate_anchors(doc, cal, inp.audio_duration_ms)
             if not (i.code == "anchor_out_of_range" and i.line_id in skip)]
        errors = [i for i in v if i.severity == "error"]
        if errors:
            raise AlignmentInputError(errors)
        issues += v + check_issues(cal, doc=doc)
        if not cal.confirmed:
            issues.append(Issue(code="calibration_unconfirmed", severity="info",
                                message="全局偏移尚未确认；按导入的 LRC 时间使用"))

    prog(0.02, "准备 token")
    prep = prepare(doc, inp.profile, inp.tokenize, inp.supports_language)
    issues += prep.issues
    if inp.line_ids is not None:
        unknown = [x for x in inp.line_ids if x not in prep.lines]
        if unknown:
            raise AlignmentInputError([Issue(code="unknown_lines", severity="error",
                                             message=f"这些行不参与演唱或不存在：{unknown}")])
    # skipped lines (after the end of the audio) take no part at all: not as targets, not as
    # context of a neighbour, not in the stability pass or a retry
    order = [lid for lid in prep.order if lid not in skip]
    selected = [lid for lid in order if inp.line_ids is None or lid in inp.line_ids]
    if skip:
        issues.append(Issue(code="lines_after_audio", severity="warning",
                            message=f"{len(skip)} 行歌词的时间在音频结束之后（视频可能是剪短的版本），这些行没有对齐、不会出现在字幕里",
                            data={"line_ids": sorted(skip)}))
    voices = {lid: prep.lines[lid].voice for lid in prep.order}

    prog(0.05, "读取声学分数")
    emissions: dict[str, Emission] = {role: inp.emission_for(role)}
    check_cancel()
    em = emissions[role]
    fm, nf = em.frame_map, em.num_frames
    # where the emission (the analysed audio) ends; a decode window ending there is not crowded
    audio_end_ms = min(float(inp.audio_duration_ms), fm.frame_time_ms_float(nf))

    def emission(r: str) -> Emission:
        if r not in emissions:
            emissions[r] = inp.emission_for(r)
        return emissions[r]

    # where the separated vocal stem is singing: a soft prior for decoding and
    # evidence for the checks (never used without a vocal stem)
    activity: Optional[VocalActivity] = None
    if inp.energy_for is not None and "vocals" in inp.available_roles:
        try:
            activity = detect_activity(*inp.energy_for("vocals"))
        except Exception as e:  # optional evidence
            issues.append(Issue(code="activity_unavailable", severity="info", message=f"无法读取人声能量：{e}"))

    n_decodes = 0

    def retarget(task: Task, r: str) -> Task:
        """Tasks are planned in frames of the chosen input's emission; another track's
        emission may start elsewhere on the timeline (its own FrameMap / origin)."""
        other = emission(r)
        if r == role or other.frame_map == fm:
            return task
        lo = fm.frame_time_ms_float(task.start_frame)
        hi = fm.frame_time_ms_float(task.end_frame)
        s = max(0, int(np.floor(other.frame_map.ms_to_frame(lo))))
        e = min(other.num_frames, int(np.ceil(other.frame_map.ms_to_frame(hi))))
        return dataclasses.replace(task, start_frame=s, end_frame=max(s, e))

    def decode(task: Task, r: str, sigma_scale: float = 1.0, line_units_override=None, label: str = "base") -> TaskOutcome:
        nonlocal n_decodes
        check_cancel()
        n_decodes += 1
        return decode_task(retarget(task, r), emission(r), prep, cfg.decode, r, sigma_scale, line_units_override,
                           label, activity=activity)

    # manual locks of the previous result (and its line times) only mean something on the same recording
    same_timeline = True
    if inp.previous is not None:
        cur_tl = timeline_sha(inp.audio_assets, inp.original_sha256)
        prev_tl = previous_timeline_sha(inp.previous, inp.audio_assets)
        same_timeline = prev_tl is not None and prev_tl == cur_tl
    previous_ranges = {lt.line_id: (lt.start_ms, lt.end_ms)
                       for lt in (inp.previous.lines if inp.previous is not None and same_timeline else [])
                       if lt.start_ms is not None and lt.end_ms is not None}

    def plan(decode_cfg=None, extra_context: int = 0, line_ids=None, force_joint=None) -> list[Task]:
        if inp.mode == "plain":
            if line_ids is not None and previous_ranges:
                # a local rerun decodes the stretch between its neighbours, not the whole song
                local = plan_plain_local(doc, line_ids, previous_ranges, decode_cfg or cfg.decode, fm, nf,
                                         inp.audio_duration_ms, skip)
                if local is not None:
                    return local
            return plan_plain(doc, nf, line_ids, skip)
        return plan_lrc(doc, cal, decode_cfg or cfg.decode, fm, nf, inp.audio_duration_ms, line_ids,
                        force_joint, extra_context, skip)

    tasks = plan(line_ids=inp.line_ids)
    prog(0.1, f"解码 {len(tasks)} 个任务")
    outcomes: dict[str, tuple[Task, TaskOutcome]] = {}
    for i, task in enumerate(tasks):
        o = decode(task, role)
        for lid in task.retained_line_ids:
            outcomes[lid] = (task, o)
        prog(0.1 + 0.4 * (i + 1) / max(1, len(tasks)), f"已解码 {i + 1}/{len(tasks)}")

    # --- stitch: boundary / order conflicts between separately decoded tasks -> joint rerun
    resolved, unresolved = 0, []
    skip_pairs: set[tuple[str, str]] = set()
    for _ in range(len(tasks) + 1):
        conflict = None
        by_voice: dict[str, list[str]] = {}
        for lid in selected:
            by_voice.setdefault(voices[lid], []).append(lid)
        for ids in by_voice.values():
            for a, b in zip(ids, ids[1:]):
                if a not in outcomes or b not in outcomes or (a, b) in skip_pairs:
                    continue
                (ta, oa), (tb, ob) = outcomes[a], outcomes[b]
                if ta is tb:
                    continue
                ra, rb = oa.line_ranges.get(a), ob.line_ranges.get(b)
                if ra and rb and rb[0] < ra[1]:
                    conflict = (a, b)
                    break
            if conflict:
                break
        if conflict is None:
            break
        a, b = conflict
        merged = merge_tasks(outcomes[a][0], outcomes[b][0], doc)
        o = decode(merged, role, label="joint")
        if o.feasible:
            resolved += 1
            for lid in merged.retained_line_ids:
                outcomes[lid] = (merged, o)
        else:
            skip_pairs.add(conflict)
            unresolved.append(conflict)
            issues.append(Issue(code="boundary_conflict", severity="warning", line_id=b,
                                message=f"{a} 与 {b} 两行重叠且联合重解码失败（{o.reason}）；"
                                        "未裁剪，保留供人工检查"))

    # --- build, apply manual locks, check, stability
    def build(outs: dict[str, tuple[Task, TaskOutcome]]
              ) -> tuple[list[UnitTiming], list[LineTiming], list[Issue], list[Issue]]:
        units: list[UnitTiming] = []
        lines: list[LineTiming] = []
        extra: list[Issue] = []
        for lid in selected:
            task, o = outs.get(lid, (None, None))
            uts = unit_timings_for_line(prep, o, lid)
            units += uts
            lt = LineTiming(line_id=lid, audio_role=o.role if o else None)
            if o is not None and o.feasible and lid in o.line_ranges:
                lt.start_ms, lt.end_ms = o.line_ranges[lid]
                if lid in o.anchor_ms:
                    lt.anchor_ms, lt.anchor_kind = o.anchor_ms[lid][0], o.anchor_ms[lid][1]  # type: ignore[assignment]
                    lt.anchor_residual_ms = o.residual(lid)
                if inp.mode == "lrc":
                    lt.window_ms = o.window_ms
                lt.context_line_ids = [x for x in task.participating_line_ids if x != lid and x not in task.retained_line_ids]
                if o.label != "base":
                    lt.candidate = o.label
            elif uts and all(u.status == "unaligned" for u in uts):
                lt.status, lt.reason = "unaligned", "没有可转为 token 的单元"
            else:
                lt.status = "failed"
                lt.reason = (o.reason if o is not None else None) or "未解码"
                extra.append(Issue(code="decode_failed", severity="error", line_id=lid,
                                   message=f"未得到对齐：{lt.reason}"))
            lines.append(lt)
        manual = _carry_manual(units, inp.previous, same_timeline, set(selected))
        _follow_units(lines, units)
        return units, lines, extra, manual

    units, lines, dec_issues, manual_issues = build(outcomes)
    locked_now = {u.unit_id: u for u in units if u.locked}
    check_issues_, coverage = chk.run_checks(units, lines, cfg.checks, voices, activity, audio_end_ms)
    stab_issues: list[Issue] = []
    if inp.mode == "lrc" and tasks:
        prog(0.55, "稳定性检查")
        alt_cfg = cfg.decode.model_copy(update={"joint_context_lines": 0 if cfg.decode.joint_context_lines > 0 else 1})
        alt_tasks = plan(decode_cfg=alt_cfg, line_ids=inp.line_ids)[:MAX_STABILITY_TASKS]
        alt_starts: dict[str, Optional[int]] = {}
        for t in alt_tasks:
            base_t = outcomes.get(t.retained_line_ids[0], (None, None))[0]
            if base_t is not None and base_t.participating_line_ids == t.participating_line_ids:
                continue
            o = decode(t, role, label="stability")
            for lid in t.retained_line_ids:
                alt_starts[lid] = o.line_ranges[lid][0] if (o.feasible and lid in o.line_ranges) else None
        # compare model starts (a manual lock is not a decoding result)
        base_starts = {lid: outcomes[lid][1].line_ranges.get(lid, (None,))[0]
                       for lid in alt_starts if lid in outcomes and outcomes[lid][1].feasible}
        stab_issues = chk.stability_issues(base_starts, alt_starts, cfg.checks.stability_tolerance_ms, lines)

    # --- limited retries for flagged lines (never because of units fixed by hand)
    def counts(i: Issue) -> bool:
        return i.unit_id is None or i.unit_id not in locked_now

    flagged = []
    for lid in selected:
        codes = {i.code for i in check_issues_ + stab_issues + dec_issues if i.line_id == lid and counts(i)}
        line_units = prep.line_units.get(lid, [])
        if codes & chk.RETRYABLE and not (line_units and all(u in locked_now for u in line_units)):
            flagged.append(lid)
    retry_report = None
    if flagged and cfg.retry.enabled:
        prog(0.65, f"重试 {len(flagged)} 行")
        current = dict(outcomes)

        def evaluate(o: TaskOutcome, lid: str) -> tuple[list[Issue], float]:
            uts = unit_timings_for_line(prep, o, lid)
            for u in uts:  # judge a variant by the times the line will really have
                lk = locked_now.get(u.unit_id)
                if lk is not None and lk.manual is not None:
                    u.start_ms, u.end_ms = lk.manual.start_ms, lk.manual.end_ms
            lt = LineTiming(line_id=lid)
            if o.feasible and lid in o.line_ranges:
                lt.start_ms, lt.end_ms = o.line_ranges[lid]
                lt.anchor_residual_ms = o.residual(lid)
                if inp.mode == "lrc":
                    lt.window_ms = o.window_ms
            idx = order.index(lid)
            neigh: list[LineTiming] = []
            for j in (idx - 1,):
                if j >= 0 and order[j] in current:
                    po = current[order[j]][1]
                    r = po.line_ranges.get(order[j])
                    if r and voices[order[j]] == voices[lid]:
                        neigh.append(LineTiming(line_id=order[j], start_ms=r[0], end_ms=r[1]))
            iss = (chk.check_units(uts, cfg.checks) + chk.check_line_gaps(uts, cfg.checks)
                   + chk.check_rest(uts, activity) + chk.check_lines(neigh + [lt], cfg.checks, voices, audio_end_ms)
                   + chk.weak_units(uts))
            iss = [i for i in iss if i.line_id == lid and counts(i)]
            alignable = [u for u in uts if chk.alignable(u)]
            timed = sum(1 for u in alignable if u.start_ms is not None)
            cov = timed / len(alignable) if alignable else 1.0
            if not o.feasible:
                iss.append(Issue(code="decode_failed", severity="error", line_id=lid, message=o.reason or ""))
            return iss, cov

        def expand(task: Task, lid: str) -> Optional[Task]:
            if inp.mode == "lrc":
                ts = [t for t in plan(extra_context=1, line_ids=[lid]) if lid in t.retained_line_ids]
                if not ts or ts[0].participating_line_ids == task.participating_line_ids:
                    return None
                t = ts[0]
                t.retained_line_ids = [lid]
                return t
            idx = order.index(lid)
            ids = [x for x in order[max(0, idx - 1): idx + 2] if voices[x] == voices[lid]]
            rng = [current[x][1].line_ranges.get(x) for x in ids if x in current]
            rng = [r for r in rng if r]
            if not rng:
                return None
            lo = min(r[0] for r in rng) - cfg.decode.left_margin_ms
            hi = max(r[1] for r in rng) + cfg.decode.right_margin_ms
            s = max(0, int(np.floor(fm.ms_to_frame(max(0, lo)))))
            e = min(nf, int(np.ceil(fm.ms_to_frame(min(inp.audio_duration_ms, hi)))))
            last = [x for x in order if voices[x] == voices[lid]][-1]
            return Task(f"local-{lid}", voices[lid], ids, [lid], s, e, [], "joint", free_tail=ids[-1] == last and e >= nf)

        ctx = RetryContext(prep=prep, mode=inp.mode, decode=decode, evaluate=evaluate, expand_task=expand,
                           available_roles=[r for r in inp.available_roles if r in ("original", "vocals")],
                           cfg=cfg.retry, stability_tolerance_ms=cfg.checks.stability_tolerance_ms,
                           profile=inp.profile, tokenize=inp.tokenize)
        retry_report = retry_lines(flagged, {lid: outcomes[lid] for lid in flagged if lid in outcomes}, ctx)
        if retry_report.committed:
            for lid, o in retry_report.committed.items():
                outcomes[lid] = (o.task, o)
            units, lines, dec_issues, manual_issues = build(outcomes)
            check_issues_, coverage = chk.run_checks(units, lines, cfg.checks, voices, activity, audio_end_ms)
            stab_issues = [i for i in stab_issues if i.line_id not in retry_report.committed]

    # --- tail strategy
    tail_issues: list[Issue] = []
    if cfg.tail.strategy != "off":
        env = None
        if inp.energy_for is not None:
            env_role = "vocals" if "vocals" in inp.available_roles else role
            try:
                env = inp.energy_for(env_role)
            except Exception as e:  # envelope is optional evidence
                tail_issues.append(Issue(code="tail_no_energy", severity="info", message=str(e)))
        by_voice_units: dict[str, list[UnitTiming]] = {}
        for u in units:
            by_voice_units.setdefault(voices[u.line_id], []).append(u)
        for vu in by_voice_units.values():
            tail_issues += apply_tail(vu, cfg.tail, env, cfg.checks.min_unit_ms)

    # final line ranges follow final unit times
    _follow_units(lines, units)
    by_line: dict[str, list[UnitTiming]] = {}
    for u in units:
        by_line.setdefault(u.line_id, []).append(u)
    for lt in lines:
        timed = [u for u in by_line.get(lt.line_id, []) if u.start_ms is not None and u.end_ms is not None]
        if timed and lt.status == "failed" and all(u.locked for u in timed):
            lt.flags.append("manual_only")

    # --- LRC mode: the song decoded once more without the lyrics' times, as a check of each line (an LRC
    # time can be wrong too: a long held note, a line the LRC times at the wrong repeat)
    cross_issues: list[Issue] = []
    cross_candidates: list[Candidate] = []
    if inp.mode == "lrc" and inp.line_ids is None and cfg.checks.cross_check_ms > 0:
        prog(0.9, "与普通模式核对")
        final = {lt.line_id: lt for lt in lines}
        by_hand = {u.line_id for u in units if u.manual is not None}  # the times someone set stay
        for t in plan_plain(doc, nf, None, skip):
            o = decode(t, role, label=PLAIN_CHECK_LABEL)
            if not o.feasible:
                continue
            for lid in t.retained_line_ids:
                lt, r = final.get(lid), o.line_ranges.get(lid)
                if lt is None or r is None or lt.start_ms is None or lid in by_hand:
                    continue
                d = r[0] - lt.start_ms
                if abs(d) < cfg.checks.cross_check_ms:
                    continue
                cross_issues.append(Issue(
                    code="mode_disagree", severity="warning", line_id=lid,
                    message=f"不用歌词时间对齐时，这一句{'早' if d < 0 else '晚'} {abs(d) / 1000:.1f} 秒；"
                            "两种方法结果不一致，建议试听，可以在“候选”里采用另一种",
                    data={"plain_start_ms": int(r[0]), "diff_ms": int(d)}))
                cross_candidates.append(Candidate(line_id=lid, label=PLAIN_CHECK_LABEL,
                                                  units=unit_timings_for_line(prep, o, lid),
                                                  summary={"role": o.role, "diff_ms": int(d),
                                                           "note": "decoded without the lyrics' times"}))

    all_issues = issues + dec_issues + check_issues_ + stab_issues + manual_issues + tail_issues + cross_issues
    all_issues = [i for i in all_issues if i.line_id is None or i.line_id in set(selected)]
    prog(0.95, "汇总结果")
    asset = inp.audio_assets[role]
    snapshot = InputSnapshot(
        mode=inp.mode,
        lyrics_text_revision=doc.text_revision(),
        lyrics_reading_revision=doc.reading_revision(),
        calibration_hash=calibration_hash(doc, cal) if inp.mode == "lrc" else None,
        audio_asset_id=asset.id,
        audio_sha256=asset.sha256,
        audio_role=role,
        line_ids=selected,
        config_hash=stable_hash(cfg.model_dump(mode="json")),
    )
    timed_lines = [lt for lt in lines if lt.start_ms is not None]
    cov = Coverage(
        full=inp.line_ids is None,
        line_ids=selected,
        from_ms=min((lt.start_ms for lt in timed_lines), default=None),  # type: ignore[type-var]
        to_ms=max((lt.end_ms for lt in timed_lines), default=None),  # type: ignore[type-var]
    )
    stats = {
        "algorithm": ALGORITHM_VERSION,
        "tasks": len(tasks),
        "decodes": n_decodes,
        "conflicts_resolved": resolved,
        "conflicts_unresolved": len(unresolved),
        "unit_coverage": coverage,
        "frame_ms": fm.frame_ms,
        "num_frames": nf,
        "retry": retry_report.log if retry_report else [],
        "elapsed_s": round(time.time() - t0, 3),
        # the original recording these times refer to (manual locks are not carried to another one)
        "original_sha256": timeline_sha(inp.audio_assets, inp.original_sha256),
        # voices / unit flags / languages / anchor tolerances / end marks (see LyricsDoc.detail_revision)
        "detail_revision": doc.detail_revision(inp.mode),
        **inp.extra_stats,
    }
    return AlignmentResult(
        id=new_id("r"), created=utcnow(), mode=inp.mode, backend=inp.backend_info, config=cfg,
        snapshot=snapshot, coverage=cov, lines=lines, units=units, issues=all_issues,
        candidates=(retry_report.candidates if retry_report else []) + cross_candidates,
        parent_result_id=inp.previous.id if (inp.previous is not None and inp.line_ids is not None) else None,
        stats=stats,
    )


def _follow_units(lines: list[LineTiming], units: list[UnitTiming]) -> None:
    """Line ranges (and the anchor residual) follow the final unit times, manual ones included."""
    by_line: dict[str, list[UnitTiming]] = {}
    for u in units:
        by_line.setdefault(u.line_id, []).append(u)
    for lt in lines:
        timed = [u for u in by_line.get(lt.line_id, []) if u.start_ms is not None and u.end_ms is not None]
        if timed:
            lt.start_ms = min(u.start_ms for u in timed)  # type: ignore[type-var]
            lt.end_ms = max(u.end_ms for u in timed)  # type: ignore[type-var]
            if lt.anchor_ms is not None:
                lt.anchor_residual_ms = lt.start_ms - lt.anchor_ms


def _fmt_ms(v: Optional[int]) -> str:
    return "—" if v is None else f"{v / 1000:.2f}s"


def _carry_manual(units: list[UnitTiming], prev: Optional[AlignmentResult], same_timeline: bool = True,
                  line_ids: Optional[set[str]] = None) -> list[Issue]:
    """Manual edits of ``prev`` onto the new ``units`` (in place); returns issues.

    A unit keeps its edits by id; a unit whose id changed because its segment's reading was
    edited takes the edits of the unit at the same position in the same segment when the segment
    still has as many units.  Edits that cannot be placed any more are reported (never dropped
    silently).  With another audio timeline (the original was replaced) nothing is applied: the
    edits stay in the unit's history and a warning says so.
    """
    if prev is None:
        return []
    issues: list[Issue] = []
    old = {u.unit_id: u for u in prev.units}
    cur_ids = {u.unit_id for u in units}

    def by_position(us: list[UnitTiming]) -> tuple[dict[tuple[str, int], UnitTiming], dict[str, int]]:
        pos: dict[tuple[str, int], UnitTiming] = {}
        n: dict[str, int] = {}
        for u in us:
            k = n.get(u.segment_id, 0)
            pos[(u.segment_id, k)] = u
            n[u.segment_id] = k + 1
        return pos, n

    old_pos, old_n = by_position(prev.units)
    _, new_n = by_position(units)
    seen: dict[str, int] = {}
    placed: set[str] = set()
    not_applied: list[UnitTiming] = []
    for u in units:
        k = seen.get(u.segment_id, 0)
        seen[u.segment_id] = k + 1
        p = old.get(u.unit_id)
        if p is None and old_n.get(u.segment_id) == new_n.get(u.segment_id):
            q = old_pos.get((u.segment_id, k))
            if q is not None and q.unit_id not in cur_ids:
                p = q  # same segment, same position: the reading of this mora was edited
        if p is None or (p.manual is None and not p.manual_history):
            continue
        placed.add(p.unit_id)
        u.manual_history = [m.model_copy() for m in p.manual_history]
        if not same_timeline:
            if p.manual is not None:
                if not any(m == p.manual for m in u.manual_history):
                    u.manual_history.append(p.manual.model_copy())
                not_applied.append(u)
            continue
        u.manual = p.manual.model_copy() if p.manual else None
        if p.reading != u.reading and u.manual is not None:
            issues.append(Issue(code="manual_reading_changed", severity="warning", line_id=u.line_id,
                                unit_id=u.unit_id,
                                message=f"读音已从「{p.reading}」改为「{u.reading}」，人工时间仍保留"))
        if u.manual is not None and u.manual.locked:
            u.start_ms, u.end_ms = u.manual.start_ms, u.manual.end_ms
            if "manual" not in u.flags:
                u.flags.append("manual")
            if u.status in ("failed",) and u.start_ms is not None:
                u.flags.append("model_failed")
    if not_applied:
        issues.append(Issue(code="manual_audio_changed", severity="warning",
                            message=f"原曲已更换：上一结果中 {len(not_applied)} 个单元的人工时间属于旧音频，"
                                    "没有套用（仍保存在修改历史中），请重新检查",
                            data={"unit_ids": [u.unit_id for u in not_applied]}))
    # edits that no unit of this run can take any more
    lost: dict[str, list[UnitTiming]] = {}
    for p in prev.units:
        if p.manual is None or p.unit_id in placed:
            continue
        if line_ids is not None and p.line_id not in line_ids:
            continue
        lost.setdefault(p.line_id, []).append(p)
    for lid, ps in lost.items():
        desc = "、".join(f"「{p.reading}」{_fmt_ms(p.manual.start_ms)}–{_fmt_ms(p.manual.end_ms)}"  # type: ignore[union-attr]
                        for p in ps[:6])
        issues.append(Issue(code="manual_dropped", severity="warning", line_id=lid,
                            message=f"读音或单元划分已修改，{len(ps)} 个人工时间无法对应到新的单元，已失效：{desc}"
                                    + ("…" if len(ps) > 6 else "") + "；请重新设置",
                            data={"units": [{"unit_id": p.unit_id, "reading": p.reading,
                                             "start_ms": p.manual.start_ms,  # type: ignore[union-attr]
                                             "end_ms": p.manual.end_ms} for p in ps]}))  # type: ignore[union-attr]
    return issues
