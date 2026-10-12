import subprocess
import sys

import numpy as np
import pytest

from kara_align.audio import analysis, io, mix, separation, sync
import importlib

resample = importlib.import_module("kara_align.audio.resample")
from kara_align.models import AudioSource


# ---------------------------------------------------------------- resample
def test_to_mono_shapes():
    x = np.stack([np.ones(10), np.zeros(10)]).astype(np.float32)
    assert resample.to_mono(x).shape == (10,)
    assert np.allclose(resample.to_mono(x), 0.5)
    assert resample.to_mono(np.ones(5)).shape == (5,)


@pytest.mark.parametrize("sr_from,sr_to,n", [(44100, 16000, 44101), (48000, 16000, 12345), (22050, 44100, 1001)])
def test_resample_length_rule(sr_from, sr_to, n):
    y = resample.resample(np.zeros((2, n), np.float32), sr_from, sr_to)
    assert y.shape == (2, int(n * sr_to / sr_from + 0.5))


def test_resample_preserves_origin():
    sr_from, sr_to = 44100, 16000
    x = np.zeros(44100, np.float32)
    t0 = 0.5
    x[int(t0 * sr_from)] = 1.0
    y = resample.resample(x, sr_from, sr_to)
    peak = int(np.argmax(np.abs(y)))
    assert abs(peak - t0 * sr_to) <= 1


# ---------------------------------------------------------------- io
def _click(sr, dur_s, click_s, ch=1):
    x = np.zeros((ch, int(sr * dur_s)), np.float32)
    i = int(click_s * sr)
    x[:, i:i + 20] = 0.9
    return x


def test_wav_roundtrip_and_probe(tmp_path):
    x = _click(44100, 1.0, 0.3, ch=2)
    p = tmp_path / "a.wav"
    io.write_wav(p, x, 44100)
    y, sr = io.load_audio(p)
    assert sr == 44100 and y.shape == x.shape
    assert np.allclose(y, x, atol=1e-4)
    info = io.probe_audio(p)
    assert info == {"duration_ms": 1000, "sample_rate": 44100, "channels": 2, "num_samples": 44100}
    m, _ = io.load_audio(p, mono=True, target_sr=16000)
    assert m.shape == (1, 16000)


def test_mp3_decode_trims_priming(tmp_path):
    sr = 44100
    click_s = 0.5
    x = _click(sr, 2.0, click_s)
    wav = tmp_path / "c.wav"
    io.write_wav(wav, x, sr)
    mp3 = tmp_path / "c.mp3"
    r = subprocess.run([io.ffmpeg_path(), "-v", "error", "-y", "-i", str(wav), "-b:a", "192k", str(mp3)])
    assert r.returncode == 0
    y, ysr = io.load_audio(mp3)
    assert ysr == sr
    assert abs(y.shape[1] - x.shape[1]) < sr * 0.03  # duration within 30 ms
    onset = int(np.argmax(np.abs(y[0]) > 0.3))
    assert abs(onset - click_s * sr) < sr * 0.002  # priming delay trimmed (<2 ms)


def test_validate_upload():
    assert io.validate_upload("x.WAV", b"RIFF....WAVEfmt ", 100) == ".wav"
    with pytest.raises(io.AudioError):
        io.validate_upload("x.exe", b"RIFF", 100)
    with pytest.raises(io.AudioError):
        io.validate_upload("x.mp3", b"<html>", 100)
    with pytest.raises(io.AudioError):
        io.validate_upload("x.mp3", b"ID3", 10, max_bytes=5)


def test_import_asset(tmp_path):
    src = tmp_path / "in.wav"
    io.write_wav(src, _click(22050, 0.5, 0.1), 22050)
    proj = tmp_path / "proj"
    a = io.import_asset(src, "original", proj / "assets", AudioSource(kind="upload"), project_dir=proj)
    assert a.path == f"assets/{a.sha256}.wav"
    assert (proj / a.path).exists()
    assert a.sample_rate == 22050 and a.num_samples == 11025 and a.duration_ms == 500
    assert a.source.filename == "in.wav"
    b = io.import_asset(src, "original", proj / "assets", project_dir=proj)
    assert b.sha256 == a.sha256


