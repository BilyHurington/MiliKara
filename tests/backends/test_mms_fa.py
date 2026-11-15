"""MMS_FA backend: the export's vocabulary quirks, paths, and frame bookkeeping.

The weights (317 MB) are never downloaded here: ``mms_fa.load`` is replaced by a fake
session whose "acoustics" are synthetic, which still exercises the column slicing, the
blank, renormalisation and the chunk arithmetic.
"""

import json

import numpy as np
import pytest

from kara_align.align import backends as B
from kara_align.align.backends import mms_fa as F
from kara_align.align.backends.chunking import conv_output_length
from kara_align.models import AlignConfig

# the real tokenizer.json of xycld/lyric-align-mms-fa, in file order
TOKENIZER = {"-": 0, "a": 1, "i": 2, "e": 3, "n": 4, "o": 5, "u": 6, "t": 7, "s": 8,
             "r": 9, "m": 10, "k": 11, "l": 12, "d": 13, "g": 14, "h": 15, "y": 16,
             "b": 17, "p": 18, "w": 19, "c": 20, "v": 21, "j": 22, "z": 23, "f": 24,
             "'": 25, "q": 26, "x": 27, "*": 28}


# -- the export's vocabulary -------------------------------------------------------

def test_graph_frame_count_matches_the_repo_arithmetic():
    # checked against the exported graph's own output shape
    assert F.HOP == 320 and F.RF == 400
    assert conv_output_length(16000, F.KERNELS, F.STRIDES) == 49


def test_read_vocab_drops_the_constant_column():
    v = F.read_vocab(TOKENIZER)
    assert v["columns"] == 28
    assert "*" not in v["vocab"]
    assert v["blank_id"] == 0  # "-" is the CTC blank, the token on silence
    assert v["vocab"]["a"] == 1 and v["vocab"]["x"] == 27


def test_tokenize_uses_the_letter_vocab():
    backend = F.MMSFABackend()
    monkeypatch_entry(backend, session=None)
    (unit,) = backend.tokenize(["u1"], ["Hoeng"])
    assert unit.token_ids == [15, 5, 3, 4, 14]  # h o e n g, lower-cased
    assert unit.unknown == []


def test_tokenize_marks_letters_the_vocab_lacks():
    backend = F.MMSFABackend()
    monkeypatch_entry(backend, session=None)
    (unit,) = backend.tokenize(["u1"], ["hoeng2"])
    assert unit.unknown == ["2"] and unit.token_ids == [15, 5, 3, 4, 14]


def monkeypatch_entry(backend, session):
    """Give the backend a loaded entry without onnxruntime or a download."""
    backend._entry = {**F.read_vocab(TOKENIZER), "session": session, "input": "waveform",
                      "output": "log_probs", "special": {F.BOGUS_TOKEN},
                      "providers": ["CPUExecutionProvider"], "variant": "fake", "notes": []}


# -- files -------------------------------------------------------------------------

def test_files_from_a_local_folder(tmp_path):
    (tmp_path / "tokenizer.json").write_text(json.dumps(TOKENIZER), encoding="utf-8")
    (tmp_path / "mms_fa.onnx").write_bytes(b"fp32")
    onnx, tok = F.files(str(tmp_path), None)
    assert onnx.name == "mms_fa.onnx" and tok.name == "tokenizer.json"
    (tmp_path / "mms_fa_uint8.onnx").write_bytes(b"int8")
    onnx, _ = F.files(str(tmp_path), None)  # quantised preferred
    assert onnx.name == "mms_fa_uint8.onnx"


def test_files_local_folder_without_a_graph(tmp_path):
    with pytest.raises(ValueError, match="没有"):
        F.files(str(tmp_path), None)


# -- registry ----------------------------------------------------------------------

def test_registry_entry():
    entry = next(b for b in B.list_backends() if b["name"] == "mms-fa")
    assert entry["languages"] == ["ja", "zh", "yue", "en"]
    assert entry["default_model"] == F.DEFAULT_MODEL_ID
    assert entry["default_revision"] == F.DEFAULT_REVISION
    assert entry["license"] == F.DEFAULT_LICENSE
    assert set(B.BACKENDS["mms-fa"]["requires"]) == {"onnxruntime", "huggingface_hub"}


