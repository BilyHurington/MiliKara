import { describe, expect, it } from 'vitest';
import { errorHint } from './errorHints';

describe('hints for common failures', () => {
  it('names what to do', () => {
    expect(errorHint('RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB')).toMatch(/显存|内存/);
    expect(errorHint('OSError: [Errno 28] No space left on device')).toMatch(/磁盘/);
    expect(errorHint('PermissionError: [WinError 32] 另一个程序正在使用此文件')).toBeTruthy();
    expect(errorHint('无法从链接获取歌词：ConnectError: timed out')).toMatch(/网络/);
    expect(errorHint('人声分离失败（退出码 1）：RuntimeError: HIP error: invalid device function')).toMatch(/ROCm/);
    expect(errorHint('torch.AcceleratorError: CUDA error: invalid kernel file')).toMatch(/ROCm/);
  });
  it('says nothing about errors it does not know', () => {
    expect(errorHint('歌词为空')).toBeNull();
    expect(errorHint(null)).toBeNull();
  });
});