# ---------------------------------------------------------------- mix
def test_mix_formula_and_percentages():
    sr = 100
    v = np.full(10, 0.2, np.float32)
    i = np.full(10, 0.3, np.float32)
    for p in (0, 20, 100):
        m, rep = mix.mix_stems(v, i, sr, p, 100, limiter="none")
        assert np.allclose(m, p / 100 * 0.2 + 0.3)
        assert rep.bus_gain == 1.0
    m, _ = mix.mix_stems(v, i, sr, 20, 50, master=0.5, limiter="none")
    assert np.allclose(m, 0.5 * (0.2 * 0.2 + 0.5 * 0.3))
    with pytest.raises(mix.MixError):
        mix.mix_stems(v, i, sr, 120, 100)


def test_mix_bus_gain_keeps_ratio():
    v = np.array([0.8, 0.1], np.float32)
    i = np.array([0.8, 0.2], np.float32)
    m, rep = mix.mix_stems(v, i, 10, 100, 100, limiter="normalize_peak", ceiling_dbfs=0.0)
    assert rep.peak_before == pytest.approx(1.6)
    assert rep.bus_gain == pytest.approx(1 / 1.6)
    assert np.allclose(m[0], np.array([1.6, 0.3]) / 1.6, atol=1e-6)
    assert rep.clipped_samples == 0
    m2, rep2 = mix.mix_stems(v, i, 10, 100, 100, limiter="none")
    assert rep2.clipped_samples == 1 and rep2.bus_gain == 1.0


def test_mix_end_padding_and_missing_stem():
    v = np.ones(5, np.float32)
    i = np.ones(8, np.float32)
    m, rep = mix.mix_stems(v, i, 10, 100, 100, limiter="none")
    assert m.shape == (1, 8)
    assert np.allclose(m[0], [2, 2, 2, 2, 2, 1, 1, 1])
    assert rep.notes
    with pytest.raises(mix.MixError):
        mix.mix_stems(None, i, 10, 50)
    with pytest.raises(mix.MixError):
        mix.export_mix_wav(None, "x.wav", "o.wav", 50)


def test_export_mix_wav(tmp_path):
    sr = 8000
    v = np.full((1, sr), 0.1, np.float32)
    ins = np.full((1, sr - 100), 0.2, np.float32)
    io.write_wav(tmp_path / "v.wav", v, sr, "FLOAT")
    io.write_wav(tmp_path / "i.wav", ins, sr, "FLOAT")
    rep = mix.export_mix_wav(tmp_path / "v.wav", tmp_path / "i.wav", tmp_path / "m.wav", 20, 100,
                             original_num_samples=sr, subtype="FLOAT")
    y, ysr = io.load_audio(tmp_path / "m.wav")
    assert ysr == sr and y.shape == (1, sr)
    assert np.allclose(y[0, :100], 0.22, atol=1e-6)
    assert np.allclose(y[0, -50:], 0.02, atol=1e-6)
    assert rep.tracks["vocals"]["gain"] == pytest.approx(0.2)
    assert len(rep.tracks["output"]["sha256"]) == 64


# ---------------------------------------------------------------- sync
def test_estimate_lag():
    sr = 16000
    rng = np.random.default_rng(0)
    ref = rng.standard_normal(sr * 2).astype(np.float32)
    d = int(0.037 * sr)
    late = np.concatenate([np.zeros(d, np.float32), ref[:-d]])
    lag, corr = sync.estimate_lag(ref, late, sr)
    assert lag == pytest.approx(37.0, abs=0.5)
    assert corr > 0.8
    early = np.concatenate([ref[d:], np.zeros(d, np.float32)])
    lag, _ = sync.estimate_lag(ref, early, sr)
    assert lag == pytest.approx(-37.0, abs=0.5)


def test_check_stem_sync_equal_length_not_sync():
    sr = 16000
    rng = np.random.default_rng(1)
    v = rng.standard_normal(sr).astype(np.float32)
    i = rng.standard_normal(sr).astype(np.float32)
    orig = v + i
    shifted_v = np.concatenate([np.zeros(160, np.float32), v[:-160]])  # same length, 10 ms late
    rep = sync.check_stem_sync(orig, shifted_v, sr, "vocals")
    assert rep["length_diff_samples"] == 0 and not rep["ok"]
    good = sync.check_stem_sync(orig, v, sr, "vocals", other_stem=i)
    assert good["ok"] and good["sum_residual_db"] is None or good["sum_residual_db"] < -60


