"""Versioned public data model.

Three independent objects are kept apart (design §3):

* ``LyricsDoc``      – editable lyrics document (Line -> Segment -> Unit)
* ``AudioAsset``     – reusable audio asset (original / vocals / instrumental / mix)
* ``AlignmentResult``– traceable alignment result

All public times are **integer milliseconds counted from the start of the
original audio**, intervals are half open ``[start_ms, end_ms)``.  Internal
code keeps sample / frame coordinates and only rounds when producing these
objects (see :mod:`kara_align.timebase`).

Missing or failed times are ``None`` accompanied by a ``reason``; nothing in
this module ever invents evenly distributed fake times.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SCHEMA_VERSION = 1

# format identifiers written into every top-level JSON document
FMT_PROJECT = "kara-align/project"
FMT_ALIGNMENT = "kara-align/alignment"
FMT_PREPARED = "kara-align/prepared"
FMT_READING_PATCH = "kara-align/reading-patch"


# content hashes end up in file names (assets/<sha>.ext, cache files): only a real sha256 is accepted
SHA256_PATTERN = r"^[0-9a-f]{64}$"
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
# a project id is the name of its folder in the workspace: no path separators, no dots
PROJECT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_hash(obj: Any, n: int = 16) -> str:
    """Deterministic content hash of a JSON-able object."""
    data = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:n]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=False)


# ---------------------------------------------------------------------------
# Lyrics document
# ---------------------------------------------------------------------------

Lang = Literal["ja", "zh", "en", "other"]
ReadingSource = Literal["rule", "manual", "ai", "import", "none"]
LineKind = Literal["lyric", "translation", "romanization", "meta", "blank"]
SourceOrigin = Literal["paste", "upload", "netease", "qq", "project", "manual"]


class Unit(_Base):
    """A pronunciation unit that should receive a time interval.

    ``surface`` is the part of the original text the unit maps to when that
    mapping is 1:1 (e.g. kana ``と``); for multi-unit kanji readings
    (``君`` -> ``き`` / ``み``) the unit surface is empty and the owning
    segment holds the surface.
    """

    id: str = Field(default_factory=lambda: new_id("u"))
    reading: str  # kana for ja, pinyin for zh, word for en
    surface: str = ""
    # extra phonetic flags kept from the reading: sokuon / hatsuon / long vowel
    flags: list[str] = Field(default_factory=list)


class Segment(_Base):
    id: str = Field(default_factory=lambda: new_id("s"))
    surface: str
    reading: Optional[str] = None
    lang: Lang = "ja"
    units: list[Unit] = Field(default_factory=list)
    reading_source: ReadingSource = "none"
    confirmed: bool = False  # manually confirmed; AI / rules must not overwrite
    uncertain: bool = False
    candidates: list[str] = Field(default_factory=list)  # alternative readings
    note: str = ""
    # karaoke display only: a long line may wrap before this segment (suggested by the AI readings;
    # used with layout.wrap == "ai").  Not part of any revision: it never changes an alignment.
    wrap_before: bool = False
    # display only, in no revision either: the ruby is shown in katakana, as the lyrics book writes a
    # reading of its own (宿敵 → ライバル); the reading itself stays hiragana for the aligner
    katakana: bool = False
    # a reading written in brackets after its word (宿敵(ライバル)): neither sung nor shown, the word
    # before it carries it as ruby.  Set by the AI readings only (brackets often hold a chorus line)
    hidden: bool = False


class LineSource(_Base):
    """Provenance of a line instance."""

    origin: SourceOrigin = "paste"
    source_id: Optional[str] = None  # SourceSnapshot.id
    raw_index: Optional[int] = None  # line index inside the raw text
    raw_text: Optional[str] = None
    tag_index: int = 0  # which of multiple time tags produced this instance
    merged_from: list[str] = Field(default_factory=list)
    split_from: Optional[str] = None


class LineAnchor(_Base):
    """A manually locked absolute anchor (original audio time).

    It does not move with later global shifts.
    """

    abs_ms: int
    hard: bool = True
    tolerance_ms: int = 80
    note: str = ""


# Shortcut keys of singers and combinations on the 演唱者 page, in the order new ones take them:
# 1–9, then a–z except l (loop) and p (listen), which the page already uses.
SINGER_KEYS = "123456789abcdefghijkmnoqrstuvwxyz"


def singer_key(value: Any) -> str:
    """A key as stored: one lower-case character of SINGER_KEYS, or "" (none / not usable)."""
    k = str(value).strip().lower() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    return k if len(k) == 1 and k in SINGER_KEYS else ""


def _singer_ids(value: Any) -> list[int]:
    """Singer numbers (1, 2, …) in order, each once; anything else is left out (never an error:
    a hand-edited project still loads)."""
    out: list[int] = []
    for v in value if isinstance(value, (list, tuple)) else []:
        if isinstance(v, bool):
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n == v and n >= 1 and n not in out:
            out.append(n)
    return out


class SingerSpan(_Base):
    """Part of a line sung by other singers than the line's own (``Line.singers``): characters
    [start, end) of ``Line.text``.  Kept by character offset, not by segment, so re-segmenting a
    line (readings, AI, regrouping) never loses it."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    singers: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _clean(cls, data: Any) -> Any:
        if isinstance(data, dict) and "singers" in data:
            data = {**data, "singers": _singer_ids(data["singers"])}
        return data


