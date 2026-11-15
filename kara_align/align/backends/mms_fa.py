"""Meta MMS forced aligner (MMS_FA) through ONNX Runtime.

Default weights: ``xycld/lyric-align-mms-fa`` pinned to an exact commit; the
INT8 variant (317 MB) is used, which needs no torch and runs on the CPU about
15x faster than real time.  The graph is an ONNX conversion of the checkpoint
``torchaudio.pipelines.MMS_FA`` ships: a wav2vec2-large CTC aligner trained on
1,130 languages' *romanised* text.

Why this backend exists: the Japanese karaoke model (``mms-ja``) has never
heard Chinese, and ``mms-mixed`` can only approximate a syllable with Japanese
morae.  MMS_FA was trained on romanised Mandarin, so plain pinyin / Jyutping
letters are exactly its input — the ``ja-hepburn`` profile already produces
them (zh and yue units become their lower-case letters, ja units Hepburn
romaji, punctuation dropped).

Graph facts (checked against the exported file, not assumed)
-----------------------------------------------------------
* input ``waveform``: float32 ``[1, samples]`` at 16 kHz, **no**
  normalisation — the feature encoder's GroupNorm does that internally, so
  unlike ``wav2vec2_ctc`` the signal is fed as it is;
* output ``log_probs``: ``[1, frames, 29]`` at 50 frames/s; ``frames`` equals
  ``conv_output_length(n, KERNELS, STRIDES)`` for the wav2vec2-large stack, so
  the hop is 320 samples (20 ms) and the receptive field 400 samples, and
  chunked inference keeps the repo's frame bookkeeping exactly;
* the head really has 28 classes (``model.aux.bias`` has 28 entries): ``-``,
  ``a``-``z`` and ``'``.  The 29th column is a *constant zero* column the
  exporter concatenated (its log-probability is 0 on every frame, i.e. a
  probability of 1 that would win every argmax), so it is dropped here and the
  remaining columns are renormalised if their mass is not 1.  ``-`` (column 0)
  is the CTC blank.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from ...interfaces import Emission, TokenizedUnit
from ...models import BackendInfo
from ...timebase import FrameMap
from .chunking import chunked_forward, conv_output_length, receptive_field
from .wav2vec2_ctc import model_cache_dir, tokenize_chars

DEFAULT_MODEL_ID = "xycld/lyric-align-mms-fa"
# commit sha of the conversion imported on 2026-11 (api/models -> sha)
DEFAULT_REVISION = "06e6cbfa5c257d23981c922f6406f5def958eb95"
# from the model card metadata (the original MMS_FA weights are CC-BY-NC-4.0)
DEFAULT_LICENSE = "cc-by-nc-4.0"
FA_HINT = "MMS_FA 对齐后端需要 onnxruntime 和 huggingface_hub：pip install 'milikara[fa]'"

SAMPLE_RATE = 16000
# wav2vec2-large feature encoder; conv_output_length() with these was checked
# against the exported graph's frame count for several input lengths
KERNELS = [10, 3, 3, 3, 3, 2, 2]
STRIDES = [5, 2, 2, 2, 2, 2, 2]
HOP = int(np.prod(STRIDES))
RF = receptive_field(KERNELS, STRIDES)

# quantised first: smaller, faster, and the card measured no accuracy loss
VARIANTS = ("mms_fa_uint8.onnx", "mms_fa.onnx")
TOKENIZER_FILE = "tokenizer.json"
# the exporter's appended constant-zero column (see the module docstring)
BOGUS_TOKEN = "*"

_CACHE: dict[tuple[str, Optional[str], str], dict[str, Any]] = {}
_LOCK = threading.Lock()


def _import_ort():
    try:
        import onnxruntime
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RuntimeError(FA_HINT) from exc
    return onnxruntime


def files(model_id: str, revision: Optional[str]) -> tuple[Path, Path]:
    """Local paths of the ONNX graph and its tokenizer.

    ``model_id`` may be a Hub repo (downloaded once, into the same models
    directory as the other backends' weights) or a local folder holding both
    files.
    """
    p = Path(model_id).expanduser()
    if p.is_dir():
        onnx = next((p / v for v in VARIANTS if (p / v).exists()), None)
        if onnx is None:
            raise ValueError(f"{p} 里没有 {' / '.join(VARIANTS)}")
        return onnx, p / TOKENIZER_FILE
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RuntimeError(FA_HINT) from exc
    cache = model_cache_dir(model_id)
    kw = {"cache_dir": cache} if cache else {}
    return (Path(hf_hub_download(model_id, VARIANTS[0], revision=revision, **kw)),
            Path(hf_hub_download(model_id, TOKENIZER_FILE, revision=revision, **kw)))


def read_vocab(tokenizer: dict[str, int]) -> dict[str, Any]:
    """Real token -> id map, the columns to keep, and the CTC blank.

    The exporter concatenates a constant-zero column for ``*`` after the graph's own
    log-softmax (see the module docstring): it is not a distribution and is dropped.
    Column 0, ``-``, is the blank.
    """
    real = {tok: int(idx) for tok, idx in tokenizer.items() if tok != BOGUS_TOKEN}
    return {"vocab": real, "columns": len(real), "blank_id": real.get("-", 0)}


def load(model_id: str, revision: Optional[str], device: str) -> dict[str, Any]:
    """Load (once per process) the ONNX session + vocabulary."""
    key = (model_id, revision, device)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        ort = _import_ort()
        onnx_path, tok_path = files(model_id, revision)
        notes: list[str] = []
        avail = ort.get_available_providers()
        wanted = {"cuda": "CUDAExecutionProvider", "gpu": "CUDAExecutionProvider"}.get(device)
        if wanted and wanted in avail:
            providers = [wanted, "CPUExecutionProvider"]
        else:
            if wanted:
                notes.append(f"{wanted} 不可用，改用 CPU")
            providers = ["CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.log_severity_level = 3  # warnings only: the graph loads with many hints otherwise
        session = ort.InferenceSession(str(onnx_path), so, providers=providers)
        vocab = json.loads(Path(tok_path).read_text(encoding="utf-8"))
        entry = {
            "session": session,
            "input": session.get_inputs()[0].name,
            "output": session.get_outputs()[0].name,
            "special": {BOGUS_TOKEN},
            "providers": providers,
            "variant": onnx_path.name,
            "notes": notes,
            **read_vocab(vocab),
        }
        _CACHE[key] = entry
        return entry


class MMSFABackend:
    name = "mms-fa"
    default_model_id: Optional[str] = DEFAULT_MODEL_ID
    default_revision: Optional[str] = DEFAULT_REVISION
    default_license: Optional[str] = DEFAULT_LICENSE
    languages = ("ja", "zh", "yue", "en")
    profile = "ja-hepburn"

    def __init__(self, model_id: Optional[str] = None, revision: Optional[str] = None,
                 device: str = "auto", chunk_s: float = 20.0, context_s: float = 3.0) -> None:
        self.model_id = model_id or self.default_model_id
        if not self.model_id:
            raise ValueError("后端 mms-fa 需要 HF 仓库名或包含 .onnx 与 tokenizer.json 的本地目录")
        if revision is None and self.model_id == DEFAULT_MODEL_ID:
            revision = DEFAULT_REVISION
        self.revision = revision
        self.requested_device = device
        self.chunk_s = chunk_s
        self.context_s = context_s
        self._entry: Optional[dict[str, Any]] = None

    # -- loading -----------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        if self._entry is None:
            self._entry = load(self.model_id, self.revision, self.requested_device)
        return self._entry

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    @property
    def hop_samples(self) -> int:
        return HOP

    def info(self) -> BackendInfo:
        e = self._entry
        extra: dict[str, Any] = {
            "device": "cpu" if not e else e["providers"][0].replace("ExecutionProvider", "").lower(),
            "chunk_s": self.chunk_s, "context_s": self.context_s,
            "normalization": "none (the feature encoder normalises internally)",
            "frame_rate_hz": SAMPLE_RATE / HOP,
        }
        if e:
            extra["providers"] = e["providers"]
            extra["variant"] = e["variant"]
            if e["notes"]:
                extra["notes"] = list(e["notes"])
        license_ = self.default_license if self.model_id == self.default_model_id else None
        return BackendInfo(
            name=self.name, model_id=self.model_id, model_revision=self.revision,
            license=license_ or "unknown (check the model card)", profile=self.profile,
            sample_rate=SAMPLE_RATE, frame_hop_samples=HOP, extra=extra,
        )

    def supports_language(self, lang: str) -> bool:
        # the model itself covers 1,130 languages, but only these have readings
        return lang in self.languages

    # -- tokens ------------------------------------------------------------
    def tokenize(self, unit_ids: Sequence[str], unit_texts: Sequence[str]) -> list[TokenizedUnit]:
        e = self._load()
        return tokenize_chars(unit_ids, unit_texts, e["vocab"], e["special"])

    # -- acoustics ---------------------------------------------------------
    def emissions(self, audio: np.ndarray, origin_samples: int = 0, cancel=None, progress=None) -> Emission:
        e = self._load()
        x = np.asarray(audio, dtype=np.float32)
        if x.ndim != 1:
            raise ValueError("emissions() 需要单声道音频")
        columns, session = e["columns"], e["session"]
        input_name, output_name = e["input"], e["output"]

        def forward(seg: np.ndarray) -> np.ndarray:
            out = session.run([output_name],
                              {input_name: np.ascontiguousarray(seg, dtype=np.float32)[None, :]})[0]
            return np.asarray(out[0], dtype=np.float32)[:, :columns]

        logp = chunked_forward(
            x, forward, hop=HOP, rf=RF,
            n_frames=lambda n: conv_output_length(n, KERNELS, STRIDES),
            chunk_samples=int(self.chunk_s * SAMPLE_RATE),
            context_samples=int(self.context_s * SAMPLE_RATE),
            cancel=cancel, progress=progress)
        # the graph's own log-softmax is over the 28 real classes; renormalise only if the
        # conversion ever leaves the columns unscaled (it costs one pass over the frames)
        mass = np.exp(logp, dtype=np.float64).sum(axis=1)
        if logp.size and not np.allclose(mass, 1.0, atol=1e-2):
            logp = logp - np.log(mass, dtype=np.float64)[:, None].astype(np.float32)
        return Emission(logp=logp.astype(np.float32, copy=False),
                        frame_map=FrameMap(SAMPLE_RATE, HOP, 0, int(origin_samples)),
                        blank_id=e["blank_id"])
