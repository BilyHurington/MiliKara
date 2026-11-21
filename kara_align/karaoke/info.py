"""Song title card: title / artist / album / credits in a top corner at the start
(and, with ``info.outro``, the same card again at the end).

The lines come from the song data (lyrics metadata filled from the music
platform, credit lines such as ``作词：…`` kept in the lyrics) and the fields the
style picks, unless the project has its own text (``Project.song_info_text``,
one line each, the first is the title).  The card slides in beside a thin accent
bar in the lyrics' sung colour, uses the lyrics' font, outline and glow, and
fades out after ``duration_ms`` — or before the first lyric / translation that
would share the top edge (after 2 s at least), and always before anything that
would be drawn where the card is (then without the 2 s; a card that would not
last a second is left out).

The ending card is the mirror image: it stays until the end of the song for
``outro_duration_ms``, comes in only after the last lyric / translation along the
top edge has gone (staying 2 s at least) and after anything drawn where it is.

With several singers, the legend (``singers.legend``) follows the opening card in
the same corner, the same way: each singer who sings in the song with a swatch of
their colour, and the combinations that have a label of their own.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from ..models import KaraokeSongInfo, KaraokeStyle, Project
from .fonts import BUNDLED_JP, HAN_FAMILIES, Measurer, covering

LABELS = {"title": "歌名", "artist": "歌手", "album": "专辑", "lyricist": "作词", "composer": "作曲", "arranger": "编曲"}
# a label may name several roles ("作词/作曲", "词曲", "Lyrics & Music"): each one is filled
_CREDIT = {"lyricist": re.compile(r"作词|作詞|^[词詞]|lyric|written", re.I),
           "arranger": re.compile(r"编曲|編曲|arrang", re.I),
           "composer": re.compile(r"作曲|^曲$|[词詞]曲|compos|music", re.I)}
_LABEL = re.compile(r"^\s*([^:：]{1,16}?)\s*[:：]\s*(.+?)\s*$")

L_CARD_PLATE, L_INFO_GLOW, L_INFO = 8, 8, 9  # the plate shares the glow's layer, drawn before it
_IN_MS, _OUT_MS, _STAGGER, _BAR_MS = 450, 400, 90, 380  # slide in / out, between lines, bar grow / shrink
_BAR = 6  # accent bar width (px at 1920 wide)


def song_fields(project: Project) -> dict[str, str]:
    """Every field the song data can fill (empty ones left out)."""
    meta = project.lyrics.meta
    out = {"title": meta.title or project.name or "", "artist": meta.artist or "", "album": meta.album or ""}
    for ln in project.lyrics.lines:
        if ln.kind != "meta":
            continue
        m = _LABEL.match(ln.text)
        if not m:
            continue
        for key, pat in _CREDIT.items():
            if pat.search(m.group(1).strip()) and not out.get(key):
                out[key] = m.group(2)
    return {k: v.strip() for k, v in out.items() if v and v.strip()}


def auto_lines(project: Project, style: KaraokeStyle) -> list[str]:
    fields = song_fields(project)
    lines = []
    for f in style.info.fields:
        v = fields.get(f)
        if v:
            lines.append(f"{LABELS[f]}：{v}" if f in ("lyricist", "composer", "arranger") else v)
    return lines


def info_lines(project: Project, style: KaraokeStyle) -> list[str]:
    """The card's lines: the project's own text, or the chosen fields."""
    if project.song_info_text is not None:
        return [x.strip() for x in project.song_info_text.splitlines() if x.strip()]
    return auto_lines(project, style)


def column_lines(project: Project, style: KaraokeStyle) -> tuple[list[str], list[str]]:
    """The opening title (title / artist / album) and the closing credits (作词 / 作曲 / 编曲) for the
    centred scrolling column (``placement="column"``).  A project's own text is one block (no credits)."""
    if project.song_info_text is not None:
        return info_lines(project, style), []
    fields = song_fields(project)
    title, credits = [], []
    for f in style.info.fields:
        v = fields.get(f)
        if not v:
            continue
        line = f"{LABELS[f]}：{v}" if f in ("lyricist", "composer", "arranger") else v
        (credits if f in ("lyricist", "composer", "arranger") else title).append(line)
    return title, credits


# where a card line may wrap: after a space / "・" / "/", or before a bracket, a tilde or " - " —
# preferably not inside brackets (「(M@STER VERSION)」, 「～For Miria MIX～」 stay together)
_OPEN, _CLOSE, _TILDES = "(（[［【〈《「『", ")）]］】〉》」』", "~〜～"
_BREAK_AFTER = " \u3000・/／、，,"


def _depths(text: str) -> list[int]:
    """Bracket depth before each character (a tilde opens when another one follows, else closes)."""
    out, depth, in_tilde = [], 0, False
    for i, c in enumerate(text):
        out.append(depth)
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth = max(0, depth - 1)
        elif c in _TILDES:
            if in_tilde:
                depth, in_tilde = max(0, depth - 1), False
            elif any(x in _TILDES for x in text[i + 1:]):
                depth, in_tilde = depth + 1, True
    return out