class Line(_Base):
    id: str = Field(default_factory=lambda: new_id("L"))
    text: str
    kind: LineKind = "lyric"
    sing: bool = True  # participates in alignment
    segments: list[Segment] = Field(default_factory=list)
    # raw LRC line start as written in the file (before embedded offset)
    imported_start_ms: Optional[int] = None
    imported_end_ms: Optional[int] = None  # only when explicitly given (e.g. next tag / enhanced LRC)
    anchor: Optional[LineAnchor] = None
    translation: Optional[str] = None
    romanization: Optional[str] = None
    voice: str = "main"  # independent lyric stream id for real simultaneous parts
    confirmed: bool = False
    source: LineSource = Field(default_factory=LineSource)
    # who sings it (karaoke colours only, never the alignment): numbers of the style's singers
    # (KaraokeStyle.singers.members, 1-based); several = sung together.  Empty: the style's own colours.
    singers: list[int] = Field(default_factory=list)
    # parts sung by someone else than ``singers`` (character ranges, sorted, not overlapping)
    singer_spans: list[SingerSpan] = Field(default_factory=list)
    # countdown dots before this line in the karaoke subtitles: None = as the style's rules say
    # (KaraokeCountdown), True / False = always / never for this line
    countdown: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def _clean_singers(cls, data: Any) -> Any:
        if isinstance(data, dict) and "singers" in data:
            data = {**data, "singers": _singer_ids(data["singers"])}
        if isinstance(data, dict) and "singer_spans" in data:
            def ok(x: Any) -> bool:
                if isinstance(x, SingerSpan):
                    return True
                if not isinstance(x, dict):
                    return False
                a, b = x.get("start"), x.get("end")
                return (isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool)
                        and not isinstance(b, bool) and 0 <= a < b)
            spans = data["singer_spans"]
            data = {**data, "singer_spans": [x for x in spans if ok(x)] if isinstance(spans, list) else []}
        return data

    def units(self) -> list[Unit]:
        return [u for s in self.segments for u in s.units]


class LyricsMeta(_Base):
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    extra: dict[str, str] = Field(default_factory=dict)


class LyricsDoc(_Base):
    format: str = "kara-align/lyrics"
    version: int = SCHEMA_VERSION
    language: Lang = "ja"
    meta: LyricsMeta = Field(default_factory=LyricsMeta)
    lines: list[Line] = Field(default_factory=list)
    # value of the ``[offset:...]`` tag exactly as written, if any
    embedded_offset_raw: Optional[str] = None
    # normalized shift in ms that must be ADDED to imported_start_ms
    # (LRC convention: positive [offset] shows lyrics earlier -> shift = -offset)
    embedded_shift_ms: int = 0
    embedded_offset_note: str = ""

    def line(self, line_id: str) -> Line:
        for ln in self.lines:
            if ln.id == line_id:
                return ln
        raise KeyError(line_id)

    def sung_lines(self) -> list[Line]:
        return [ln for ln in self.lines if ln.sing and ln.kind == "lyric"]

    def text_revision(self) -> str:
        """Identity of the lyrics *text* (ids + texts + sing flags)."""
        return stable_hash([(ln.id, ln.text, ln.sing, ln.kind) for ln in self.lines])

    def reading_revision(self) -> str:
        """Identity of the text plus all readings / unit groupings."""
        return stable_hash(
            [
                (ln.id, ln.text, ln.sing, ln.kind,
                 [(s.surface, s.reading, [(u.id, u.reading) for u in s.units]) for s in ln.segments])
                for ln in self.lines
            ]
        )

    def detail_revision(self, mode: str = "plain") -> str:
        """Identity of the other inputs that change an alignment but are in neither revision
        above: voices, unit flags (sokuon / long …) and segment languages (they change the
        model spelling), and in LRC mode the anchors' hardness / tolerance and the end marks
        (``imported_end_ms``, times of blank lines).  Kept apart from the two revisions so
        results made before it existed are not all marked outdated."""
        sung = [ln for ln in self.lines if ln.sing and ln.kind == "lyric"]
        data: list[Any] = [
            [(ln.id, ln.voice, [(s.lang, [u.flags for u in s.units]) for s in ln.segments]) for ln in sung]]
        if mode == "lrc":
            data.append([(ln.id, ln.imported_end_ms,
                          (ln.anchor.hard, ln.anchor.tolerance_ms) if ln.anchor is not None else None)
                         for ln in sung])
            data.append([(ln.id, ln.imported_start_ms) for ln in self.lines if ln.kind == "blank"])
        return stable_hash(data)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


class CalibrationCheck(_Base):
    """A mark on another line used to verify a single global shift."""

    line_id: str
    marked_ms: int
    residual_ms: int  # marked - effective (with the current shift)


class Calibration(_Base):
    """LRC first-onset calibration.

    base_i      = imported_start_i + embedded_shift_ms
    effective_i = base_i + user_shift_ms
    marking line k at marked_ms sets user_shift = marked_ms - base_k (never accumulated)
    """

    user_shift_ms: int = 0
    confirmed: bool = False  # includes an explicit "zero offset is correct"
    reference_line_id: Optional[str] = None
    marked_ms: Optional[int] = None
    checks: list[CalibrationCheck] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)  # for undo / audit


# ---------------------------------------------------------------------------
# Audio assets
# ---------------------------------------------------------------------------

AudioRole = Literal["original", "vocals", "instrumental", "mix"]


class AudioSource(_Base):
    kind: Literal["upload", "separation", "import", "mix"] = "upload"
    filename: Optional[str] = None
    model: Optional[str] = None  # separation model file / id
    model_version: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    parent_sha256: Optional[Sha256] = None
    notes: list[str] = Field(default_factory=list)


