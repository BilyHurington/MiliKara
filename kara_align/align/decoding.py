"""Token preparation and task decoding shared by the runner and retries.

Tokens are mapped back to public units **by sequence position** (each token
position remembers its unit id); token values are never used for matching, so
repeated choruses and repeated syllables stay distinct.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from ..interfaces import Emission, TokenizedUnit, TranslitProfile
from ..models import DecodeConfig, Issue, Line, LyricsDoc, UnitTiming
from ..reading.profiles import is_hold
import numpy as np

from .activity import VocalActivity
from .ctc import AnchorSpec, FramePriors, NoFeasiblePath, ctc_align
from .planning import Task


@dataclass
class UnitInfo:
    unit_id: str
    line_id: str
    segment_id: str
    reading: str
    lang: str
    text: str = ""
    token_ids: list[int] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    english: bool = False  # aligned on the letters of an English word (_english_texts)


@dataclass
class Prepared:
    lines: dict[str, Line]
    order: list[str]  # sung line ids in document order
    units: dict[str, UnitInfo]
    line_units: dict[str, list[str]]
    issues: list[Issue]

    def tokens_for_line(self, line_id: str) -> list[tuple[str, list[int]]]:
        return [(u, self.units[u].token_ids) for u in self.line_units.get(line_id, [])]


def prepare(
    doc: LyricsDoc,
    profile: TranslitProfile,
    tokenize: Callable[[list[str], list[str]], list[TokenizedUnit]],
    supports_language: Optional[Callable[[str], bool]] = None,
) -> Prepared:
    lines = {ln.id: ln for ln in doc.sung_lines()}
    order = [ln.id for ln in doc.sung_lines()]
    units: dict[str, UnitInfo] = {}
    line_units: dict[str, list[str]] = {}
    issues: list[Issue] = []
    for lid in order:
        ln = lines[lid]
        line_units[lid] = []
        warned: set[str] = set()
        if not ln.segments or not ln.units():
            issues.append(Issue(code="no_units", severity="error", line_id=lid,
                                message="该行没有发音单元，请先准备读音"))
        for seg in ln.segments:
            if supports_language is not None and not supports_language(seg.lang) and seg.lang not in warned:
                warned.add(seg.lang)
                issues.append(Issue(code="model_language_mismatch", severity="warning", line_id=lid,
                                    message=f"后端模型未针对语言「{seg.lang}」训练",
                                    data={"lang": seg.lang}))
            if not seg.units and not seg.hidden and any(c.isalnum() for c in seg.surface):
                # e.g. an unknown word, or a reading removed: silently not aligned otherwise
                issues.append(Issue(code="segment_no_reading", severity="warning", line_id=lid,
                                    message=f"片段「{seg.surface}」没有读音，不参与对齐；可以在“注音”里点它补上读音",
                                    data={"segment_id": seg.id, "surface": seg.surface}))
            for u in seg.units:
                units[u.id] = UnitInfo(u.id, lid, seg.id, u.reading, seg.lang)
                line_units[lid].append(u.id)
    ids = [u for lid in order for u in line_units[lid]]
    infos = [units[u] for u in ids]
    # one profile call per line: っ / ー take their neighbour within the line only (a line-final っ
    # has no consonant to double, a line-initial ー no vowel to lengthen; never the next / previous line)
    texts: list[str] = []
    for lid in order:
        li = [units[u] for u in line_units[lid]]
        if li:
            flags = {u.id: list(u.flags) for u in lines[lid].units()}
            texts += profile.unit_texts([i.reading for i in li], [i.lang for i in li],
                                        [flags.get(i.unit_id, []) for i in li])
    texts, english = _english_texts(lines, order, line_units, texts)
    toks = tokenize(ids, list(texts)) if infos else []
    by_id = {t.unit_id: t for t in toks}
    held = held_units([(i.unit_id, i.line_id, i.reading) for i in infos], {t.unit_id for t in toks if t.token_ids})
    for i, text in zip(infos, texts):
        i.text = text
        i.english = i.unit_id in english
        t = by_id.get(i.unit_id)
        if t is not None:
            i.token_ids = list(t.token_ids)
            i.unknown = list(t.unknown)
        if not i.token_ids and i.unit_id in held:
            continue  # ー: timed from the unit it lengthens (apply_holds)
        if not i.token_ids:
            issues.append(Issue(code="untokenizable_unit", severity="warning", line_id=i.line_id, unit_id=i.unit_id,
                                message=f"单元「{i.reading}」没有模型 token（转写「{text}」），保持未对齐",
                                data={"unknown": i.unknown}))
        elif i.unknown:
            issues.append(Issue(code="partial_tokens", severity="warning", line_id=i.line_id, unit_id=i.unit_id,
                                message=f"单元「{i.reading}」中的字符 {i.unknown} 不在模型词表中",
                                data={"unknown": i.unknown}))
    return Prepared(lines, order, units, line_units, issues)


# ---------------------------------------------------------------------------
# English words: the model writes what it hears of them in English spelling ("i will give you all
# my love"), not in the romaji of their katakana reading ("yuu ooru mai rabu"), so their units are
# aligned on the word's own letters, split over the units (ら "lo" ぶ "ve")


def _is_word(surface: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'’\-]*", surface))


def _spelled(word: str, reading: str) -> bool:
    """A single letter, or capitals read letter by letter (OK = おーけー): sung as the letters'
    Japanese names, which the model hears as such (R = "aaru"), not as English."""
    from ..reading.japanese import LETTER_NAMES, to_hiragana

    if len(word) == 1:
        return True
    if not word.isupper():
        return False
    r = to_hiragana(reading or "")

    def match(i: int, j: int) -> bool:
        if i == len(word):
            return j == len(r)
        return any(r.startswith(n, j) and match(i + 1, j + len(n)) for n in LETTER_NAMES.get(word[i], []))

    return match(0, 0)


_SIMILAR = [set("rl"), set("bvp"), set("szct"), set("uowy"), set("ieyj"), set("kcqgx"), set("fh"), set("ae"), set("dt"), set("mn")]


def _sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return 0.5 if any(a in g and b in g for g in _SIMILAR) else 1.0


def _distance(piece: str, romaji: str) -> float:
    """Edit distance, letters that sound alike substituted cheaply."""
    prev = [float(j) for j in range(len(romaji) + 1)]
    for i, a in enumerate(piece, 1):
        cur = [float(i)] + [0.0] * len(romaji)
        for j, b in enumerate(romaji, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + _sub_cost(a, b))
        prev = cur
    return prev[-1]


def split_letters(letters: str, romaji: Sequence[str]) -> list[str]:
    """Split ``letters`` into one contiguous piece per unit, following the units' romaji (a unit
    without romaji, ー, gets none; every other unit at least one letter while there are enough)."""
    n, m = len(romaji), len(letters)
    need = [bool(r) for r in romaji]
    strict = m >= sum(need)
    INF = float("inf")
    # best[i][j]: units 0..i-1 took letters 0..j-1
    best = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(m + 1):
            for k in (range(j + 1) if need[i - 1] else (j,)):
                if best[i - 1][k] == INF:
                    continue
                piece = letters[k:j]
                if need[i - 1] and strict and not piece:
                    continue
                c = best[i - 1][k] + (_distance(piece, romaji[i - 1]) if need[i - 1] else 0.0)
                if c < best[i][j]:
                    best[i][j], back[i][j] = c, k
    if best[n][m] == INF:
        return list(romaji)
    out, j = [], m
    for i in range(n, 0, -1):
        k = back[i][j]
        out.append(letters[k:j])
        j = k
    return out[::-1]


def _english_texts(lines: dict, order: list[str], line_units: dict[str, list[str]],
                   texts: list[str]) -> tuple[list[str], set[str]]:
    """The texts of units of English words (Latin letters written with a kana reading): the word's
    letters, split over its units; and the ids of those units."""
    out = list(texts)
    english: set[str] = set()
    pos = {u: i for i, u in enumerate(u for lid in order for u in line_units[lid])}
    for lid in order:
        for seg in lines[lid].segments:
            word = seg.surface.strip()
            if not _is_word(word) or _spelled(word, seg.reading or "".join(u.reading for u in seg.units)):
                continue
            idx = [pos[u.id] for u in seg.units if u.id in pos]
            if not idx:
                continue
            letters = re.sub(r"[^a-z]", "", word.lower())
            for i, piece in zip(idx, split_letters(letters, [out[i] for i in idx])):
                out[i] = piece
            english.update(u.id for u in seg.units if u.id in pos)
    return out, english


def held_units(units: Sequence[tuple[str, str, str]], with_tokens: set[str]) -> set[str]:
    """Ids of the ー units that lengthen a unit with tokens before them in the same line
    (``units``: (unit id, line id, reading) in order)."""
    out: set[str] = set()
    holder: Optional[tuple[str, str]] = None  # (line id, unit id) of the unit a ー would lengthen
    for uid, lid, reading in units:
        if is_hold(reading):
            if holder is not None and holder[0] == lid:
                out.add(uid)
            continue
        holder = (lid, uid) if uid in with_tokens else None
    return out


# a held vowel (ー) lasts until the next unit starts, but at most this long past the model's end of
# the unit it lengthens (a rest may follow); line-final: this long, the tail step may extend it
HOLD_MAX_MS = 800
HOLD_END_MS = 300
MIN_PIECE_MS = 20


def apply_holds(uts: list[UnitTiming]) -> None:
    """Time the ー units of one line (in place): a unit and the ー after it share the time from the
    unit's start to where the held vowel ends, one equal piece each (each is one mora)."""
    i = 0
    while i < len(uts):
        p = uts[i]
        j = i + 1
        while j < len(uts) and is_hold(uts[j].reading) and uts[j].start_ms is None:
            j += 1
        holds = uts[i + 1:j]
        if holds and p.start_ms is not None and p.end_ms is not None and not is_hold(p.reading):
            nxt = next((u.start_ms for u in uts[j:] if u.start_ms is not None), None)
            end = p.end_ms + (min(HOLD_MAX_MS, max(0, nxt - p.end_ms)) if nxt is not None else HOLD_END_MS)
            end = max(end, p.start_ms + MIN_PIECE_MS * (len(holds) + 1))
            if nxt is not None and nxt >= p.start_ms + MIN_PIECE_MS * (len(holds) + 1):
                end = min(end, nxt)
            step = (end - p.start_ms) / (len(holds) + 1)
            p.end_ms = int(round(p.start_ms + step))
            for k, h in enumerate(holds, start=1):
                h.start_ms = int(round(p.start_ms + step * k))
                h.end_ms = int(round(p.start_ms + step * (k + 1))) if k < len(holds) else int(end)
                h.status, h.reason = "ok", None
                h.acoustic_score = p.acoustic_score
                h.flags = [f for f in h.flags if f != "partial_tokens"] + ["held"]
        i = max(j, i + 1)


