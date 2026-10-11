"""External AI round trip (web chat copy/paste only, no LLM API).

* :func:`build_prompt` – prompt text with current lyrics, line ids, existing
  readings and the exact return format, plus a snapshot identifier.
* :func:`extract_json` – pull the JSON object out of a chat reply.
* :func:`validate_patch` – treat the reply as untrusted data and validate it
  against the *current* document, producing a preview.
* :func:`apply_patch` – apply the valid lines as a reading patch.

The snapshot identity is based on lyrics text and readings only, never on
calibration / offsets, so changing the LRC offset does not block a patch and
a patch can never restore an old offset (it cannot carry times at all).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional, Sequence

from ..models import FMT_READING_PATCH, AiRoundtrip, Line, LyricsDoc, Segment, stable_hash
from .japanese import is_kana, is_kana_text, letter_name, split_morae, to_hiragana
from .prepare import _assign_surfaces, units_from_spec

MAX_REPLY_CHARS = 2_000_000
PATCH_VERSION = 1

_TIME_KEY_RE = re.compile(
    r"^(start|end|begin|time|times|timestamp|timestamps|ms|start_ms|end_ms|offset|offset_ms|duration|"
    r"duration_ms|t|t0|t1|sec|seconds|anchor)$",
    re.I,
)
_TIME_KEY_PART_RE = re.compile(r"(^|_)(start|end|time|timestamp|offset|duration|ms)(_|$)", re.I)
_OMISSION_RE = re.compile(r"^\s*[\(（\[]?\s*(同上|同前|略|repeat|x\s*\d+|×\s*\d+|…+|\.{3,})\s*[\)）\]]?\s*$", re.I)


def line_reading_hash(line: Line) -> str:
    return stable_hash([line.text, [(s.surface, s.reading, [u.reading for u in s.units], s.confirmed)
                                    for s in line.segments]])


def snapshot_id_for(doc: LyricsDoc, line_ids: Sequence[str]) -> str:
    return "snap-" + stable_hash([doc.text_revision(), doc.reading_revision(), list(line_ids)], 12)


@dataclass
class PromptBundle:
    prompt: str
    snapshot_id: str
    roundtrip: AiRoundtrip


def _is_locked(seg: Segment) -> bool:
    return seg.confirmed or seg.reading_source == "manual"


def _line_payload(line: Line) -> dict[str, Any]:
    segs = []
    for s in line.segments:
        item: dict[str, Any] = {"surface": s.surface}
        if s.reading:
            item["reading"] = s.reading
            item["units"] = [u.reading for u in s.units]
        if _is_locked(s):
            item["locked"] = True
        if s.uncertain:
            item["uncertain"] = True
        segs.append(item)
    return {"id": line.id, "text": line.text, "current_segments": segs}


def build_prompt(doc: LyricsDoc, line_ids: Optional[Sequence[str]] = None, lang: str = "ja") -> PromptBundle:
    if line_ids is None:
        lines = doc.sung_lines()
    else:
        wanted = set(line_ids)
        lines = [ln for ln in doc.lines if ln.id in wanted]
    ids = [ln.id for ln in lines]
    snap = snapshot_id_for(doc, ids)
    payload = [_line_payload(ln) for ln in lines]
    example = {
        "format": FMT_READING_PATCH,
        "version": PATCH_VERSION,
        "snapshot": snap,
        "lines": [{
            "id": "<行ID>",
            "text": "<与输入完全相同的原文>",
            "segments": [
                {"surface": "君", "reading": "きみ", "units": ["き", "み"], "candidates": [], "uncertain": False},
                {"surface": "と", "reading": "と", "units": ["と"], "candidates": [], "uncertain": False},
            ],
        }],
    }
    lang_note = {
        "ja": "歌词为日语。reading 使用平假名（外来语也写平假名或片假名均可），units 按拍（mora）切分：拗音（きゃ）合为一拍，促音っ、拨音ん、长音ー各自单独一拍。",
        "zh": "歌词为中文。reading 使用不带声调的拼音，每个汉字一个 unit。",
        "en": "Lyrics are English. reading is the lowercase word, one unit per word.",
    }.get(lang, "")
    rules = [
        "原样保留每行的 id、text 和行的顺序；不得增删、合并、拆分或改写任何歌词行。",
        "每行所有 segments 的 surface 按顺序拼接后必须与该行 text 完全一致（包括标点和空格）。",
        "segments 按词切分：汉字词连同它的送假名是一个 segment（好き、始まり、震える、聞いた，不要切成 好／き…），"
        "助词单独成段（に、を、は）；熟字训、当て字等只能整体读的词作为一个 segment（真新＝まっさら，不要拆成 真／新）。"
        "current_segments 的切分只是程序给的参考，可以合并或重新切分（locked 片段除外）。",
        "reading 写实际发音（助词は读作わ时写わ，へ读作え时写え），units 拼接后必须等于 reading。",
        "逐个念出的拉丁字母（如 R O M A N T I C）：每个字母单独一个 segment，reading 写字母名的读法"
        "（R＝あーる、M＝えむ、C＝しー、W＝だぶりゅー），整个字母只算一个 unit（units 为 [\"あーる\"]），不要按拍拆开。"
        "日语歌词里的英文单词按歌里实际的唱法写成假名（now＝なう、friends＝ふれんず），units 照常按拍切分。",
        "用阿拉伯数字写的数字也要注音，按歌里实际的唱法写成假名（24＝にじゅうよん 或 にじゅうよ，3＝さん，"
        "英语唱法 1＝わん），units 按拍切分；current_segments 里数字的读音只是常见读法，不一定是歌里的唱法。",
        "卡拉OK字幕里一行放不下时需要折成两行：歌词行较长（约 18 个字以上）时，在最适合换行的位置（意思和节奏的停顿处），"
        "给从新一行开始的那个 segment 加上 \"wrap\": true（每行最多一处）；短的行不要加。",
        "重复的副歌也必须逐行完整输出，禁止用“同上”“略”“x2”等省略。",
        "不要输出任何时间、时间戳、偏移或时长字段；不要猜测时间。",
        "不要为了表现拖长演唱而新增元音或长音（例如不要把「空」写成そおおら）。",
        "不要输出罗马字或音素；模型用的拼写由程序按固定规则生成。",
        "读音不确定时，uncertain 设为 true，并在 candidates 中给出其他可能读音（平假名）。",
        "标记为 locked 的片段是人工确认的读音，请原样照抄，不要修改。",
        "只返回一个 JSON 对象（可放在 ```json 代码块中），不要附加解释。",
    ]
    prompt = (
        "你是日语/中文歌词注音助手。请为下面的歌词逐行标注读音，并按指定 JSON 格式返回。\n"
        "You are a lyrics reading annotator. Annotate readings line by line and return JSON exactly in the format below.\n\n"
        f"{lang_note}\n\n"
        "规则 / Rules:\n" + "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules)) + "\n\n"
        f"返回格式（snapshot 必须原样填写 \"{snap}\"）/ Return format:\n"
        "```json\n" + json.dumps(example, ensure_ascii=False, indent=2) + "\n```\n\n"
        f"待标注歌词（共 {len(lines)} 行；current_segments 为程序已有的切分和读音，仅供参考：非 locked 部分可以重新切分和修正）/ Lyrics:\n"
        "```json\n" + json.dumps({"snapshot": snap, "lines": payload}, ensure_ascii=False, indent=1) + "\n```\n"
    )
    rt = AiRoundtrip(
        snapshot_id=snap,
        text_revision=doc.text_revision(),
        reading_revision=doc.reading_revision(),
        line_ids=ids,
        prompt=prompt,
        report={"line_hashes": {ln.id: line_reading_hash(ln) for ln in lines},
                "line_texts": {ln.id: ln.text for ln in lines}},
    )
    return PromptBundle(prompt=prompt, snapshot_id=snap, roundtrip=rt)


class PatchParseError(ValueError):
    pass


def extract_json(text: str) -> Any:
    """Extract the first JSON object from a chat reply.  Never evaluates code."""
    if text is None:
        raise PatchParseError("回传内容为空")
    if len(text) > MAX_REPLY_CHARS:
        raise PatchParseError(f"回传内容过大（{len(text)} 字符 > {MAX_REPLY_CHARS}）")
    t = text.strip().lstrip("﻿")
    candidates: list[str] = []
    for m in re.finditer(r"```(?:json|JSON)?\s*\n?(.*?)```", t, re.S):
        candidates.append(m.group(1).strip())
    candidates.append(t)
    decoder = json.JSONDecoder()
    last_err: Optional[Exception] = None
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError as e:
            last_err = e
        # scan for the first decodable object starting at a '{'
        for m in re.finditer(r"\{", c):
            try:
                obj, _ = decoder.raw_decode(c, m.start())
                if isinstance(obj, dict):
                    return obj
            except ValueError as e:
                last_err = e
    raise PatchParseError(f"回传中没有找到 JSON 对象：{last_err}")


@dataclass
class SegmentDiff:
    surface: str
    old_reading: Optional[str]
    new_reading: Optional[str]
    old_units: list[str]
    new_units: list[str]
    changed: bool
    locked: bool = False


@dataclass
class LinePatchResult:
    line_id: str
    status: str  # ok | stale_text | stale_reading | unknown_line | locked_skipped | invalid | duplicate
    reasons: list[str] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)  # normalized patch segments
    diff: list[SegmentDiff] = field(default_factory=list)

    @property
    def applicable(self) -> bool:
        return self.status == "ok"


@dataclass
class PatchReport:
    ok: bool
    snapshot: Optional[str]
    roundtrip_id: Optional[str]
    errors: list[str] = field(default_factory=list)  # whole-patch errors
    warnings: list[str] = field(default_factory=list)
    lines: list[LinePatchResult] = field(default_factory=list)
    missing_line_ids: list[str] = field(default_factory=list)

    def applicable_ids(self) -> list[str]:
        return [lr.line_id for lr in self.lines if lr.applicable]

    def to_dict(self) -> dict:
        return asdict(self)


def _find_time_keys(obj: Any, path: str = "") -> list[str]:
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            if _TIME_KEY_RE.match(ks) or _TIME_KEY_PART_RE.search(ks):
                found.append(f"{path}.{ks}" if path else ks)
            found.extend(_find_time_keys(v, f"{path}.{ks}" if path else ks))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_find_time_keys(v, f"{path}[{i}]"))
    return found


_PARTICLE_OK = {("は", "わ"), ("へ", "え"), ("を", "お")}


def _kana_reading_ok(surface: str, reading: str) -> Optional[str]:
    """For pure-kana surfaces the reading must match (particle substitutions allowed)."""
    s = [m.text for m in split_morae(surface)]
    r = [m.text for m in split_morae(reading)]
    if len(r) > len(s):
        return f"读音比原文多出拍数（{surface} -> {reading}），疑似为拖长演唱新增元音"
    if len(r) != len(s):
        return f"假名原文与读音拍数不一致（{surface} -> {reading}）"
    for a, b in zip(s, r):
        if a != b and (a, b) not in _PARTICLE_OK and not (a == "ー"):
            return f"假名原文与读音不一致（{surface} -> {reading}）"
    return None


def _validate_segments(line: Line, raw_segs: Any, reasons: list[str]) -> list[dict]:
    if not isinstance(raw_segs, list) or not raw_segs:
        reasons.append("segments 缺失或不是数组")
        return []
    out = []
    for i, rs in enumerate(raw_segs):
        if not isinstance(rs, dict):
            reasons.append(f"segment[{i}] 不是对象")
            continue
        surface = rs.get("surface")
        if not isinstance(surface, str):
            reasons.append(f"segment[{i}].surface 缺失")
            continue
        reading = rs.get("reading")
        units = rs.get("units")
        cands = rs.get("candidates") or []
        if reading is not None and not isinstance(reading, str):
            reasons.append(f"segment[{i}].reading 不是字符串")
            continue
        if units is not None and (not isinstance(units, list) or not all(isinstance(u, str) and u for u in units)):
            reasons.append(f"segment[{i}].units 必须是非空字符串数组")
            continue
        if not isinstance(cands, list) or not all(isinstance(c, str) for c in cands):
            reasons.append(f"segment[{i}].candidates 必须是字符串数组")
            continue
        reading = (reading or "").strip()
        has_jp = any(is_kana(c) or (0x3400 <= ord(c) <= 0x9FFF) for c in surface)
        is_latin = bool(re.search(r"[A-Za-z]", surface)) and not has_jp
        lang = "ja"
        if reading:
            if is_kana_text(reading):
                reading = to_hiragana(reading)
                units = [to_hiragana(u) for u in units] if units else None
                cands = [to_hiragana(c) for c in cands]
                if is_kana_text(surface):
                    err = _kana_reading_ok(surface, reading)
                    if err:
                        reasons.append(f"segment[{i}]: {err}")
                        continue
            elif is_latin and re.fullmatch(r"[a-z' ]+", reading.lower()):
                lang = "en"
                reading = reading.lower()
            else:
                reasons.append(f"segment[{i}] 读音必须是假名（{surface} -> {reading}）")
                continue
            if units is not None and "".join(units) != reading:
                reasons.append(f"segment[{i}] units {units} 拼接后不等于 reading {reading}")
                continue
            if lang == "ja" and letter_name(surface.strip()):
                units = [reading]  # a spelled-out letter is one unit, however the reply split it
        else:
            if has_jp:
                reasons.append(f"segment[{i}] 缺少读音（{surface}）")
                continue
            units = None
            if is_latin:
                lang = "en"
        out.append({"surface": surface, "reading": reading or None, "units": units, "lang": lang,
                    "candidates": [c for c in cands if c], "uncertain": bool(rs.get("uncertain", False)),
                    "wrap_before": rs.get("wrap") is True})
    if "".join(s["surface"] for s in out) != line.text and not reasons:
        reasons.append("surface 拼接结果与当前原文不一致")
    return out


def _spans(segs: Iterable) -> list[tuple[int, int, Any]]:
    pos, out = 0, []
    for s in segs:
        surf = s["surface"] if isinstance(s, dict) else s.surface
        out.append((pos, pos + len(surf), s))
        pos += len(surf)
    return out


def validate_patch(doc: LyricsDoc, patch_obj: Any, roundtrips: Sequence[AiRoundtrip] = ()) -> PatchReport:
    rep = PatchReport(ok=False, snapshot=None, roundtrip_id=None)
    if not isinstance(patch_obj, dict):
        rep.errors.append("回传内容不是 JSON 对象")
        return rep
    fmt = patch_obj.get("format")
    if fmt is not None and fmt != FMT_READING_PATCH:
        rep.errors.append(f"未知格式 {fmt!r}")
    ver = patch_obj.get("version", PATCH_VERSION)
    if ver != PATCH_VERSION:
        rep.errors.append(f"不支持的补丁版本 {ver!r}")
    time_keys = _find_time_keys(patch_obj)
    if time_keys:
        rep.errors.append("回传包含时间相关字段，AI 不得修改时间: " + ", ".join(time_keys[:10]))
    lines = patch_obj.get("lines")
    if not isinstance(lines, list):
        rep.errors.append("缺少 lines 数组")
    snap = patch_obj.get("snapshot")
    rep.snapshot = snap if isinstance(snap, str) else None
    rt = next((r for r in roundtrips if r.snapshot_id == rep.snapshot), None) if rep.snapshot else None
    if rt is None:
        rep.warnings.append("未找到对应的提示词快照，将逐行按当前原文校验")
    else:
        rep.roundtrip_id = rt.id
        if rt.text_revision != doc.text_revision():
            rep.warnings.append("生成提示词后歌词已变更，建议重新生成提示词；逐行按当前原文校验")
        elif rt.reading_revision != doc.reading_revision():
            rep.warnings.append("生成提示词后读音已被修改，相关行将提示重新生成")
    if rep.errors:
        return rep

    current = {ln.id: ln for ln in doc.lines}
    seen: set[str] = set()
    order: list[str] = []
    line_hashes = (rt.report.get("line_hashes", {}) if rt else {}) or {}
    for i, pl in enumerate(lines):
        if not isinstance(pl, dict):
            rep.lines.append(LinePatchResult(line_id=f"#{i}", status="invalid", reasons=["行不是对象"]))
            continue
        lid = pl.get("id")
        lid = str(lid) if lid is not None else f"#{i}"
        text = pl.get("text")
        if lid in seen:
            rep.lines.append(LinePatchResult(line_id=lid, status="duplicate", reasons=["行 ID 重复"]))
            continue
        seen.add(lid)
        order.append(lid)
        ln = current.get(lid)
        if ln is None:
            rep.lines.append(LinePatchResult(line_id=lid, status="unknown_line", reasons=["当前歌词中不存在该行 ID"]))
            continue
        if not isinstance(text, str) or _OMISSION_RE.match(text) or (
                isinstance(pl.get("segments"), list) and any(
                    isinstance(s, dict) and isinstance(s.get("surface"), str) and _OMISSION_RE.match(s["surface"])
                    for s in pl["segments"]) and not _OMISSION_RE.match(ln.text)):
            rep.lines.append(LinePatchResult(line_id=lid, status="invalid",
                                             reasons=["该行被省略（如“同上”）或缺少原文"]))
            continue
        if text != ln.text:
            rep.lines.append(LinePatchResult(line_id=lid, status="stale_text",
                                             reasons=[f"原文不一致：回传 {text!r}，当前 {ln.text!r}"]))
            continue
        if lid in line_hashes and line_hashes[lid] != line_reading_hash(ln):
            status_reading_changed = True
        else:
            status_reading_changed = False
        reasons: list[str] = []
        segs = _validate_segments(ln, pl.get("segments"), reasons)
        if reasons:
            rep.lines.append(LinePatchResult(line_id=lid, status="invalid", reasons=reasons))
            continue
        # locked segments: patch must keep the same span; their content is preserved
        locked_conflict = []
        patch_spans = {(a, b): s for a, b, s in _spans(segs)}
        for a, b, cur in _spans(ln.segments):
            if _is_locked(cur) and (a, b) not in patch_spans:
                locked_conflict.append(cur.surface)
        if locked_conflict:
            rep.lines.append(LinePatchResult(line_id=lid, status="locked_skipped", segments=segs,
                                             reasons=["补丁的切分与人工锁定片段冲突: " + "、".join(locked_conflict)]))
            continue
        diff = _diff(ln, segs)
        status = "stale_reading" if status_reading_changed else "ok"
        lr = LinePatchResult(line_id=lid, status=status, segments=segs, diff=diff)
        if status_reading_changed:
            lr.reasons.append("生成提示词后该行读音已被修改，请重新生成提示词")
        if any(d.locked and d.changed for d in diff):
            lr.reasons.append("人工锁定片段将保持原读音，不被补丁覆盖")
        rep.lines.append(lr)

    expected = rt.line_ids if rt else []
    rep.missing_line_ids = [lid for lid in expected if lid not in seen]
    if rep.missing_line_ids:
        rep.warnings.append(f"回传缺少 {len(rep.missing_line_ids)} 行（可能被省略），这些行不会更新: "
                            + ", ".join(rep.missing_line_ids[:10]))
    if expected:
        exp_order = [lid for lid in expected if lid in seen]
        got_order = [lid for lid in order if lid in set(expected)]
        if exp_order != got_order:
            rep.warnings.append("回传行顺序与提示词不一致（已按行 ID 匹配）")
    rep.ok = any(lr.applicable for lr in rep.lines)
    return rep


def _diff(line: Line, segs: list[dict]) -> list[SegmentDiff]:
    cur_spans = {(a, b): s for a, b, s in _spans(line.segments)}
    out = []
    for a, b, s in _spans(segs):
        cur = cur_spans.get((a, b))
        new_units = s["units"] if s["units"] is not None else (
            [m.text for m in split_morae(s["reading"])] if s["reading"] and s["lang"] == "ja"
            else ([s["reading"]] if s["reading"] else []))
        old_units = [u.reading for u in cur.units] if cur else []
        old_reading = cur.reading if cur else None
        locked = bool(cur and _is_locked(cur))
        out.append(SegmentDiff(surface=s["surface"], old_reading=old_reading, new_reading=s["reading"],
                               old_units=old_units, new_units=new_units,
                               changed=(old_reading != s["reading"] or old_units != new_units or cur is None),
                               locked=locked))
    return out


def apply_patch(doc: LyricsDoc, report: PatchReport, *, include_line_ids: Optional[Sequence[str]] = None,
                include_stale_reading: bool = False) -> tuple[LyricsDoc, dict]:
    """Return a new document with the applicable lines' readings replaced.

    Only segments are touched; times, anchors, offsets and line metadata are
    never modified.  Confirmed / manual segments are kept verbatim.
    """
    new_doc = doc.model_copy(deep=True)
    lines = {ln.id: ln for ln in new_doc.lines}
    allowed = set(include_line_ids) if include_line_ids is not None else None
    applied, unchanged, skipped = [], [], []
    for lr in report.lines:
        ok = lr.status == "ok" or (include_stale_reading and lr.status == "stale_reading")
        if not ok or (allowed is not None and lr.line_id not in allowed):
            skipped.append(lr.line_id)
            continue
        ln = lines[lr.line_id]
        if "".join(s["surface"] for s in lr.segments) != ln.text:
            skipped.append(lr.line_id)
            continue
        cur_spans = {(a, b): s for a, b, s in _spans(ln.segments)}
        new_segs: list[Segment] = []
        for a, b, spec in _spans(lr.segments):
            cur = cur_spans.get((a, b))
            if cur is not None and _is_locked(cur):
                # the reading stays as confirmed; the line-break hint is display only and still taken
                new_segs.append(cur.model_copy(update={"wrap_before": bool(spec.get("wrap_before"))}))
                continue
            seg = _segment_from_spec(spec)
            if cur is not None:
                seg.id = cur.id
                if [u.reading for u in cur.units] == [u.reading for u in seg.units]:
                    for nu, ou in zip(seg.units, cur.units):
                        nu.id = ou.id
            new_segs.append(seg)
        before = [s.model_dump() for s in ln.segments]
        ln.segments = new_segs
        (applied if [s.model_dump() for s in new_segs] != before else unchanged).append(lr.line_id)
    return new_doc, {"applied": applied, "unchanged": unchanged, "skipped": skipped}


def _segment_from_spec(spec: dict) -> Segment:
    reading = spec["reading"]
    lang = spec["lang"]
    if reading:
        units = units_from_spec(reading, spec["units"], lang)
        seg = Segment(surface=spec["surface"], reading=reading, lang=lang, units=units, reading_source="ai",
                      confirmed=False, uncertain=spec["uncertain"], candidates=list(spec["candidates"]),
                      wrap_before=bool(spec.get("wrap_before")))
        _assign_surfaces(seg)
        if lang == "en":
            for u in seg.units:
                u.surface = spec["surface"] if len(seg.units) == 1 else ""
    else:
        seg = Segment(surface=spec["surface"], reading=None, lang=lang, units=[], reading_source="none",
                      wrap_before=bool(spec.get("wrap_before")))
    return seg