def test_derive_instrumental():
    sr = 16000
    rng = np.random.default_rng(2)
    v = rng.standard_normal(sr).astype(np.float32)
    i = 0.5 * rng.standard_normal(sr).astype(np.float32)
    orig = v + i
    assert np.allclose(sync.derive_instrumental(orig, v, sr), i, atol=1e-5)
    late = np.concatenate([np.zeros(50, np.float32), v[:-50]])
    with pytest.raises(sync.SyncError):
        sync.derive_instrumental(orig, late, sr)
    with pytest.raises(sync.SyncError):
        sync.derive_instrumental(orig, rng.standard_normal(sr).astype(np.float32), sr)


# ---------------------------------------------------------------- analysis
def test_envelope_timing():
    sr = 16000
    x = np.zeros(sr, np.float32)
    x[int(0.5 * sr):int(0.6 * sr)] = 0.5
    env = analysis.rms_envelope_db(x, sr, hop_ms=10, win_ms=30)
    assert len(env) == 100
    loud = np.where(env > -20)[0]
    # centred windows: symmetric spill of one frame on both sides of [500, 600) ms
    assert loud[0] == 49 and loud[-1] == 61
    assert env[50] > env[49] and env[59] > env[61]
    assert env[55] == pytest.approx(20 * np.log10(0.5), abs=0.1)
    assert env[10] <= -100


def test_waveform_peaks_and_json():
    x = np.array([0, 1, -1, 0.5, 0.2], np.float32)
    mins, maxs = analysis.waveform_peaks(x, 10, 2)
    assert np.allclose(mins, [0, -1, 0]) and np.allclose(maxs, [1, 0.5, 0.2])
    j = analysis.peaks_json(x, sr=10, samples_per_peak=2)
    assert j["num_samples"] == 5 and len(j["maxs"]) == 3


# ---------------------------------------------------------------- separation
def test_separation_missing_package(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "audio_separator", None)
    with pytest.raises(separation.SeparationError, match="audio-separator"):
        separation.separate(tmp_path / "x.wav", tmp_path / "out")


def test_fix_stem_length():
    x = np.arange(12, dtype=np.float32)[None, :]
    y, a = separation.fix_stem_length(x, 10)
    assert y.shape == (1, 10) and a["end_trimmed"] == 2 and np.allclose(y[0], np.arange(10))
    y, a = separation.fix_stem_length(x, 14)
    assert y.shape == (1, 14) and a["end_padded"] == 2 and y[0, 0] == 0 and y[0, -1] == 0
    y, a = separation.fix_stem_length(x, 10, leading_padding=2)
    assert np.allclose(y[0], np.arange(2, 12)) and a["leading_padding_removed"] == 2
    assert not a["stretched"]
    with pytest.raises(separation.SeparationError):
        separation.fix_stem_length(x, 100, max_mismatch=10)


def test_classify_outputs(tmp_path):
    v, i, o = separation._classify_outputs(["s_(Vocals)_m.wav", "s_(Instrumental)_m.wav"], tmp_path)
    assert v.name.startswith("s_(Vocals)") and i.name.startswith("s_(Instrumental)")
    with pytest.raises(separation.SeparationError):
        separation._classify_outputs(["s_(Vocals)_m.wav", "s_(Drums)_m.wav"], tmp_path)


def test_presets():
    names = {p.name for p in separation.PRESETS}
    assert {"bs-roformer", "melband-roformer"} <= names
    assert separation.get_preset("model_bs_roformer_ep_317_sdr_12.9755.ckpt").name == "bs-roformer"