# per frame, what singing after the last line (a free tail) costs beyond what the model heard there
TAIL_FILLER_COST = 1.0


@dataclass
class UnitSpan:
    start_ms: int
    end_ms: int
    score: float
    start_frame: int
    end_frame: int
    max_gap_ms: int = 0  # longest blank gap between this unit's own tokens


# a unit whose tokens are split by a longer blank gap is flagged ("token_gap"):
# typically the reading does not match what is sung
TOKEN_GAP_MS = 300


@dataclass
class TaskOutcome:
    task: Task
    role: str
    feasible: bool
    reason: Optional[str] = None
    units: dict[str, UnitSpan] = field(default_factory=dict)  # unit_id -> span (all participating)
    line_ranges: dict[str, tuple[int, int]] = field(default_factory=dict)
    anchor_ms: dict[str, tuple[int, str]] = field(default_factory=dict)
    window_ms: tuple[int, int] = (0, 0)
    mean_acoustic: Optional[float] = None
    sigma_scale: float = 1.0
    label: str = "base"

    def residual(self, line_id: str) -> Optional[int]:
        if line_id in self.anchor_ms and line_id in self.line_ranges:
            return self.line_ranges[line_id][0] - self.anchor_ms[line_id][0]
        return None


def decode_task(
    task: Task,
    emission: Emission,
    prep: Prepared,
    cfg: DecodeConfig,
    role: str,
    sigma_scale: float = 1.0,
    line_units_override: Optional[dict[str, Sequence[tuple[str, Sequence[int]]]]] = None,
    label: str = "base",
    activity: Optional[VocalActivity] = None,
) -> TaskOutcome:
    em = emission.slice(task.start_frame, task.end_frame)
    fm = em.frame_map
    window_ms = (fm.frame_start_ms(0), fm.frame_start_ms(em.num_frames))
    out = TaskOutcome(task, role, False, window_ms=window_ms, sigma_scale=sigma_scale, label=label)
    targets: list[int] = []
    pos_unit: list[str] = []
    pos_line: list[str] = []
    first_tok: dict[str, int] = {}
    for lid in task.participating_line_ids:
        seq = (line_units_override or {}).get(lid)
        if seq is None:
            seq = prep.tokens_for_line(lid)
        for uid, toks in seq:
            if not toks:
                continue
            first_tok.setdefault(lid, len(targets))
            targets.extend(int(t) for t in toks)
            pos_unit.extend([uid] * len(toks))
            pos_line.extend([lid] * len(toks))
    if not targets:
        out.reason = "该任务中没有可转为 token 的单元"
        return out
    if em.num_frames == 0:
        out.reason = "解码窗口为空"
        return out
    frame_ms = fm.frame_ms
    anchors: list[AnchorSpec] = []
    for a in task.anchors:
        if a.line_id not in first_tok:
            continue
        out.anchor_ms[a.line_id] = (a.ms, a.kind)
        ti = first_tok[a.line_id]
        if a.kind == "hard":
            anchors.append(AnchorSpec(ti, "hard", fm.ms_to_frame(a.ms),
                                      lo_frame=fm.ms_to_frame(a.ms - a.tolerance_ms),
                                      hi_frame=fm.ms_to_frame(a.ms + a.tolerance_ms)))
        else:
            anchors.append(AnchorSpec(ti, "soft", fm.ms_to_frame(a.ms),
                                      sigma_frames=cfg.soft_sigma_ms * sigma_scale * task.sigma_scale / frame_ms,
                                      lam=cfg.soft_lambda, huber_delta=cfg.huber_delta))
    priors = frame_priors(pos_line, em.num_frames, frame_ms, cfg,
                          activity.for_frames(fm, 0, em.num_frames) if activity is not None else None)
    try:
        path = ctc_align(em.logp, targets, em.blank_id, anchors, band=cfg.band_frames, priors=priors,
                         tail_penalty=TAIL_FILLER_COST if task.free_tail else None)
    except NoFeasiblePath as e:
        out.reason = e.reason
        return out
    out.feasible = True
    out.mean_acoustic = path.mean_acoustic
    agg: dict[str, list] = {}
    for span, uid in zip(path.spans, pos_unit):
        a = agg.get(uid)
        n = span.end_frame - span.start_frame
        if a is None:
            agg[uid] = [span.start_frame, span.end_frame, span.acoustic_score * n, n, 0]
        else:
            a[4] = max(a[4], span.start_frame - a[1])
            a[0] = min(a[0], span.start_frame)
            a[1] = max(a[1], span.end_frame)
            a[2] += span.acoustic_score * n
            a[3] += n
    for uid, (s, e, tot, n, gap) in agg.items():
        out.units[uid] = UnitSpan(fm.frame_start_ms(s), fm.frame_start_ms(e), tot / max(1, n), s, e,
                                  int(round(gap * frame_ms)))
    for lid in task.participating_line_ids:
        spans = [out.units[u] for u in prep.line_units.get(lid, []) if u in out.units]
        if line_units_override and lid in line_units_override:
            spans = [out.units[u] for u, _ in line_units_override[lid] if u in out.units]
        if spans:
            out.line_ranges[lid] = (min(s.start_ms for s in spans), max(s.end_ms for s in spans))
    return out