def wrap_text(text: str, width, max_w: float) -> list[str]:
    """``text`` in rows no wider than ``max_w`` (``width``: text -> px), broken where it reads well:
    the longest first row that ends at a good place outside brackets, else at a good place, else
    between any two characters; spaces at the break are dropped."""
    text = text.strip()
    rows: list[str] = []
    while text and width(text) > max_w:
        depth = _depths(text)
        cuts = [i for i in range(1, len(text))
                if text[i] in _OPEN or (text[i] in _TILDES and depth[i] == 0) or text[i - 1] in _BREAK_AFTER
                or text[i:i + 3] == " - "]
        fit = [i for i in cuts if width(text[:i].rstrip()) <= max_w]
        outside = [i for i in fit if depth[i] == 0]
        if outside or fit:
            i = (outside or fit)[-1]
        else:  # no good place fits: as many characters as fit
            i = 1
            while i < len(text) and width(text[:i + 1]) <= max_w:
                i += 1
        head, text = text[:i].rstrip(" \u3000"), text[i:].lstrip(" \u3000")
        if head:
            rows.append(head)
    if text:
        rows.append(text)
    return rows or [""]


def placement_of(project: Project, style: KaraokeStyle) -> str:
    """Where the song-info card is drawn: "corner", "column" (with the lyrics) or "cover" (under the
    cover panel of a split background).  "auto" — the default — picks what the frame offers: the panel
    when there is one, else the scrolling column in the centre layout, else a corner.  A pick that
    does not apply where it is asked for falls back to a corner (there is no panel, or no column)."""
    from .cover import split_side

    pick = style.info.placement
    if pick == "corner":
        return "corner"
    centre = style.layout.position == "center"
    front = centre and split_side(project, style) is not None  # only the centre layout has the panel
    if pick == "cover":
        return "cover" if front else "corner"
    if pick == "column":
        return "column" if centre else "corner"
    return "cover" if front else ("column" if centre else "corner")


def _sub_alpha(info: KaraokeSongInfo) -> str:
    """Tag making the smaller lines lighter than the title: ``info.sub_opacity`` % of the colour."""
    if info.sub_opacity >= 100:
        return ""
    return f"\\1a&H{int(round(255 * (1 - info.sub_opacity / 100))):02X}&"


def _plate(x0: float, y0: float, x1: float, y1: float, k: float, t0: float, t1: float, *,
           dy: float = 0.0, dur: int = 0, fad: str = "", alpha: int = 0x78) -> str:
    """A soft dark plate behind the card, so its text stays readable over a blurred cover: one
    rectangle, its edges blurred, at ``alpha`` (0x00 = opaque … 0xFF = invisible).  It travels with the
    card: ``dy`` over ``dur`` ms."""
    pad = 16 * k
    w, h = max(1.0, x1 - x0 + 2 * pad), max(1.0, y1 - y0 + 2 * pad)
    rect = f"m 0 0 l {w:.1f} 0 l {w:.1f} {h:.1f} l 0 {h:.1f}"
    px, py = x0 - pad, y0 - pad
    where = (f"\\move({px:.1f},{py:.1f},{px:.1f},{py + dy:.1f},0,{dur})" if (dy and dur > 0)
             else f"\\pos({px:.1f},{py:.1f})")
    return _dialogue(L_CARD_PLATE, t0, t1,
                     f"\\an7{where}\\bord0\\shad0\\blur{max(1.0, 10 * k):.1f}\\1c&H000000&\\1a&H{alpha:02X}&{fad}",
                     f"{{\\p1}}{rect}{{\\p0}}")


def info_events(project: Project, style: KaraokeStyle, W: int, H: int, k: float, family: str,
                time_offset_ms: float, *,
                boxes: Sequence[tuple[float, float, float, float, float, float]] = (),
                warnings: Optional[list[str]] = None, end_ms: Optional[float] = None,
                repeat_at: Sequence[tuple[float, float]] = ()) -> list[str]:
    """Dialogue lines of the card (style ``KInfo``) in its corner at the top, for its whole
    duration: when a translation or lyric (``boxes``: (from, to, x0, y0, x1, y1) of what else is
    drawn) would be where the card is, the card gets narrower (its lines wrap) so it ends before it.
    ``end_ms`` (the end of the song on the subtitles' timeline): the ending card too; ``repeat_at``
    ((from, to) on that timeline): extra showings, in the long stretches with no lyric on screen."""
    info = style.info
    lines = info_lines(project, style)
    if not info.enabled or not lines:
        return []
    if placement_of(project, style) != "corner":
        return []  # drawn with the lyrics by build_ass (in the column, or under the cover panel)
    # a title / album with characters the lyric font lacks: a font with all of them (Windows: libass would
    # fill them in from a font of its own, at another size)
    family = covering(family, style.text.bold, _text_of("".join(lines)), [BUNDLED_JP, *HAN_FAMILIES])
    t0 = max(0.0, info.start_ms + time_offset_ms)
    # "persist" keeps the card up from its start time to the end of the song; otherwise it goes after
    # duration_ms (the ending card, when there is one, starting where it left off)
    t1 = end_ms if (info.mode == "persist" and end_ms is not None) else t0 + info.duration_ms
    # "persist" is the user's ask for the card to stay: it is not moved out of the lyrics' way
    events = _card(lines, style, W, H, k, family, t0, t1, () if info.mode == "persist" else boxes, warnings,
                   at_end=False)
    if info.outro and end_ms is not None and end_ms - info.outro_duration_ms > t1:
        events += _card(lines, style, W, H, k, family, end_ms - info.outro_duration_ms, end_ms, boxes, warnings,
                        at_end=True)
    for a, b in repeat_at:
        events += _card(lines, style, W, H, k, family, a, b, boxes, warnings, at_end=False)
    return events