class AudioAsset(_Base):
    id: str = Field(default_factory=lambda: new_id("a"))
    role: AudioRole
    sha256: Sha256
    path: Optional[str] = None  # relative to the project directory, or None when missing
    duration_ms: int
    sample_rate: int
    channels: int
    num_samples: int
    # sample 0 of this file corresponds to this time on the original timeline
    origin_offset_samples: int = 0
    sync_checked: bool = False
    sync_report: Optional[dict[str, Any]] = None
    source: AudioSource = Field(default_factory=AudioSource)


# ---------------------------------------------------------------------------
# Alignment configuration and results
# ---------------------------------------------------------------------------

AlignMode = Literal["plain", "lrc"]


class DecodeConfig(_Base):
    left_margin_ms: int = 1500
    right_margin_ms: int = 1500
    soft_sigma_ms: int = 400
    soft_lambda: float = 4.0
    huber_delta: float = 1.0
    hard_tolerance_ms: int = 80
    joint_context_lines: int = 1
    tight_gap_ms: int = 400  # neighbour lines closer than this are aligned jointly
    band_frames: Optional[int] = None
    # pauses *inside* a line cost this much per second, so a line cannot stretch
    # across an interlude for free (between-line pauses stay free)
    line_gap_cost: float = 1.0
    # with a vocal stem: extra cost per second of an in-line pause where the stem
    # is silent, and per second of singing placed where the stem is silent
    rest_gap_cost: float = 4.0
    rest_token_cost: float = 25.0
    # an LRC end marker (a timed blank line) bounds the search this far after it
    end_marker_margin_ms: int = 6000


class CheckConfig(_Base):
    min_unit_ms: int = 40
    max_unit_ms: int = 6000
    anchor_deviation_ms: int = 700
    edge_crowd_ms: int = 120
    stability_tolerance_ms: int = 150
    min_coverage: float = 0.999
    max_line_gap_ms: int = 4000  # a longer pause between two units of one line is suspicious


class RetryConfig(_Base):
    enabled: bool = True
    max_candidates_per_line: int = 4
    max_total_candidates: int = 60


class TailConfig(_Base):
    strategy: Literal["off", "trim", "energy"] = "off"
    max_extend_ms: int = 800
    max_trim_ms: int = 600
    energy_floor_db: float = -35.0


class AlignConfig(_Base):
    backend: str = "mms-ja"
    model_id: Optional[str] = None  # override weights (e.g. HF repo id)
    model_revision: Optional[str] = None
    device: str = "auto"
    audio_role: Literal["original", "vocals"] = "original"
    chunk_s: float = 20.0
    context_s: float = 3.0
    decode: DecodeConfig = Field(default_factory=DecodeConfig)
    checks: CheckConfig = Field(default_factory=CheckConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    tail: TailConfig = Field(default_factory=TailConfig)


class BackendInfo(_Base):
    name: str
    model_id: str
    model_revision: Optional[str] = None
    license: Optional[str] = None
    profile: str  # transliteration profile used to build tokens
    sample_rate: int
    frame_hop_samples: Optional[int] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ManualEdit(_Base):
    start_ms: Optional[int]
    end_ms: Optional[int]
    locked: bool = True
    at: str = Field(default_factory=utcnow)
    note: str = ""


class TailAdjustment(_Base):
    original_end_ms: Optional[int]
    new_end_ms: Optional[int]
    method: str
    reason: str


UnitStatus = Literal["ok", "failed", "unaligned", "skipped"]


class UnitTiming(_Base):
    unit_id: str
    line_id: str
    segment_id: str
    reading: str
    # final times (after tail correction and manual overrides)
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    status: UnitStatus = "ok"
    reason: Optional[str] = None
    # raw model prediction, never overwritten
    model_start_ms: Optional[int] = None
    model_end_ms: Optional[int] = None
    tail: Optional[TailAdjustment] = None
    manual: Optional[ManualEdit] = None
    manual_history: list[ManualEdit] = Field(default_factory=list)
    # normalized acoustic score (mean logp per frame); NOT a probability
    acoustic_score: Optional[float] = None
    flags: list[str] = Field(default_factory=list)

    @property
    def locked(self) -> bool:
        return bool(self.manual and self.manual.locked)


class Issue(_Base):
    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    line_id: Optional[str] = None
    unit_id: Optional[str] = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class LineTiming(_Base):
    line_id: str
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    status: UnitStatus = "ok"
    reason: Optional[str] = None
    anchor_ms: Optional[int] = None  # effective anchor used (lrc mode)
    anchor_kind: Optional[Literal["soft", "hard"]] = None
    anchor_residual_ms: Optional[int] = None
    window_ms: Optional[tuple[int, int]] = None
    context_line_ids: list[str] = Field(default_factory=list)
    audio_role: Optional[str] = None
    candidate: Optional[str] = None  # which retry candidate produced the committed result
    flags: list[str] = Field(default_factory=list)


class Candidate(_Base):
    """Alternative result for a line kept for manual listening (not committed)."""

    id: str = Field(default_factory=lambda: new_id("c"))
    line_id: str
    label: str
    units: list[UnitTiming]
    summary: dict[str, Any] = Field(default_factory=dict)


class InputSnapshot(_Base):
    mode: AlignMode
    lyrics_text_revision: str
    lyrics_reading_revision: str
    calibration_hash: Optional[str] = None
    audio_asset_id: str
    audio_sha256: str
    audio_role: str
    line_ids: list[str]
    config_hash: str


class Coverage(_Base):
    full: bool = True
    line_ids: list[str] = Field(default_factory=list)
    from_ms: Optional[int] = None
    to_ms: Optional[int] = None


class AlignmentResult(_Base):
    format: str = FMT_ALIGNMENT
    version: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("r"))
    created: str = Field(default_factory=utcnow)
    time_unit: str = "ms, integer, from original audio start, [start, end)"
    mode: AlignMode
    backend: BackendInfo
    config: AlignConfig
    snapshot: InputSnapshot
    coverage: Coverage = Field(default_factory=Coverage)
    lines: list[LineTiming] = Field(default_factory=list)
    units: list[UnitTiming] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    stale: bool = False
    stale_reason: Optional[str] = None
    parent_result_id: Optional[str] = None  # for local reruns
    stats: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class SourceSnapshot(_Base):
    id: str = Field(default_factory=lambda: new_id("src"))
    origin: SourceOrigin
    kind: Literal["lyrics", "lrc", "translation", "romanization", "readings", "project"] = "lyrics"
    filename: Optional[str] = None
    url: Optional[str] = None
    platform_song_id: Optional[str] = None
    text: str
    sha256: str = ""
    fetched_meta: dict[str, Any] = Field(default_factory=dict)
    created: str = Field(default_factory=utcnow)