def test_registry_error_without_onnxruntime(monkeypatch):
    import importlib.util as iu
    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec",
                        lambda name, *a, **k: None if name in ("onnxruntime", "huggingface_hub")
                        else real(name, *a, **k))
    with pytest.raises(RuntimeError, match=r"milikara\[fa\]"):
        B.get_backend(AlignConfig(backend="mms-fa"))
    entry = next(b for b in B.list_backends() if b["name"] == "mms-fa")
    assert entry["available"] is False and "onnxruntime" in entry["missing"]

def test_backend_metadata_without_loading():
    pytest.importorskip("onnxruntime")
    backend = B.get_backend(AlignConfig(backend="mms-fa"))
    assert isinstance(backend, F.MMSFABackend)
    assert backend.supports_language("yue") and backend.supports_language("zh")
    assert not backend.supports_language("ko")
    info = backend.info()
    assert info.sample_rate == 16000 and info.frame_hop_samples == 320
    assert info.profile == "ja-hepburn"  # zh/yue letters feed MMS_FA as they are
    assert info.model_revision == F.DEFAULT_REVISION
    assert info.license == F.DEFAULT_LICENSE
    own = F.MMSFABackend(model_id="me/other", revision="r")
    assert own.info().license.startswith("unknown")


# -- acoustics, on a fake session ---------------------------------------------------

class FakeSession:
    """Emits log-softmax over 28 classes plus the exporter's constant-zero column."""

    def __init__(self, normalised=True, favoured=1):
        self.normalised, self.favoured = normalised, favoured
        self.rows = 0

    def run(self, names, feeds):
        n = int(feeds["waveform"].shape[1])
        frames = conv_output_length(n, F.KERNELS, F.STRIDES)
        self.rows += frames
        logits = np.full((frames, 28), -30.0, np.float32)
        logits[:, self.favoured] = 0.0  # token "a" everywhere
        if self.normalised:
            logits -= np.log(np.exp(logits, dtype=np.float64).sum(axis=1))[:, None].astype(np.float32)
        else:
            logits *= 1.7  # raw scores: the mass is not 1 any more
        out = np.concatenate([logits, np.zeros((frames, 1), np.float32)], axis=1)[None, ...]
        return [out]


def _backend_with_fake(session, **kw):
    backend = F.MMSFABackend(**kw)
    backend._entry = {**F.read_vocab(TOKENIZER), "session": session, "input": "waveform",
                      "output": "log_probs", "special": {F.BOGUS_TOKEN},
                      "providers": ["CPUExecutionProvider"], "variant": "fake", "notes": []}
    return backend


def test_emissions_drops_the_zero_column_and_keeps_the_blank():
    session = FakeSession()
    e = _backend_with_fake(session).emissions(np.zeros(16000, np.float32), origin_samples=8000)
    assert e.logp.shape == (49, 28)
    assert e.blank_id == 0
    assert np.allclose(np.exp(e.logp).sum(axis=1), 1.0, atol=1e-5)
    assert (e.logp.argmax(axis=1) == 1).all()  # never the bogus column
    assert e.frame_map.hop_samples == F.HOP and e.frame_map.frame_start_ms(0) == 500


def test_emissions_renormalises_unnormalised_columns():
    e = _backend_with_fake(FakeSession(normalised=False)).emissions(np.zeros(16000, np.float32))
    assert np.allclose(np.exp(e.logp).sum(axis=1), 1.0, atol=1e-3)


def test_emissions_are_chunked_without_seams():
    # longer than one chunk: the frame count is the model's, not the chunk plan's
    session = FakeSession()
    backend = _backend_with_fake(session, chunk_s=1.0, context_s=0.2)
    e = backend.emissions(np.zeros(16000 * 3, np.float32))
    assert e.logp.shape == (conv_output_length(16000 * 3, F.KERNELS, F.STRIDES), 28)
    assert session.rows > e.logp.shape[0]  # context frames were computed and cut away