def _text_of(text: str) -> str:
    """``text`` without its colour emoji (drawn as pictures, not by a font)."""
    from .pictures import emoji_font, split_emoji

    return "".join(t for t, e in split_emoji(text) if not e) if emoji_font() is not None else text


def legend_entries(project: Project, style: KaraokeStyle) -> list[tuple[str, list[str], str, str]]:
    """(text, colours, mix, picture) of each row of the legend: every singer who sings in the song (by
    number, with its picture if it has one here), then the combinations heard that have a label of their
    own; nothing for fewer than two singers."""
    from .pictures import picture_path

    from ..lyrics.singers import effective
    from .linetags import combo_label

    sg = style.singers
    if not sg.legend or not sg.members:
        return []
    used: list[tuple[int, ...]] = []
    for ln in project.lyrics.sung_lines():
        for ids in effective(ln):
            ids = tuple(i for i in ids if 1 <= i <= len(sg.members))
            if ids and ids not in used:
                used.append(ids)
    singles = sorted({i for ids in used for i in ids})
    if len(singles) < 2:
        return []
    rows = []
    for i in singles:
        m = sg.members[i - 1]
        name = m.name.strip() or f"演唱者 {i}"
        label = m.label.strip()
        rows.append((f"{name}（{label}）" if label and label != name else name, [m.color], "split",
                     m.picture if picture_path(m.picture) is not None else ""))
    seen: set[str] = set()
    for ids in used:
        label = combo_label(sg, ids) if len(ids) >= 2 else ""
        if label and label not in seen:
            seen.add(label)
            rows.append((label, [sg.members[i - 1].color for i in ids], sg.look(ids)[0], ""))
    return rows


def legend_events(project: Project, style: KaraokeStyle, W: int, H: int, k: float, family: str,
                  time_offset_ms: float, *,
                  boxes: Sequence[tuple[float, float, float, float, float, float]] = (),
                  warnings: Optional[list[str]] = None,
                  slot: Optional[tuple[float, float, float]] = None,
                  pts: Optional[Sequence[tuple[float, float]]] = None) -> list[str]:
    """The legend card: right after the opening song card (else at its start time), for ``legend_ms``.
    ``slot`` (x centre, width, top): drawn with the lyrics — in the column, or under the cover panel —
    instead of in the corner.  ``pts``: the waypoints to move along (the column's motion)."""
    rows = legend_entries(project, style)
    if not rows:
        return []
    # names: in one font that has all their characters (not filled in from another, in another style)
    family = covering(family, style.text.bold, _text_of("".join(r[0] for r in rows)), [BUNDLED_JP, *HAN_FAMILIES],
                      whole=True)
    info = style.info
    t0 = max(0.0, info.start_ms + time_offset_ms)
    if info.enabled and info_lines(project, style):
        t0 += info.duration_ms
    t1 = t0 + style.singers.legend_ms
    if slot is not None:
        x_center, width, top = slot
        waypoints = list(pts) if pts is not None else [(t0, top), (t1, top)]
        top = waypoints[0][1] if pts is not None else top
        return centered_block([r[0] for r in rows], style, W, H, k, family, waypoints, x_center,
                              anchor="top", width=width, max_h=max(0.0, H - top - 12 * k),
                              swatches=[(r[1], r[2]) for r in rows], pictures=[r[3] for r in rows])
    return _card([r[0] for r in rows], style, W, H, k, family, t0, t1, boxes, warnings,
                 at_end=False, swatches=[(r[1], r[2]) for r in rows], pictures=[r[3] for r in rows], what="分色列表")


