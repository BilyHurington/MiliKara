"""What is written beside a lyric line, at its start or end: who sings it (``singers.labels``) and the
style's own text or symbols (``decor``).

A line is laid out as ``[start blocks] lyric [end blocks]``: the blocks count in its width (centring,
wrapping, shrinking), and the start blocks go with the first piece of a wrapped line, the end blocks
with the last.  A block is one row of text, or two small rows that together are exactly as tall as
the lyric: the upper row's glyphs start where the lyric's start, the lower row's end where the
lyric's end (the font's em box, ``Measurer.ink``).  Several singers: their labels in their own colours,
one row each for two singers, two rows shared out evenly for more; a combination of exactly these
singers with a label of its own (e.g. 全员) in one row, in the lyrics' own colour (not one member's).
Each run glows like its singer's lyrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import KaraokeSingers, KaraokeStyle, Line

GAP_EM = 0.3  # between a block and the lyric (or the next block), of the lyric size
ROW_GAP_EM = 0.08  # between two rows, of the lyric's em height
ONE_ROW_EM = 0.62  # a label in one row: its em height, of the lyric's


@dataclass
class TagBlock:
    """One block beside a line: rows of (text, colour, glow colour) runs, all in one font size."""

    rows: list[list[tuple[str, str, str]]]
    size: float  # font size (unscaled px)
    width: float = 0.0  # widest row (unscaled px)
    kind: str = "label"  # "label" (who sings) / "decor" (the style's symbols)


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
    """The start / end blocks of each line for one style (measured in its lyric font)."""

    def __init__(self, style: KaraokeStyle, colour: dict[int, tuple[str, str]], family: str, bold: bool,
                 main_size: float, ink: tuple[float, float]):
        """``colour``: each singer's (text colour, glow colour)."""
        from .fonts import Measurer

        self.style = style
        self.sg = style.singers
        self.colour = colour
        self.main_size = main_size
        self.ink = ink
        em = main_size * max(0.05, ink[1] - ink[0])  # the lyric's em height
        self.em = em
        # two rows: each row's em height is half the lyric's (less the gap between them)
        self.two_size = max(1.0, (em * (1 - ROW_GAP_EM)) / 2 / max(0.05, ink[1] - ink[0]))
        self.one_size = max(1.0, em * ONE_ROW_EM / max(0.05, ink[1] - ink[0]))
        dec = style.decor
        self.decor_size = max(1.0, main_size * dec.size_pct / 100)
        self.neutral = (style.text.color_unsung, style.glow.color_unsung)
        self.decor_colour = (dec.color or style.text.color_unsung, style.glow.color_unsung)
        self.gap = main_size * GAP_EM
        self._m = {s: Measurer(family, bold, s) for s in {self.two_size, self.one_size, self.decor_size}}

    def _block(self, rows: list[list[tuple[str, str, str]]], size: float, kind: str) -> TagBlock:
        m = self._m[size]
        width = max(m.width("".join(r[0] for r in row)) for row in rows)
        return TagBlock(rows, size, width, kind)

    def label(self, ids: tuple[int, ...]) -> Optional[TagBlock]:
        rows = [[(t, *self.colour.get(i, self.neutral)) for t, i in row] for row in label_rows(self.sg, ids)]
        if not rows:
            return None
        return self._block(rows, self.two_size if len(rows) == 2 else self.one_size, "label")

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
        if dec.start.strip():
            start.append(self._block([[(dec.start.strip(), *self.decor_colour)]], self.decor_size, "decor"))
        if dec.end.strip():
            end.append(self._block([[(dec.end.strip(), *self.decor_colour)]], self.decor_size, "decor"))
        if label is not None and where == "end":
            end.append(label)
        return (LineTags(start, self.gap) if start else None, LineTags(end, self.gap) if end else None)

    def row_baselines(self, block: TagBlock, main_y: float, scale: float) -> list[float]:
        """The y of each row's line-box bottom (``\\an1`` / ``\\an3``) for a lyric whose line box ends at
        ``main_y`` and is ``main_size * scale`` tall: two rows fill the lyric's em box from its top to its
        bottom; one row sits in its middle."""
        size, (i0, i1) = block.size * scale, self.ink
        top = main_y - self.main_size * scale  # the lyric's line box
        em_top, em_bottom = top + self.main_size * scale * i0, top + self.main_size * scale * i1
        if len(block.rows) == 2:
            # upper row: its em top on the lyric's; lower row: its em bottom on the lyric's
            return [em_top + size * (1 - i0), em_bottom + size * (1 - i1)]
        mid = (em_top + em_bottom) / 2
        return [mid + size * (1 - (i0 + i1) / 2)]
