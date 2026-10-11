"""Reading preparation: fill Line.segments with rule readings, manual edits.

Priority (design §4.1): manual confirmed > AI > rules.  Rules never overwrite
confirmed / manual / AI segments unless the line text itself changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..models import Line, LyricsDoc, Segment, Unit
from . import chinese, english, japanese
from .japanese import is_kana_text, keeps_katakana, split_morae, to_hiragana

PROTECTED_SOURCES = ("manual", "ai")


def detect_lang(text: str, hint: Optional[str] = None) -> str:
    if any(0x3041 <= ord(c) <= 0x30FF for c in text):
        return "ja"
    has_han = any(japanese.is_kanji(c) for c in text)
    if has_han:
        return hint if hint in ("ja", "zh") else "ja"
    if re.search(r"[A-Za-z]", text):
        return "en" if hint not in ("ja", "zh") else hint
    return hint or "other"


def rule_segments_for(text: str, lang: str) -> list[Segment]:
    if lang == "zh":
        return chinese.rule_segments(text)
    if lang == "en":
        return english.rule_segments(text)
    return japanese.rule_segments(text)


def _surface(line: Line) -> str:
    return "".join(s.surface for s in line.segments)


def _is_protected(seg: Segment) -> bool:
    return seg.confirmed or seg.reading_source in PROTECTED_SOURCES


@dataclass
class PrepareReport:
    prepared: list[str] = field(default_factory=list)  # line ids filled by rules
    kept: list[str] = field(default_factory=list)  # protected lines left alone
    rederived: list[str] = field(default_factory=list)  # protected but text changed -> re-derived
    regrouped: list[str] = field(default_factory=list)  # segments regrouped by word, readings kept
    messages: list[str] = field(default_factory=list)


def prepare_line(line: Line, lang_hint: Optional[str] = None, *, overwrite_rule: bool = True,
                 report: Optional[PrepareReport] = None) -> Line:
    """Fill ``line.segments`` in place and return the line."""
    report = report if report is not None else PrepareReport()
    if line.kind != "lyric" or not line.sing:
        return line
    text_changed = bool(line.segments) and _surface(line) != line.text
    if line.segments and not text_changed and regroup_words(line):
        report.regrouped.append(line.id)
    protected = [s for s in line.segments if _is_protected(s)]
    if line.segments and not text_changed:
        if protected:
            # keep protected segments, re-derive only unprotected rule segments if asked
            if overwrite_rule:
                _refresh_unprotected(line, lang_hint)
            report.kept.append(line.id)
            return line
        if not overwrite_rule:
            return line
    lang = detect_lang(line.text, lang_hint)
    new_segs = rule_segments_for(line.text, lang)
    if line.segments and not text_changed:
        _reuse_ids(line.segments, new_segs)
    line.segments = new_segs
    if text_changed and protected:
        report.rederived.append(line.id)
        report.messages.append(f"{line.id}: 文本已变更，人工/AI 读音已按规则重新生成，需要重新确认")
    else:
        report.prepared.append(line.id)
    return line


def _refresh_unprotected(line: Line, lang_hint: Optional[str]) -> None:
    lang = detect_lang(line.text, lang_hint)
    out: list[Segment] = []
    for seg in line.segments:
        if _is_protected(seg) or seg.reading_source not in ("rule", "none"):
            out.append(seg)
            continue
        fresh = rule_segments_for(seg.surface, seg.lang if seg.lang in ("ja", "zh", "en") else lang)
        _reuse_ids([seg], fresh)
        out.extend(fresh)
    line.segments = out


def _kana_splittable(seg: Segment) -> bool:
    """A kana segment whose units map 1:1 onto its surface can be cut between units."""
    return (seg.lang == "ja" and not seg.confirmed and bool(seg.units) and is_kana_text(seg.surface)
            and all(u.surface for u in seg.units) and "".join(u.surface for u in seg.units) == seg.surface)


def regroup_words(line: Line) -> bool:
    """Regroup a Japanese line's segments along word boundaries (MeCab), in place.

    Older rule segmentation split by character class (好 | きになってく); an AI
    reply often keeps that split.  This merges a kanji segment with its
    okurigana and splits kana runs between words (好き | に | なっ | てく)
    **without touching any reading or unit**: units keep their ids, readings
    and times, so alignment results stay valid.  Confirmed segments, segments
    without units (punctuation), other languages and two separately read kanji
    segments are never merged; a kanji segment is never cut.  Returns True
    when changed.
    """
    if not line.segments or not any(s.lang == "ja" and s.units for s in line.segments):
        return False
    words = japanese.word_spans(line.text)
    if words is None or _surface(line) != line.text:
        return False
    cuts = {0, len(line.text)} | {a for a, _, _, _ in words} | {b for _, b, _, _ in words}

    # atomic pieces: (start, end, segment, units, reading, barrier)
    pieces = []
    pos = 0
    for seg in line.segments:
        barrier = not seg.units or seg.confirmed or seg.lang != "ja"
        if _kana_splittable(seg):
            for u in seg.units:
                pieces.append((pos, pos + len(u.surface), seg, [u], u.reading, False))
                pos += len(u.surface)
        else:
            reading = seg.reading if seg.reading is not None else "".join(u.reading for u in seg.units)
            pieces.append((pos, pos + len(seg.surface), seg, list(seg.units), reading, barrier))
            pos += len(seg.surface)

    def kanji_piece(pc) -> bool:
        return any(japanese.is_kanji(c) for c in pc[2].surface) and not _kana_splittable(pc[2])

    groups: list[list[tuple]] = []
    for pc in pieces:
        if (groups and not pc[5] and not groups[-1][-1][5] and pc[0] not in cuts
                # two separately read kanji segments keep their own readings (今|君)
                and not (kanji_piece(pc) and any(kanji_piece(x) for x in groups[-1]))):
            groups[-1].append(pc)
        else:
            groups.append([pc])

    new_segs: list[Segment] = []
    for g in groups:
        uniq = list({id(pc[2]): pc[2] for pc in g}.values())  # owning segments, in order
        units = [u for pc in g for u in pc[3]]
        if len(uniq) == 1 and [u.id for u in units] == [u.id for u in uniq[0].units]:
            new_segs.append(uniq[0])  # unchanged
            continue
        surface = line.text[g[0][0]:g[-1][1]]
        reading = "".join(pc[4] for pc in g)
        kanji = [o for o in uniq if not _kana_splittable(o)]
        kanji_owner = kanji[0] if kanji else None
        cands: list[str] = []
        if len(kanji) == 1 and kanji_owner.candidates:
            # the kanji's alternative readings, with the kana around it
            before = "".join(pc[4] for pc in g[:next(i for i, pc in enumerate(g) if pc[2] is kanji_owner)])
            after = "".join(pc[4] for pc in g[next(i for i, pc in enumerate(g) if pc[2] is kanji_owner) + 1:])
            cands = [before + c + after for c in kanji_owner.candidates]
        sources = {o.reading_source for o in uniq}
        seg = Segment(
            surface=surface, reading=reading, lang="ja", units=units,
            reading_source="ai" if "ai" in sources else ("manual" if "manual" in sources else "rule"),
            uncertain=any(o.uncertain for o in uniq), candidates=cands,
            note="; ".join(dict.fromkeys(o.note for o in uniq if o.note)),
        )
        if kanji_owner is not None:
            seg.id = kanji_owner.id
        _assign_surfaces(seg)
        new_segs.append(seg)
    if [(s.id, s.surface) for s in new_segs] == [(s.id, s.surface) for s in line.segments]:
        return False
    line.segments = new_segs
    return True


def _reuse_ids(old: Sequence[Segment], new: Sequence[Segment]) -> None:
    """Keep ids of segments / units whose surface and unit grouping are unchanged."""
    pool = {}
    for s in old:
        pool.setdefault((s.surface, tuple(u.reading for u in s.units)), []).append(s)
    for s in new:
        key = (s.surface, tuple(u.reading for u in s.units))
        if pool.get(key):
            o = pool[key].pop(0)
            s.id = o.id
            for nu, ou in zip(s.units, o.units):
                nu.id = ou.id


def prepare_doc(doc: LyricsDoc, *, overwrite_rule: bool = True) -> PrepareReport:
    report = PrepareReport()
    for ln in doc.lines:
        prepare_line(ln, doc.language, overwrite_rule=overwrite_rule, report=report)
    return report


def units_from_spec(reading: str, units: Optional[Sequence[str]], lang: str) -> list[Unit]:
    if units is None:
        if lang == "ja" or is_kana_text(reading):
            return japanese.reading_units(to_hiragana(reading))
        return [Unit(reading=reading)]
    if "".join(units) != reading:
        raise ValueError(f"单元 {list(units)!r} 拼接后与读音 {reading!r} 不一致")
    out = []
    for u in units:
        flags = []
        if lang == "ja":
            m = split_morae(u)
            flags = sorted({f for x in m for f in x.flags})
            u = to_hiragana(u)
        out.append(Unit(reading=u, flags=flags))
    return out


# particles written は / へ / を but sung わ / え / お: the mora still maps onto its kana
_PARTICLE_SOUND = {("は", "わ"), ("へ", "え"), ("を", "お")}


def _assign_surfaces(seg: Segment) -> None:
    """For pure kana segments with 1:1 unit mapping keep per-unit surfaces.

    A unit per mora whose reading only differs from the written kana by a particle's sound
    (には → に|わ) or a long-vowel mark keeps its kana too, so the segment stays splittable.
    """
    for u in seg.units:
        u.surface = ""
    if not is_kana_text(seg.surface) or not seg.units:
        return
    if to_hiragana(seg.surface) == "".join(u.reading for u in seg.units):
        pos = 0
        for u in seg.units:
            u.surface = seg.surface[pos:pos + len(u.reading)]
            pos += len(u.reading)
        return
    morae = split_morae(seg.surface)
    if len(morae) != len(seg.units) or sum(len(m.text) for m in morae) != len(seg.surface):
        return
    if not all(m.text == u.reading or (m.text, u.reading) in _PARTICLE_SOUND or "long" in m.flags
               for m, u in zip(morae, seg.units)):
        return
    pos = 0
    for m, u in zip(morae, seg.units):
        u.surface = seg.surface[pos:pos + len(m.text)]
        pos += len(m.text)


def replace_units_keep_ids(seg: Segment, new_units: list[Unit]) -> None:
    """Replace units, preserving ids only when the grouping is unchanged."""
    if [u.reading for u in seg.units] == [u.reading for u in new_units]:
        for nu, ou in zip(new_units, seg.units):
            nu.id = ou.id
    seg.units = new_units
    _assign_surfaces(seg)


def set_segment_reading(line: Line, segment_id: str, reading: str, units: Optional[Sequence[str]] = None,
                        source: str = "manual", confirm: bool = True) -> Segment:
    seg = next((s for s in line.segments if s.id == segment_id), None)
    if seg is None:
        raise KeyError(segment_id)
    katakana = seg.lang == "ja" and keeps_katakana(seg.surface, reading)
    if seg.lang == "ja":
        reading = to_hiragana(reading)
    new_units = units_from_spec(reading, units, seg.lang)
    replace_units_keep_ids(seg, new_units)
    seg.reading = reading
    seg.katakana = katakana
    seg.hidden = False  # a reading: it is sung
    seg.reading_source = source  # type: ignore[assignment]
    seg.confirmed = confirm
    seg.uncertain = False
    return seg


def resegment_line(line: Line, segments_spec: Sequence[dict]) -> Line:
    """Replace a line's segmentation.

    ``segments_spec`` items: ``{"surface", "reading"?, "units"?, "lang"?, "confirmed"?}``.
    Surfaces must concatenate to ``line.text``.  Ids are reused for unchanged segments.
    """
    surfaces = [str(s.get("surface", "")) for s in segments_spec]
    if "".join(surfaces) != line.text:
        raise ValueError("各片段原文拼接后与该行文字不一致")
    new: list[Segment] = []
    for spec in segments_spec:
        surface = spec["surface"]
        lang = spec.get("lang") or detect_lang(surface, "ja")
        reading = spec.get("reading")
        if reading:
            if lang == "ja":
                reading = to_hiragana(reading)
            units = units_from_spec(reading, spec.get("units"), lang)
            seg = Segment(surface=surface, reading=reading, lang=lang, units=units, reading_source="manual",
                          confirmed=bool(spec.get("confirmed", True)))
        else:
            seg = Segment(surface=surface, reading=None, lang=lang, units=[], reading_source="none")
        _assign_surfaces(seg)
        new.append(seg)
    _reuse_ids(line.segments, new)
    line.segments = new
    return line


def capability_warnings(doc: LyricsDoc, backend_langs: Sequence[str]) -> list[str]:
    """Warn when segments use languages the acoustic model does not cover."""
    langs = set(backend_langs)
    counts: dict[str, int] = {}
    missing: list[str] = []
    for ln in doc.sung_lines():
        for s in ln.segments:
            if s.units and s.lang not in langs:
                counts[s.lang] = counts.get(s.lang, 0) + 1
            if not s.units and s.surface.strip() and s.uncertain:
                missing.append(f"{ln.id}:{s.surface}")
    msgs = []
    names = {"zh": "中文", "en": "英文", "ja": "日语", "other": "其他语言"}
    for lang, n in sorted(counts.items()):
        msgs.append(f"{n} 个{names.get(lang, lang)}片段不在当前模型支持的语言内（{', '.join(sorted(langs))}），"
                    f"将按固定转写送入模型，结果可能不可靠；不会强制日语化")
    if missing:
        msgs.append(f"{len(missing)} 个片段缺少读音，将不参与对齐: {', '.join(missing[:10])}")
    return msgs
