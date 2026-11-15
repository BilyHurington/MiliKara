"""Switching the lyrics' language: readings, default backend, and what it does to calibration.

A model that cannot hear the language still returns *some* alignment, and the offset
estimator would happily average the nonsense — that is the failure this guards against.
"""

import numpy as np
import pytest
import soundfile as sf

from kara_align import service as S
from kara_align.auto_calibrate import suggest_calibration
from kara_align.align.backends.fake import ScriptedBackend
from kara_align.reading import cantonese

jyutping = pytest.mark.skipif(cantonese.source() is None, reason="需要 Jyutping 词典")


def _wav(path, seconds=6.0, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    sf.write(path, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), sr)
    return str(path)


def _project(tmp_path, text, mode="lrc", name="proj", config=None):
    h = S.create_dir(tmp_path / name, "t", mode)
    if config is not None:
        S.update_settings(h, config=config)
    pv = S.parse_lyrics(h, text, origin="paste")
    assert pv["error"] is None, pv
    S.apply_lyrics(h, pv["preview_id"])
    return h


def test_a_mandarin_default_project_picks_the_sinophone_backend(tmp_path):
    """Untouched default + Chinese lyrics: the ja-only backend would only see pinyin letters."""
    h = _project(tmp_path, "[00:01.00]我爱你")
    assert h.project.lyrics.language == "zh"
    assert h.project.config.backend == "mms-mixed"


def test_japanese_lyrics_keep_the_ja_backend(tmp_path):
    h = _project(tmp_path, "[00:01.00]きみと")
    assert h.project.config.backend == "mms-ja"


def test_an_explicit_backend_or_model_is_left_alone(tmp_path):
    h = _project(tmp_path, "[00:01.00]我爱你", config={"backend": "mms-ja", "model_id": "someone/else"})
    assert h.project.config.backend == "mms-ja"


@jyutping
def test_switching_to_cantonese_rereads_in_jyutping(tmp_path):
    # standard written Chinese carries no Cantonese marker, so the reading is pinyin until told otherwise
    h = _project(tmp_path, "[00:01.00]我愛你的歌", config={"backend": "scripted"})
    assert h.project.lyrics.language == "zh"
    assert [u.reading for ln in h.project.lyrics.sung_lines() for s in ln.segments for u in s.units][0] == "wo"
    msgs = S.set_lyrics_language(h, "yue")
    assert any("粤语" in m for m in msgs)
    readings = [u.reading for ln in h.project.lyrics.sung_lines() for s in ln.segments for u in s.units]
    assert readings[0] == "ngo"  # 我, not pinyin "wo"
    assert h.project.lyrics.language == "yue"


def test_switching_to_cantonese_moves_the_default_backend(tmp_path):
    h = _project(tmp_path, "[00:01.00]我哋嘅歌")
    assert h.project.config.backend == "mms-mixed"  # zh already moved it
    S.set_lyrics_language(h, "ja")
    assert h.project.config.backend == "mms-ja"
    S.set_lyrics_language(h, "yue")
    assert h.project.config.backend == "mms-mixed"


def test_an_unknown_language_is_rejected(tmp_path):
    h = _project(tmp_path, "[00:01.00]きみと")
    with pytest.raises(S.ServiceError):
        S.set_lyrics_language(h, "klingon")


def test_calibration_is_not_confident_when_the_model_cannot_hear_the_language(tmp_path):
    """The scripted backend claims ja/en only: a Cantonese lyric under it must not be trusted."""
    # this model aligns the letters of the Jyutping perfectly — the point is that a perfect-looking
    # offset from a backend that does not claim the language must still not be applied on its own
    ScriptedBackend.default_script = [("ngodei", 1000, 2600), ("gego", 2900, 4200), ("m", 4500, 4800),
                                      ("hai", 5000, 5400)]
    h = _project(tmp_path, "[00:01.00]我哋\n[00:02.90]嘅歌\n[00:04.50]唔\n[00:05.00]係", config={"backend": "scripted"})
    assert h.project.lyrics.language == "yue"  # 哋 嘅 唔 are Cantonese-only characters
    S.add_audio(h, _wav(tmp_path / "a.wav"), "original")
    out = suggest_calibration(h)
    assert out["confident"] is False
    assert "不支持" in out["reason"]


def test_calibration_stays_confident_for_a_language_the_model_knows(tmp_path):
    """Control: the same four lines in Japanese, same backend, keeps its verdict."""
    ScriptedBackend.default_script = [("kimito", 1000, 1600), ("aruita", 3000, 3600), ("michi", 4500, 4800),
                                      ("sora", 5000, 5400)]
    h = _project(tmp_path, "[00:01.00]きみと\n[00:03.00]あるいた\n[00:04.50]みち\n[00:05.00]そら",
                 config={"backend": "scripted"})
    S.add_audio(h, _wav(tmp_path / "a.wav"), "original")
    assert suggest_calibration(h)["confident"] is True
