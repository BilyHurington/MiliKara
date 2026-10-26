"""What is written beside a lyric line, at its start or end: who sings it (``singers.labels``) and the
style's own text or symbols (``decor``).

A line is laid out as ``[start blocks] lyric [end blocks]``: the blocks count in its width (centring,
wrapping, shrinking), and the start blocks go with the first piece of a wrapped line, the end blocks
with the last.  A block is one row of text, or two small rows that together are exactly as tall as
the lyric: the upper row's glyphs start where the lyric's start, the lower row's end where the
lyric's end (the font's em box, ``Measurer.ink``).  Several singers: their labels in their own colours,
one row each for two singers, two rows shared out evenly for more; a combination of exactly these
singers with a label of its own (e.g. 全员) in one row, in the lyrics' own colour (not one member's).
Each run glows like its singer's lyrics.  Names or symbols the lyric font lacks: all of it in a font
that has every character (a character filled in from another font would come out at another width
and in another style, so two rows of as many characters would differ in length).

Pictures (karaoke.pictures) go in the rows as runs of their own: colour emoji in the text (as tall as
the text's em), a singer's picture (``label_content``: instead of or before the name; as tall as the
lyric's em in one row, as its row's in two) and the style's picture at each end (as tall as its symbols).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import KaraokeSingers, KaraokeStyle, Line

GAP_EM = 0.3  # between a block and the lyric (or the next block), of the lyric size
ROW_GAP_EM = 0.08  # between two rows, of the lyric's em height
ONE_ROW_EM = 0.62  # a label in one row: its em height, of the lyric's
PIC_GAP_EM = 0.15  # between a picture and the text beside it, of the picture's height

# (text, colour, glow colour, picture: "" for text, else "emoji:<emoji>" / "pic:<id>")
Run = tuple[str, str, str, str]


@dataclass
class Placed:
    """Where a run sits in its row (unscaled px): from the row's left, its width; a picture's height."""

    x: float
    width: float
    height: float = 0.0


@dataclass
class TagBlock:
    """One block beside a line: rows of runs, all in one font size."""

    rows: list[list[Run]]
    size: float  # font size (unscaled px)
    width: float = 0.0  # widest row (unscaled px)
    kind: str = "label"  # "label" (who sings) / "decor" (the style's symbols)
    places: list[list[Placed]] = field(default_factory=list)  # each row's runs
    row_widths: list[float] = field(default_factory=list)

    @property
    def has_pictures(self) -> bool:
        return any(r[3] for row in self.rows for r in row)


@dataclass
class LineTags:
    """The blocks at one end of a line; each block has a gap on its lyric side."""

    blocks: list[TagBlock] = field(default_factory=list)
    gap: float = 0.0  # unscaled px

    @property
    def width(self) -> float:
        return sum(b.width for b in self.blocks) + self.gap * len(self.blocks)


def label_of(sg: KaraokeSingers, i: int) -> str:
    """A singer's label: its own, else its name, else its number."""
    m = sg.members[i - 1]
    return (m.label or m.name).strip() or f"演唱者 {i}"


def combo_label(sg: KaraokeSingers, ids: tuple[int, ...]) -> str:
    """The label of a combination of exactly these singers (any order), "" when there is none."""
    combo = next((c for c in sg.combos if sorted(c.singers) == sorted(ids) and c.label.strip()), None)
    return combo.label.strip() if combo else ""


def label_rows(sg: KaraokeSingers, ids: tuple[int, ...]) -> list[list[tuple[str, int]]]:
    """The label rows for a line sung by ``ids`` (in the order they first sing), as (text, singer) runs
    (singer 0: the lyrics' own colour): one row for one singer or a combination with its own label; two
    rows otherwise — the first half of the singers above (one more when odd), joined by "/"."""
    if not ids:
        return []
    if len(ids) == 1:
        return [[(label_of(sg, ids[0]), ids[0])]]
    own = combo_label(sg, ids)
    if own:
        return [[(own, 0)]]
    half = (len(ids) + 1) // 2
    rows = []
    for part in (ids[:half], ids[half:]):
        row: list[tuple[str, int]] = []
        for n, i in enumerate(part):
            if n:
                row.append(("/", part[n - 1]))
            row.append((label_of(sg, i), i))
        rows.append(row)
    return rows


