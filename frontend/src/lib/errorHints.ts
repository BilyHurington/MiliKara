// What to do about a failure: a hint for the errors people meet most, from the error text.

const HINTS: [RegExp, string][] = [
  [/out of memory|CUDA error: out of memory|MPS backend out of memory|DefaultCPUAllocator|MemoryError|内存不足/i,
    '显存或内存不够：关掉其他占内存的程序后重试；还不行的话，在设置里把人声分离改为“只用 CPU”。'],
  [/invalid device function|invalid kernel file|no kernel image is available|hipErrorNoBinaryForGpu|TensileLibrary/i,
    '安装的 torch 不支持这块显卡：AMD 显卡请按 README 的安装说明重新安装与型号对应的 ROCm 版 torch；NVIDIA 显卡可能太旧，可以在设置里把人声分离改为“只用 CPU”。'],
  [/No space left|ENOSPC|disk full|磁盘已满|空间不足/i,
    '磁盘空间不足：清理出几 GB 空间后重试（项目和缓存在用户目录的 .kara_align 文件夹里）。'],
  [/ConnectError|ConnectTimeout|ReadTimeout|TimeoutException|getaddrinfo|Name or service not known|SSLError|无法连接|网络/i,
    '网络连接失败：检查网络或代理后重试；歌词也可以直接粘贴 LRC 文本，不用链接。'],
  [/Permission denied|PermissionError|拒绝访问|being used by another process|被占用|正在使用/i,
    '文件被占用或没有权限：关掉正在使用这个文件的程序（播放器、杀毒软件、同步盘）后重试。'],
  [/FileNotFoundError|No such file|找不到文件|不存在/i,
    '找不到文件：原来的音视频可能被移动或删除了，请重新添加。'],
  [/ffmpeg|libass|Invalid data found|moov atom/i,
    '处理音视频时出错：文件可能损坏或格式不支持，换一个文件试试；离线版可以重新解压，源码安装请确认 ffmpeg 带 libass。'],
];

export function errorHint(error: string | null | undefined): string | null {
  if (!error) return null;
  return HINTS.find(([re]) => re.test(error))?.[1] ?? null;
}