def _card(lines: list[str], style: KaraokeStyle, W: int, H: int, k: float, family: str, t0: float, t1: float,
          boxes: Sequence[tuple[float, float, float, float, float, float]], warnings: Optional[list[str]], *,
          at_end: bool, swatches: Optional[list[tuple[list[str], str]]] = None, pictures: Optional[list[str]] = None,
          what: str = "歌曲信息") -> list[str]:
    """One showing of the card from t0 to t1, in its corner at the top, as narrow as it needs to be
    to stay clear of what else is drawn there (its lines wrap).  Only when not even a narrow card
    fits beside it: shorter (gone before that shows / in after it has gone), else moved down below
    it, else in the other corner."""
    info, txt, glow = style.info, style.text, style.glow

    title_size = info.size * k
    sub_size = max(18 * k, title_size * 0.56)
    margin = info.margin * k
    bar_w = _BAR * k
    if swatches is not None:
        # the legend: rows of one size, each beside a swatch of its colours instead of the accent bar
        title_size = sub_size = max(18 * k, info.size * k * 0.66)
        bar_w = sub_size * 0.72
    gap = 14 * k
    sub_alpha_tag = _sub_alpha(info)
    # singers' pictures: a column between the swatches and the names (all names start at one x)
    from .pictures import aspect, drawing, emoji_font, split_emoji

    pic_h = sub_size * 0.95
    with_pics = [p for p in (pictures or []) if p]
    col = (pic_h * max(aspect(f"pic:{p}") for p in with_pics) + gap * 0.6) if with_pics else 0.0
    max_w = W * 0.42
    color = _bgr(info.color or txt.color_unsung)
    accent = _bgr(info.accent or txt.color_sung)
    outline_c = _bgr(txt.outline_color)

    pad = (txt.outline + (glow.size + glow.blur if glow.enabled else 0)) * k + 8 * k
    lowest = H * 0.45  # a card moved down stays in the upper part of the frame
    slide_room = 28 * k  # the lines slide in from beyond the text by this much

    def rows(width: float) -> tuple[list, float, float]:
        """(rows, height, reach) of the card with its lines wrapped to ``width``: each row
        (text, size, bold, width, y from the card's top, level, line, first row of its line); level 0 =
        the title (every row of the legend).  A line too wide wraps (at a space, before a bracket …)
        instead of being squeezed."""
        placed: list = []
        y = bottom = 0.0
        for i, text in enumerate(lines):
            size = title_size if i == 0 else sub_size
            bold = txt.bold if (i == 0 or swatches is not None) else False
            m = Measurer(family, bold, size)
            for j, row in enumerate(wrap_text(text, m.width, width)):
                if placed:  # below the previous row: a wrapped row closer, the title's rows further
                    prev_size, prev_level = placed[-1][1], placed[-1][5]
                    y += prev_size * (1.1 if j else (1.18 if prev_level == 0 else 1.32))
                placed.append((row, size, bold, m.width(row), y, 0 if (i == 0 or swatches is not None) else 1, i, j == 0))
                bottom = y + size
        reach = bar_w + gap + col + min(width, max(r[3] for r in placed)) + slide_room
        return placed, bottom, reach  # (the accent bar is as tall as the text)

    def region(right: bool, top: float, block_h: float, reach: float) -> tuple[float, float, float, float]:
        """Where the card is drawn (sliding in, outline and glow included)."""
        x0, x1 = (W - margin - reach - pad, W - margin + pad) if right else (margin - pad, margin + reach + pad)
        return x0, top - pad, x1, top + block_h + pad

    def in_the_way(area, a: float, b: float) -> list:
        x0, y0, x1, y1 = area
        return [bx for bx in boxes if bx[2] < x1 and x0 < bx[4] and bx[3] < y1 and y0 < bx[5]
                and bx[0] - 200 < b and a < bx[1] + 200]

    right = info.position == "top-right"
    where = "结尾" if at_end else "开头"

    # 1) in its corner at the top for its whole time, as narrow as needed to end before whatever
    #    else is drawn there (a translation along the top edge): the lines wrap
    min_w = max(title_size * 4, 240 * k)
    width = max_w
    for _ in range(8):
        placed, block_h, reach = rows(width)
        hits = in_the_way(region(right, margin, block_h, reach), t0, t1)
        if not hits:
            top, n0, n1 = margin, t0, t1
            break
        # the room between the corner and the nearest thing in the way
        room = (W - margin - max(bx[4] for bx in hits) if right else min(bx[2] for bx in hits) - margin) \
            - 2 * pad - bar_w - gap - col - slide_room
        if room < min_w or room >= width - 1:
            placed = None
            break
        width = room
    else:
        placed = None

    if placed is None:
        # 2) nothing narrow enough fits beside it (e.g. a translation across the whole top): the
        #    full-width card, gone before it shows / come in after it has gone; else moved down
        #    below it; else the other corner (same order); else no card
        placed, block_h, reach = rows(max_w)

        def clear(side: bool, top: float) -> tuple[float, float]:
            hits = in_the_way(region(side, top, block_h, reach), t0, t1)
            start, end = t0, t1
            if at_end:  # comes in after the last of them has gone
                moved = True
                while moved:
                    moved = False
                    for bx in hits:
                        if bx[0] - 200 < end and bx[1] + 200 > start:
                            start, moved = bx[1] + 200, True
            else:  # leaves before the first of them shows
                for bx in hits:
                    if bx[1] > start and bx[0] - 200 < end:
                        end = bx[0] - 200
            return start, end

        def place(side: bool, move: bool) -> Optional[tuple[float, float, float]]:
            x0, _, x1, _ = region(side, margin, block_h, reach)
            tops = [margin] + sorted({bx[5] + pad + 6 * k for bx in boxes
                                      if bx[2] < x1 and x0 < bx[4] and bx[1] > t0 and bx[0] < t1
                                      and bx[5] + pad + 6 * k > margin})
            for top in (tops if move else tops[:1]):
                if top + block_h > lowest:
                    break
                a, b = clear(side, top)
                if b - a >= 1000:
                    return top, a, b
            return None

        got = None
        for side, move in ((right, False), (right, True), (not right, False), (not right, True)):
            got = place(side, move)
            if got is not None:
                break
        if got is None:
            if warnings is not None:
                warnings.append(f"{where}的歌词 / 翻译占满了画面顶部，{where}的{what}没有显示"
                                f"（可调整它的{'显示时长' if at_end else '开始时间'}）")
            return []
        if side != right and warnings is not None:
            warnings.append(f"{where}的歌词 / 翻译占满了画面顶部，{where}的{what}改到了另一侧")
        right = side
        top, n0, n1 = got
    t0, t1 = n0, n1
    slide = 28 * k * (1 if right else -1)
    x_text = (W - margin - bar_w - gap - col) if right else (margin + bar_w + gap + col)
    an = 9 if right else 7

    # coming in: the bar grows down, then the lines slide in from the edge one after another;
    # leaving is the same played backwards: the lines slide back out (the last one first), then the
    # bar shrinks up.  (An event has one \\move: a line is one event coming in and one going out.)
    events: list[str] = []
    for i, (text, size, bold, width, y, level, _, _) in enumerate(placed):
        y += top
        sx = f"\\fscx{max_w / width * 100:.1f}" if width > max_w else ""
        a = t0 + _STAGGER * i  # lines come in one after another
        out_end = t1 - 200 - _STAGGER * i  # and leave in the opposite order, before the bar
        out_start = max(out_end - _OUT_MS, a + _IN_MS)
        font = f"\\fn{family}\\fs{size:.1f}\\b{int(bold)}"
        looks = []
        if glow.enabled:
            looks.append((L_INFO_GLOW, f"\\1a&HFF&\\3c{_bgr(glow.color_unsung)}"
                                       f"\\3a&H{int(round(255 * (1 - glow.strength / 100))):02X}&"
                                       f"\\bord{glow.size * k * (0.7 if level == 0 else 0.5):.1f}"
                                       f"\\blur{glow.blur * k:.1f}\\shad0"))
        sub_alpha = "" if level == 0 else sub_alpha_tag
        looks.append((L_INFO, f"\\1c{color}{sub_alpha}\\3c{outline_c}"
                              f"\\bord{txt.outline * k * (0.7 if level == 0 else 0.55):.2f}"
                              f"\\shad{txt.shadow * k * 0.6:.2f}"))

        def slid(layer: int, x: float, yy: float, head: str, look: str, body: str) -> None:
            """One piece of the row: slides in to (x, yy) and back out."""
            move_in = f"\\move({x + slide:.1f},{yy:.1f},{x:.1f},{yy:.1f},0,{_IN_MS})"
            if out_end - out_start >= 150:
                events.append(_dialogue(layer, a, out_start, f"{head}{move_in}\\fad(350,0){look}", body))
                move_out = f"\\move({x:.1f},{yy:.1f},{x + slide:.1f},{yy:.1f},0,{out_end - out_start:.0f})"
                events.append(_dialogue(layer, out_start, out_end,
                                        f"{head}{move_out}\\fad(0,{out_end - out_start:.0f}){look}", body))
            else:  # too short a card for both: in, then a plain fade
                events.append(_dialogue(layer, a, t1, f"{head}{move_in}\\fad(350,300){look}", body))

        runs = split_emoji(text) if emoji_font() is not None else [(text, False)]
        if not any(e for _, e in runs):
            for layer, look in looks:
                slid(layer, x_text, y, f"\\an{an}{font}{sx}", look, _escape(text))
            continue
        # colour emoji in the row: each run where it falls, the emoji as pictures as tall as the text's em
        m = Measurer(family, bold, size)
        i0, i1 = m.ink
        eh = max(1, round(size * (i1 - i0)))
        egap = eh * 0.08
        widths = [eh + 2 * egap if e else m.width(t) for t, e in runs]
        x = x_text - (sum(widths) if right else 0.0)
        for (t, e), w in zip(runs, widths):
            if e:
                px, py = round(x + egap), round(y + size * (i0 + i1) / 2 - eh / 2)
                for tags, path in drawing(f"emoji:{t}", eh, eh):
                    slid(L_INFO, px, py, "\\an7", f"\\bord0\\shad0\\blur0{tags}\\p1", path)
            else:
                for layer, look in looks:
                    slid(layer, x, y, f"\\an7{font}", look, _escape(t))
            x += w
    bar_x = (W - margin - bar_w) if right else margin
    if info.plate:
        # a soft dark plate behind the card, so it stays readable over a blurred cover
        span = max(0.0, reach - slide_room)
        px0 = (W - margin - span) if right else margin
        events.append(_plate(px0, top, px0 + span, top + block_h, k, t0, t1, fad="\\fad(350,300)"))
    if pictures and col:
        # the singers' pictures, between the swatches and the names, in and out with their rows
        for i, (_, size, _, _, y, _, line, first) in enumerate(placed):
            pid = pictures[line] if line < len(pictures) else ""
            if not (first and pid):
                continue
            w = pic_h * aspect(f"pic:{pid}")
            px = (bar_x - gap * 0.6 - w) if right else (bar_x + bar_w + gap * 0.6)
            py = top + y + (size - pic_h) / 2
            a, end = t0 + _STAGGER * i, t1 - 200 - _STAGGER * i
            for tags, path in drawing(f"pic:{pid}", max(1, round(w)), max(1, round(pic_h))):
                events.append(_dialogue(L_INFO, a, end, f"\\an7\\pos({round(px)},{round(py)})\\bord0\\shad0\\blur0"
                                                        f"{tags}\\fad(350,300)\\p1", path))
    if swatches is not None:
        # beside the first row of each name: a square of its colours (a combination's side by side, blended
        # when it blends), framed in the lyrics' outline; in and out with its row
        side = bar_w
        for i, (_, size, _, _, y, _, line, first) in enumerate(placed):
            if not first:
                continue
            colours, mix = swatches[line]
            stripes = _blend(colours, 12) if (mix == "gradient" and len(colours) > 1) else colours
            a, end = t0 + _STAGGER * i, t1 - 200 - _STAGGER * i
            sy = top + y + (size - side) / 2
            each = side / len(stripes)
            for n, c in enumerate(stripes):
                w = each + (0.6 if n < len(stripes) - 1 else 0)  # (no hairline between stripes)
                rect = f"m 0 0 l {w:.1f} 0 l {w:.1f} {side:.1f} l 0 {side:.1f}"
                events.append(_dialogue(L_INFO, a, end, f"\\an7\\pos({bar_x + n * each:.1f},{sy:.1f})\\bord0\\shad0"
                                        f"\\1c{_bgr(c)}\\fad(350,300)", f"{{\\p1}}{rect}{{\\p0}}"))
            frame = f"m 0 0 l {side:.1f} 0 l {side:.1f} {side:.1f} l 0 {side:.1f}"
            events.append(_dialogue(L_INFO_GLOW, a, end, f"\\an7\\pos({bar_x:.1f},{sy:.1f})\\1a&HFF&\\3c{outline_c}"
                                    f"\\bord{max(1.5, txt.outline * k * 0.5):.1f}\\shad{txt.shadow * k * 0.4:.2f}"
                                    f"\\fad(350,300)", f"{{\\p1}}{frame}{{\\p0}}"))
        return events
    rect = f"m 0 0 l {bar_w:.1f} 0 l {bar_w:.1f} {block_h:.1f} l 0 {block_h:.1f}"
    bar = f"\\an7\\pos({bar_x:.1f},{top:.1f})\\bord0\\shad{txt.shadow * k * 0.4:.2f}\\1c{accent}"
    shape = f"{{\\p1}}{rect}{{\\p0}}"
    shrink = t1 - _BAR_MS
    if shrink >= t0 + _BAR_MS:
        events.append(_dialogue(L_INFO, t0, shrink, f"{bar}\\fscy0\\t(0,{_BAR_MS},0.6,\\fscy100)\\fad(250,0)", shape))
        events.append(_dialogue(L_INFO, shrink, t1, f"{bar}\\t(0,{_BAR_MS},1.6,\\fscy0)\\fad(0,250)", shape))
    else:
        events.append(_dialogue(L_INFO, t0, t1, f"{bar}\\fscy0\\t(0,{_BAR_MS},0.6,\\fscy100)\\fad(250,500)", shape))
    return events