class Tagger:
    """The start / end blocks of each line for one style (measured in its lyric font, or the font that has
    all their characters)."""

    def __init__(self, style: KaraokeStyle, colour: dict[int, tuple[str, str]], family: str, bold: bool,
                 main_size: float, ink: tuple[float, float]):
        """``colour``: each singer's (text colour, glow colour); ``ink``: the lyric font's em box."""
        from .fonts import BUNDLED_JP, HAN_FAMILIES, Measurer, covering
        from .pictures import emoji_font

        self.style = style
        self.sg = sg = style.singers
        self.colour = colour
        self.main_size = main_size
        self.ink = ink
        dec = style.decor
        self.emoji = emoji_font() is not None
        self.missing: set[str] = set()  # pictures that are gone (a style from another computer)
        text = "".join(t for t, pic in self._split("".join([
            *(label_of(sg, i) for i in range(1, len(sg.members) + 1)), *(c.label for c in sg.combos),
            dec.start, dec.end, "/"])) if not pic)
        self.family = covering(family, bold, text, [BUNDLED_JP, *HAN_FAMILIES], whole=True)
        # the em box of what is written beside the lines (its own font's when that is another)
        self.tag_ink = Measurer(self.family, bold, main_size).ink if self.family != family else ink
        em = main_size * max(0.05, ink[1] - ink[0])  # the lyric's em height
        span = max(0.05, self.tag_ink[1] - self.tag_ink[0])
        # two rows: each row's em height is half the lyric's (less the gap between them)
        self.two_size = max(1.0, em * (1 - ROW_GAP_EM) / 2 / span)
        self.one_size = max(1.0, em * ONE_ROW_EM / span)
        self.decor_size = max(1.0, main_size * dec.size_pct / 100)
        self.neutral = (style.text.color_unsung, style.glow.color_unsung)
        self.decor_colour = (dec.color or style.text.color_unsung, style.glow.color_unsung)
        self.gap = main_size * GAP_EM
        self._m = {s: Measurer(self.family, bold, s) for s in {self.two_size, self.one_size, self.decor_size}}
        self.lyric_em = em

    def _split(self, text: str) -> list[tuple[str, str]]:
        """``text`` as (text, "") and ("", "emoji:<emoji>") runs (no emoji font: all text)."""
        from .pictures import split_emoji

        if not getattr(self, "emoji", False):
            return [(text, "")] if text else []
        return [("", f"emoji:{t}") if e else (t, "") for t, e in split_emoji(text)]

    def _runs(self, text: str, colour: tuple[str, str]) -> list[Run]:
        return [(t, *colour, pic) for t, pic in self._split(text)]

    def _picture(self, pid: str, colour: tuple[str, str]) -> list[Run]:
        from .pictures import picture_path

        if not pid:
            return []
        if picture_path(pid) is None:
            self.missing.add(pid)
            return []
        return [("", *colour, f"pic:{pid}")]

    def _block(self, rows: list[list[Run]], size: float, kind: str, pic_height: Optional[float] = None) -> TagBlock:
        """A block of these rows: each run placed (a picture ``pic_height`` tall, else as tall as the em)."""
        from .pictures import aspect

        m = self._m[size]
        em = size * max(0.05, self.tag_ink[1] - self.tag_ink[0])
        places, widths = [], []
        for row in rows:
            x, placed = 0.0, []
            for n, (text, _, _, pic) in enumerate(row):
                if pic:
                    h = (pic_height if pic.startswith("pic:") and pic_height else em)
                    gap = h * PIC_GAP_EM
                    if n:
                        x += gap
                    placed.append(Placed(x, h * aspect(pic), h))
                    x += h * aspect(pic) + (gap if n + 1 < len(row) else 0.0)
                else:
                    w = m.width(text)
                    placed.append(Placed(x, w))
                    x += w
            places.append(placed)
            widths.append(x)
        return TagBlock(rows, size, max(widths, default=0.0), kind, places, widths)

    def label(self, ids: tuple[int, ...]) -> Optional[TagBlock]:
        sg = self.sg
        rows: list[list[Run]] = []
        for row in label_rows(sg, ids):
            runs: list[Run] = []
            for t, i in row:
                colour = self.colour.get(i, self.neutral)
                pid = sg.members[i - 1].picture if i and t != "/" else ""
                pic = self._picture(pid, colour) if sg.label_content != "text" else []
                runs += pic
                if not pic or sg.label_content == "both":
                    runs += self._runs(t, colour)
            rows.append(runs)
        if not rows:
            return None
        two = len(rows) == 2
        # a singer's picture in one row: as tall as the lyric's em (a face too small to know otherwise)
        return self._block(rows, self.two_size if two else self.one_size, "label",
                           None if two else self.lyric_em)

    def tags(self, line: Line, singers: tuple[int, ...]) -> tuple[Optional[LineTags], Optional[LineTags]]:
        """(start, end) blocks of a line sung by ``singers`` (in the order they first sing)."""
        dec = self.style.decor
        where = self.sg.labels
        label = self.label(singers) if where != "off" and self.sg.members else None
        start: list[TagBlock] = []
        end: list[TagBlock] = []
        # outermost: who sings; then the symbols next to the lyric
        if label is not None and where == "start":
            start.append(label)
        # the picture outermost, the text next to the lyric
        first = self._picture(dec.start_picture, self.decor_colour) + self._runs(dec.start.strip(), self.decor_colour)
        last = self._runs(dec.end.strip(), self.decor_colour) + self._picture(dec.end_picture, self.decor_colour)
        if first:
            start.append(self._block([first], self.decor_size, "decor"))
        if last:
            end.append(self._block([last], self.decor_size, "decor"))
        if label is not None and where == "end":
            end.append(label)
        return (LineTags(start, self.gap) if start else None, LineTags(end, self.gap) if end else None)

    def row_baselines(self, block: TagBlock, main_y: float, scale: float) -> list[float]:
        """The y of each row's line-box bottom (``\\an1`` / ``\\an3``) for a lyric whose line box ends at
        ``main_y`` and is ``main_size * scale`` tall: two rows fill the lyric's em box from its top to its
        bottom; one row sits in its middle."""
        size, (i0, i1) = block.size * scale, self.tag_ink
        top = main_y - self.main_size * scale  # the lyric's line box
        em_top, em_bottom = top + self.main_size * scale * self.ink[0], top + self.main_size * scale * self.ink[1]
        if len(block.rows) == 2:
            # upper row: its em top on the lyric's; lower row: its em bottom on the lyric's
            return [em_top + size * (1 - i0), em_bottom + size * (1 - i1)]
        mid = (em_top + em_bottom) / 2
        return [mid + size * (1 - (i0 + i1) / 2)]

    def picture_top(self, block: TagBlock, baseline: float, height: float, scale: float) -> float:
        """The top of a picture ``height`` (scaled) tall in the row whose line box ends at ``baseline``:
        centred on the row's em box."""
        size, (i0, i1) = block.size * scale, self.tag_ink
        top = baseline - size
        return top + size * (i0 + i1) / 2 - height / 2
