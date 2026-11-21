"""The song-info card: where it goes, how long it stays, and the singer legend and progress line that
go with the centre layout."""

import re

from kara_align.karaoke import ass as A
from kara_align.models import AudioAsset, KaraokeSinger, KaraokeStyle

from .test_round5_render import _line, _ms, _project, _seg


def _song(*, dur=30_000, times=None, singers=False, two_lines=False):
    """A project with a title and an artist (and, optionally, a second line far away or two singers)."""
    lines = [_line([_seg("窓", "まど"), _seg("に", "に")])]
    if two_lines:
        lines.append(_line([_seg("君", "きみ")]))
    p, r = _project(lines, times=times or {0: (1000, 300)})
    p.audio = [AudioAsset(role="original", sha256="ab" * 32, duration_ms=dur, sample_rate=22050, channels=1,
                          num_samples=dur * 22)]
    p.lyrics.meta.title, p.lyrics.meta.artist = "窓に舞う桜", "歌い手"
    if singers:
        for ln in p.lyrics.lines:
            ln.singers = [1, 2]
    st = KaraokeStyle()
    st.info.enabled = True
    st.info.mode = "start"
    st.singers.members = [KaraokeSinger(name="Ann", color="#ED35B3"), KaraokeSinger(name="Bo", color="#2F80ED")]
    return p, r, st


def _cards(p, r, st) -> list[str]:
    return [line for line in A.build_ass(p, r, st)[0].splitlines() if ",KInfo," in line]


def _tags(line) -> str:
    return "".join(line.split(",,")[2:])  # the override tags


def _span(line) -> tuple[int, int]:
    return _ms(line.split(",")[1]), _ms(line.split(",")[2])


def _move_y(line):
    m = re.search(r"\\move\([-\d.]+,([-\d.]+),[-\d.]+,([-\d.]+),", line)
    return (float(m.group(1)), float(m.group(2))) if m else None


def test_auto_placement_follows_the_layout():
    p, r, st = _song()
    corner = _cards(p, r, st)
    assert corner and all("\\an7" in _tags(line) for line in corner)  # pinned to the corner
    st.layout.position = "center"
    column = _cards(p, r, st)
    assert column and all("\\an2" in _tags(line) for line in column)  # travels with the lyrics
    assert {_pos_x(line) for line in column} == {960.0}


def _pos_x(line) -> float:
    m = re.search(r"\\pos\(([\d.]+),", line) or re.search(r"\\move\(([\d.]+),", line)
    return float(m.group(1))


def test_corner_placement_in_the_centre_layout_draws_one_card_only():
    p, r, st = _song()
    st.layout.position = "center"
    st.info.placement = "corner"
    cards = _cards(p, r, st)
    assert cards and all("\\an7" in _tags(line) for line in cards)  # the corner card, no copy in the column


def test_persist_keeps_the_card_to_the_end_of_the_song():
    p, r, st = _song()
    st.info.placement = "corner"

    def title_end() -> int:
        first = next(line for line in _cards(p, r, st) if "\\fs56.0" in line)  # the opening title row
        return _span(first)[1]

    assert title_end() < 10_000  # "start": gone after duration_ms
    st.info.mode = "persist"
    assert title_end() >= 29_000  # there for the whole song


def test_repeat_shows_the_card_again_in_a_long_gap():
    p, r, st = _song(two_lines=True, times={0: (1000, 300), 1: (20_000, 300)})
    st.info.placement = "corner"
    once = {_span(line)[0] for line in _cards(p, r, st)}
    assert 7_500 not in once  # "start": only at the beginning
    st.info.mode = "repeat"
    again = {_span(line)[0] for line in _cards(p, r, st)}
    assert 7_500 in again  # back once the opening card's duration is over, in the 3 s → 20 s break


def test_the_singer_legend_travels_with_the_title_card():
    p, r, st = _song(singers=True)
    st.layout.position = "center"
    cards = _cards(p, r, st)
    title = next(line for line in cards if "\\fs56.0" in line and "\\move(" in line)
    legend = next(line for line in cards if "\\fs37.0" in line and "\\move(" in line)
    (ta, tb), (la, lb) = _move_y(title), _move_y(legend)
    assert la > ta and lb > tb  # under the title
    assert abs((la - ta) - (lb - tb)) < 0.5  # and it moves exactly with it


def test_the_progress_line_grows_across_the_song():
    p, r, st = _song()
    st.progress.enabled = True
    line = next(line for line in A.build_ass(p, r, st)[0].splitlines() if "\\clip(0," in line and "l 1920 0" in line)
    assert ",1920," in line  # grows to the full width
    t0, t1 = _span(line)
    assert t0 == 0 and t1 == 30_000  # from the start to the end of the song


def test_plate_and_fainter_sub_lines():
    p, r, st = _song()
    st.info.plate = True
    st.info.sub_opacity = 60
    text = A.build_ass(p, r, st)[0]
    assert "\\1c&H000000&\\1a&H78&" in text  # the dark plate behind the card
    assert "\\1a&H66&" in text  # the artist row, 60 % opaque


def test_a_prompter_card_keeps_the_reading_line_until_the_first_lyric():
    p, r, st = _song(times={0: (6000, 300)})
    st.layout.position = "center"
    st.layout.center_scroll = "prompter"
    st.layout.prompter_neighbors = False  # one line at a time: the card must own the reading line first
    text = A.build_ass(p, r, st)[0]
    cards = [line for line in text.splitlines() if ",KInfo," in line]
    assert cards, "the card is drawn in a prompter column"
    assert {_span(line) for line in cards} == {(500, 5650)}  # up to the first lyric, less its fade-out
    first = min(_span(line)[0] for line in text.splitlines() if ",KMain," in line)
    assert first == 5650  # the lyric waited for the card instead of sharing the reading line


def _y_start(line) -> float:
    m = re.search(r"\\move\([-\d.]+,([-\d.]+),", line) or re.search(r"\\pos\([-\d.]+,([-\d.]+)\)", line)
    return float(m.group(1))


def test_the_first_lyric_keeps_clear_of_the_title_card():
    p, r, st = _song(two_lines=True, times={0: (1000, 300), 1: (12_000, 300)})
    st.layout.position = "center"
    text = A.build_ass(p, r, st)[0]
    card_y = max(_y_start(line) for line in text.splitlines() if ",KInfo," in line)
    rows = sorted({_y_start(line) for line in text.splitlines() if ",KMain," in line})
    assert len(rows) >= 2
    # the card sits on the reading line, so the first lyric starts on the row below it: never squeezed
    # against the card's last row (and its countdown dots, which ride above the lyric)
    below = [y for y in rows if y > card_y]
    assert below and below[0] - card_y >= (rows[1] - rows[0]) * 0.5