class AiRoundtrip(_Base):
    id: str = Field(default_factory=lambda: new_id("ai"))
    created: str = Field(default_factory=utcnow)
    snapshot_id: str  # lyrics snapshot identifier embedded in the prompt
    text_revision: str
    reading_revision: str
    line_ids: list[str]
    prompt: str
    response_raw: Optional[str] = None
    status: Literal["prompted", "validated", "applied", "rejected"] = "prompted"
    report: dict[str, Any] = Field(default_factory=dict)
    applied_at: Optional[str] = None


MIX_LIMITS = {"vocal_keep_pct": (0.0, 100.0), "instrumental_pct": (0.0, 100.0), "master": (0.0, 4.0)}


class MixSettings(_Base):
    vocal_keep_pct: float = Field(default=100.0, ge=0.0, le=100.0, allow_inf_nan=False)
    instrumental_pct: float = Field(default=100.0, ge=0.0, le=100.0, allow_inf_nan=False)
    master: float = Field(default=1.0, ge=0.0, le=4.0, allow_inf_nan=False)
    limiter: Literal["none", "normalize_peak"] = "normalize_peak"

    @model_validator(mode="before")
    @classmethod
    def _drop_unusable(cls, data: Any) -> Any:
        # a project saved before these ranges existed still loads: an unusable stored value goes back to
        # its default (new input is checked strictly by the service before it gets here)
        if isinstance(data, dict):
            data = dict(data)
            for k, (lo, hi) in MIX_LIMITS.items():
                v = data.get(k)
                if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool)
                                          and math.isfinite(v) and lo <= v <= hi):
                    data.pop(k)
            if data.get("limiter") not in (None, "none", "normalize_peak"):
                data.pop("limiter")
        return data


class VideoAsset(_Base):
    """A video uploaded as the original; its audio track became the original asset."""

    id: str = Field(default_factory=lambda: new_id("v"))
    sha256: Sha256
    path: Optional[str] = None  # relative to the project directory, None when missing
    filename: Optional[str] = None
    container: str  # file extension, e.g. ".mp4"
    duration_ms: int
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    # original audio stream start relative to the file start (restored when muxing)
    audio_offset_s: float = 0.0
    # width / height are the displayed (rotation applied) size; False on videos imported before that
    upright: bool = False
    # sha256 of the audio extracted from it (= the original asset it produced)
    audio_sha256: Sha256


class BackgroundAsset(_Base):
    """A picture, or a video played in a loop, shown behind the karaoke subtitles instead of a video
    of the song (kara_align.karaoke.background)."""

    id: str = Field(default_factory=lambda: new_id("b"))
    sha256: Sha256
    path: str  # relative to the project directory (content-addressed in assets/)
    filename: Optional[str] = None
    kind: Literal["image", "video"]
    width: int
    height: int
    duration_ms: Optional[int] = None  # video only


# ---------------------------------------------------------------------------
# Karaoke subtitle style (ASS). Pixel values are defined for a frame 1920 px wide
# and scaled by the actual video width (same share of the width at any resolution).
#
# Every colour is ``#RRGGBB`` ("" only where it means "follow another colour") and every
# number has a range (the editor's slider limits): these values go straight into ASS tags.
# Styles are stored in projects, the preset library and the settings; a value there that
# no longer fits (hand-edited, an older version's range) is clamped / replaced by its
# default when loaded, so a style never makes them unloadable.  Validating with the
# context ``{"strict": True}`` (a style sent to be saved) rejects such values instead.
# ---------------------------------------------------------------------------

COLOR_RE = r"^#[0-9A-Fa-f]{6}$"
COLOR_OR_EMPTY_RE = r"^(#[0-9A-Fa-f]{6})?$"
_FONT_MAX = 200
_DROP = object()  # _lenient_value(): use the field's default


def _color(default: str, *, follow: bool = False) -> Any:
    return Field(default=default, pattern=COLOR_OR_EMPTY_RE if follow else COLOR_RE)


def _font() -> Any:
    return Field(default="", max_length=_FONT_MAX)


