"""The 使用说明.txt of a portable package, for one platform and variant.

    python packaging/notes.py windows cuda v1.1.0 out.txt     (cpu / cuda / rocm)
    python packaging/notes.py macos - v1.1.0 out.txt

Used by the packaging workflows and for the updater's app package (which carries the notes of every
variant, so an updated folder gets the new text too).  Written with CRLF and a BOM on Windows
(Notepad), plain UTF-8 on macOS.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VARIANTS = {
    ("windows", "cpu"): ("CPU 版", "- 这个版本只用 CPU 计算：人声分离比显卡版慢（一首歌约需几分钟到十几分钟）。有 NVIDIA 显卡时建议用显卡版。"),
    ("windows", "cuda"): ("NVIDIA 显卡版", "- 这个版本用 NVIDIA 显卡（CUDA 12.8）加速人声分离和对齐：需要 GeForce GTX 16 / RTX 20 系列或更新的显卡，\n"
                                         "  以及 570 或更新版本的显卡驱动。没有合适的显卡时自动改用 CPU（较慢）。"),
    ("windows", "rocm"): ("AMD 显卡版", "- 这个版本用 AMD 显卡（ROCm）加速人声分离和对齐：支持 Radeon RX 7000 / 9000 系列、RX 6800 / 6900，\n"
                                         "  以及 Ryzen 的 780M / 760M、Ryzen AI 300 系列（880M / 890M 等）和 Ryzen AI Max 核显；请把显卡驱动更新到最新。\n"
                                         "  同时有核显和独立显卡时使用独立显卡。显卡用不了时改用 CPU，但比 CPU 版慢很多，这时请改用 CPU 版。"),
    ("macos", None): ("", ""),
}


def render(platform: str, variant: str | None, version: str) -> str:
    label, note = VARIANTS[(platform, variant if platform == "windows" else None)]
    text = (HERE / platform / "使用说明.txt").read_text(encoding="utf-8")
    return text.replace("@VERSION@", version).replace("@VARIANT_LABEL@", label).replace("@VARIANT_NOTE@", note)


def encode(platform: str, text: str) -> bytes:
    if platform == "windows":
        return "﻿".encode() + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    return text.encode("utf-8")


def main() -> None:
    platform, variant, version, out = sys.argv[1:5]
    Path(out).write_bytes(encode(platform, render(platform, None if variant == "-" else variant, version)))


if __name__ == "__main__":
    main()
