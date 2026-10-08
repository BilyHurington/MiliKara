"""Which GPU torch runs on.

NVIDIA (CUDA) and AMD (ROCm) builds of torch both answer through ``torch.cuda``, so ``"cuda"`` is the
device string for either.  On ROCm the first device is not always the right one: Ryzen processors
have an integrated GPU too, which is much slower and is often not covered by the installed torch
(its kernels are built per GPU family, chosen at install).  There the discrete card with the most
memory is used, if this torch runs on it: a card it has no kernels for can end the whole process
(ROCm's BLAS exits instead of raising an error), so each card is first tried in a process of its own.
CUDA keeps torch's own default (the first device, or ``CUDA_VISIBLE_DEVICES``).
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Optional

# a few kernels and a matrix product on cuda:N (argv[1]); exit code 0 when the result is right
_PROBE = ("import sys, torch; x = torch.ones(64, 64, device=torch.device('cuda', int(sys.argv[1])));"
          "sys.exit(float((x @ x).sum()) != 64 ** 3)")
_runs: dict[int, bool] = {}


def is_rocm(torch: Any) -> bool:
    """A ROCm (AMD) build of torch."""
    return bool(getattr(torch.version, "hip", None))


def _card_runs(index: int) -> bool:
    """This torch runs on ROCm device ``index`` (tried once per process, ~2 s: mostly importing torch)."""
    if index not in _runs:
        try:
            r = subprocess.run([sys.executable, "-c", _PROBE, str(index)], capture_output=True, timeout=120)
            _runs[index] = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _runs[index] = False
    return _runs[index]


def cuda_device(torch: Any) -> Optional[str]:
    """``"cuda"`` or ``"cuda:N"``: the GPU to use; None when this torch runs on none of the AMD GPUs
    here (call when ``torch.cuda.is_available()``)."""
    if not is_rocm(torch):
        return "cuda"

    def rank(i: int) -> tuple[bool, int]:
        # discrete before integrated (which can report more, shared, memory), then the most memory
        p = torch.cuda.get_device_properties(i)
        return bool(getattr(p, "is_integrated", False)), -p.total_memory

    for i in sorted(range(torch.cuda.device_count()), key=rank):
        if _card_runs(i):
            return "cuda" if i == 0 else f"cuda:{i}"
    return None


def describe(torch: Any) -> str:
    """``"CUDA 12.8 · NVIDIA GeForce RTX 4070"`` / ``"ROCm (HIP 7.16) · AMD Radeon RX 7900 XT"`` for
    the device :func:`cuda_device` picks (call when ``torch.cuda.is_available()``)."""
    dev = cuda_device(torch)
    if dev is None:
        names = "、".join(torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count()))
        return f"ROCm (HIP {torch.version.hip}) · 安装的 torch 不支持这里的显卡（{names}），使用 CPU"
    name = torch.cuda.get_device_name(torch.device(dev))
    if is_rocm(torch):
        return f"ROCm (HIP {torch.version.hip}) · {name}"
    return f"CUDA {torch.version.cuda} · {name}"