# a row of a centred block: (text, size, bold, level, entry, first of its entry, text width,
# width of what is drawn before the text — the swatch and/or picture of a legend row)
_BlockRow = tuple[str, float, bool, int, int, bool, float, float]


def _block_rows(lines: list[str], style: KaraokeStyle, k: float, family: str, max_w: float, scale: float = 1.0,
                swatches: Optional[Sequence[tuple[list[str], str]]] = None,
                pictures: Optional[Sequence[str]] = None) -> list[_BlockRow]:
    """``lines`` wrapped (and the smaller ones set smaller than the first), each row measured."""
    from .pictures import aspect

    info, txt = style.info, style.text
    legend = swatches is not None
    out: list[_BlockRow] = []
    for i, text in enumerate(lines):
        if legend:
            size = max(18 * k, info.size * k * 0.66) * scale
            bold = False
        else:
            size = (info.size * k if i == 0 else max(18 * k, info.size * k * 0.56)) * scale
            bold = bool(txt.bold) if i == 0 else False
        m = Measurer(family, bold, size)
        prefix = 0.0
        if legend:
            prefix = size * 0.72 + 14 * k  # the swatch, then its gap
            pid = pictures[i] if pictures is not None and i < len(pictures) else ""
            if pid:
                prefix += size * 0.95 * aspect(f"pic:{pid}") + 14 * k * 0.6  # the singers' picture
        for j, row in enumerate(wrap_text(text, m.width, max(1.0, max_w - prefix))):
            out.append((row, size, bold, i, i, j == 0, m.width(row), prefix))
    return out


