import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderUI } from '@/test/helpers';
import { MultiToggle, NumberInput, SliderField } from './index';

describe('NumberInput', () => {
  it('commits the typed number on blur', async () => {
    const onCommit = vi.fn();
    renderUI(<NumberInput value={1990} onCommit={onCommit} />);
    const input = screen.getByRole('spinbutton');
    await userEvent.clear(input);
    await userEvent.type(input, '1975');
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledWith(1975);
  });

  it('commits even when blur happens in the same tick as the change', () => {
    // regression: the blur handler must read the element, not a stale draft
    const onCommit = vi.fn();
    renderUI(<NumberInput value={1990} onCommit={onCommit} />);
    const input = screen.getByRole('spinbutton') as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '1970' } });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledWith(1970);
  });

  it('commits on Enter and ignores unchanged values', async () => {
    const onCommit = vi.fn();
    renderUI(<NumberInput value={10} onCommit={onCommit} />);
    const input = screen.getByRole('spinbutton');
    fireEvent.focus(input);
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    await userEvent.clear(input);
    await userEvent.type(input, '25{Enter}');
    expect(onCommit).toHaveBeenCalledWith(25);
  });

  it('Escape discards the draft', async () => {
    const onCommit = vi.fn();
    renderUI(<NumberInput value={10} onCommit={onCommit} />);
    const input = screen.getByRole('spinbutton') as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '99{Escape}');
    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe('10');
  });

  it('empty input commits null (clear)', () => {
    const onCommit = vi.fn();
    renderUI(<NumberInput value={10} onCommit={onCommit} />);
    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledWith(null);
  });
});

function ControlledSlider({ onCommit, initial = 20 }: { onCommit: (v: number) => void; initial?: number }) {
  const [v, setV] = useState(initial);
  return <SliderField label="人声保留" value={v} onChange={setV} onCommit={onCommit} />;
}

describe('SliderField', () => {
  it('typing a percentage updates the slider and commits', () => {
    const onCommit = vi.fn();
    renderUI(<ControlledSlider onCommit={onCommit} />);
    const box = screen.getByRole('textbox', { name: /人声保留/ });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: '35' } });
    fireEvent.blur(box);
    expect(onCommit).toHaveBeenCalledWith(35);
    expect(screen.getByRole('slider')).toHaveAttribute('aria-valuenow', '35');
  });

  it('clamps to the range and accepts a trailing %', () => {
    const onCommit = vi.fn();
    renderUI(<ControlledSlider onCommit={onCommit} />);
    const box = screen.getByRole('textbox', { name: /人声保留/ });
    fireEvent.change(box, { target: { value: '150%' } });
    fireEvent.blur(box);
    expect(onCommit).toHaveBeenLastCalledWith(100);
    fireEvent.change(box, { target: { value: '-5' } });
    fireEvent.blur(box);
    expect(onCommit).toHaveBeenLastCalledWith(0);
  });

  it('ignores non-numbers', () => {
    const onCommit = vi.fn();
    renderUI(<ControlledSlider onCommit={onCommit} />);
    const box = screen.getByRole('textbox', { name: /人声保留/ });
    fireEvent.change(box, { target: { value: 'abc' } });
    fireEvent.blur(box);
    expect(onCommit).not.toHaveBeenCalled();
    expect((box as HTMLInputElement).value).toBe('20');
  });

  it('Escape discards the typed value', async () => {
    const onCommit = vi.fn();
    renderUI(<ControlledSlider onCommit={onCommit} />);
    const box = screen.getByRole('textbox', { name: /人声保留/ }) as HTMLInputElement;
    await userEvent.click(box);
    await userEvent.keyboard('{Control>}a{/Control}77{Escape}');
    expect(onCommit).not.toHaveBeenCalled();
    expect(box.value).toBe('20');
  });

  it('arrow keys nudge (shift = ×10)', () => {
    renderUI(<ControlledSlider onCommit={() => {}} />);
    const box = screen.getByRole('textbox', { name: /人声保留/ });
    fireEvent.focus(box);
    fireEvent.keyDown(box, { key: 'ArrowUp' });
    expect(screen.getByRole('slider')).toHaveAttribute('aria-valuenow', '21');
    fireEvent.keyDown(box, { key: 'ArrowDown', shiftKey: true });
    expect(screen.getByRole('slider')).toHaveAttribute('aria-valuenow', '11');
  });

  it('track is long: flexible with a minimum width', () => {
    renderUI(<ControlledSlider onCommit={() => {}} />);
    const track = screen.getByRole('slider').closest('.min-w-40');
    expect(track).toHaveClass('flex-1');
  });
});

// MultiToggle: several choices; the last one stays; a choice that became unavailable can be taken off.
const OPTIONS = [
  { value: 'original', label: '原唱' },
  { value: 'instrumental', label: '伴唱', disabled: true },
  { value: 'none', label: '无声', disabled: true },
] as const;

describe('MultiToggle', () => {
  it('keeps one chosen, and lets a chosen but unavailable option be taken off', async () => {
    const onChange = vi.fn();
    const { rerender } = renderUI(<MultiToggle label="声音" value={['original']} onChange={onChange} options={[...OPTIONS]} />);
    await userEvent.click(screen.getByRole('button', { name: '原唱' }));
    expect(onChange).not.toHaveBeenCalled();  // the last one stays
    expect(screen.getByRole('button', { name: '无声' })).toBeDisabled();
    // 伴唱 was chosen before separation was turned off: shown pressed, and it can still be taken off
    rerender(<MultiToggle label="声音" value={['original', 'instrumental']} onChange={onChange} options={[...OPTIONS]} />);
    const inst = screen.getByRole('button', { name: '伴唱', pressed: true });
    expect(inst).toBeEnabled();
    await userEvent.click(inst);
    expect(onChange).toHaveBeenLastCalledWith(['original']);
  });
});