def _lenient_value(field: Any, value: Any) -> Any:
    """``value`` made to fit ``field`` (clamped, fixed up), or ``_DROP`` for the field's default."""
    import math
    import re
    import typing

    ann = field.annotation
    args = typing.get_args(ann)
    if type(None) in args:  # Optional[X]
        if value is None:
            return value
        ann = next(a for a in args if a is not type(None))
        args = typing.get_args(ann)
    meta = field.metadata
    ge = next((m.ge for m in meta if hasattr(m, "ge")), None)
    le = next((m.le for m in meta if hasattr(m, "le")), None)
    pattern = next((m.pattern for m in meta if getattr(m, "pattern", None)), None)
    max_len = next((m.max_length for m in meta if getattr(m, "max_length", None)), None)
    if ann is bool:
        return value if isinstance(value, bool) or value in (0, 1) else _DROP
    if ann in (int, float):
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                return _DROP
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return _DROP
        value = value if ge is None else max(ge, value)
        value = value if le is None else min(le, value)
        return int(round(value)) if ann is int else float(value)
    if typing.get_origin(ann) is Literal:
        return value if value in args else _DROP
    if typing.get_origin(ann) is list and args and typing.get_origin(args[0]) is Literal:
        if not isinstance(value, list):
            return _DROP
        ok = typing.get_args(args[0])
        return list(dict.fromkeys(v for v in value if isinstance(v, str) and v in ok))
    if ann is str:
        if not isinstance(value, str):
            return _DROP
        if pattern in (COLOR_RE, COLOR_OR_EMPTY_RE):
            v = value.strip()
            if re.fullmatch(r"#?[0-9A-Fa-f]{3}", v):  # #FFF
                v = "#" + "".join(c * 2 for c in v.lstrip("#"))
            elif re.fullmatch(r"[0-9A-Fa-f]{6}", v):
                v = "#" + v
            return v if re.fullmatch(pattern, v) else _DROP
        if max_len == _FONT_MAX:  # a font family name: must not break the ASS syntax
            value = re.sub(r"[,{}\\\x00-\x1f  ]", "", value).strip()
        if max_len is not None:
            value = value[:max_len]
        return value if pattern is None or re.fullmatch(pattern, value) else _DROP
    return value


class _KaraokeBase(_Base):
    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data: Any, info: Any) -> Any:
        """Clamp / fix what a stored style carries (see the section comment); when strict only
        colours are written out (#F80 → #FF8800), anything else that does not fit is an error."""
        if not isinstance(data, dict):
            return data
        strict = (info.context or {}).get("strict")
        out = dict(data)
        for name, field in cls.model_fields.items():
            if name not in out:
                continue
            if strict:
                if isinstance(out[name], str) and any(getattr(m, "pattern", None) in (COLOR_RE, COLOR_OR_EMPTY_RE)
                                                      for m in field.metadata):
                    v = _lenient_value(field, out[name])
                    out[name] = out[name] if v is _DROP else v
                continue
            if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel):
                if not isinstance(out[name], (dict, BaseModel)):
                    del out[name]  # not an object: the default part
                continue
            v = _lenient_value(field, out[name])
            if v is _DROP:
                del out[name]
            else:
                out[name] = v
        return out


class KaraokeText(_KaraokeBase):
    font: str = _font()  # font family; "" = best available Japanese font
    size: int = Field(default=88, ge=24, le=200)
    bold: bool = True
    color_unsung: str = _color("#FFFFFF")
    color_sung: str = _color("#2F80ED")
    outline_color: str = _color("#0B1F3A")
    outline: float = Field(default=4.5, ge=0, le=16)
    shadow: float = Field(default=2.0, ge=0, le=16)
    shadow_color: str = _color("#000000")
    shadow_opacity: int = Field(default=45, ge=0, le=100)  # %


class KaraokeRuby(_KaraokeBase):
    enabled: bool = True
    script: Literal["hiragana", "katakana", "romaji"] = "hiragana"
    target: Literal["kanji", "all"] = "kanji"
    size_pct: int = Field(default=45, ge=20, le=80)  # of the lyric size
    gap: int = Field(default=2, ge=-20, le=60)  # px between ruby and lyric
    fit: Literal["widen", "overflow"] = "widen"
    # "own": each reading syllable sweeps on its own timing; "base": the sung part of the ruby is exactly
    # the part above the sung part of the lyric (one sweep line through both)
    sweep: Literal["own", "base"] = "own"
    follow_colors: bool = True
    font: str = _font()  # "" = same as the lyric font
    color_unsung: str = _color("#FFFFFF")
    color_sung: str = _color("#2F80ED")
    outline_color: str = _color("#0B1F3A")
    outline: float = Field(default=3.0, ge=0, le=12)


class KaraokeTranslation(_KaraokeBase):
    """Translation subtitle (shown when the lyrics carry translations)."""

    enabled: bool = False
    # opposite: one line at the other edge of the frame (top when lyrics are at the
    # bottom); block: one line just outside the lyric block; line: under each lyric line
    position: Literal["opposite", "block", "line"] = "opposite"
    size_pct: int = Field(default=60, ge=20, le=100)  # of the lyric size
    font: str = _font()  # "" = same as the lyric font
    bold: bool = True
    color: str = _color("#FFFFFF")
    outline_color: str = _color("#0B1F3A")
    outline: float = Field(default=3.0, ge=0, le=12)
    shadow: float = Field(default=1.5, ge=0, le=12)
    glow: bool = True  # also glow when the glow effect is on
    # with singers: the glow takes the colours of whoever sings the line (several: blended from left to
    # right, in the order they sing); the text keeps its own colour.  Off: the translation's own look only
    singer_glow: bool = True