def _rows_height(rows: Sequence[_BlockRow], k: float) -> float:
    return sum(r[1] for r in rows) + 14 * k * max(0, len(rows) - 1)


def block_height(lines: list[str], style: KaraokeStyle, W: int, k: float, family: str,
                 width: Optional[float] = None) -> float:
    """How tall ``centered_block`` draws ``lines`` in one still segment (what to leave room for)."""
    info = style.info
    max_w = width if width is not None else min(W * 0.7, W - 2 * (info.margin * k))
    family = covering(family, style.text.bold, _text_of("".join(lines)), [BUNDLED_JP, *HAN_FAMILIES])
    return _rows_height(_block_rows(lines, style, k, family, max_w), k)


def centered_block(lines: list[str], style: KaraokeStyle, W: int, H: int, k: float, family: str,
                   pts: Sequence[tuple[float, float]], x_center: float, *, anchor: str = "center",
                   width: Optional[float] = None, max_h: Optional[float] = None,
                   swatches: Optional[Sequence[tuple[list[str], str]]] = None,
                   pictures: Optional[Sequence[str]] = None) -> list[str]:
    """The title / credits (or, with ``swatches``, the singer legend) as a centred block in the scrolling
    column, or under the cover panel: the lines stacked, the title bigger and bolder, the rest smaller
    and lighter, in the lyric font / outline / glow.  ``width`` / ``max_h``: the room it has — it wraps
    to that width and shrinks (then drops its last rows) to fit that height.  The block's anchor (its
    centre, top, or bottom) follows ``pts`` — (video time, y) waypoints — with a \\move per segment
    (\\pos when a segment is still), fading in on the first and out on the last.  ``info.plate``: a soft
    dark plate behind the whole block."""
    from .pictures import aspect, drawing

    info, txt, glow = style.info, style.text, style.glow
    family = covering(family, txt.bold, _text_of("".join(lines)), [BUNDLED_JP, *HAN_FAMILIES])

    legend = swatches is not None
    color = _bgr(info.color or txt.color_unsung)
    outline_c = _bgr(txt.outline_color)
    max_w = width if width is not None else min(W * 0.7, W - 2 * (info.margin * k))
    gap = 14 * k
    fade_in, fade_out = max(0, style.timing.fade_in_ms), max(0, style.timing.fade_out_ms)
    sub_alpha = _sub_alpha(info)

    rows = _block_rows(lines, style, k, family, max_w, swatches=swatches, pictures=pictures)
    total = _rows_height(rows, k)
    scale = 1.0
    for _ in range(8):  # too tall for the room it has: set it smaller, a step at a time
        if max_h is None or total <= max_h or not rows:
            break
        scale *= 0.92
        rows = _block_rows(lines, style, k, family, max_w, scale, swatches, pictures)
        total = _rows_height(rows, k)
    while max_h is not None and len(rows) > 1 and total > max_h:  # still too tall: the last rows go
        rows = rows[:-1]
        total = _rows_height(rows, k)

    def top_of(y: float) -> float:
        return y - total if anchor == "bottom" else (y if anchor == "top" else y - total / 2)

    events: list[str] = []
    for si, ((ta, ya), (tb, yb)) in enumerate(zip(pts, pts[1:])):
        if tb <= ta:
            continue
        dy = yb - ya
        dur = max(1, int(tb - ta))
        if len(pts) == 2:
            fad = f"\\fad({fade_in},{fade_out})" if (fade_in or fade_out) else ""
        elif si == 0:
            fad = f"\\fad({fade_in},0)" if fade_in else ""
        elif si == len(pts) - 2:
            fad = f"\\fad(0,{fade_out})" if fade_out else ""
        else:
            fad = ""
        y = top_of(ya)

        def spot(x: float, yy: float) -> str:
            """Where a swatch / picture (\an7, its top-left corner) goes — along with the text."""
            if abs(dy) >= 0.5:
                return f"\\an7\\move({x:.1f},{yy:.1f},{x:.1f},{yy + dy:.1f},0,{dur})"
            return f"\\an7\\pos({x:.1f},{yy:.1f})"

        if info.plate and rows:
            half = max(r[6] + r[7] for r in rows) / 2
            events.append(_plate(x_center - half, y, x_center + half, y + total, k, ta, tb, dy=dy, dur=dur, fad=fad))
        for text, size, bold, level, entry, first, tw, prefix in rows:
            y += size  # the row's baseline (\an2)
            row_left = x_center - (prefix + tw) / 2
            text_x = row_left + prefix + tw / 2  # its centre (\an2): the whole row is centred
            if legend and first:
                side = size * 0.72
                sy = y - size * 0.78
                colours, mix = swatches[entry]
                stripes = _blend(colours, 12) if (mix == "gradient" and len(colours) > 1) else colours
                each = side / len(stripes)
                for n, c in enumerate(stripes):
                    w = each + (0.6 if n < len(stripes) - 1 else 0)  # (no hairline between stripes)
                    rect = f"m 0 0 l {w:.1f} 0 l {w:.1f} {side:.1f} l 0 {side:.1f}"
                    events.append(_dialogue(L_INFO, ta, tb, f"{spot(row_left + n * each, sy)}\\bord0"
                                                            f"\\shad0\\1c{_bgr(c)}{fad}", f"{{\\p1}}{rect}{{\\p0}}"))
                frame = f"m 0 0 l {side:.1f} 0 l {side:.1f} {side:.1f} l 0 {side:.1f}"
                events.append(_dialogue(L_INFO_GLOW, ta, tb, f"{spot(row_left, sy)}\\1a&HFF&"
                                                             f"\\3c{outline_c}\\bord{max(1.5, txt.outline * k * 0.5):.1f}"
                                                             f"\\shad{txt.shadow * k * 0.4:.2f}{fad}",
                                        f"{{\\p1}}{frame}{{\\p0}}"))
                pid = pictures[entry] if pictures is not None and entry < len(pictures) else ""
                if pid:
                    ph = size * 0.95
                    pw = ph * aspect(f"pic:{pid}")
                    for tags, path in drawing(f"pic:{pid}", max(1, round(pw)), max(1, round(ph))):
                        events.append(_dialogue(L_INFO, ta, tb, f"{spot(row_left + side + gap * 0.6, y - size * 0.95)}"
                                                                f"\\bord0\\shad0\\blur0{tags}"
                                                                f"{fad}\\p1", path))
            if abs(dy) >= 0.5:
                pos = f"\\an2\\move({text_x:.1f},{y:.1f},{text_x:.1f},{y + dy:.1f},0,{dur})"
            else:
                pos = f"\\an2\\pos({text_x:.1f},{y:.1f})"
            font = f"\\fn{family}\\fs{size:.1f}\\b{int(bold)}"
            if glow.enabled:
                events.append(_dialogue(L_INFO_GLOW, ta, tb,
                                        f"{font}{pos}\\1a&HFF&\\3c{_bgr(glow.color_unsung)}"
                                        f"\\3a&H{int(round(255 * (1 - glow.strength / 100))):02X}&"
                                        f"\\bord{glow.size * k * (0.7 if level == 0 else 0.5):.1f}"
                                        f"\\blur{glow.blur * k:.1f}\\shad0{fad}", _escape(text)))
            events.append(_dialogue(L_INFO, ta, tb,
                                    f"{font}{pos}\\1c{color}{'' if level == 0 else sub_alpha}\\3c{outline_c}"
                                    f"\\bord{txt.outline * k * (0.7 if level == 0 else 0.55):.2f}"
                                    f"\\shad{txt.shadow * k * 0.6:.2f}{fad}", _escape(text)))
            y += gap
    return events


