"""Acoustic backend registry (plain dict, no plugin framework).

Heavy dependencies (torch / transformers) are imported lazily, so this
package imports without them; ``get_backend`` raises a clear error instead.
"""

from __future__ import annotations

import importlib.util
from typing import Any, Callable

from ...models import AlignConfig
from .fake import ScriptedBackend
from .mms_fa import DEFAULT_LICENSE as FA_LICENSE, DEFAULT_MODEL_ID as FA_MODEL_ID
from .mms_fa import DEFAULT_REVISION as FA_REVISION, FA_HINT, MMSFABackend
from .wav2vec2_ctc import (DEFAULT_LICENSE, DEFAULT_MODEL_ID, DEFAULT_REVISION, ML_HINT,
                           MMSJapaneseBackend, MMSSinophoneBackend, Wav2Vec2CTCBackend)


def _ml_available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))


def _fa_available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("onnxruntime", "huggingface_hub"))


def _make_hf(cls):
    def factory(config: AlignConfig):
        if not _ml_available():
            raise RuntimeError(ML_HINT)
        return cls(model_id=config.model_id, revision=config.model_revision, device=config.device,
                   chunk_s=config.chunk_s, context_s=config.context_s)
    return factory


def _make_fa(cls):
    def factory(config: AlignConfig):
        if not _fa_available():
            raise RuntimeError(FA_HINT)
        return cls(model_id=config.model_id, revision=config.model_revision, device=config.device,
                   chunk_s=config.chunk_s, context_s=config.context_s)
    return factory


BACKENDS: dict[str, dict[str, Any]] = {
    "mms-ja": {
        "factory": _make_hf(MMSJapaneseBackend),
        "description": "MMS-300m forced aligner fine-tuned on Japanese karaoke romaji (CTC)",
        "languages": ["ja"],
        "default_model": DEFAULT_MODEL_ID,
        "default_revision": DEFAULT_REVISION,
        "license": DEFAULT_LICENSE,
        "requires": ["torch", "transformers"],
    },
    "mms-mixed": {
        "factory": _make_hf(MMSSinophoneBackend),
        "description": "Same Japanese karaoke model, reading Chinese/Cantonese syllables as Japanese morae "
                       "(mixed cn/粤/ja/en lyrics; approximate, cannot hear tones)",
        "languages": ["ja", "zh", "yue", "en"],
        "default_model": DEFAULT_MODEL_ID,
        "default_revision": DEFAULT_REVISION,
        "license": DEFAULT_LICENSE,
        "requires": ["torch", "transformers"],
    },
    "mms-fa": {
        "factory": _make_fa(MMSFABackend),
        "description": "Meta's MMS forced aligner (MMS_FA, ONNX INT8): trained on 1,130 languages' "
                       "romanised text, so pinyin / Jyutping / romaji letters align as they are",
        "languages": ["ja", "zh", "yue", "en"],
        "default_model": FA_MODEL_ID,
        "default_revision": FA_REVISION,
        "license": FA_LICENSE,
        "requires": ["onnxruntime", "huggingface_hub"],
    },
    "wav2vec2-ctc": {
        "factory": _make_hf(Wav2Vec2CTCBackend),
        "description": "Any compatible Hugging Face Wav2Vec2ForCTC character model (set model_id)",
        "languages": ["ja"],
        "default_model": None,
        "default_revision": None,
        "license": "depends on the chosen weights",
        "requires": ["torch", "transformers"],
    },
    "scripted": {
        "factory": lambda config: ScriptedBackend(),
        "description": "Deterministic scripted emissions for tests/demos (not a real aligner)",
        "languages": ["ja", "en"],
        "default_model": "scripted",
        "default_revision": "1",
        "license": "n/a",
        "requires": [],
    },
}


def register_backend(name: str, factory: Callable[[AlignConfig], Any], **meta: Any) -> None:
    BACKENDS[name] = {"factory": factory, "requires": [], **meta}


def get_backend(config: AlignConfig):
    entry = BACKENDS.get(config.backend)
    if entry is None:
        raise ValueError(f"未知的对齐后端 {config.backend!r}；可用：{', '.join(sorted(BACKENDS))}")
    return entry["factory"](config)


def list_backends() -> list[dict[str, Any]]:
    out = []
    for name, e in BACKENDS.items():
        req = e.get("requires", [])
        available = all(importlib.util.find_spec(m) is not None for m in req)
        out.append({
            "name": name,
            "description": e.get("description", ""),
            "languages": e.get("languages", []),
            "default_model": e.get("default_model"),
            "default_revision": e.get("default_revision"),
            "license": e.get("license"),
            "available": available,
            "missing": [m for m in req if importlib.util.find_spec(m) is None],
        })
    return out


__all__ = ["BACKENDS", "ScriptedBackend", "Wav2Vec2CTCBackend", "MMSJapaneseBackend",
           "MMSSinophoneBackend", "MMSFABackend", "get_backend", "list_backends", "register_backend"]