class KaraokeGlow(_KaraokeBase):
    """Soft glowing edge around the text (a blurred wide border under it)."""

    enabled: bool = False
    color_unsung: str = _color("#FF8AC2")
    color_sung: str = _color("#FFF2B3")  # the glow changes colour as each syllable is sung
    size: float = Field(default=9.0, ge=1, le=40)  # px at 1920 wide
    blur: float = Field(default=7.0, ge=0, le=30)
    strength: int = Field(default=85, ge=10, le=100)  # %
    ruby: bool = True  # glow the ruby too


class KaraokeLayout(_KaraokeBase):
    position: Literal["bottom", "top"] = "bottom"
    lines: int = Field(default=2, ge=1, le=3)
    arrangement: Literal["alternate", "center"] = "alternate"
    margin_v: int = Field(default=70, ge=0, le=400)  # px from the top / bottom edge
    line_spacing: int = Field(default=26, ge=0, le=200)  # px between stacked lines
    margin_h: int = Field(default=140, ge=0, le=600)  # px left and right (the widest a line may get)
    # alternating lines: extra inset toward the centre for lines that fit, so two
    # short lines are not pinned to opposite edges (long lines use the full width)
    alternate_indent: int = Field(default=240, ge=0, le=800)
    # a line wider than the room between the margins: "auto" splits it in two (or more) at a space
    # or punctuation (else between words), "ai" prefers the break the AI readings suggested
    # (Segment.wrap_before), "off" keeps it whole.  The halves take turns in the rows like lines.
    wrap: Literal["off", "auto", "ai"] = "auto"
    # a line that is still too wide may reach this close to the frame's edges (px at 1920 wide)
    # before it is shrunk
    edge_margin: int = Field(default=50, ge=0, le=400)
    shrink_long_lines: bool = True  # scale down lines wider than the frame


class KaraokeTiming(_KaraokeBase):
    lead_in_ms: int = Field(default=1000, ge=0, le=8000)  # line appears at least this long before its first syllable
    hold_ms: int = Field(default=500, ge=0, le=5000)  # and stays after its last one
    # show the next line as soon as its slot is free (at most early_max_ms ahead)
    early_show: bool = True
    early_max_ms: int = Field(default=4000, ge=1000, le=10000)
    highlight: Literal["sweep", "instant"] = "sweep"  # \kf or \k
    # show (and highlight) the lyrics this much before they are sung; 0 = off.  On by default: a
    # syllable's sweep runs over its whole length, so it only looks "sung" about half-way through
    # (≈ 100 ms for a typical mora); starting a little early makes the sweep feel on time.
    # Applies to every subtitle / LRC export, never to the alignment data itself.
    advance_ms: int = Field(default=150, ge=0, le=2000)
    fade_in_ms: int = Field(default=200, ge=0, le=2000)  # lines ease in / out
    fade_out_ms: int = Field(default=200, ge=0, le=2000)


class KaraokeEffects(_KaraokeBase):
    """Effects around the lyrics, fired by each syllable as it is sung (see karaoke.effects)."""

    kind: Literal["none", "pulse", "ring", "shine", "sparkle", "petals", "hearts", "ball"] = "none"
    amount: int = Field(default=100, ge=20, le=200)  # % (how many particles per syllable)
    size: int = Field(default=100, ge=40, le=250)  # %
    color: str = _color("", follow=True)  # "" = the sung glow colour when the glow is on, else the sung lyric colour
    ruby: bool = False  # also fire on the ruby syllables
    # particles (stars, petals, hearts, the ball) drawn behind the lyrics so they never cover a glyph;
    # off: in front of the lyrics and ruby
    behind: bool = True

    @model_validator(mode="before")
    @classmethod
    def _from_screen_effects(cls, data):
        """The first version had full-screen particles; map them to the nearest lyric effect."""
        if isinstance(data, dict) and "kind" not in data and "particles" in data:
            data = {**data, "kind": {"sakura": "petals", "stars": "sparkle"}.get(data.get("particles"), "none")}
        return data


class KaraokeCountdown(_KaraokeBase):
    """Dots above a line counting down to its first syllable (the last one goes as its sweep starts):
    before the first line and after a long pause.  Time based: in the last ``dots`` seconds one dot
    goes each second (evenly over a shorter wait).  A line can say otherwise (``Line.countdown``)."""

    intro: bool = True  # before the first line
    interlude: bool = True  # before a line after a pause of at least min_gap_ms
    min_gap_ms: int = Field(default=6000, ge=2000, le=30000)
    dots: int = Field(default=3, ge=2, le=5)


class KaraokeOutput(_KaraokeBase):
    # "reduced vocals" audio for burn-in: vocals at this %, instrumental at 100 %
    # (independent of the Export page's mix, which comes later in the flow)
    vocal_keep_pct: float = Field(default=20.0, ge=0.0, le=100.0)


SongInfoField = Literal["title", "artist", "album", "lyricist", "composer", "arranger"]