def _blend(colours: list[str], n: int) -> list[str]:
    """``n`` colours blending through ``colours`` from left to right (hex #RRGGBB)."""
    rgb = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in colours]
    out = []
    for s in range(n):
        f = s / (n - 1) * (len(rgb) - 1)
        j = min(int(f), len(rgb) - 2)
        t = f - j
        out.append("#" + "".join(f"{round(a + (b - a) * t):02X}" for a, b in zip(rgb[j], rgb[j + 1])))
    return out


def progress_events(style: KaraokeStyle, W: int, H: int, k: float, t0: float, t1: float) -> list[str]:
    """A thin line along the top or bottom edge showing how far into the audio the video is: in the sung
    colour, from the left edge, growing to the full width between ``t0`` and ``t1``."""
    pr = style.progress
    if not pr.enabled or t1 <= t0:
        return []
    thick = max(1.0, pr.thickness * k)
    y0 = 0.0 if pr.position == "top" else H - thick
    alpha = int(round(255 * (1 - pr.opacity / 100)))
    rect = f"m 0 0 l {W:.0f} 0 l {W:.0f} {thick:.1f} l 0 {thick:.1f}"
    return [_dialogue(L_INFO, t0, t1,
                      f"\\an7\\pos(0,{y0:.1f})\\bord0\\shad0\\1c{_bgr(pr.color or style.text.color_sung)}"
                      f"\\1a&H{alpha:02X}&\\clip(0,{y0:.1f},1,{y0 + thick:.1f})"
                      f"\\t(0,{int(round(t1 - t0))},\\clip(0,{y0:.1f},{W},{y0 + thick:.1f}))",
                      f"{{\\p1}}{rect}{{\\p0}}")]


def info_style(family: str, size: float) -> str:
    return (f"Style: KInfo,{family},{size:.1f},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,"
            "0,0,7,0,0,0,1")


def _dialogue(layer: int, t0: float, t1: float, tags: str, body: str) -> str:
    from .ass import ass_time

    return f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},KInfo,,0,0,0,,{{{tags}}}{body}"


def _bgr(hex_rgb: str) -> str:
    from .ass import bgr_tag

    return bgr_tag(hex_rgb)


def _escape(text: str) -> str:
    from .ass import escape_text

    return escape_text(text)
