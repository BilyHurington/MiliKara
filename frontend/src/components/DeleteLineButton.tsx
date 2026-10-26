// Removes one lyric line (e.g. an LRC's closing "(終わり)" that nobody sings), after a second click.

import { Trash2 } from 'lucide-react';
import { api } from '@/lib/api';
import type { Line, ProjectView } from '@/lib/types';
import { ppath, run, setPV, toast } from '@/store/app';
import { ConfirmButton } from '@/components/ui';

export function DeleteLineButton({ line, index }: { line: Line; index: number }) {
  const remove = () => run(async () => {
    setPV(await api.del<ProjectView>(ppath(`/lines/${line.id}`)));
    toast('ok', `已删除第 ${index} 行`, line.text);
  }, '删除失败');
  return (
    <ConfirmButton size="xs" variant="ghost" icon={<Trash2 className="size-3.5" />} aria-label={`删除第 ${index} 行`}
      title="删除这一行（如标记歌曲结束的“(終わり)”）" question="删除这一行？" confirmLabel="删除" onConfirm={() => void remove()} />
  );
}