class KaraokeSongInfo(_KaraokeBase):
    """Song title card shown in a top corner at the start, and again at the end (see karaoke.info).

    Which lines it shows is part of the style; a project can replace the text
    with its own (``Project.song_info_text``)."""

    enabled: bool = False
    position: Literal["top-left", "top-right"] = "top-left"
    fields: list[SongInfoField] = Field(default_factory=lambda: ["title", "artist"])
    start_ms: int = Field(default=500, ge=0, le=60000)  # audio time
    duration_ms: int = Field(default=7000, ge=1000, le=60000)
    # the same card again at the end of the song (on whenever the card is), until the song ends
    outro: bool = True
    outro_duration_ms: int = Field(default=7000, ge=1000, le=60000)
    size: int = Field(default=56, ge=20, le=160)  # title size at 1920 wide; other lines are smaller
    margin: int = Field(default=56, ge=0, le=400)  # from the top and side edges
    color: str = _color("", follow=True)  # "" = the lyrics' unsung colour
    accent: str = _color("", follow=True)  # accent bar; "" = the lyrics' sung colour


class KaraokeSinger(_KaraokeBase):
    """One singer's colours.  Only ``color`` is needed: every "" colour is derived from it the way
    a colour template derives a style (themes.singer_colors)."""

    name: str = Field(default="", max_length=40)
    # the key that assigns this singer on the 演唱者 page ("" = none)
    key: str = Field(default="", max_length=1)
    color: str = _color("#ED35B3")
    color_unsung: str = _color("", follow=True)
    color_sung: str = _color("", follow=True)
    outline_color: str = _color("", follow=True)
    glow_unsung: str = _color("", follow=True)
    glow_sung: str = _color("", follow=True)


SingerMix = Literal["split", "gradient"]
SingerDirection = Literal["vertical", "horizontal"]


class KaraokeSingerCombo(_KaraokeBase):
    """Singers who often sing together: key ``key`` on the 演唱者 page assigns ``singers`` (e.g.
    3 = 1+2; a key no singer and no other combination has).  Parts sung by these singers look as
    ``mix`` / ``direction`` say (None: as the singers' own setting, KaraokeSingers.look)."""

    key: str = Field(default="", max_length=1)
    singers: list[int] = Field(default_factory=list)
    mix: Optional[SingerMix] = None
    direction: Optional[SingerDirection] = None

    @model_validator(mode="before")
    @classmethod
    def _clean(cls, data: Any) -> Any:
        if isinstance(data, dict) and "singers" in data:
            data = {**data, "singers": _singer_ids(data["singers"])}
        if isinstance(data, dict) and isinstance(data.get("key"), int):
            data = {**data, "key": str(data["key"])}  # (keys were numbers 1–9 before)
        return data


