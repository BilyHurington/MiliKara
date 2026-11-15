"""Unified text parsing for pasted or uploaded lyrics.

Pasting and uploading share this code path; the only difference is the
provenance recorded in :class:`~kara_align.models.SourceSnapshot` and
:class:`~kara_align.models.LineSource`.  Line ids are derived from the output
position (``L0001``...), so parsing identical text twice gives identical ids
and an identical ``text_revision``.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Optional

from ..models import (
    FMT_ALIGNMENT,
    FMT_PREPARED,
    FMT_PROJECT,
    FMT_READING_PATCH,
    Line,
    LineSource,
    LyricsDoc,
    LyricsMeta,
    SourceSnapshot,
)
from .lrc import OFFSET_NOTE, ParsedLrc, has_valid_times, parse_lrc

DetectedFormat = Literal[
    "lrc", "plain", "json-project", "json-prepared", "json-alignment", "json-reading-patch", "unknown"
]

# a line starting with a time tag (or with an enhanced-LRC word tag, "<mm:ss.xx>")
_TIMED_LINE = re.compile(r"^\s*(?:\[\d{1,4}:\d{1,2}(?:[.:]\d{1,6})?\]|<\d{1,4}:\d{1,2}(?:[.:]\d{1,6})?>)")
_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN = re.compile(r"[A-Za-z]")

# credit lines: a short label made only of credit words, then a colon ("作词 : X", "Mixed by: X").
# The label must consist of such words entirely, so 「君の曲：…」 or "Mix it up: …" stay lyrics.
_CREDIT_LABEL = re.compile(r"^\s*([^:：]{1,24}?)\s*[:：]\s*(.*)$")
_EN = r"(?![A-Za-z])"  # an English credit word ends at a word boundary
# unambiguous credit words: a label containing one is a credit anywhere in the song
_CREDIT_STRONG = (
    r"作编曲|作編曲|作词|作曲|编曲|作詞|編曲|词曲|詞曲|制作人|制作|製作人|製作|监制|監製|混音|缩混|混缩|和声|和聲|"
    r"录音|錄音|母带|吉他|贝斯|貝斯|弦乐|弦樂|键盘|鍵盤|出品|发行|發行|策划|策劃|统筹|統籌|配唱|人声|人聲|企划|企劃|"
    r"监督|監督|原唱|伴唱|和音|演奏|"
    rf"(?:OP|SP|ISRC|lyricists?|lyrics|composers?|composed|composition|arrang\w*|written|writers?|produc\w*|"
    rf"mix\w+|master\w*|engineer\w*|record\w*|vocals|guitars|drums|keyboards?|strings|programming){_EN}"
)
# everyday words that also start lyric lines (「曲」「鼓」, "mix", "drum", "vocal"): a label made
# only of them is a credit only near the top (before the first sung line) or at 00:00
_CREDIT_WEAK = rf"词|詞|曲|鼓|歌|唄|(?:mix|vocal|guitar|bass|drum|piano|music|lyric){_EN}"
# words that only qualify another credit word
_CREDIT_FILLER = rf"工程师|工程師|助理|师|師|人|者|(?:by|and|assistant|additional|chief|co){_EN}"
_CREDIT_TOKEN = re.compile(
    rf"(?P<strong>{_CREDIT_STRONG})|(?P<weak>{_CREDIT_WEAK})|(?P<filler>{_CREDIT_FILLER})"
    r"|(?P<sep>[\s&/／、,，·・+＋-]+)",
    re.IGNORECASE)
# NetEase often starts with a "歌名 - 歌手" line at 00:00
_TITLE_LINE = re.compile(r"^\S.{0,80}\s[-–—]\s.{1,80}\S$")


class LyricsModeError(ValueError):
    """Input is unusable in the requested mode (never silently downgraded)."""


class LyricsFormatError(ValueError):
    """Input is not lyrics text (e.g. a project JSON) or cannot be parsed."""


@dataclass
class ParseResult:
    doc: LyricsDoc
    warnings: list[str] = field(default_factory=list)
    detected: DetectedFormat = "plain"
    snapshot: Optional[SourceSnapshot] = None


# byte-order marks, zero-width / direction marks and soft hyphens (music platforms sometimes
# leave them in lyrics); Unicode line / paragraph separators are line breaks
_INVISIBLE = {**dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060\u200e\u200f\u00ad"
                                        "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"), None),
              0x2028: "\n", 0x2029: "\n", 0x0085: "\n"}
# half-width katakana (and its half-width punctuation / voicing marks)
_HALFWIDTH_KANA = re.compile(r"[\uff61-\uff9f]+")


def normalize_text(text: str) -> str:
    """Line breaks unified, invisible characters removed, kana in one canonical form:
    decomposed kana (か + U+3099) composed (NFC) and half-width katakana (ｶﾞﾝﾊﾞﾚ) made
    full width (NFKC on those runs only), so readings, units and surfaces line up."""
    text = text.translate(_INVISIBLE).replace("\r\n", "\n").replace("\r", "\n")
    text = _HALFWIDTH_KANA.sub(lambda m: unicodedata.normalize("NFKC", m.group()), text)
    return unicodedata.normalize("NFC", text)


def detect_format(text: str, filename: Optional[str] = None) -> DetectedFormat:
    t = normalize_text(text).strip()
    if not t:
        return "unknown"
    if t[0] in "{[" and not _TIMED_LINE.match(t) and not re.match(r"^\[[A-Za-z#]+:", t):
        try:
            data = json.loads(t)
        except ValueError:
            data = None
        if data is not None:
            fmt = data.get("format", "") if isinstance(data, dict) else ""
            for prefix, name in (
                (FMT_PROJECT, "json-project"),
                (FMT_PREPARED, "json-prepared"),
                (FMT_ALIGNMENT, "json-alignment"),
                (FMT_READING_PATCH, "json-reading-patch"),
            ):
                if isinstance(fmt, str) and fmt.startswith(prefix):
                    return name  # type: ignore[return-value]
            return "unknown"
    if any(_TIMED_LINE.match(ln) for ln in t.split("\n")):
        return "lrc"
    if filename and filename.lower().endswith(".lrc"):
        # an .lrc without any time tag is still just text
        return "plain"
    return "plain"


def detect_language(texts: list[str]) -> str:
    from ..reading.cantonese import looks_cantonese

    joined = "".join(texts)
    if _KANA.search(joined):
        return "ja"
    if _HAN.search(joined):
        # Cantonese is written in the same hanzi: only the colloquial characters (嘅 咗 喺 唔 …) tell it
        # apart, so standard written Chinese sung in Cantonese stays "zh" until the user says otherwise
        return "yue" if looks_cantonese(texts) else "zh"
    if _LATIN.search(joined):
        return "en"
    return "other"


def is_credit_line(text: str, *, at_top: bool = False) -> bool:
    """A "label: name" credit line (作词 : X, Mixed by: X).

    The label must be made of credit words only.  Labels of everyday words alone (「曲」「词」
    「鼓」, "Mix", "Drum", "Vocal") also start lyric lines (「曲：…」, "Mix: …"), so they only
    count ``at_top`` – before the first sung line or at 00:00, where platforms put credits.
    """
    m = _CREDIT_LABEL.match(text)
    if not m:
        return False
    label = m.group(1).strip()
    kinds = set()
    pos = 0
    while pos < len(label):
        t = _CREDIT_TOKEN.match(label, pos)
        if t is None or t.end() == pos:
            return False
        kinds.add(t.lastgroup)
        pos = t.end()
    if "strong" in kinds:
        return True
    return at_top and "weak" in kinds


def _meta_from_tags(tags: dict[str, str]) -> LyricsMeta:
    meta = LyricsMeta(title=tags.get("ti"), artist=tags.get("ar"), album=tags.get("al"))
    length = tags.get("length")
    if length:
        m = re.match(r"^(\d+):(\d{1,2})(?:[.:](\d{1,3}))?$", length.strip())
        if m:
            frac = m.group(3) or ""
            meta.duration_ms = int(m.group(1)) * 60_000 + int(m.group(2)) * 1000 + (
                int(frac) * 10 ** (3 - len(frac)) if frac else 0
            )
    meta.extra = {k: v for k, v in tags.items() if k not in ("ti", "ar", "al", "offset")}
    return meta


def _is_placeholder(text: str) -> bool:
    """QQ Music writes "//" for a line without translation."""
    return bool(text) and set(text.strip()) <= {"/"}


def _make_lines(items: list[tuple[Optional[int], str, LineSource, Optional[int]]]) -> list[Line]:
    """Lines from (start, text, source, end); credits / title lines at the top become meta."""
    lines: list[Line] = []
    top = True  # no sung line yet
    for i, (start, text, source, end) in enumerate(items):
        at_zero = start is not None and start < 1000
        if not text or _is_placeholder(text):
            kind, sing = "blank", False
        elif is_credit_line(text, at_top=top or at_zero):
            kind, sing = "meta", False
        elif top and start is not None and start == 0 and _TITLE_LINE.match(text):
            kind, sing = "meta", False  # "歌名 - 歌手" at 00:00 (NetEase)
        else:
            kind, sing = "lyric", True
            top = False
        lines.append(Line(id=f"L{i + 1:04d}", text=text, kind=kind, sing=sing,
                          imported_start_ms=start, imported_end_ms=end, source=source))
    return lines


def _script(text: str) -> str:
    if _KANA.search(text):
        return "kana"
    if _HAN.search(text):
        return "han"
    if _LATIN.search(text):
        return "latin"
    return "other"


def _split_translations(parsed: ParsedLrc) -> dict[tuple[int, int], str]:
    """Bilingual LRC (a Japanese line and its translation under the same time tag, as copied from
    many lyric sites): the line without kana at a shared time is the translation of the other.
    The translation entries are removed from ``parsed``; returns {(raw_index, tag_index) of the
    lyric line: translation}.  Needs at least two such pairs, so one odd line is left alone.

    Once the file is recognised as bilingual, a shared time whose lyric has no kana either (an
    English line, an all-kanji line such as 永遠) is paired too when the line written in the
    translation's place (usually second) has no kana and is Chinese or in another script than
    the lyric.  Two lines with kana are never paired (a duet); a "//" placeholder (QQ Music:
    no translation for this line) is dropped without becoming a translation."""
    by_time: dict[int, list] = {}
    for e in parsed.entries:
        if e.text:
            by_time.setdefault(e.time_ms, []).append(e)
    pairs, rest = [], []
    for group in by_time.values():
        if len(group) != 2:
            continue
        kana = [bool(_KANA.search(e.text)) for e in group]
        if kana.count(True) != 1:
            rest.append(group)
            continue
        lyric, trans = (group[0], group[1]) if kana[0] else (group[1], group[0])
        if _HAN.search(trans.text) or _LATIN.search(trans.text):
            pairs.append((lyric, trans))
        elif _is_placeholder(trans.text):
            pairs.append((lyric, trans))
    real = [(ly, tr) for ly, tr in pairs if not _is_placeholder(tr.text)]
    if len(real) < 2:
        return {}
    # which of the two lines holds the translation in this file (by raw order)
    second = sum(1 for ly, tr in real if (tr.raw_index, tr.tag_index) > (ly.raw_index, ly.tag_index))
    trans_second = second * 2 >= len(real)
    for group in rest:
        a, b = sorted(group, key=lambda e: (e.raw_index, e.tag_index))
        lyric, trans = (a, b) if trans_second else (b, a)
        if _KANA.search(trans.text) or _KANA.search(lyric.text):
            continue  # two Japanese lines (or kana in the "translation"): left alone
        if _is_placeholder(trans.text):
            pairs.append((lyric, trans))
        elif _script(trans.text) == "han" or _script(trans.text) != _script(lyric.text):
            pairs.append((lyric, trans))
    drop = {id(t) for _, t in pairs}
    parsed.entries = [e for e in parsed.entries if id(e) not in drop]
    return {(lyric.raw_index, lyric.tag_index): trans.text for lyric, trans in pairs
            if not _is_placeholder(trans.text)}


def _attach_translations(lines: list[Line], translations: dict[tuple[int, int], str], warnings: list[str]) -> None:
    if not translations:
        return
    n = 0
    for ln in lines:
        t = translations.get((ln.source.raw_index, ln.source.tag_index))
        if t is not None:
            ln.translation = t
            n += 1
    warnings.append(f"检测到 {n} 行翻译（与歌词同一时间标签、没有假名的行），已作为翻译，不参与演唱。")


def _lines_from_lrc(parsed: ParsedLrc, keep_times: bool, origin: str, source_id: str) -> list[Line]:
    """Build line instances in time order; untimed lines keep their raw position."""
    items: list[tuple[Optional[int], str, LineSource, Optional[int]]] = []
    for e in parsed.entries:
        if not keep_times and not e.text:
            continue
        src = LineSource(origin=origin, source_id=source_id, raw_index=e.raw_index,
                         raw_text=e.raw_text, tag_index=e.tag_index)
        items.append((e.time_ms if keep_times else None, e.text, src,
                      e.end_ms if keep_times else None))
    for u in parsed.untimed:
        if not u.text:
            continue
        src = LineSource(origin=origin, source_id=source_id, raw_index=u.raw_index, raw_text=u.text)
        # insert after the last first-tag instance written before it in the raw text
        pos = 0
        for j, (_, _, s, _) in enumerate(items):
            if s.raw_index is not None and s.raw_index < u.raw_index and s.tag_index == 0:
                pos = j + 1
        items.insert(pos, (None, u.text, src, None))
    return _make_lines(items)


def parse_lyrics_text(
    text: str,
    *,
    mode: Literal["plain", "lrc"],
    origin: str = "paste",
    filename: Optional[str] = None,
    source_id: Optional[str] = None,
) -> ParseResult:
    """Parse lyrics (plain text or LRC) into a :class:`LyricsDoc`.

    * plain mode + LRC input: only the text is used, times are dropped and a
      warning says so explicitly;
    * lrc mode without any valid time: :class:`LyricsModeError`.
    """
    norm = normalize_text(text)
    detected = detect_format(norm, filename)
    if detected.startswith("json") or (detected == "unknown" and norm.strip()):
        raise LyricsFormatError(
            f"输入看起来是 {detected}，不是歌词文本；请通过对应的项目 / 补丁入口导入。"
        )
    if not norm.strip():
        raise LyricsFormatError("歌词文本为空。")

    snapshot = SourceSnapshot(
        origin=origin, kind="lrc" if detected == "lrc" else "lyrics", filename=filename, text=norm,
        sha256=hashlib.sha256(norm.encode("utf-8")).hexdigest(),
    )
    if source_id:
        snapshot.id = source_id
    warnings: list[str] = []
    doc = LyricsDoc()

    if detected == "lrc":
        parsed = parse_lrc(norm)
        warnings.extend(parsed.warnings)
        doc.meta = _meta_from_tags(parsed.meta)
        doc.embedded_offset_raw = parsed.offset_raw
        doc.embedded_shift_ms = parsed.embedded_shift_ms if parsed.offset_raw is not None else 0
        if parsed.offset_raw is not None:
            doc.embedded_offset_note = OFFSET_NOTE
        if mode == "lrc":
            if not has_valid_times(parsed):
                raise LyricsModeError(
                    "LRC 增强模式需要行时间，但输入中没有找到任何时间。"
                    "请提供带时间的 LRC，或切换到普通模式。"
                )
            translations = _split_translations(parsed)
            doc.lines = _lines_from_lrc(parsed, True, origin, snapshot.id)
            _attach_translations(doc.lines, translations, warnings)
            if parsed.untimed:
                n = sum(1 for u in parsed.untimed if u.text)
                if n:
                    warnings.append(f"{n} 行没有时间标签，已保留但不作为锚点。")
        else:
            translations = _split_translations(parsed)
            doc.lines = _lines_from_lrc(parsed, False, origin, snapshot.id)
            _attach_translations(doc.lines, translations, warnings)
            warnings.append(
                "普通模式：已忽略 LRC 时间标签，只使用歌词正文"
                "（不使用外部时间锚点）。"
            )
    else:
        if mode == "lrc":
            raise LyricsModeError(
                "LRC 增强模式需要带时间的歌词，但输入没有时间标签。"
                "请提供带时间的 LRC，或切换到普通模式。"
            )
        items = []
        for idx, raw in enumerate(norm.split("\n")):
            line = raw.strip()
            if line:
                items.append((idx, line))
        doc.lines = _make_lines([
            (None, line, LineSource(origin=origin, source_id=snapshot.id, raw_index=idx, raw_text=raw), None)
            for idx, line in items
        ])

    n_meta = sum(1 for ln in doc.lines if ln.kind == "meta")
    if n_meta:
        warnings.append(f"{n_meta} 行作者 / 制作信息已排除在对齐之外（可重新加入）。")
    doc.language = detect_language([ln.text for ln in doc.lines if ln.kind == "lyric"])  # type: ignore[assignment]
    return ParseResult(doc=doc, warnings=warnings, detected=detected, snapshot=snapshot)
