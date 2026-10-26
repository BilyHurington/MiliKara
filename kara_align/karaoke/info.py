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

from ..models import KaraokeStyle, Project
from .fonts import BUNDLED_JP, HAN_FAMILIES, Measurer, covering

LABELS = {"title": "歌名", "artist": "歌手", "album": "专辑", "lyricist": "作词", "composer": "作曲", "arranger": "编曲"}
# a label may name several roles ("作词/作曲", "词曲", "Lyrics & Music"): each one is filled
_CREDIT = {"lyricist": re.compile(r"作词|作詞|^[词詞]|lyric|written", re.I),
           "arranger": re.compile(r"编曲|編曲|arrang", re.I),
           "composer": re.compile(r"作曲|^曲$|[词詞]曲|compos|music", re.I)}
_LABEL = re.compile(r"^\s*([^:：]{1,16}?)\s*[:：]\s*(.+?)\s*$")

L_INFO_GLOW, L_INFO = 8, 9
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


def info_events(project: Project, style: KaraokeStyle, W: int, H: int, k: float, family: str,
                time_offset_ms: float, *,
                boxes: Sequence[tuple[float, float, float, float, float, float]] = (),
                warnings: Optional[list[str]] = None, end_ms: Optional[float] = None) -> list[str]:
    """Dialogue lines of the card (style ``KInfo``) in its corner at the top, for its whole
    duration: when a translation or lyric (``boxes``: (from, to, x0, y0, x1, y1) of what else is
    drawn) would be where the card is, the card gets narrower (its lines wrap) so it ends before it.
    ``end_ms`` (the end of the song on the subtitles' timeline): the ending card too."""
    info = style.info
    lines = info_lines(project, style)
    if not info.enabled or not lines:
        return []
    # a title / album with characters the lyric font lacks: a font with all of them (Windows: libass would
    # fill them in from a font of its own, at another size)
    family = covering(family, style.text.bold, _text_of("".join(lines)), [BUNDLED_JP, *HAN_FAMILIES])
    t0 = max(0.0, info.start_ms + time_offset_ms)
    t1 = t0 + info.duration_ms
    events = _card(lines, style, W, H, k, family, t0, t1, boxes, warnings, at_end=False)
    if info.outro and end_ms is not None and end_ms - info.outro_duration_ms > t1:
        events += _card(lines, style, W, H, k, family, end_ms - info.outro_duration_ms, end_ms, boxes, warnings,
                        at_end=True)
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
                  warnings: Optional[list[str]] = None) -> list[str]:
    """The legend card: right after the opening song card (else at its start time), for ``legend_ms``."""
    rows = legend_entries(project, style)
    if not rows:
        return []
    info = style.info
    # names: in one font that has all their characters (not filled in from another, in another style)
    family = covering(family, style.text.bold, _text_of("".join(r[0] for r in rows)), [BUNDLED_JP, *HAN_FAMILIES],
                      whole=True)
    t0 = max(0.0, info.start_ms + time_offset_ms)
    if info.enabled and info_lines(project, style):
        t0 += info.duration_ms
    return _card([r[0] for r in rows], style, W, H, k, family, t0, t0 + style.singers.legend_ms, boxes, warnings,
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
        sub_alpha = "" if level == 0 else "\\1a&H18&"
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