class KaraokeSingers(_KaraokeBase):
    """Singers for songs with several voices (the 演唱者 page); which lines / words each one sings is
    kept in the lyrics (``Line.singers``, ``Line.singer_spans``), by number."""

    members: list[KaraokeSinger] = Field(default_factory=list)
    # parts sung together (unless a combination of the same singers says otherwise): each singer's
    # colours in its own band (split) or blended (gradient)
    mix: SingerMix = "split"
    # vertical: top to bottom (upper half 1, lower half 2); horizontal: left to right across each run
    # sung together
    direction: SingerDirection = "vertical"
    # the reading over a part sung together: "split" like its lyric, "first" in the first singer's
    # colours, "auto": the first singer's when split top to bottom (too small for bands), else split
    ruby: Literal["auto", "split", "first"] = "auto"
    # singers who sing together: a key, and their own look
    combos: list[KaraokeSingerCombo] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _members(cls, data: Any, info: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if not (info.context or {}).get("strict"):
            for key in ("members", "combos"):
                if key in data:
                    m = data[key]
                    data = {**data, key: [x for x in m if isinstance(x, (dict, BaseModel))] if isinstance(m, list) else []}
        # saved before singers had their own keys: singer n was key n
        members = data.get("members")
        if isinstance(members, list):
            data = {**data, "members": [{**m, "key": str(i + 1) if i < 9 else ""} if isinstance(m, dict) and "key" not in m else m
                                        for i, m in enumerate(members)]}
        return data

    @model_validator(mode="after")
    def _keys(self, info: Any) -> "KaraokeSingers":
        """Every key (of singers and combinations) is one of SINGER_KEYS and used once; a combination
        needs at least two of the singers.  When loading, what does not fit loses its key (a
        combination: is dropped); when saving it is refused."""
        strict = (info.context or {}).get("strict")
        seen: dict[str, str] = {}

        def check(raw: str, owner: str) -> str:
            k = singer_key(raw)
            problem = (f"{owner}的快捷键「{raw}」不能用（可以用 1–9、A–Z，L 和 P 除外）" if raw and not k
                       else f"快捷键 {k.upper()} 同时给了{seen[k]}和{owner}" if k in seen else None)
            if problem:
                if strict:
                    raise ValueError(problem)
                return ""
            if k:
                seen[k] = owner
            return k

        members = [m.model_copy(update={"key": check(m.key, f"第 {i + 1} 位演唱者")}) for i, m in enumerate(self.members)]
        n = len(members)
        keep = []
        combos_seen: set[tuple[int, ...]] = set()
        for c in self.combos:
            ids = [i for i in c.singers if i <= n]
            name = "组合 " + "+".join(map(str, ids))
            problem = (f"组合 {c.key.upper() or '（无快捷键）'} 至少要有两位演唱者" if len(ids) < 2
                       else f"{name}已经有了" if tuple(ids) in combos_seen else None)
            if problem:
                if strict:
                    raise ValueError(problem)
                continue
            combos_seen.add(tuple(ids))
            keep.append(c.model_copy(update={"singers": ids, "key": check(c.key, name)}))
        self.members, self.combos = members, keep
        return self

    def look(self, ids: tuple[int, ...] | list[int]) -> tuple[str, str]:
        """(mix, direction) of a part sung by ``ids``: a combination of exactly these singers (in this
        order, else in any order) sets its own, the rest as the singers' setting."""
        ids = tuple(ids)
        combo = next((c for c in self.combos if tuple(c.singers) == ids), None) \
            or next((c for c in self.combos if sorted(c.singers) == sorted(ids)), None)
        return ((combo.mix if combo and combo.mix else self.mix),
                (combo.direction if combo and combo.direction else self.direction))

    def ruby_split(self, direction: str) -> bool:
        """Is the reading over a part sung together split like its lyric (else: the first singer's)?"""
        return self.ruby == "split" or (self.ruby == "auto" and direction == "horizontal")

    def free_key(self) -> str:
        """The first key (in SINGER_KEYS order) no singer or combination has; "" when all are taken."""
        used = {m.key for m in self.members} | {c.key for c in self.combos}
        return next((k for k in SINGER_KEYS if k not in used), "")


class KaraokeTheme(_KaraokeBase):
    """The colour template a style's colours came from (karaoke.themes).  The editor clears it
    as soon as a colour or effect is changed by hand ("自定义")."""

    template: Literal["plain", "glow"]
    color: str = Field(pattern=COLOR_RE)
    secondary: str = _color("", follow=True)


class KaraokeStyle(_KaraokeBase):
    version: int = 2
    preset: str = Field(default="", max_length=400)  # name of the saved style it was loaded from ("" = none)
    layout: KaraokeLayout = Field(default_factory=KaraokeLayout)
    text: KaraokeText = Field(default_factory=KaraokeText)
    ruby: KaraokeRuby = Field(default_factory=KaraokeRuby)
    translation: KaraokeTranslation = Field(default_factory=KaraokeTranslation)
    glow: KaraokeGlow = Field(default_factory=KaraokeGlow)
    timing: KaraokeTiming = Field(default_factory=KaraokeTiming)
    effects: KaraokeEffects = Field(default_factory=KaraokeEffects)
    info: KaraokeSongInfo = Field(default_factory=KaraokeSongInfo)
    theme: Optional[KaraokeTheme] = None  # None = colours set by hand (or a preset)
    output: KaraokeOutput = Field(default_factory=KaraokeOutput)
    singers: KaraokeSingers = Field(default_factory=KaraokeSingers)
    countdown: KaraokeCountdown = Field(default_factory=KaraokeCountdown)

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data: Any, info: Any) -> Any:
        """v1 kept the translation switches in ``layout`` and had built-in preset names.
        When loading (not strict), a theme that no longer validates is dropped: the colours
        stay, the style just shows as set by hand."""
        if not isinstance(data, dict):
            return data
        if not (info.context or {}).get("strict"):
            data = dict(data)
            try:
                data["version"] = int(data.get("version") or 1)
            except (TypeError, ValueError):
                data["version"] = 2
            if data.get("theme") is not None:
                try:
                    KaraokeTheme.model_validate(data["theme"], context={"strict": True})
                except Exception:
                    data["theme"] = None
        if int(data.get("version") or 1) >= 2:
            return data
        data = dict(data)
        lay = dict(data.get("layout") or {})
        tr = dict(data.get("translation") or {})
        for old, new in (("show_translation", "enabled"), ("translation_position", "position"),
                         ("translation_size_pct", "size_pct")):
            if old in lay:
                tr.setdefault(new, lay.pop(old))
        text = data.get("text") or {}
        tr.setdefault("color", text.get("color_unsung", "#FFFFFF"))
        tr.setdefault("outline_color", text.get("outline_color", "#0B1F3A"))
        data["layout"], data["translation"] = lay, tr  # fades: the new defaults apply
        if data.get("preset") in ("classic", "fresh", "sakura", "minimal", "custom"):
            data["preset"] = ""
        data["version"] = 2
        return data


class Project(_Base):
    format: str = FMT_PROJECT
    version: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("p"), pattern=PROJECT_ID_PATTERN)
    name: str = "untitled"
    created: str = Field(default_factory=utcnow)
    updated: str = Field(default_factory=utcnow)
    mode: AlignMode = "plain"
    lyrics: LyricsDoc = Field(default_factory=LyricsDoc)
    sources: list[SourceSnapshot] = Field(default_factory=list)
    calibration: Calibration = Field(default_factory=Calibration)
    ai_roundtrips: list[AiRoundtrip] = Field(default_factory=list)
    config: AlignConfig = Field(default_factory=AlignConfig)
    audio: list[AudioAsset] = Field(default_factory=list)
    results: list[AlignmentResult] = Field(default_factory=list)
    active_result_id: Optional[str] = None
    mix: MixSettings = Field(default_factory=MixSettings)
    video: Optional[VideoAsset] = None
    # shown behind the subtitles instead of the video (or of black) when set
    background: Optional[BackgroundAsset] = None
    karaoke: KaraokeStyle = Field(default_factory=KaraokeStyle)
    # the karaoke title card's own text (one line each; the first is the title); None = from the song data
    song_info_text: Optional[str] = None

    def asset(self, role: str) -> Optional[AudioAsset]:
        for a in self.audio:
            if a.role == role:
                return a
        return None

    def result(self, result_id: Optional[str] = None) -> Optional[AlignmentResult]:
        rid = result_id or self.active_result_id
        for r in self.results:
            if r.id == rid:
                return r
        return None
