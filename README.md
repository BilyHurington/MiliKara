<p align="center">
  <img src="docs/images/icon.png" alt="MiliKara icon" width="128" height="128">
</p>

<h1 align="center">MiliKara</h1>

<p align="center">
  用一首歌和它的歌词，做出逐字扫光的日语卡拉OK视频。
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-1BA8D8.svg"></a>
  <a href="https://github.com/BilyHurington/MiliKara/releases"><img alt="Release" src="https://img.shields.io/github/v/release/BilyHurington/MiliKara?include_prereleases&color=1BA8D8&label=Release"></a>
  <img alt="Platform: Windows" src="https://img.shields.io/badge/Platform-Windows-1BA8D8.svg">
  <img alt="Platform: macOS" src="https://img.shields.io/badge/Platform-macOS-1BA8D8.svg">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-1BA8D8.svg">
  <img alt="Runs locally" src="https://img.shields.io/badge/Runs-locally-1BA8D8.svg">
</p>

> 本软件受 [StrangeUtaGame](https://github.com/karaoke-studio/StrangeUtaGame) 启发。MiliKara 是自动对齐工具，不是卡拉OK手动打轴软件；如果需要手动打轴，请使用 StrangeUtaGame 原软件。

> **名称说明**：本项目原名 KiraKara，因与 [KiraKara player](https://rl.fmpeach.top) 及其相关项目重名，从 v1.1.0 起改名为 MiliKara（两者之间没有关系）。旧版本的项目和设置在新版本里可以直接使用。

放入视频（或音频 + 背景图），粘贴网易云 / QQ 音乐的歌曲链接或 LRC 歌词，MiliKara 会自动给汉字注音、分离人声、把每一个假名（拍）对齐到演唱的时间上，然后把带注音、翻译和歌曲信息的卡拉OK字幕直接烧录进视频。全部在本机运行。

<p align="center">
  <img src="docs/images/simple-form.png" alt="极简模式：放入视频，粘贴歌曲链接，选好字幕配色，点“开始制作”" width="640">
  <br>
  <sub>极简模式：放入视频，粘贴歌曲链接，选好字幕配色，点“开始制作”</sub>
</p>

<p align="center">
  <img src="docs/images/detail-review.png" alt="详细模式（人工检查）：每一行、每一个假名的时间都可以试听、拖动修改和局部重跑" width="860">
  <br>
  <sub>详细模式（人工检查）：每一行、每一个假名的时间都可以试听、拖动修改和局部重跑</sub>
</p>

<p align="center">
  <img src="docs/images/output-frame.jpg" alt="成品视频的一帧：逐字扫光、汉字注音，顶部是中文翻译" width="860">
  <br>
  <sub>成品视频的一帧：逐字扫光、汉字注音，顶部是中文翻译</sub>
</p>

## 特点

- **按拍对齐**：每个假名（拍）都有自己的开始和结束时间，扫光跟着演唱一个字一个字走；长音「ー」、促音「っ」、逐个念出的字母（R O M A…）都按实际唱法处理。
- **利用歌词里的时间**：LRC 的每行时间作为软锚点，长前奏、间奏、重复的副歌也不会对错行；视频和歌词差了多少，添加任务时标一下第一句（或交给自动检测）即可。
- **注音**：规则读音 + 可选的 AI 注音（复制提示词到任意 AI 聊天网页，或一键交给 Claude Code / Codex / OpenAI 兼容 API），回复都经过校验再采用。
- **人声分离**：MelBand / BS-RoFormer 分出人声后对齐更准，也能生成降低人声的伴唱视频。
- **好看的字幕**：配色模版（主色 + 辅色自动搭配）、荧光边缘、逐字特效、平假名 / 片假名 / 罗马音注音、翻译、开头和结尾的歌曲信息卡；预览和烧录使用同一个渲染器（libass），所见即所得。
- **多人演唱分色**：给每位歌手一个颜色，按整行或逐词指定；几个人一起唱的部分每个字分成几种颜色（上下 / 左右，分色或渐变）。歌词里写着“A：”之类的演唱者时可以自动识别。
- **两种用法**：**极简模式**一键出片，可以排队连续做多首；**详细模式**逐步检查和微调每一个字的时间。
- **可以脚本调用**：所有功能都有本地 HTTP API，例如把别处下载的音频和背景图直接提交成任务。

## 安装

**Windows 离线版**：在 Releases 里下载 `MiliKara-…-windows-x64-cpu.7z`（CPU 版）、`…-cuda.7z`（NVIDIA 显卡版，需要 570 或更新的驱动）或 `…-rocm.7z`（AMD 显卡版，v1.2.0 起：Radeon RX 6800 / 6900、RX 7000 / 9000 系列，以及 Ryzen 的 780M / 760M、Ryzen AI 300 系列和 Ryzen AI Max 核显，请把显卡驱动更新到最新）。超过 2 GB 时分成 `.7z.001`、`.7z.002`… 几个分卷，需要全部下载。用 7-Zip 解压，双击 `MiliKara.bat`。自带 Python、依赖、ffmpeg 和两个默认模型，不需要联网。

**macOS 离线版**（Apple 芯片，macOS 14.8.5 或更新）：下载 `MiliKara-…-macos-arm64.zip`，解压后第一次在访达里右键点 `MiliKara.command` →“打开”，以后双击即可。

**更新离线版**：关闭 MiliKara 后双击文件夹里的 `更新.bat`（macOS：`更新.command`），只下载有变化的部分（程序约 1 MB；从 v1.0 更新时还会补上约 32 MB 的字体），模型、依赖和项目都不用重新下载，出问题可以退回上一版。v1.1.0 之前的旧版（KiraKara）没有这个文件：在 Releases 里下载 `MiliKara-updater-windows.bat`（macOS：`MiliKara-updater-macos.zip`，解压）放进原来的文件夹，双击即可。访问 GitHub 慢时，也可以把程序更新包 `MiliKara-版本-app.zip` 放进文件夹，更新程序会直接用它。

离线版都由 GitHub Actions 构建（`.github/workflows/windows-package.yml`、`macos-package.yml`；打包脚本在 `packaging/`）。

**从源码安装**：需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)，以及带 libass 的 `ffmpeg`（macOS：`brew install ffmpeg-full`）。目前主要在 macOS（Apple 芯片）上使用和测试；Linux / Windows 上可以用 CPU、NVIDIA 显卡或 AMD 显卡（见下）运行。

```bash
git clone <仓库地址> MiliKara && cd MiliKara
uv venv --python 3.12 .venv
uv pip install -e ".[ml,separation]"    # 对齐模型（torch + transformers）和人声分离
```

**AMD 显卡**（ROCm）：在上面的 `uv pip install -e …` 之前，先从 AMD 的软件源装上与显卡对应的 torch：

```bash
uv pip install "torch[device-gfx1100]" --index-url https://stable.repo.amd.com/rocm/whl-next/
```

把 `gfx1100` 换成你的显卡：RX 7900 系列 `gfx1100`，RX 7800 / 7700 `gfx1101`，RX 7600 `gfx1102`，RX 9070 系列 `gfx1201`，RX 9060 `gfx1200`；Radeon 890M / 880M `gfx1150`，Ryzen AI Max `gfx1151`，780M / 760M `gfx1103`（完整列表见 [AMD 的说明](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/frameworks/pytorch/install.html)）。显卡驱动请更新到最新（AMD 列出的配套驱动是 Adrenalin 26.10.41.05）。装好后 `python -c "import torch; print(torch.cuda.is_available())"` 应输出 `True`，诊断信息里显示 `ROCm … · 显卡名`；“自动”会使用这块显卡（同时有核显时用独立显卡）。RX 7900 XT 上一首 3 分钟的歌人声分离约 40 秒（同一台电脑上 CPU 版约 6 分钟）。AMD 的 torch 用 CPU 计算时比 CPU 版慢十几倍：显卡用不了时，请删掉 `.venv` 按上面的步骤重新安装（跳过这一步）。

模型在第一次使用时下载到项目目录的 `models/` 里（对齐模型约 1.2 GB，默认的分离模型约 1 GB），之后可以离线使用。环境变量 `KARA_ALIGN_MODELS` 可以把它指到别处。

可选：`python packaging/fetch_fonts.py` 下载内置的中日文字体 Noto Sans CJK（约 40 MB，SIL OFL）到 `fonts/`。Windows / Linux 上它是默认字体，歌词、翻译或歌曲信息里有当前字体没有的字时也会用它；离线包里已经自带。

## 使用

```bash
.venv/bin/milikara serve        # 然后打开 http://127.0.0.1:8765
```

默认打开**极简模式**：

1. **放入视频**（或切换到“音频 + 背景”，放入音频和一张背景图 / 一段循环播放的背景视频）。
2. **粘贴歌词**：网易云 / QQ 音乐的歌曲链接（会自动取歌词和翻译），或直接粘贴 LRC / 纯文本歌词。
3. 选好字幕配色，点 **开始制作**。
4. 几秒后会弹出波形，**标出第一句开始唱的位置**（设置里也可以改成自动检测）；AI 注音选的是“手动（网页聊天）”时，接着把提示词发给 AI 并粘贴回复。
5. 其余步骤自动完成，任务完成后点 **下载视频**。点开任务可以进入详细模式继续调整。

<p align="center">
  <img src="docs/images/simple-tasks.png" alt="任务列表：上面的任务在等你确认第一句的位置，下面的已经完成，可以下载视频" width="760">
  <br>
  <sub>任务列表：上面的任务在等你确认第一句的位置，下面的已经完成，可以下载视频</sub>
</p>

## 文档

- **[使用教程](docs/tutorial.md)**：极简模式、详细模式、设置和常见问题，附截图。
- **[HTTP API](docs/api.md)**：所有接口、参数和返回值，以及用脚本提交任务的例子。
- [技术说明](docs/technical.md)：时间与数据约定、对齐算法、命令行、开发。

## 许可与说明

- MiliKara 本身的代码以 [MIT 协议](LICENSE) 发布。它使用的第三方依赖、模型和工具（如 PyTorch、transformers、python-audio-separator、ffmpeg 等）各自遵循它们自己的许可。
- 默认的对齐模型 [`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`](https://huggingface.co/NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn) 的许可是 **CC-BY-NC-SA-4.0（非商用）**；人声分离使用 [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) 和它的模型，许可以上游为准。
- 音频、视频和项目文件只保存在本机。只有这些情况会联网：第一次下载模型、从音乐链接获取歌词、AI 注音（歌词会发给你选择的 AI 服务），以及检查新版本（只读取 GitHub 上最新版本的版本号，可以在设置里关闭）。
- 请只处理你有权使用的音视频和歌词。
