"""The GPU torch runs on: AMD (ROCm) builds skip the processor's integrated GPU and cards the installed
torch cannot run on; CUDA is left as it is."""

from types import SimpleNamespace

import pytest

from kara_align import gpu


def _torch(devices, hip="7.16.26385", cuda=None):
    """A stand-in for torch with ``devices``: (name, integrated, memory in GB) each."""
    props = [SimpleNamespace(name=n, is_integrated=i, total_memory=gb << 30) for n, i, gb in devices]

    def index(d):
        return d if isinstance(d, int) else (d.index or 0)

    return SimpleNamespace(
        version=SimpleNamespace(hip=hip, cuda=cuda),
        device=lambda s: SimpleNamespace(index=int(s.split(":")[1]) if ":" in s else None),
        cuda=SimpleNamespace(device_count=lambda: len(props),
                             get_device_properties=lambda d: props[index(d)],
                             get_device_name=lambda d: props[index(d)].name))


@pytest.fixture
def runs(monkeypatch):
    """The cards this torch runs on (indices; all by default); records the cards tried."""
    state = SimpleNamespace(ok=None, tried=[])

    def card_runs(i):
        state.tried.append(i)
        return state.ok is None or i in state.ok

    monkeypatch.setattr(gpu, "_card_runs", card_runs)
    return state


def test_rocm_uses_the_discrete_card_not_the_integrated_gpu(runs):
    t = _torch([("AMD Radeon(TM) Graphics", True, 16), ("AMD Radeon RX 7900 XT", False, 20)])
    assert gpu.is_rocm(t)
    assert gpu.cuda_device(t) == "cuda:1"
    assert gpu.describe(t) == "ROCm (HIP 7.16.26385) · AMD Radeon RX 7900 XT"
    assert runs.tried[0] == 1  # the integrated GPU is not even tried
    # the integrated GPU can report more (shared) memory: a discrete card still wins
    t = _torch([("AMD Radeon(TM) Graphics", True, 24), ("AMD Radeon RX 7600", False, 8)])
    assert gpu.cuda_device(t) == "cuda:1"
    # two cards: the one with more memory
    t = _torch([("AMD Radeon RX 7600", False, 8), ("AMD Radeon RX 7900 XTX", False, 24)])
    assert gpu.cuda_device(t) == "cuda:1"


def test_rocm_skips_cards_the_installed_torch_cannot_run_on(runs):
    t = _torch([("AMD Radeon RX 7900 XT", False, 20), ("AMD Radeon(TM) Graphics", True, 12)])
    runs.ok = {1}  # torch installed for the integrated GPU only
    assert gpu.cuda_device(t) == "cuda:1"
    runs.ok = set()  # for neither (another card's device package): the CPU
    assert gpu.cuda_device(t) is None
    assert gpu.describe(t) == ("ROCm (HIP 7.16.26385) · 安装的 torch 不支持这里的显卡"
                               "（AMD Radeon RX 7900 XT、AMD Radeon(TM) Graphics），使用 CPU")


def test_one_gpu_or_cuda_keeps_the_default_device(runs):
    assert gpu.cuda_device(_torch([("AMD Radeon RX 9070 XT", False, 16)])) == "cuda"
    assert gpu.cuda_device(_torch([("AMD Radeon 780M", True, 8)])) == "cuda"  # only an integrated GPU: that one
    runs.tried.clear()
    t = _torch([("NVIDIA GeForce RTX 3060", False, 12), ("NVIDIA GeForce RTX 4090", False, 24)], hip=None, cuda="12.8")
    assert not gpu.is_rocm(t)
    assert gpu.cuda_device(t) == "cuda"  # CUDA: torch's default (CUDA_VISIBLE_DEVICES chooses)
    assert gpu.describe(t) == "CUDA 12.8 · NVIDIA GeForce RTX 3060"
    assert runs.tried == []  # nothing tried


def test_a_card_is_tried_once_in_a_process_of_its_own(monkeypatch):
    monkeypatch.setattr(gpu, "_runs", {})
    monkeypatch.setattr(gpu, "_PROBE", "import os, sys; os._exit(0 if sys.argv[1] == '0' else 1)")
    assert gpu._card_runs(0) and not gpu._card_runs(1)
    monkeypatch.setattr(gpu, "_PROBE", "raise SystemExit(1)")
    assert gpu._card_runs(0) and not gpu._card_runs(1)  # remembered