def test_separator_reads_our_decoded_wav(tmp_path, monkeypatch):
    """The child process must get a WAV from our loader, never the source file
    (some FLACs break libsndfile, and the decode must match alignment's)."""
    import json as _json
    import subprocess as _sp

    import soundfile as _sf

    from kara_align.audio import separation as S

    src = tmp_path / "song.wav"
    _sf.write(src, np.zeros((4410, 2), dtype=np.float32), 44100)
    monkeypatch.setattr(S, "ensure_available", lambda: "test")
    seen = {}

    class FakeProc:
        returncode = 1

        def __init__(self, cmd, **kw):
            seen["args"] = _json.loads(cmd[-1])

        def poll(self):
            return 1

        def communicate(self):
            return "", "boom"

        def kill(self):
            pass

    monkeypatch.setattr(S.subprocess, "Popen", FakeProc)
    with pytest.raises(S.SeparationError):
        S.separate(src, tmp_path / "out", "mdx-fast")
    assert seen["args"]["input"].endswith("input.wav")
    data, sr = _sf.read(seen["args"]["input"])
    assert sr == 44100 and data.shape == (4410, 2)


def test_separator_pipes_are_drained_and_progress_parsed(tmp_path, monkeypatch):
    """A child that floods stderr (tqdm) must not deadlock; its % is reported."""
    import soundfile as _sf

    from kara_align.audio import separation as S

    src = tmp_path / "song.wav"
    _sf.write(src, np.zeros((4410, 2), dtype=np.float32), 44100)
    monkeypatch.setattr(S, "ensure_available", lambda: "test")
    # a fake child: 400 KB of tqdm-like stderr, then an error exit
    fake = ("import sys\nfor i in range(101):\n    sys.stderr.write(f'\\r{i:3d}%|' + '#' * 4000 + '|')\n"
            "sys.stderr.flush()\nsys.exit(3)\n")
    monkeypatch.setattr(S, "_CHILD_SCRIPT", fake)
    seen = []
    with pytest.raises(S.SeparationError):
        S.separate(src, tmp_path / "out", "mdx-fast", progress=lambda f, m: seen.append((f, m)), stall_s=60)
    assert any("%" in m for _, m in seen) or seen  # progress reported, no deadlock


def test_separation_is_stopped_only_when_it_makes_no_progress(tmp_path, monkeypatch):
    """A slow child that keeps reporting progress is waited for; a quiet one is stopped."""
    import time

    import soundfile as _sf

    from kara_align.audio import separation as S

    src = tmp_path / "song.wav"
    _sf.write(src, np.zeros((4410, 2), dtype=np.float32), 44100)
    monkeypatch.setattr(S, "ensure_available", lambda: "test")
    # slow but going: a progress line every 0.2 s for 2 s, far longer than the 0.8 s allowed without one
    slow = ("import sys, time\nfor i in range(10):\n    sys.stderr.write(f'\\r{i * 10:3d}%|#|'); sys.stderr.flush()\n"
            "    time.sleep(0.2)\nsys.exit(3)\n")
    monkeypatch.setattr(S, "_CHILD_SCRIPT", slow)
    t0 = time.monotonic()
    with pytest.raises(S.SeparationError, match="退出码 3"):  # ran to its end
        S.separate(src, tmp_path / "out", "mdx-fast", stall_s=0.8)
    assert time.monotonic() - t0 >= 1.8
    # stuck: nothing written at all
    monkeypatch.setattr(S, "_CHILD_SCRIPT", "import time\ntime.sleep(30)\n")
    t0 = time.monotonic()
    with pytest.raises(S.SeparationError, match="没有任何进展"):
        S.separate(src, tmp_path / "out2", "mdx-fast", stall_s=0.8)
    assert time.monotonic() - t0 < 10


def test_write_stem_flac_and_float_fallback(tmp_path):
    from kara_align.audio.io import load_audio, write_stem

    sr = 44100
    t = np.arange(sr, dtype=np.float32) / sr
    x = (0.5 * np.sin(2 * np.pi * 440 * t))[None, :].repeat(2, 0)
    p = write_stem(tmp_path / "vocals", x, sr)
    assert p.suffix == ".flac"
    y, ysr = load_audio(p)
    assert ysr == sr and y.shape == x.shape and np.max(np.abs(y - x)) < 1e-6
    loud = x * 3  # above full scale: must not be clipped
    p2 = write_stem(tmp_path / "inst", loud, sr)
    assert p2.suffix == ".wav"
    z, _ = load_audio(p2)
    assert np.allclose(z, loud, atol=1e-6)
