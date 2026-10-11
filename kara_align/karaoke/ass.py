"""Karaoke subtitles as ASS, laid out chunk by chunk.

Every lyric line becomes a row of *chunks*.  A chunk is a piece of the line
that carries one ruby group (a kanji word and its reading) or plain text; kana
around kanji are split off so ruby sits only over kanji.  Widths are measured
with the fonts libass will draw with (``fonts.Measurer``), so the layout is
computed here: chunk widths (a reading may overhang a neighbour without ruby,
never another reading), the line's place in its row (alternating left / right,
shrunk to fit the margins), the row (``schedule``: lines take turns in
``layout.lines`` rows; a line sung while every row is still busy — a duet, a
backing vocal — gets an extra row beyond the block) and when it shows
(``lead_in`` / ``hold`` / early show, hidden across long pauses inside it).

Each chunk is written as its own positioned event (lyric) plus an optional ruby
event above it:

* ruby ``own``: both are ``\\kf`` / ``\\k`` karaoke events built from the unit
  times of the alignment result (manual edits included);
* ruby ``base`` (与歌词对齐): lyric and ruby are drawn twice — unsung, and sung
  cut by an animated ``\\clip`` at the lyric's sweep position (the unsung copy by
  the matching ``\\iclip``, so fades never show one through the other).  The
  ruby's cut follows the lyric's while it is over the lyric and covers the whole
  reading once its chunk is sung.

Around that: optional glow layers (a blurred border under the text), the
translation (under each line, or one line at an edge), per-syllable effects
(``effects``) and the song title card (``info``).  Fades are shortened so a line
never fades while it is sung.

Times written here are on the original audio timeline plus ``time_offset_ms``
(the audio start inside a video, for burn-in / use with that video), minus the
style's ``advance_ms``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import AlignmentResult, KaraokeStyle, Line, Project, Segment
from ..reading.japanese import is_kanji, to_hiragana, to_katakana
from .fonts import (BUNDLED_JP, BUNDLED_SC, HAN_FAMILIES, Measurer, bundled, covering_family, default_family,
                    installed, lacking, system_han_fallback)

REF_WIDTH = 1920  # style pixel values are defined for a frame this wide; other widths scale
DEFAULT_SIZE = (1920, 1080)
PAUSE_HIDE_MS = 6000  # a pause inside a line at least this long hides the line meanwhile


# ---------------------------------------------------------------------------
# text helpers




def has_kanji(s: str) -> bool:
    return any(is_kanji(c) for c in s)


def _rgb(hex_rgb: str) -> tuple[str, str, str]:
    """#RRGGBB → ("RR", "GG", "BB"); anything else is white (styles are validated, this is a last guard)."""
    h = (hex_rgb or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    h = h.upper()
    return h[0:2], h[2:4], h[4:6]


def ass_color(hex_rgb: str, alpha_pct_transparent: float = 0) -> str:
    """#RRGGBB → &HAABBGGRR (alpha 0 = opaque)."""
    r, g, b = _rgb(hex_rgb)
    a = max(0, min(255, round(alpha_pct_transparent * 255 / 100)))
    return f"&H{a:02X}{b}{g}{r}"


def bgr_tag(hex_rgb: str) -> str:
    """#RRGGBB → &HBBGGRR& (colour override tag value)."""
    r, g, b = _rgb(hex_rgb)
    return f"&H{b}{g}{r}&"


_bgr_tag = bgr_tag


def ass_time(ms: float) -> str:
    cs = max(0, int(round(ms / 10)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


# line / paragraph separators and other control characters: one space each
_BREAKS = {c: " " for c in [*range(0x00, 0x20), 0x7F, 0x85, 0x2028, 0x2029]}
_ESCAPE = str.maketrans({**_BREAKS, "\\": "＼", "{": "｛", "}": "｝"})


def escape_text(s: str) -> str:
    """User text as literal ASS text.  libass has no escape for a backslash (``\\\\`` is shown
    as two, and a trailing one before the next ``{\\k..}`` breaks the tag), and ``\\N`` /
    ``\\n`` / ``\\h`` are line breaks / spaces: backslashes and braces become full-width.
    Line breaks of any kind (``\\n``, ``\\r``, U+2028 …) and other control characters become
    spaces (an event is one line of the file)."""
    return s.translate(_ESCAPE)


# ---------------------------------------------------------------------------
# chunk model


@dataclass
class Part:
    text: str
    start: Optional[int]
    end: Optional[int]


@dataclass
class Chunk:
    base: list[Part]  # lyric text pieces with their own karaoke timing
    ruby: list[Part] = field(default_factory=list)  # empty = no ruby
    wrap_before: bool = False  # the first chunk of a segment the AI suggested a line break before
    singers: tuple[int, ...] = ()  # who sings it (Line.singers / singer_spans); () = the style's own colours

    @property
    def base_text(self) -> str:
        return "".join(p.text for p in self.base)

    @property
    def ruby_text(self) -> str:
        return "".join(p.text for p in self.ruby)


@dataclass
class LaidLine:
    line: Line
    chunks: list[Chunk]
    start: int  # first sung time
    end: int  # last sung time
    translation: Optional[str] = None
    show_from: int = 0
    show_to: int = 0
    slot: int = 0  # row: 0 … lines-1 in the block; beyond it (-1, -2 / lines, lines+1) extra rows
    units: list[tuple[int, int]] = field(default_factory=list)  # timed units (start, end), for pauses
    # a long line wrapped into pieces (wrap_line): the translation goes with the first piece and is
    # shown until the last one is sung
    trans_until: Optional[int] = None
    countdown_ms: int = 0  # countdown dots before it (plan_countdowns): the time they need; 0 = none

    @property
    def text(self) -> str:
        return "".join(c.base_text for c in self.chunks)


def _ruby_text(reading: str, script: str, romaji: Optional[str]) -> str:
    if script == "katakana":
        return to_katakana(to_hiragana(reading))
    if script == "romaji":
        return romaji if romaji is not None else reading
    return to_hiragana(reading)


def _kana_split(runs: list[tuple[str, bool]], reading: str) -> Optional[list[int]]:
    """Reading lengths of each run: kana runs match literally, kanji runs take at least one kana.

    Every valid split is tried.  The one where the kanji readings are the most even
    (the fewest kana on the busiest kanji) wins; if two splits are equally even the
    split is ambiguous (物の怪 / もののけ: も|の|のけ or もの|の|け) and None is returned.
    """
    found: list[list[int]] = []

    def walk(i: int, pos: int, acc: list[int]) -> None:
        if len(found) > 64:
            return
        if i == len(runs):
            if pos == len(reading):
                found.append(acc)
            return
        text, is_k = runs[i]
        if not is_k:
            kana = to_hiragana(text)
            if reading.startswith(kana, pos):
                walk(i + 1, pos + len(kana), acc + [len(kana)])
            return
        # leave at least one kana for every later kanji run and the literal kana runs
        rest = sum(1 if k else len(t) for t, k in runs[i + 1:])
        for n in range(1, len(reading) - pos - rest + 1):
            walk(i + 1, pos + n, acc + [n])

    walk(0, 0, [])
    if not found:
        return None

    def cost(lens: list[int]) -> float:
        return max(n / len(t) for n, (t, k) in zip(lens, runs) if k)

    best = min(cost(x) for x in found)
    top = [x for x in found if abs(cost(x) - best) < 1e-9]
    return top[0] if len(top) == 1 else None


def _split_affixes(seg: Segment) -> list[tuple[str, list]]:
    """Split the kana of a kanji word off its kanji: [(surface, units)].

    Handles a kana prefix, okurigana and kana between kanji (笑い合え →
    笑 い 合 え), so ruby sits only over kanji.  Only splits where the
    reading boundary falls on a unit boundary, so every piece keeps whole
    units (and their times); otherwise the word stays one piece.  A split the
    reading allows in more than one way is not made (_kana_split()).
    """
    surface, units = seg.surface, seg.units
    if not has_kanji(surface) or not units:
        return [(surface, units)]
    reading = "".join(to_hiragana(u.reading) for u in units)
    runs = [(m.group(), bool(m.group(1))) for m in re.finditer(r"([^ぁ-ヿ]+)|([ぁ-ヿ]+)", surface)]
    if len(runs) == 1:
        return [(surface, units)]
    lens = _kana_split(runs, reading)
    if lens is None:
        return [(surface, units)]
    edges = [0]
    for n in lens:
        edges.append(edges[-1] + n)
    unit_edges = [0]
    for u in units:
        unit_edges.append(unit_edges[-1] + len(u.reading))
    pieces: list[tuple[str, list]] = []
    ui = 0
    text_parts: list[str] = []
    for (text, _), end in zip(runs, edges[1:]):
        text_parts.append(text)
        if end not in unit_edges:
            continue  # the boundary cuts a unit: keep this run with the next one
        k = unit_edges.index(end)
        pieces.append(("".join(text_parts), units[ui:k]))
        ui, text_parts = k, []
    if text_parts or ui < len(units):
        return [(surface, units)]
    return pieces


def build_chunks(line: Line, times: dict[str, tuple[Optional[int], Optional[int]]], style: KaraokeStyle,
                 romaji: dict[str, str]) -> list[Chunk]:
    ruby_cfg = style.ruby
    chunks: list[Chunk] = []
    # who sings each character (only when the line says so; the segments spell out the line's text)
    chars = None
    if line.singers or line.singer_spans:
        from ..lyrics.singers import effective

        chars = effective(line)
        if "".join(s.surface for s in line.segments) != line.text:
            chars = [tuple(line.singers)] * sum(len(s.surface) for s in line.segments)
    pos = 0

    def sung_by(n: int) -> tuple[int, ...]:
        if chars is None:
            return ()
        from ..lyrics.singers import range_singers

        return range_singers(chars, pos, pos + n, line.text if len(line.text) == len(chars) else None)

    for seg in line.segments:
        if seg.hidden:  # a reading in brackets: its word shows it as ruby
            pos += len(seg.surface)
            continue
        if not seg.units:
            chunks.append(Chunk([Part(seg.surface, None, None)], wrap_before=seg.wrap_before,
                                singers=sung_by(len(seg.surface))))
            pos += len(seg.surface)
            continue
        pieces = _split_affixes(seg) if seg.lang == "ja" else [(seg.surface, seg.units)]
        for n_piece, (surface, units) in enumerate(pieces):
            t = [times.get(u.id, (None, None)) for u in units]
            kanji = has_kanji(surface)
            hira = to_hiragana(surface)
            readings = [u.reading for u in units]
            # kana (or latin) whose units map 1:1 onto the surface: per-unit karaoke
            if not kanji and seg.lang == "ja" and hira == "".join(readings):
                base, at = [], 0
                for u, (s, e) in zip(units, t):
                    base.append(Part(surface[at:at + len(u.reading)], s, e))
                    at += len(u.reading)
            elif all(u.surface for u in units) and "".join(u.surface for u in units) == surface:
                base = [Part(u.surface, s, e) for u, (s, e) in zip(units, t)]
            else:
                starts = [s for s, _ in t if s is not None]
                ends = [e for _, e in t if e is not None]
                base = [Part(surface, min(starts) if starts else None, max(ends) if ends else None)]
            ruby: list[Part] = []
            # "仅汉字" counts digits as kanji: how 24 is sung is not written either
            wants = ruby_cfg.enabled and seg.lang == "ja" and (kanji or ruby_cfg.target == "all"
                                                                or any(c.isdigit() for c in surface))
            if wants:
                # a reading the lyrics write in katakana (宿敵 → ライバル) stays katakana over hiragana ruby
                script = "katakana" if seg.katakana and ruby_cfg.script == "hiragana" else ruby_cfg.script
                ruby = [Part(_ruby_text(u.reading, script, romaji.get(u.id)), s, e)
                        for u, (s, e) in zip(units, t)]
                if "".join(p.text for p in ruby) == surface:
                    ruby = []  # e.g. hiragana ruby over hiragana
            chunks.append(Chunk(base, ruby, wrap_before=seg.wrap_before and n_piece == 0,
                                singers=sung_by(len(surface))))
            pos += len(surface)
    _fill_missing_times(chunks)
    return chunks


def _fill_missing_times(chunks: list[Chunk]) -> None:
    """Untimed parts (punctuation, unaligned units) highlight instantly at
    their neighbour's boundary; this only affects display, never the data."""
    seq = [p for c in chunks for p in c.base]
    last = None
    for p in seq:
        if p.start is None or p.end is None:
            p.start = p.end = last
        else:
            last = p.end
    nxt = None
    for p in reversed(seq):
        if p.start is None:
            p.start = p.end = nxt
        else:
            nxt = p.start
    for c in chunks:
        for p in c.ruby:
            if p.start is None or p.end is None:
                p.start = c.base[0].start
                p.end = c.base[-1].end


# ---------------------------------------------------------------------------
# scheduling and layout


# a lyric line may wrap after these (or before a space)
_PUNCT = set(" \u3000、。，,.!?！？…‥・~〜～♪☆★「」『』()（）[]［］【】-—")
_OPENING = set("「『(（[［【")


def _blank(c: Chunk) -> bool:
    return not c.base_text.strip()


def _trim(piece: list[Chunk]) -> list[Chunk]:
    """A piece of a wrapped line without the space at its ends (copies; the line keeps its chunks)."""
    piece = list(piece)
    for idx, strip in ((0, str.lstrip), (-1, str.rstrip)):
        c = piece[idx]
        part = c.base[idx]
        text = strip(part.text)
        if text != part.text and text:
            base = list(c.base)
            base[idx] = Part(text, part.start, part.end)
            piece[idx] = Chunk(base, c.ruby, c.wrap_before, c.singers)
    return piece


def wrap_line(ll: LaidLine, extent, avail: float, mode: str, max_pieces: int = 4) -> list[LaidLine]:
    """A line wider than ``avail`` (``extent``: chunks -> px) split into pieces that fit, each sung
    one after the other (they take turns in the rows like lines).  Breaks go between chunks (a chunk
    is a word or a kana run): with ``mode == "ai"`` where the AI readings suggested one first; else
    after punctuation / at a space, outside brackets; else the most even split between words.  A
    piece keeps at least 3 characters.  The translation goes with the first piece and lasts until
    the last one is sung."""
    if mode == "off" or len(ll.chunks) < 2 or extent(ll.chunks) <= avail:
        return [ll]
    chunks = ll.chunks
    depth, d = [], 0
    for c in chunks:  # bracket depth before each chunk
        depth.append(d)
        for ch in c.base_text:
            d = d + 1 if ch in _OPENING else max(0, d - 1) if ch in "」』)）]］】" else d
    best = None
    for b in range(1, len(chunks)):
        left, right = [c for c in chunks[:b]], [c for c in chunks[b:]]
        while left and _blank(left[-1]):
            left.pop()
        while right and _blank(right[0]):
            right.pop(0)
        if not left or not right:
            continue
        left, right = _trim(left), _trim(right)
        lt, rt = "".join(c.base_text for c in left).strip(), "".join(c.base_text for c in right).strip()
        if len(lt) < 3 or len(rt) < 3:
            continue
        prev, nxt = chunks[b - 1].base_text, chunks[b].base_text
        if nxt[:1] in "、。，,.!?！？…‥ー" or prev[-1:] in _OPENING:
            rank = 9  # never before closing punctuation or after an opening bracket, if avoidable
        elif mode == "ai" and chunks[b].wrap_before:
            rank = 0
        else:
            space = not prev.strip() or not nxt.strip() or prev[-1:].isspace() or nxt[:1].isspace()
            sentence = prev.rstrip()[-1:] in "、。，,.!?！？…‥"
            natural = space or sentence or prev[-1:] in _PUNCT or nxt[:1] in _PUNCT
            rank = (1 if space else 2 if sentence else 3 if natural else 5) if depth[b] == 0 else (4 if natural else 5)
        wl, wr = extent(left), extent(right)
        if rank and max(wl, wr) > 0.72 * (wl + wr):
            rank += 3  # very uneven: only when nothing better fits (the AI's own choice is kept)
        # the AI's choice is taken even when a piece is still too wide (that piece wraps again)
        fits = rank == 0 or (wl <= avail and wr <= avail)
        key = (not fits, rank, max(wl, wr))
        if best is None or key < best[0]:
            best = (key, left, right)
    if best is None:
        return [ll]
    out = []
    for piece in (best[1], best[2]):
        times = [p for c in piece for p in c.base if p.start is not None and p.end is not None]
        if not times:
            return [ll]
        a, z = min(p.start for p in times), max(p.end for p in times)
        sub = LaidLine(ll.line, piece, a, z, units=[u for u in ll.units if a <= u[0] <= z])
        out.append(sub)
    out[0].translation, out[0].trans_until = ll.translation, max(x.end for x in out)
    if max_pieces > 2:  # a piece still too wide: split again
        pieces: list[LaidLine] = []
        for i, x in enumerate(out):
            more = wrap_line(x, extent, avail, mode, max_pieces - 1) if extent(x.chunks) > avail else [x]
            pieces += more
        pieces[0].translation, pieces[0].trans_until = ll.translation, max(x.end for x in pieces)
        for x in pieces[1:]:
            x.translation, x.trans_until = None, None
        out = pieces
    return out


def plan_countdowns(lines: list[LaidLine], style: KaraokeStyle) -> None:
    """Which lines (sorted by start) get countdown dots: the first line (``countdown.intro``) and a
    line after a pause of at least ``min_gap_ms`` since everything before it was sung
    (``countdown.interlude``), unless the line itself says otherwise (``Line.countdown``).  Only
    the first piece of a wrapped line."""
    cd = style.countdown
    seen: set[str] = set()
    sung_to: Optional[int] = None
    for ll in lines:
        if ll.line.id not in seen:
            seen.add(ll.line.id)
            want = ll.line.countdown
            if want is None:
                want = cd.intro if sung_to is None else (cd.interlude and ll.start - sung_to >= cd.min_gap_ms)
            ll.countdown_ms = cd.dots * 1000 if want else 0
        sung_to = ll.end if sung_to is None else max(sung_to, ll.end)


def countdown_dots(appear: float, t0: float, n: int) -> list[float]:
    """When each of ``n`` dots (left to right) goes, for a line shown from ``appear`` and sung from
    ``t0``: the rightmost first, one a second, the leftmost as the singing starts; evenly over a shorter
    wait.  [] when there is hardly any time to show them."""
    wait = t0 - appear
    if wait < 300 or n < 1:
        return []
    step = min(1000.0, wait / n)
    return [t0 - i * step for i in range(n)]


def schedule(lines: list[LaidLine], style: KaraokeStyle) -> int:
    """Give each line a display window and a slot (rows stacked on screen); return how many
    lines had to go to an extra row.

    Lines take the rows in turn; a line with countdown dots takes the top row (when it is free) and
    the turn goes on from there.  A line appears ``lead_in_ms`` before its first syllable
    (with early show: as soon as its row is free, at most ``early_max_ms`` ahead), but never
    before the previous line in its row has gone; that line's hold is cut short if needed so
    each line is visible at least 0.2 s before it is sung.  A row whose line is still being
    sung is never taken: the line goes to the row that frees first, and when every row is
    busy (lines sung at the same time: a duet, a backing vocal inside a long line) to an
    extra row beyond the block (above it at the bottom of the frame, below it at the top).
    No line is ever dropped.
    """
    tm = style.timing
    n = max(1, style.layout.lines)
    lead, hold = max(0, tm.lead_in_ms), max(0, tm.hold_ms)
    early = max(lead, tm.early_max_ms) if tm.early_show else lead
    step = -1 if style.layout.position == "bottom" else 1  # extra rows go away from the frame edge
    last: dict[int, LaidLine] = {}
    turn = 0
    extra = 0
    for ll in lines:
        ll.show_to = ll.end + hold

        # a line with countdown dots appears as its countdown begins (the first dot goes a second later),
        # not earlier with the early show: the dots never sit still waiting
        lead_ll = ll.countdown_ms or lead
        early_ll = ll.countdown_ms or early

        def appear(slot: int) -> int:
            prev = last.get(slot)
            free_at = prev.show_to if prev is not None else 0
            t = max(0, ll.start - early_ll, min(free_at, ll.start - lead_ll))
            if prev is not None and prev.show_to > t:
                t = max(t, min(prev.show_to, max(prev.end, ll.start - 200)))
            return t

        free = [s for s in range(n) if s not in last or last[s].end <= ll.start]
        natural = turn % n
        if free:
            if ll.countdown_ms and 0 in free:
                # a line with countdown dots takes the top row: its dots sit above it, where no other
                # line is (in a lower row they would cover the line above); the next ones go on in turn
                slot = 0
            elif natural in free and appear(natural) <= ll.start - 200:
                slot = natural
            else:
                slot = min(free, key=lambda s: (appear(s), s != natural, s))
        else:
            extra += 1
            rows = sorted((s for s in last if not 0 <= s < n), key=abs)  # nearest the block first
            slot = next((s for s in rows if last[s].end <= ll.start), None)
            if slot is None:
                slot = (-1 if step < 0 else n) + step * len(rows)
        ll.slot = slot
        ll.show_from = appear(slot)
        prev = last.get(slot)
        if prev is not None:
            prev.show_to = min(prev.show_to, ll.show_from)
        last[slot] = ll
        if 0 <= slot < n:
            turn = slot + 1
    return extra


def visible_spans(ll: LaidLine, style: KaraokeStyle) -> list[tuple[int, int]]:
    """Display intervals of a scheduled line.

    Normally one interval.  If the line itself contains a long pause (the
    singer stops mid-line for an interlude), the line is hidden during it:
    it stays ``hold_ms`` after the last sung part before the pause and comes
    back ``lead_in_ms`` before singing resumes.
    """
    tm = style.timing
    min_pause = max(PAUSE_HIDE_MS, tm.hold_ms + tm.lead_in_ms + 2000)
    times = ll.units or [(p.start, p.end) for c in ll.chunks for p in c.base
                         if p.start is not None and p.end is not None]
    spans: list[tuple[int, int]] = []
    a = ll.show_from
    reach: Optional[int] = None
    for s, e in sorted(times):
        if reach is not None and s - reach >= min_pause:
            spans.append((a, reach + tm.hold_ms))
            a = s - tm.lead_in_ms
        reach = e if reach is None else max(reach, e)
    spans.append((a, ll.show_to))
    return [(x, y) for x, y in spans if y > x]


def alternate_insets(laid: list[LaidLine], geom: list[tuple], indent: float, avail: float) -> list[float]:
    """How far the left / right rows sit in from their margins (the alternating layout's indent).

    One indent for the whole song, so every left row starts at the same x and every right row ends
    at the same x.  It is the style's indent, reduced once as far as the lyrics need: every line
    must fit (a long line needs a smaller indent), and a left and a right row shown together keep
    the staircase — the upper (left) one starts and ends no further right than the lower (right)
    one, i.e. ``2 · indent <= avail − the longer line's extent``."""
    ext = [g[1] + g[2] + g[3] for g in geom]
    # (lines wider than the room between the margins reach toward the edges instead: no indent,
    # and they do not narrow everyone else's)
    side = [i for i, g in enumerate(geom) if g[5] != "center" and not (len(g) > 6 and g[6])]
    inset = indent
    for i in side:
        inset = min(inset, max(0.0, avail - ext[i]))  # the line itself must fit
        for j in side:
            if geom[j][5] == geom[i][5] or laid[j].show_to <= laid[i].show_from or laid[j].show_from >= laid[i].show_to:
                continue
            inset = min(inset, max(0.0, avail - max(ext[i], ext[j])) / 2)
    inset = max(0.0, inset)
    return [0.0 if g[5] == "center" or (len(g) > 6 and g[6]) else inset for g in geom]


def _sung_within(ll: LaidLine, a: float, b: float) -> tuple[float, float]:
    """First start and last end of the line's singing inside the display span [a, b)."""
    times = ll.units or [(p.start, p.end) for c in ll.chunks for p in c.base
                         if p.start is not None and p.end is not None]
    inside = [(s, e) for s, e in times if a <= s < b] or [(ll.start, ll.end)]
    return min(s for s, _ in inside), max(e for _, e in inside)


def fade_tag(fade_in: float, fade_out: float, t_from: float, t_to: float, sung_from: float, sung_to: float) -> str:
    """\\fad shortened so the line never fades while it is sung (a line right after another
    in the same row has almost no time to ease in)."""
    fi = int(max(0, min(fade_in, sung_from - t_from)))
    fo = int(max(0, min(fade_out, t_to - sung_to)))
    return f"\\fad({fi},{fo})" if (fi or fo) else ""


def translation_windows(laid: list[LaidLine], style: KaraokeStyle) -> list[tuple[LaidLine, int, int]]:
    """When each line's translation is shown as a single line (not under its lyric).

    One translation at a time, following the singing: from shortly before a line
    is sung until it is done (plus the hold), cut short when the next line starts.
    """
    tm = style.timing
    lead = min(tm.lead_in_ms, 800)
    out: list[tuple[LaidLine, int, int]] = []
    lines = [ll for ll in laid if ll.translation]
    prev_to = 0
    for i, ll in enumerate(lines):
        end = max(ll.end, ll.trans_until or 0)  # (a wrapped line: until its last piece is sung)
        t_to = end + tm.hold_ms
        if i + 1 < len(lines):
            t_to = min(t_to, max(lines[i + 1].start - lead, end))
        t_from = max(ll.start - lead, prev_to, 0)
        if t_to > t_from:
            out.append((ll, t_from, t_to))
            prev_to = t_to
    return out


Box = tuple[float, float, float, float, float, float]  # shown from, to (ASS time), x0, y0, x1, y1


def translation_placements(laid: list[LaidLine], style: KaraokeStyle, W: int, H: int, block_top: float,
                           block_h: float, size: float, measurer, margin_h: float,
                           margin_v: float) -> list[tuple[int, int, str, str, tuple[float, float, float, float], LaidLine]]:
    """(from, to, position tags, text, (x0, y0, x1, y1), line): the translation as one line at the
    other edge of the frame or just outside the lyric block."""
    lay = style.layout
    bottom = lay.position == "bottom"
    gap = size * 0.6
    if style.translation.position == "opposite":
        an, y = (8, margin_v) if bottom else (2, H - margin_v)
    else:  # "block": right outside the lyric block, on the side away from the edge
        an, y = (2, block_top - gap) if bottom else (8, block_top + block_h + gap)
    avail = max(1.0, W - 2 * margin_h)
    out = []
    for ll, t0, t1 in translation_windows(laid, style):
        text = ll.translation or ""
        w = measurer.width(text) or 1.0
        fs = f"\\fscx{avail / w * 100:.1f}\\fscy{avail / w * 100:.1f}" if w > avail else ""
        w, h = min(w, avail), size * min(1.0, avail / w)
        box = (W / 2 - w / 2, y if an == 8 else y - h, W / 2 + w / 2, y + h if an == 8 else y)
        out.append((t0, t1, f"\\an{an}\\pos({W / 2:.1f},{y:.1f}){fs}", text, box, ll))
    return out


def _karaoke(parts: list[Part], t0: int, tag: str) -> str:
    out, cursor = [], 0
    for p in parts:
        s = int(round((p.start - t0) / 10)) if p.start is not None else cursor
        e = int(round((p.end - t0) / 10)) if p.end is not None else s
        s = max(s, cursor)
        e = max(e, s)
        if s > cursor:
            out.append(f"{{\\k{s - cursor}}}")
        out.append(f"{{\\{tag}{e - s}}}{escape_text(p.text)}")
        cursor = e
    return "".join(out)


def chunk_widths(chunks: list[Chunk], m_main, m_ruby, ruby_size: float, fit: str) -> list[float]:
    """Width of each chunk on the line.

    A ruby wider than its lyric may overhang a neighbour that has no ruby of
    its own (usually kana) by up to one ruby character, and by no more than
    that neighbour is wide — half of it when a ruby on its other side overhangs
    it too — so two readings never meet, as in normal Japanese typesetting.  At
    the line's edges it may overhang by one ruby character (counted in the
    line's extent, ruby_overhang()).  With ``fit == "widen"`` the lyric is spaced
    out only by what is still needed; ``"overflow"`` never widens.
    """
    base = [m_main.width(c.base_text) for c in chunks]
    if fit != "widen" or m_ruby is None:
        return base
    pad = ruby_size * 0.1
    need = [max(0.0, m_ruby.width(c.ruby_text) + pad - bw) if c.ruby else 0.0 for c, bw in zip(chunks, base)]
    n = len(chunks)

    def room(i: int, j: int) -> float:
        """How far chunk i's ruby may reach over its neighbour j (-1 / n: the line's edge)."""
        if not 0 <= j < n:
            return ruby_size
        if chunks[j].ruby:
            return 0.0
        other = j - 1 if j < i else j + 1  # the neighbour's other side
        shared = 0 <= other < n and need[other] > 0
        return min(ruby_size, base[j] / 2 if shared else base[j])

    out = []
    for i, bw in enumerate(base):
        if need[i] <= 0:
            out.append(bw)
            continue
        # the ruby is centred, so the overhang is symmetric: limited by the tighter side
        allowance = min(room(i, i - 1), room(i, i + 1))
        out.append(bw + max(0.0, need[i] - 2 * allowance))
    return out


def ruby_overhang(chunks: list[Chunk], widths: list[float], m_ruby) -> tuple[float, float]:
    """How far the readings reach past the line's left and right edge (unscaled px)."""
    if m_ruby is None or not chunks:
        return 0.0, 0.0
    total = sum(widths)
    left = right = 0.0
    x = 0.0
    for c, w in zip(chunks, widths):
        if c.ruby:
            half = m_ruby.width(c.ruby_text) / 2
            left = max(left, half - (x + w / 2))
            right = max(right, x + w / 2 + half - total)
        x += w
    return max(0.0, left), max(0.0, right)


def piece_segments(c: Chunk, cx: float, width: float, t0: int, m_main) -> list[tuple[int, int, float, float]]:
    """Where and when the lyric's sweep runs over a chunk: [(start, end) ms from t0, x from, x to].

    The lyric's \\kf fills each piece from its left to its right edge over the piece's time
    (\\k: at once when it starts) and jumps over the space between pieces; ``width`` is the
    drawn width of the chunk's text centred at ``cx``.  The same centisecond timing as
    _karaoke()."""
    widths = [m_main.width(p.text) for p in c.base]
    norm = width / (sum(widths) or 1.0)
    x, cursor = cx - width / 2, 0
    segs: list[tuple[int, int, float, float]] = []
    for p, pw in zip(c.base, widths):
        s = int(round((p.start - t0) / 10)) if p.start is not None else cursor
        e = int(round((p.end - t0) / 10)) if p.end is not None else s
        s = max(s, cursor)
        e = max(e, s)
        segs.append((s * 10, e * 10, x, x + pw * norm))
        x += pw * norm
        cursor = e
    return segs


def sweep_time(segs: list[tuple[int, int, float, float]], x: float, instant: bool) -> int:
    """When the sweep of piece_segments() reaches ``x`` (ms from t0): before the chunk its
    start, past it its end."""
    for s, e, xa, xb in segs:
        if x <= xa:
            return s
        if x < xb:
            return s if instant or xb <= xa else int(s + (e - s) * (x - xa) / (xb - xa))
    return segs[-1][1] if segs else 0


Move = tuple[int, int, float]  # the sweep edge goes to x over [from, to] ms (to = from + 1: a jump)


def sweep_moves(segs: list[tuple[int, int, float, float]], hi: float, instant: bool) -> list[Move]:
    """How the sweep edge over a chunk moves (from x = 0: nothing sung).

    Nothing is sung before the chunk's first piece starts; then the edge jumps to the lyric's
    left edge (so a reading reaching further left turns sung there at once), follows the
    lyric's sweep, and when the last piece is done jumps to ``hi`` (the right edge of what
    is drawn: a reading wider than its lyric turns sung completely).  Lyric and ruby of a
    chunk follow the same sweep over the lyric, so both are cut by one vertical line meanwhile."""
    moves: list[Move] = []
    if not segs:
        return moves
    cur: Optional[float] = None
    for i, (s, e, xa, xb) in enumerate(segs):
        if i == len(segs) - 1 and (instant or e <= s):
            xb = max(xb, hi)
        if cur is None or abs(xa - cur) > 0.5:  # on to the next piece
            moves.append((s, s + 1, xa))
        moves.append((s, e, xb) if not instant and e > s else (s, s + 1, xb))
        cur = xb
    last_e = segs[-1][1]
    if hi > (cur or 0) + 0.5:
        moves.append((last_e, last_e + 1, hi))
    return moves


def sweep_clip(segs: list[tuple[int, int, float, float]], hi: float, instant: bool, H: int,
               inverse: bool = False) -> str:
    """An animated \\clip (``inverse``: \\iclip, the rest) whose right edge is the sweep over one chunk
    (sweep_moves())."""
    name = "iclip" if inverse else "clip"

    def clip(x: float) -> str:
        return f"\\{name}(0,0,{int(round(x))},{H})"

    if not segs:
        return clip(hi)
    return clip(0) + "".join(f"\\t({a},{b},{clip(x)})" for a, b, x in sweep_moves(segs, hi, instant))


Rect = tuple[float, float, float, float]  # x0, y0, x1, y1


def band_clip(moves: list[Move], rect: Rect, sung: bool, hi: float) -> str:
    """An animated rectangular \\clip showing one band (``rect``) of a chunk: its part left of the sweep
    edge (``sung``) or right of it.  The two meet at the same rounded x, and neighbouring bands share
    their rounded edges, so every pixel of the chunk comes from exactly one of them."""
    x0, y0, x1, y1 = (int(round(v)) for v in rect)

    def at(x: float) -> int:
        return min(max(int(round(x)), x0), x1)

    def clip(x: float) -> str:
        e = at(x)
        return f"\\clip({x0},{y0},{e},{y1})" if sung else f"\\clip({e},{y0},{x1},{y1})"

    if not moves:
        return clip(hi)
    out, cur = [clip(0)], 0.0
    for a, b, x in moves:
        if b - a <= 1 or abs(x - cur) < 1e-9:  # a jump
            if at(x) != at(cur):
                out.append(f"\\t({a},{b},{clip(x)})")
            cur = x
            continue
        # a steady move from cur to x: only the stretch inside the band animates this clip
        lo, hi_ = sorted((cur, x))
        cuts = [a, b] + [int(round(a + (b - a) * (edge - cur) / (x - cur)))
                         for edge in (x0, x1) if lo < edge < hi_]
        cuts = sorted(set(cuts))
        for ta, tb in zip(cuts, cuts[1:]):
            xa = cur + (x - cur) * (ta - a) / (b - a)
            xb = cur + (x - cur) * (tb - a) / (b - a)
            if at(xa) != at(xb):
                out.append(f"\\t({ta},{max(tb, ta + 1)},{clip(xb)})")
        cur = x
    return "".join(out)


@dataclass
class Band:
    """One copy of a chunk's text for singers: in ``rect`` (None: all of it), in singer ``singer``'s
    colours (0: the style's own) or, for a gradient strip, in ``colors`` written into the event."""

    rect: Optional[Rect]
    singer: int
    colors: Optional[dict[str, str]] = None
    blend: bool = False


BASE_BAND = Band(None, 0)
_ROLES = ("sung", "unsung", "outline", "glow_sung", "glow_unsung", "translation", "sparkle")


def plan_bands(ids: tuple[int, ...], colors: dict[int, dict[str, str]], mix: str, direction: str,
               top: float, bottom: float, span: Optional[tuple[float, float]], W: int, H: int,
               k: float = 1.0, within: Optional[tuple[float, float]] = None) -> list[Band]:
    """The copies a chunk's text is drawn in: one for a single singer; for several, a band per singer
    (``mix == "split"``) or thin strips blending their colours (``"gradient"``), stacked top to
    bottom (``direction == "vertical"``, between ``top`` and ``bottom``: where the glyphs are) or
    side by side across ``span`` (``"horizontal"``: the x range of the whole run sung together, so a
    run of chunks is split / blended once from its left to its right end).  The outer bands reach the
    frame's edges, so outline and glow beyond the glyphs are drawn too.  ``within``: only the bands
    reaching into this x range (what the chunk can draw)."""
    ids = tuple(i for i in ids if i in colors)
    if not ids:
        return [BASE_BAND]
    if len(ids) == 1:
        return [Band(None, ids[0], colors[ids[0]])]
    n = len(ids)
    grad = mix == "gradient"

    def band(i: int, steps: int, rect: Rect) -> Band:
        if not grad:
            return Band(rect, ids[i], colors[ids[i]])
        from .themes import blend

        t = (i + 0.5) / steps
        cols = {r: blend([colors[j][r] for j in ids], t) for r in _ROLES}
        return Band(rect, ids[min(n - 1, int(t * n))], cols, True)

    out: list[Band] = []
    if direction == "vertical" or span is None:
        steps = n if not grad else max(2 * n, min(16, round((bottom - top) / (5 * k))))
        edges = [top + (bottom - top) * i / steps for i in range(steps + 1)]
        edges[0], edges[-1] = 0, H
        for i in range(steps):
            out.append(band(i, steps, (0, edges[i], W, edges[i + 1])))
        return out
    a, b = span
    steps = n if not grad else max(2 * n, min(32, round((b - a) / (10 * k))))
    edges = [a + (b - a) * i / steps for i in range(steps + 1)]
    edges[0], edges[-1] = 0, W
    for i in range(steps):
        if within is None or (edges[i + 1] > within[0] and edges[i] < within[1]):
            out.append(band(i, steps, (edges[i], 0, edges[i + 1], H)))
    return out


def _actor(name: str) -> str:
    """A singer's name for the Name field of an event (no commas, braces or line breaks)."""
    return re.sub(r"[,{}\\\x00-\x1f]", "", name or "").strip()[:40]


def _unit_times(result: AlignmentResult) -> dict[str, tuple[Optional[int], Optional[int]]]:
    return {u.unit_id: (u.start_ms, u.end_ms) for u in result.units}


def _romaji(project: Project) -> dict[str, str]:
    from ..reading.profiles import get_profile

    prof = get_profile("ja-hepburn")
    out: dict[str, str] = {}
    for ln in project.lyrics.lines:
        units = [u for s in ln.segments for u in s.units]
        segs = [s for s in ln.segments for _ in s.units]
        if not units:
            continue
        texts = prof.unit_texts([u.reading for u in units], [s.lang for s in segs], [u.flags for u in units])
        for u, t in zip(units, texts):
            out[u.id] = t
    return out


def resolution(project: Project) -> tuple[int, int]:
    """The frame the subtitles are made for: the background's, else the video's, else 1920×1080."""
    b = project.background
    if b is not None:
        from .background import frame_for

        return frame_for(b.width, b.height)
    v = project.video
    if v is not None and v.width and v.height:
        return int(v.width), int(v.height)
    return DEFAULT_SIZE


# Layers, bottom to top: effects drawn behind the text, translation glow, translation,
# text glow (unsung), text glow (sung), lyrics, ruby; then effects in front (7) and the title card (8, 9).
L_FX, L_TRANS_GLOW, L_TRANS, L_GLOW, L_GLOW_SUNG, L_MAIN, L_RUBY = 0, 1, 2, 3, 4, 5, 6


def build_ass(project: Project, result: AlignmentResult, style: Optional[KaraokeStyle] = None, *,
              time_offset_ms: float = 0.0, size: Optional[tuple[int, int]] = None) -> tuple[str, list[str]]:
    """Return (ASS text, warnings).  ``size``: the frame the subtitles are drawn on (PlayRes)."""
    from .effects import FX_STYLE, Syllable, ball_room, syllable_events
    from .info import info_events, info_style

    style = style or project.karaoke
    audio_offset_ms = time_offset_ms  # the title card keeps real time
    # show / highlight everything a little before it is sung (display only)
    time_offset_ms -= style.timing.advance_ms
    W, H = size or resolution(project)
    W, H = max(16, int(W)), max(16, int(H))
    k = W / REF_WIDTH  # by width: the text takes the same share of the frame's width at any size
    lay, txt, rb, tr, glow, tm = style.layout, style.text, style.ruby, style.translation, style.glow, style.timing
    # a style may name a font this machine does not have (styles travel between machines, the built-in
    # 暖阳 uses macOS fonts): then the default font is used for measuring *and* drawing, so the layout
    # still matches what libass draws
    missing_fonts: list[str] = []

    def usable(name: str) -> str:
        if name and not installed(name):
            missing_fonts.append(name)
            return ""
        return name

    family = usable(txt.font) or default_family()
    ruby_family = (usable(rb.font) or family) if rb.enabled else family
    trans_family = (usable(tr.font) or family) if tr.enabled else family
    if tr.enabled and trans_family == BUNDLED_JP and not usable(tr.font) and bundled(BUNDLED_SC):
        trans_family = BUNDLED_SC  # the bundled font's Chinese face: a Chinese translation in Chinese glyph forms
    font_notes: list[str] = []
    if tr.enabled and not system_han_fallback():
        # a translation (usually Chinese) in a Japanese font lacks many characters; libass fills them in
        # from a font of its own choosing at that font's scale, so they jump in size (Windows: Yu Gothic).
        # A font with every character is used instead — unless it is the one the style chose.
        trans_text = "".join(ln.translation or "" for ln in project.lyrics.sung_lines())
        miss = lacking(trans_family, tr.bold, trans_text) if trans_text else ""
        if miss:
            chosen = bool(tr.font) and trans_family == tr.font
            better = None if chosen else covering_family(trans_text, tr.bold, HAN_FAMILIES)
            if better:
                font_notes.append(f"翻译里有 {trans_family} 没有的字（如「{miss[:6]}」），翻译改用 {better}")
                trans_family = better
            else:
                font_notes.append(f"字体 {trans_family} 没有翻译里的一些字（如「{miss[:6]}」），这些字会用别的字体显示，"
                                  "大小可能不一致；可以在“卡拉OK字幕”里给翻译换一个中文字体")
    # guards for styles built in code without validation: every size positive, room left between the margins
    main_size = max(1.0, txt.size * k)
    ruby_size = max(1.0, main_size * max(1, rb.size_pct) / 100)
    trans_size = max(1.0, main_size * max(1, tr.size_pct) / 100)
    gap = rb.gap * k
    trans_gap = 6 * k
    m_main = Measurer(family, txt.bold, main_size)
    m_trans = Measurer(trans_family, tr.bold, trans_size)
    m_ruby = Measurer(ruby_family, txt.bold, ruby_size) if rb.enabled else None
    times = _unit_times(result)
    romaji = _romaji(project) if (rb.enabled and rb.script == "romaji") else {}
    # singers (多人演唱): each one's colours, by number; parts sung together are drawn in bands
    from .themes import singer_colors

    sg = style.singers
    scol: dict[int, dict[str, str]] = {i + 1: singer_colors(m) for i, m in enumerate(sg.members)}
    sname = {i + 1: _actor(m.name) or str(i + 1) for i, m in enumerate(sg.members)}
    warnings: list[str] = []
    if missing_fonts:
        warnings.append(f"这台电脑没有字体 {'、'.join(sorted(set(missing_fonts)))}，已改用 {family}")
    warnings += font_notes

    margin_h = min(lay.margin_h * k, W * 0.4)
    avail = W - 2 * margin_h

    def extent(chunks: list[Chunk]) -> float:
        """How wide a line of these chunks is drawn, readings reaching past its ends included."""
        widths = chunk_widths(chunks, m_main, m_ruby, ruby_size, rb.fit)
        over_l, over_r = ruby_overhang(chunks, widths, m_ruby)
        return (sum(widths) or 1.0) + over_l + over_r

    covered = set(result.coverage.line_ids) if not result.coverage.full else None
    laid: list[LaidLine] = []
    skipped = 0
    for ln in project.lyrics.sung_lines():
        if covered is not None and ln.id not in covered:
            continue
        chunks = build_chunks(ln, times, style, romaji)
        starts = [p.start for c in chunks for p in c.base if p.start is not None]
        ends = [p.end for c in chunks for p in c.base if p.end is not None]
        if not starts or not ends:
            skipped += 1
            continue
        unit_times = [times[u.id] for u in ln.units() if u.id in times and None not in times[u.id]]
        # a line too wide for the room between the margins is wrapped into pieces
        laid += wrap_line(LaidLine(ln, chunks, min(starts), max(ends),
                                   translation=(ln.translation or None) if tr.enabled else None,
                                   units=unit_times), extent, avail, lay.wrap)  # type: ignore[arg-type]
    if skipped:
        warnings.append(f"{skipped} 行没有任何时间，未写入字幕")
    if tr.enabled and not any(ll.translation for ll in laid):
        warnings.append("已开启翻译字幕，但歌词里没有翻译")
    laid.sort(key=lambda x: x.start)
    plan_countdowns(laid, style)
    extra = schedule(laid, style)
    if extra:
        warnings.append(f"{extra} 行与其他行同时演唱（对唱 / 和声），已临时显示在歌词区外多出的一行")

    has_ruby = rb.enabled and any(c.ruby for ll in laid for c in ll.chunks)
    per_line_trans = tr.enabled and tr.position == "line"
    ruby_h = (ruby_size + gap) if has_ruby else 0.0
    slot_h = main_size + ruby_h + ((trans_size + trans_gap) if per_line_trans else 0)
    n = max(1, lay.lines)
    # the bouncing ball hops above each line: rows keep room for it
    spacing = max(lay.line_spacing * k, ball_room(style, main_size, k))
    margin_v = min(lay.margin_v * k, H * 0.4)
    block_h = n * slot_h + (n - 1) * spacing
    block_top = max(0.0, H - margin_v - block_h) if lay.position == "bottom" else margin_v
    top_row = min((ll.slot for ll in laid), default=0)

    fade_in, fade_out = max(0, tm.fade_in_ms), max(0, tm.fade_out_ms)
    g_alpha = f"&H{int(round(255 * (1 - glow.strength / 100))):02X}&"

    def glow_tags(color: str, width: float, font: str, size: float, bold: bool) -> str:
        return (f"\\fn{font}\\fs{size:.1f}\\b{1 if bold else 0}\\3c{bgr_tag(color)}\\3a{g_alpha}"
                f"\\bord{width:.1f}\\blur{glow.blur * k:.1f}\\shad0")

    events: list[str] = []
    boxes: list[Box] = []  # where the lyrics and translations are, and when (for the title card)

    def emit(layer: int, t_from: float, t_to: float, name: str, tags: str, body: str, fad: str,
             actor: str = "") -> None:
        events.append(f"Dialogue: {layer},{ass_time(t_from + time_offset_ms)},{ass_time(t_to + time_offset_ms)},"
                      f"{name},{actor},0,0,0,,{{{tags}{fad}}}{body}")

    def band_look(b: Band, name: str, unsung: str) -> tuple[str, str, str, str, str]:
        """(style name, unsung colour, sung glow, unsung glow, actor) of one band; a band of the style's own
        colours uses the style as before."""
        if not b.singer:
            return name, unsung, glow.color_sung, glow.color_unsung, ""
        c = b.colors or scol[b.singer]
        return f"{name}_{b.singer}", c["unsung"], c["glow_sung"], c["glow_unsung"], sname.get(b.singer, "")

    def blend_tags(b: Band, *roles: str) -> str:
        """A gradient strip's own colours (\\1c sung, \\2c unsung, \\3c outline) written into the event."""
        if not (b.blend and b.colors):
            return ""
        tag_of = {"sung": "1c", "unsung": "2c", "outline": "3c"}
        return "".join(f"\\{tag_of[r]}{bgr_tag(b.colors[r])}" for r in roles)

    def cut_of(b: Band) -> str:
        return "" if b.rect is None else "\\clip({},{},{},{})".format(*(int(round(v)) for v in b.rect))

    def emit_text(layer: int, t_from: float, t_to: float, name: str, pos: str, parts: list[Part], width: float,
                  with_glow: bool, font: str, size: float, fad: str,
                  bands: tuple[list[Band], list[Band]] = ([BASE_BAND], [BASE_BAND])) -> None:
        """A karaoke text event, with its glow layers when the glow is on (singers: one copy per band,
        each cut to its band; ``bands``: the text's and the glow's)."""
        plain = escape_text("".join(p.text for p in parts))
        fill, glows = bands
        if glow.enabled and with_glow:
            for b in glows:
                _, _, g_sung, g_unsung, actor = band_look(b, name, "")
                emit(L_GLOW, t_from, t_to, "KGlow", pos + glow_tags(g_unsung, width, font, size, txt.bold) + cut_of(b),
                     plain, fad, actor)
                # \ko: the border (the glow) appears as each syllable is sung
                emit(L_GLOW_SUNG, t_from, t_to, "KGlow", pos + glow_tags(g_sung, width, font, size, txt.bold) + cut_of(b),
                     _karaoke(parts, int(t_from), "ko"), fad, actor)
        for b in fill:
            sty, _, _, _, actor = band_look(b, name, "")
            emit(layer, t_from, t_to, sty, pos + blend_tags(b, "sung", "unsung", "outline") + cut_of(b),
                 _karaoke(parts, int(t_from), tag), fad, actor)

    ruby_unsung = (txt if rb.follow_colors else rb).color_unsung

    def emit_following(layer: int, name: str, t_from: float, t_to: float, pos: str, text: str, unsung: str,
                       font: str, size: float, glow_width: float, with_glow: bool,
                       segs: list[tuple[int, int, float, float]], hi: float, fad: str,
                       bands: tuple[list[Band], list[Band]] = ([BASE_BAND], [BASE_BAND])) -> None:
        """Text swept by a moving \\clip instead of \\kf: the text in the unsung colour cut right of the
        sweep and in the sung colour (the style's primary) cut left of it, so every pixel comes from one
        of the two, also while fading.  Singer bands: the same for each band, both cuts inside it."""
        plain = escape_text(text)
        moves: list[Move] = []

        def cuts(b: Band) -> tuple[str, str]:
            nonlocal moves
            if b.rect is None:
                return sweep_clip(segs, hi, instant, H), sweep_clip(segs, hi, instant, H, True)
            moves = moves or sweep_moves(segs, hi, instant)
            return band_clip(moves, b.rect, True, hi), band_clip(moves, b.rect, False, hi)

        fill, glows = bands
        if glow.enabled and with_glow:
            for b in glows:
                clip, iclip = cuts(b)
                _, _, g_sung, g_unsung, actor = band_look(b, name, unsung)
                emit(L_GLOW, t_from, t_to, "KGlow",
                     pos + glow_tags(g_unsung, glow_width, font, size, txt.bold) + iclip, plain, fad, actor)
                emit(L_GLOW_SUNG, t_from, t_to, "KGlow",
                     pos + glow_tags(g_sung, glow_width, font, size, txt.bold) + clip, plain, fad, actor)
        for b in fill:
            clip, iclip = cuts(b)
            sty, un, _, _, actor = band_look(b, name, unsung)
            emit(layer, t_from, t_to, sty, pos + f"\\1c{bgr_tag(un)}" + blend_tags(b, "outline") + iclip, plain,
                 fad, actor)
            emit(layer, t_from, t_to, sty, pos + blend_tags(b, "sung", "outline") + clip, plain, fad, actor)

    def emit_trans(t_from: float, t_to: float, pos: str, text: str, fad: str, ids: tuple[int, ...],
                   span: tuple[float, float]) -> None:
        """A translation in its own colours; its glow in the colours of the line's singers (``ids``, in the
        order they sing; several: blended from left to right across ``span``, the text's x range)."""
        ids = ids if tr.singer_glow else ()
        actor = sname.get(ids[0], "") if ids else ""
        if glow.enabled and tr.glow:
            size = glow.size * k * 0.7
            if len(ids) >= 2:
                for b in plan_bands(ids, scol, "gradient", "horizontal", 0, 0, span, W, H, k):
                    emit(L_TRANS_GLOW, t_from, t_to, "KGlow",
                         pos + glow_tags(b.colors["glow_unsung"], size, trans_family, trans_size, tr.bold) + cut_of(b),
                         escape_text(text), fad, actor)
            else:
                emit(L_TRANS_GLOW, t_from, t_to, "KGlow",
                     pos + glow_tags(scol[ids[0]]["glow_unsung"] if ids else glow.color_unsung, size, trans_family,
                                     trans_size, tr.bold),
                     escape_text(text), fad, actor)
        emit(L_TRANS, t_from, t_to, "KTrans", pos, escape_text(text), fad, actor)

    def live(ids: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(i for i in ids if i in scol)

    def line_singers(ll: LaidLine) -> tuple[int, ...]:
        """Who sings a line, in the order they first sing in it (its parts from left to right)."""
        out: list[int] = []
        for c in ll.chunks:
            for i in live(c.singers):
                if i not in out:
                    out.append(i)
        return tuple(out) or live(tuple(ll.line.singers))

    ruby_bands = rb.follow_colors  # a ruby with its own colours keeps them (no singer colours)
    main_ink = m_main.ink
    ruby_ink = m_ruby.ink if m_ruby is not None else (0.0, 1.0)

    def bands_for(ids: tuple[int, ...], cx: float, width: float, bottom_y: float, size: float,
                  ink: tuple[float, float], run: Optional[tuple[float, float]],
                  ruby: bool = False) -> tuple[list[Band], list[Band]]:
        """The bands of a chunk's text and of its glow, in the look of these singers (their combination's
        or the singers' setting).  The glow of a part sung together always blends (a blurred edge cut
        sharp between two colours shows as a seam beside the glyphs).  A reading is split like its
        lyric, or takes the first singer's colours (KaraokeSingers.ruby)."""
        ids = live(ids)
        mix, direction = sg.look(ids)
        if len(ids) < 2 or (ruby and not sg.ruby_split(direction)):
            one = plan_bands(ids[:1], scol, mix, direction, 0, 0, None, W, H, k)
            return one, one
        top = bottom_y - size
        y0, y1 = top + size * ink[0], top + size * ink[1]
        span = run if direction == "horizontal" else None
        reach = (cx - width / 2 - edge, cx + width / 2 + edge)
        fill = plan_bands(ids, scol, mix, direction, y0, y1, span, W, H, k, reach)
        if not glow.enabled or mix == "gradient":
            return fill, fill
        return fill, plan_bands(ids, scol, "gradient", direction, y0, y1, span, W, H, k, reach)

    def runs_of(ll: LaidLine, cxs: list[float], scale: float) -> list[Optional[tuple[float, float]]]:
        """For each chunk sung together (side by side): the x range of its whole run, the neighbouring
        chunks with the same singers (their lyric and readings)."""
        out: list[Optional[tuple[float, float]]] = [None] * len(ll.chunks)
        i = 0
        while i < len(ll.chunks):
            ids = live(ll.chunks[i].singers)
            j = i
            while j + 1 < len(ll.chunks) and live(ll.chunks[j + 1].singers) == ids:
                j += 1
            if len(ids) >= 2 and sg.look(ids)[1] == "horizontal":
                lo, hi = float("inf"), float("-inf")
                for c, cx in zip(ll.chunks[i:j + 1], cxs[i:j + 1]):
                    half = max(m_main.width(c.base_text), m_ruby.width(c.ruby_text) if (c.ruby and m_ruby) else 0) * scale / 2
                    lo, hi = min(lo, cx - half), max(hi, cx + half)
                for x in range(i, j + 1):
                    out[x] = (lo, hi)
            i = j + 1
        return out

    def emit_countdown(ll: LaidLine, t_from: float, x0: float, line_top: float, scale: float) -> float:
        """Countdown dots above the start of the line, going one by one until its first syllable (their
        times shifted like the lyrics, so the last goes as the sweep starts); returns their top edge."""
        goes = countdown_dots(t_from, ll.start, style.countdown.dots)
        if not goes:
            return line_top
        r = max(3.0, main_size * scale * 0.12)
        cy = line_top - r * 1.9
        ids = live(ll.chunks[0].singers) if ll.chunks else ()
        c = scol[ids[0]] if ids else None
        fill = c["sung"] if c else txt.color_sung
        outline = c["outline"] if c else txt.outline_color
        kappa = 0.5523 * r
        circle = (f"m {r:.1f} 0 b {r + kappa:.1f} 0 {2 * r:.1f} {r - kappa:.1f} {2 * r:.1f} {r:.1f} "
                  f"b {2 * r:.1f} {r + kappa:.1f} {r + kappa:.1f} {2 * r:.1f} {r:.1f} {2 * r:.1f} "
                  f"b {r - kappa:.1f} {2 * r:.1f} 0 {r + kappa:.1f} 0 {r:.1f} "
                  f"b 0 {r - kappa:.1f} {r - kappa:.1f} 0 {r:.1f} 0")
        for i, t_go in enumerate(goes):
            cx = x0 + r + i * r * 3.2
            fi = int(max(0, min(fade_in, (t_go - t_from) / 2)))
            tags = (f"\\an5\\pos({cx:.1f},{cy:.1f})\\1c{bgr_tag(fill)}\\3c{bgr_tag(outline)}"
                    f"\\bord{max(1.0, txt.outline * k * 0.5):.1f}\\shad0\\p1")
            emit(L_RUBY, t_from, t_go, "KDots", tags, circle, f"\\fad({fi},0)" if fi else "",
                 sname.get(ids[0], "") if ids else "")
        return cy - r * 1.5

    def fx_color(ids: tuple[int, ...]) -> Optional[str]:
        """Effects fired by a singer's part: in the singer's colours (its sung glow / sung colour when the
        effect follows, else a pale tint like a template's sparkles)."""
        ids = live(ids)
        if not ids:
            return None
        c = scol[ids[0]]
        if style.effects.color:
            return c["sparkle"]
        return c["glow_sung"] if glow.enabled else c["sung"]

    tag = "kf" if tm.highlight == "sweep" else "k"
    instant = tm.highlight != "sweep"
    following = rb.sweep == "base" and has_ruby
    edge = (max(txt.outline, 0) + max(txt.shadow, 0) + (glow.size + glow.blur if glow.enabled else 0)) * k + 2
    fx_on = style.effects.kind != "none"
    syllables: list[Syllable] = []
    # widths first: how far a line sits in from its edge depends on the lines shown next to it
    # a line still wider than the room between the margins (it could not wrap) may reach out to
    # `edge_x` from the frame's edges, moving only as far as it needs to; only wider than that it shrinks
    edge_x = min(lay.edge_margin * k, margin_h)
    room = W - 2 * edge_x
    geom = []
    for ll in laid:
        widths = chunk_widths(ll.chunks, m_main, m_ruby, ruby_size, rb.fit)
        line_w = sum(widths) or 1.0
        over_l, over_r = ruby_overhang(ll.chunks, widths, m_ruby)
        extent = line_w + over_l + over_r  # readings reaching past the line's ends count too
        scale = min(1.0, room / extent) if lay.shrink_long_lines else 1.0
        if scale < 1.0:
            warnings.append(f"「{ll.text}」过长，已缩小到 {scale:.0%}")
        align = "center"
        if lay.arrangement == "alternate" and n > 1 and 0 <= ll.slot < n:
            align = "left" if ll.slot == 0 else ("right" if ll.slot == n - 1 else "center")
        wide = extent * scale > avail + 0.5
        geom.append((widths, line_w * scale, over_l * scale, over_r * scale, scale, align, wide))
    insets = alternate_insets(laid, geom, lay.alternate_indent * k, avail)
    for ll, (widths, line_w, over_l, over_r, scale, align, wide), inset in zip(laid, geom, insets):
        ext = line_w + over_l + over_r
        if align == "left":
            # a wide line keeps its start while it can, else moves toward the left edge
            start_x = max(edge_x, min(margin_h, W - edge_x - ext)) if wide else margin_h + inset
            x0 = start_x + over_l
        elif align == "right":
            end_x = min(W - edge_x, max(W - margin_h, edge_x + ext)) if wide else W - margin_h - inset
            x0 = end_x - over_r - line_w
        else:  # centred, but its readings kept inside the margins (the edges for a wide line)
            side = edge_x if wide else margin_h
            lo, hi = side + over_l, W - side - over_r - line_w
            x0 = min(max((W - line_w) / 2, lo), hi) if lo <= hi else (lo + hi) / 2
        slot_top = block_top + ll.slot * (slot_h + spacing)
        main_y = slot_top + ruby_h + main_size
        # shrunk lines keep their bottom edge where it was
        ruby_y = main_y - main_size * scale - gap * scale
        line_top = (ruby_y - ruby_size * scale) if has_ruby else (main_y - main_size * scale)
        room_above = spacing if ll.slot > top_row else line_top  # free room above the line (the ball)
        fs = "" if scale >= 1.0 else f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}"
        spans = visible_spans(ll, style)
        if len(spans) > 1:
            pause = (spans[1][0] - spans[0][1] + tm.hold_ms + tm.lead_in_ms) / 1000
            warnings.append(f"「{ll.line.text}」中间停顿约 {pause:.0f} 秒，停顿期间暂时隐藏该行")
        cxs, x = [], x0
        for w in widths:
            cxs.append(x + w * scale / 2)
            x += w * scale
        for n_span, (t_from, t_to) in enumerate(spans):
            # an ASS event cannot start before 0:00: start it there and time the sweep from there,
            # or the fill would lag by what was cut off
            t_from = max(t_from, -time_offset_ms)
            if t_to <= t_from:
                continue
            fad = fade_tag(fade_in, fade_out, t_from, t_to, *_sung_within(ll, t_from, t_to))
            bottom = main_y + ((trans_gap + trans_size) if (ll.translation and per_line_trans) else 0)
            top = line_top
            if ll.countdown_ms and n_span == 0:
                top = emit_countdown(ll, t_from, x0, line_top, scale)
            boxes.append((t_from + time_offset_ms, t_to + time_offset_ms, x0 - over_l, top,
                          x0 + line_w + over_r, bottom))
            runs = runs_of(ll, cxs, scale)
            for c, cx, run in zip(ll.chunks, cxs, runs):
                main_pos = f"\\an2\\pos({cx:.1f},{main_y:.1f}){fs}"
                ruby_pos = f"\\an2\\pos({cx:.1f},{ruby_y:.1f}){fs}"
                bw = m_main.width(c.base_text) * scale
                rw = m_ruby.width(c.ruby_text) * scale if (c.ruby and m_ruby is not None) else 0.0
                segs = piece_segments(c, cx, bw, int(t_from), m_main) if (following or fx_on) else []
                main_bands = bands_for(c.singers, cx, bw, main_y, main_size * scale, main_ink, run)
                r_bands = ([BASE_BAND], [BASE_BAND])
                if c.ruby and m_ruby is not None and ruby_bands:
                    r_bands = bands_for(c.singers, cx, rw, ruby_y, ruby_size * scale, ruby_ink, run, ruby=True)
                if following:
                    # lyric and ruby cut by one computed line: libass places its own \\kf boundary by
                    # glyph ink, a few pixels away from any position computed outside it
                    # once sung, the edges beyond the last glyph's advance (outline, glow) turn too
                    hi = cx + bw / 2 + edge
                    emit_following(L_MAIN, "KMain", t_from, t_to, main_pos, c.base_text, txt.color_unsung,
                                   family, main_size, glow.size * k, True, segs, hi, fad, main_bands)
                else:
                    emit_text(L_MAIN, t_from, t_to, "KMain", main_pos, c.base, glow.size * k, True, family,
                              main_size, fad, main_bands)
                if c.ruby and following:
                    hi = max(cx + bw / 2, cx + rw / 2) + edge
                    emit_following(L_RUBY, "KRuby", t_from, t_to, ruby_pos, c.ruby_text, ruby_unsung, ruby_family,
                                   ruby_size, glow.size * k * 0.55, glow.ruby, segs, hi, fad, r_bands)
                elif c.ruby:
                    emit_text(L_RUBY, t_from, t_to, "KRuby", ruby_pos, c.ruby, glow.size * k * 0.55, glow.ruby,
                              ruby_family, ruby_size, fad, r_bands)
                if fx_on:
                    group = f"{ll.line.id}@{t_from}"
                    color = fx_color(c.singers)
                    syllables += _syllables(c.base, cx, main_y, main_size, scale, m_main, family, False, t_from,
                                            t_to, group, line_top, room_above, color=color)
                    if c.ruby and style.effects.ruby and m_ruby is not None:
                        # with the ruby following the lyric, a reading syllable is sung when the sweep
                        # passes it, not at its own time
                        timing = (lambda xa, xb: (int(t_from) + sweep_time(segs, xa, instant),
                                                  int(t_from) + sweep_time(segs, xb, instant))) if following else None
                        syllables += _syllables(c.ruby, cx, ruby_y, ruby_size, scale, m_ruby, ruby_family, True,
                                                t_from, t_to, group, line_top, room_above, timing, color=color)
            if ll.translation and per_line_trans:
                ty = main_y + trans_gap + trans_size
                # a translation wider than the room between the margins is shrunk, and kept inside them
                tw = m_trans.width(ll.translation) or 1.0
                tfs = f"\\fscx{avail / tw * 100:.1f}\\fscy{avail / tw * 100:.1f}" if tw > avail else ""
                tw = min(tw, avail)
                an, tx = {"left": (1, max(margin_h, min(x0, W - margin_h - tw))),
                          "right": (3, min(W - margin_h, max(x0 + line_w, margin_h + tw))),
                          "center": (2, W / 2)}[align]
                left = {1: tx, 3: tx - tw, 2: tx - tw / 2}[an]
                emit_trans(t_from, t_to, f"\\an{an}\\pos({tx:.1f},{ty:.1f}){tfs}", ll.translation, fad,
                           line_singers(ll), (left, left + tw))

    if tr.enabled and not per_line_trans:
        fad = f"\\fad({fade_in},{fade_out})" if (fade_in or fade_out) else ""
        for t0, t1, pos, text, (bx0, by0, bx1, by1), ll in translation_placements(
                laid, style, W, H, block_top, block_h, trans_size, m_trans, margin_h, margin_v):
            emit_trans(t0, t1, pos, text, fad, line_singers(ll), (bx0, bx1))
            boxes.append((t0 + time_offset_ms, t1 + time_offset_ms, bx0, by0, bx1, by1))

    if fx_on:
        for layer, t0, t1, tags, body in syllable_events(style, syllables, k):
            events.append(f"Dialogue: {layer},{ass_time(t0 + time_offset_ms)},{ass_time(t1 + time_offset_ms)},"
                          f"KFx,,0,0,0,,{{{tags}}}{body}")

    # the title card: in its corner at the top, narrowed (wrapped) to keep clear of what else is drawn;
    # the same card again at the end of the song
    orig = project.asset("original")
    song_end = (orig.duration_ms + audio_offset_ms) if orig is not None and orig.duration_ms else None
    events += info_events(project, style, W, H, k, family, audio_offset_ms, boxes=boxes, warnings=warnings,
                          end_ms=song_end)

    shadow_back = ass_color(txt.shadow_color, 100 - txt.shadow_opacity)

    def style_line(name: str, font: str, size: float, sung: str, unsung: str, outline_c: str, outline: float,
                   shadow: float, bold: bool, fill_alpha: int = 0) -> str:
        return (f"Style: {name},{font},{size:.1f},{ass_color(sung, fill_alpha)},{ass_color(unsung, fill_alpha)},"
                f"{ass_color(outline_c)},{shadow_back},{-1 if bold else 0},0,0,0,100,100,0,0,1,"
                f"{max(0.0, outline) * k:.2f},{max(0.0, shadow) * k:.2f},2,0,0,0,1")

    rc = txt if rb.follow_colors else rb
    ruby_outline = rb.outline if not rb.follow_colors else max(1.0, txt.outline * 0.6)
    # each singer's own styles (KMain_1 …): its colours, the rest as the style's
    singer_styles: list[str] = []
    for n, c in scol.items():
        singer_styles.append(style_line(f"KMain_{n}", family, main_size, c["sung"], c["unsung"], c["outline"],
                                        txt.outline, txt.shadow, txt.bold))
        if rb.follow_colors:
            singer_styles.append(style_line(f"KRuby_{n}", ruby_family, ruby_size, c["sung"], c["unsung"],
                                            c["outline"], ruby_outline, txt.shadow * 0.6, txt.bold))
    header = [
        "[Script Info]",
        "; generated by MiliKara",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        style_line("KMain", family, main_size, txt.color_sung, txt.color_unsung, txt.outline_color, txt.outline,
                   txt.shadow, txt.bold),
        style_line("KRuby", ruby_family, ruby_size, rc.color_sung, rc.color_unsung, rc.outline_color,
                   ruby_outline, txt.shadow * 0.6, txt.bold),
        style_line("KTrans", trans_family, trans_size, tr.color, tr.color, tr.outline_color, tr.outline, tr.shadow,
                   tr.bold),
        # glow layers: invisible fill, the (blurred) border is the glow; sizes set per event
        style_line("KGlow", family, main_size, "#FFFFFF", "#FFFFFF", "#FFFFFF", 0, 0, txt.bold, fill_alpha=100),
        *singer_styles,
        # countdown dots (drawings; colours set per event)
        style_line("KDots", family, main_size, txt.color_sung, txt.color_sung, txt.outline_color, txt.outline * 0.5,
                   0, txt.bold),
        FX_STYLE,
        info_style(family, main_size),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(header + events) + "\n", warnings


def _syllables(parts: list[Part], cx: float, bottom_y: float, size: float, scale: float, measurer, font: str,
               ruby: bool, t_from: float, t_to: float, group: str = "", top: float = 0.0, room: float = 1e9,
               timing=None, color: Optional[str] = None) -> list:
    """Where each sung piece of a chunk sits on screen (the chunk text is centred at ``cx``, its
    bottom at ``bottom_y`` as drawn with \\an2); ``timing(x0, x1)`` gives a piece's (start, end)
    when it is not its own (a reading that follows the lyric's sweep)."""
    from .effects import Syllable

    widths = [measurer.width(p.text) * scale for p in parts]
    left = cx - sum(widths) / 2
    out = []
    for p, w in zip(parts, widths):
        start, end = (p.start, p.end) if timing is None else timing(left, left + w)
        if start is not None and end is not None and p.text.strip() and t_from <= start < t_to:
            # \an5 at the middle of the line box (its height is the font size) sits exactly on the
            # glyphs drawn with \an2 at the bottom, and scales around their centre
            out.append(Syllable(text=p.text, start=int(start), end=int(max(end, start)), x=left + w / 2,
                                y=bottom_y - size * scale * 0.5, w=max(w, size * scale * 0.4), h=size * scale,
                                font=font, size=size * scale, ruby=ruby, visible_until=int(t_to), group=group,
                                top=top, room=room, color=color))
        left += w
    return out