def frame_priors(pos_line: Sequence[str], num_frames: int, frame_ms: float, cfg: DecodeConfig,
                 rest: Optional[np.ndarray]) -> Optional[FramePriors]:
    """In-line pause cost and rest costs for one decode (see :mod:`.ctc`)."""
    sec = frame_ms / 1000.0
    U = len(pos_line)
    gap_states = np.zeros(2 * U + 1, dtype=bool)
    for k in range(1, U):
        gap_states[2 * k] = pos_line[k] == pos_line[k - 1]
    gap = np.full(num_frames, -cfg.line_gap_cost * sec)
    token = None
    if rest is not None:
        gap = gap - cfg.rest_gap_cost * sec * rest
        token = -cfg.rest_token_cost * sec * rest
    if not gap_states.any() or not np.any(gap):
        gap = None
    if token is not None and not np.any(token):
        token = None
    if gap is None and token is None:
        return None
    return FramePriors(token=token, gap=gap, gap_states=gap_states if gap is not None else None)


def unit_timings_for_line(prep: Prepared, outcome: Optional[TaskOutcome], line_id: str) -> list[UnitTiming]:
    """Public unit timings for one line from an outcome (None/failed -> null times)."""
    res = []
    for uid in prep.line_units.get(line_id, []):
        info = prep.units[uid]
        ut = UnitTiming(unit_id=uid, line_id=line_id, segment_id=info.segment_id, reading=info.reading)
        if not info.token_ids and is_hold(info.reading):
            # timed by apply_holds from the unit it lengthens, when that one has times
            if outcome is not None and outcome.feasible:
                ut.status, ut.reason = "unaligned", "长音前面没有可以延长的单元"
            else:
                ut.status = "failed"
                ut.reason = (outcome.reason if outcome is not None else None) or "未解码"
        elif not info.token_ids:
            ut.status = "unaligned"
            ut.reason = "该单元没有模型 token" + (f"（未知字符 {info.unknown}）" if info.unknown else "")
        elif outcome is None or not outcome.feasible:
            ut.status = "failed"
            ut.reason = (outcome.reason if outcome is not None else None) or "未解码"
        elif uid not in outcome.units:
            ut.status = "failed"
            ut.reason = "解码路径中缺少该单元"
        else:
            sp = outcome.units[uid]
            ut.start_ms = ut.model_start_ms = sp.start_ms
            ut.end_ms = ut.model_end_ms = sp.end_ms
            ut.acoustic_score = sp.score
            if info.unknown:
                ut.flags.append("partial_tokens")
            if info.english and info.text and not any(c in "aeiouy" for c in info.text):
                ut.flags.append("consonant")  # (ん "n", ぐ "g" of "Spring"): a few frames is right
            if sp.max_gap_ms >= TOKEN_GAP_MS:
                ut.flags.append("token_gap")
        res.append(ut)
    apply_holds(res)
    return res
