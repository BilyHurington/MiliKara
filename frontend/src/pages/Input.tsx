// Step 2: audio and lyrics input.

import { ArrowRight } from 'lucide-react';
import { setStep, useApp, useProject } from '@/store/app';
import { useDraft } from '@/store/drafts';
import { Button, Callout, PageHeader } from '@/components/ui';
import { AudioCard } from './input/AudioCard';
import { LinesTable } from './input/LinesTable';
import { LyricsInputCard } from './input/LyricsInputCard';
import { PairingCard } from './input/PairingCard';
import { INTRO } from '@/lib/guide';

export function InputPage() {
  const project = useProject()!;
  // kept (like the lyrics drafts below) while the page is left and come back to
  const [extraTracks, setExtraTracks] = useDraft<Record<string, string>>('input.extraTracks', {});
  const ready = project.lyrics.lines.some((l) => l.kind === 'lyric') && project.audio.some((a) => a.role === 'original');
  const markers = useApp((s) => s.pv?.view.singer_markers ?? 0);

  return (
    <>
      <PageHeader
        eyebrow="第 2 步"
        title="音频与歌词"
        description={INTRO.input}
        actions={(
          <Button variant={ready ? 'primary' : 'secondary'} onClick={() => setStep('enhance')} icon={<ArrowRight className="size-4" />}>
            下一步：注音与分离
          </Button>
        )}
      />
      <div className="space-y-6">
        <AudioCard />
        <LyricsInputCard onExtraTracks={setExtraTracks} />
        <PairingCard extraTracks={extraTracks} />
        {markers > 0 && (
          <Callout tone="info" title={`有 ${markers} 行歌词开头写着演唱者（如「A：」）`}
            actions={<Button size="sm" variant="secondary" onClick={() => setStep('singers')}>去“演唱者”页识别</Button>}>
            这些名字会被当作歌词来对齐和显示。建议在对齐前到“演唱者”页识别：按名字给这些行分色，并把名字从歌词里去掉。
          </Callout>
        )}
        <LinesTable />
      </div>
    </>
  );
}
