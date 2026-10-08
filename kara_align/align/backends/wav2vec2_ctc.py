"""Hugging Face wav2vec2 / MMS CTC backend.

Default weights: ``NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`` pinned to
an exact commit (never "latest").  The model outputs per-frame logits over a
character vocabulary (``a``-``z``, ``'``, word delimiter ``|``, ``[UNK]``,
``[PAD]``); ``[PAD]`` is the CTC blank.

Design decisions
----------------
* Tokens: each unit text is lower-cased and mapped character by character.
  Whitespace is skipped (it is not a sound); any other character missing from
  the vocabulary is reported in ``TokenizedUnit.unknown`` – never silently
  dropped – and the caller decides how to flag the unit.
* The word delimiter ``|`` is **not** inserted between units: units are
  sub-word morae and the delimiter does not belong to any unit.  The default
  model does emit ``|`` between words (greedy decode of TTS speech gives
  ``kimi|to|aruitamichi``), so by default (``delimiter_as_blank=True``) the
  delimiter's probability mass is merged into the blank column
  (``logaddexp``) – frames spent on word gaps then behave like blank instead
  of being forced onto a neighbouring mora.  Recorded in ``BackendInfo.extra``.
* Normalisation: the feature extractor uses zero-mean / unit-variance over
  its input.  We normalise over the *whole analysed signal once* and then feed
  chunks unnormalised, so the chunked result only depends on the audio and the
  chunk context, not on chunk boundaries' statistics.
* Frames: frame ``t`` covers samples ``[t*hop, t*hop + rf)`` of the analysed
  16 kHz signal (hop = product of conv strides = 320, rf = 400 for this
  architecture).  ``FrameMap.offset_samples`` is 0, i.e. a frame's time is the
  start of its receptive field; all values come from the model config.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from ...gpu import cuda_device, is_rocm
from ...interfaces import Emission, TokenizedUnit
from ...models import BackendInfo
from ...timebase import FrameMap
from .chunking import chunked_forward, conv_output_length, receptive_field

DEFAULT_MODEL_ID = "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"
# commit sha verified via HfApi().model_info(...) on 2026-09-23
DEFAULT_REVISION = "2ab2b5f46539ee284703c281f286b01d2410ee12"
# from the model card metadata of that revision
DEFAULT_LICENSE = "cc-by-nc-sa-4.0"
ML_HINT = "CTC 对齐后端需要 torch 和 transformers：pip install 'milikara[ml]'"

_MODEL_CACHE: dict[tuple[str, Optional[str], str], dict[str, Any]] = {}
_LOCK = threading.Lock()


def _import_ml():
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RuntimeError(ML_HINT) from exc
    import torch
    import transformers
    return torch, transformers


def resolve_device(device: str) -> str:
    torch, _ = _import_ml()
    if device and device != "auto":
        return device
    if torch.cuda.is_available() and (dev := cuda_device(torch)):
        return dev  # NVIDIA, or AMD (ROCm): its discrete card
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def model_cache_dir(model_id: str) -> Optional[str]:
    """Where a Hub model is kept: ``<models>/alignment`` (store.models_dir), in the Hub's own layout,
    passed to the loader explicitly.  The first time, a copy already in the default Hugging Face
    cache is copied over (copied, not moved: other programs may use it).  A local folder given as
    the model is used as it is (None)."""
    if Path(model_id).expanduser().exists():
        return None
    from ...project.store import models_dir

    d = models_dir("alignment")
    name = "models--" + model_id.replace("/", "--")
    if not (d / name).exists():
        hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
        src = Path(os.environ.get("HF_HUB_CACHE", hf_home / "hub")) / name
        if src.is_dir():
            tmp = d / (name + ".part")
            try:
                shutil.rmtree(tmp, ignore_errors=True)
                # snapshots link to blobs (relatively); Windows copies the files (symlinks need privileges there)
                shutil.copytree(src, tmp, symlinks=os.name != "nt")
                os.replace(tmp, d / name)
            except OSError:
                shutil.rmtree(tmp, ignore_errors=True)  # downloaded again instead
    return str(d)


def load_model(model_id: str, revision: Optional[str], device: str) -> dict[str, Any]:
    """Load (once per process) model + vocab; reused across calls."""
    key = (model_id, revision, device)
    with _LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        torch, transformers = _import_ml()
        where = {"cache_dir": cache} if (cache := model_cache_dir(model_id)) else {}
        model = transformers.Wav2Vec2ForCTC.from_pretrained(model_id, revision=revision, **where)
        model.eval().to(device)
        tok = transformers.AutoTokenizer.from_pretrained(model_id, revision=revision, **where)
        try:
            fe = transformers.AutoFeatureExtractor.from_pretrained(model_id, revision=revision, **where)
            sr = int(fe.sampling_rate)
            do_norm = bool(getattr(fe, "do_normalize", True))
        except Exception:
            sr, do_norm = 16000, True
        vocab = dict(tok.get_vocab())
        entry = {
            "model": model,
            "vocab": vocab,
            "blank_id": int(tok.pad_token_id if tok.pad_token_id is not None else model.config.pad_token_id),
            "word_delimiter": getattr(tok, "word_delimiter_token", "|"),
            "special": set(tok.all_special_tokens) | {"[UNK]", "[PAD]"},
            "sample_rate": sr,
            "do_normalize": do_norm,
            "kernels": list(model.config.conv_kernel),
            "strides": list(model.config.conv_stride),
            "vocab_size": int(model.config.vocab_size),
        }
        _MODEL_CACHE[key] = entry
        return entry


class Wav2Vec2CTCBackend:
    name = "wav2vec2-ctc"
    default_model_id: Optional[str] = None
    default_revision: Optional[str] = None
    default_license: Optional[str] = None
    languages = ("ja",)
    profile = "ja-hepburn"

    def __init__(self, model_id: Optional[str] = None, revision: Optional[str] = None,
                 device: str = "auto", chunk_s: float = 20.0, context_s: float = 3.0,
                 delimiter_as_blank: bool = True, name: Optional[str] = None) -> None:
        self.model_id = model_id or self.default_model_id
        if not self.model_id:
            raise ValueError("后端 wav2vec2-ctc 需要指定 model_id（HF 仓库或本地路径）")
        if revision is None and self.model_id == DEFAULT_MODEL_ID:
            revision = DEFAULT_REVISION
        self.revision = revision
        self.requested_device = device
        self.chunk_s = chunk_s
        self.context_s = context_s
        self.delimiter_as_blank = delimiter_as_blank
        if name:
            self.name = name
        self._entry: Optional[dict[str, Any]] = None
        self.device: Optional[str] = None
        self.notes: list[str] = []

    # -- loading -----------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        if self._entry is None:
            self.device = resolve_device(self.requested_device)
            self._entry = load_model(self.model_id, self.revision, self.device)
        return self._entry

    @property
    def sample_rate(self) -> int:
        return self._load()["sample_rate"]

    @property
    def hop_samples(self) -> int:
        return int(np.prod(self._load()["strides"]))

    def info(self) -> BackendInfo:
        e = self._entry
        # the device that ran (or will run) the model; resolved without loading it
        device = self.device or resolve_device(self.requested_device)
        extra: dict[str, Any] = {"device": device, "delimiter_as_blank": self.delimiter_as_blank,
                                 "chunk_s": self.chunk_s, "context_s": self.context_s,
                                 "normalization": "global zero-mean unit-variance"}
        if self.notes:
            extra["notes"] = list(self.notes)
        license_ = self.default_license if self.model_id == self.default_model_id else None
        return BackendInfo(
            name=self.name, model_id=self.model_id, model_revision=self.revision,
            license=license_ or "unknown (check the model card)", profile=self.profile,
            sample_rate=e["sample_rate"] if e else 16000,
            frame_hop_samples=int(np.prod(e["strides"])) if e else None,
            extra=extra,
        )

    def supports_language(self, lang: str) -> bool:
        return lang in self.languages

    # -- tokens ------------------------------------------------------------
    def tokenize(self, unit_ids: Sequence[str], unit_texts: Sequence[str]) -> list[TokenizedUnit]:
        e = self._load()
        return tokenize_chars(unit_ids, unit_texts, e["vocab"], e["special"])

    # -- acoustics ---------------------------------------------------------
    def emissions(self, audio: np.ndarray, origin_samples: int = 0, cancel=None, progress=None) -> Emission:
        e = self._load()
        torch, _ = _import_ml()
        x = np.asarray(audio, dtype=np.float32)
        if x.ndim != 1:
            raise ValueError("emissions() 需要单声道音频")
        if e["do_normalize"] and x.size:
            x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)
        kernels, strides = e["kernels"], e["strides"]
        hop = int(np.prod(strides))
        rf = receptive_field(kernels, strides)
        sr = e["sample_rate"]

        model = [e["model"]]  # the model this run uses (the CPU copy after an MPS / ROCm failure)

        def forward(seg: np.ndarray) -> np.ndarray:
            with torch.inference_mode():
                inp = torch.from_numpy(np.ascontiguousarray(seg)).unsqueeze(0)
                try:
                    logits = model[0](inp.to(self.device)).logits
                except (RuntimeError, NotImplementedError) as exc:
                    # ROCm too: its torch has kernels for the GPU families chosen at install only
                    rocm = self.device.startswith("cuda") and is_rocm(torch)
                    if self.device != "mps" and not rocm:
                        raise
                    self.notes.append(f"{'AMD 显卡（ROCm）' if rocm else 'MPS '}运行失败（{type(exc).__name__}），已改用 CPU")
                    self.device = "cpu"
                    # the CPU model has its own cache entry; the shared GPU entry is left as it was
                    self._entry = load_model(self.model_id, self.revision, "cpu")
                    model[0] = self._entry["model"]
                    logits = model[0](inp).logits
                return torch.log_softmax(logits[0].float(), dim=-1).cpu().numpy()

        logp = chunked_forward(
            x, forward, hop=hop, rf=rf,
            n_frames=lambda n: conv_output_length(n, kernels, strides),
            chunk_samples=int(self.chunk_s * sr), context_samples=int(self.context_s * sr),
            cancel=cancel, progress=progress)
        delim_id = e["vocab"].get(e["word_delimiter"]) if e["word_delimiter"] else None
        if self.delimiter_as_blank and delim_id is not None and delim_id != e["blank_id"]:
            logp[:, e["blank_id"]] = np.logaddexp(logp[:, e["blank_id"]], logp[:, delim_id])
            logp[:, delim_id] = -1e4  # finite: avoids NaN in downstream arithmetic
        return Emission(logp=logp.astype(np.float32), frame_map=FrameMap(sr, hop, 0, int(origin_samples)),
                        blank_id=e["blank_id"])


def tokenize_chars(unit_ids: Sequence[str], unit_texts: Sequence[str], vocab: dict[str, int],
                   special: set[str]) -> list[TokenizedUnit]:
    out = []
    for uid, text in zip(unit_ids, unit_texts):
        ids: list[int] = []
        unknown: list[str] = []
        for ch in (text or "").lower():
            if ch.isspace():
                continue
            if ch in vocab and ch not in special:
                ids.append(vocab[ch])
            else:
                unknown.append(ch)
        out.append(TokenizedUnit(unit_id=uid, token_text=text or "", token_ids=ids, unknown=unknown))
    return out


class MMSJapaneseBackend(Wav2Vec2CTCBackend):
    name = "mms-ja"
    default_model_id = DEFAULT_MODEL_ID
    default_revision = DEFAULT_REVISION
    default_license = DEFAULT_LICENSE
    languages = ("ja",)
    profile = "ja-hepburn"
