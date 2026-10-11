// Design-system primitives. Pages compose only these (plus Tailwind utility
// classes using the semantic tokens from index.css).

import * as RDialog from '@radix-ui/react-dialog';
import * as RSlider from '@radix-ui/react-slider';
import * as RSwitch from '@radix-ui/react-switch';
import * as RTabs from '@radix-ui/react-tabs';
import * as RTooltip from '@radix-ui/react-tooltip';
import { AlertTriangle, Check, CheckCircle2, Info, Loader2, UploadCloud, X, XCircle } from 'lucide-react';
import {
  forwardRef, useEffect, useId, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import { cn } from '@/lib/format';
import { isEnter, isEscape } from '@/lib/keys';

// ------------------------------------------------------------------ Button

type Variant = 'primary' | 'secondary' | 'ghost' | 'outline' | 'danger' | 'soft';
type Size = 'xs' | 'sm' | 'md' | 'lg';

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-accent-fg hover:brightness-110 shadow-sm shadow-accent/20',
  secondary: 'bg-surface-2 text-fg hover:bg-surface-3 border border-line',
  outline: 'border border-line-strong text-fg hover:bg-surface-2',
  ghost: 'text-muted hover:text-fg hover:bg-surface-2',
  danger: 'bg-danger text-white hover:brightness-110',
  soft: 'bg-accent-soft text-accent hover:brightness-95 dark:hover:brightness-125',
};
const SIZES: Record<Size, string> = {
  xs: 'h-7 px-2 text-xs gap-1 rounded-md',
  sm: 'h-8 px-3 text-[13px] gap-1.5 rounded-lg',
  md: 'h-9 px-3.5 text-sm gap-2 rounded-lg',
  lg: 'h-11 px-5 text-[15px] gap-2 rounded-xl',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
  /**
   * Shown as a tooltip while the button is disabled (why it cannot be used
   * now).  When given, the button always sits in a wrapper (a disabled button
   * gets no pointer events), so it keeps its identity and focus when it flips.
   */
  disabledReason?: ReactNode;
  /** class of that wrapper (e.g. flex-1 when the button should grow) */
  wrapperClassName?: string;
}

/** Button look for other elements (e.g. a download link), without nesting a <button> in an <a>. */
export function buttonClass(variant: Variant = 'secondary', size: Size = 'md', className?: string) {
  return cn(
    'focus-ring inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap font-medium transition active:translate-y-px',
    VARIANTS[variant], SIZES[size], className,
  );
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading, icon, className, children, disabled, disabledReason, wrapperClassName, ...rest }, ref,
) {
  const btn = (
    <button
      ref={ref}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        'focus-ring inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap font-medium transition',
        'disabled:pointer-events-none disabled:opacity-45 active:translate-y-px',
        VARIANTS[variant], SIZES[size], className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" /> : icon}
      {children}
    </button>
  );
  if (disabledReason !== undefined || wrapperClassName) {
    const why = disabled && !loading ? disabledReason : null;
    return (
      <Tip keep content={why}>
        <span className={cn('inline-flex', why && 'cursor-not-allowed', wrapperClassName)}>{btn}</span>
      </Tip>
    );
  }
  return btn;
});

export function IconButton({ label, className, size = 'sm', disabledReason, ...rest }: ButtonProps & { label: string }) {
  const dims = { xs: 'size-7', sm: 'size-8', md: 'size-9', lg: 'size-11' }[size];
  const btn = <Button aria-label={label} variant="ghost" size={size} className={cn(dims, '!px-0', className)} {...rest} />;
  // always on a wrapper: a disabled button gets no pointer events, and the tooltip must still show
  return (
    <Tip content={rest.disabled && disabledReason ? <>{label}：{disabledReason}</> : label}>
      <span className="inline-flex">{btn}</span>
    </Tip>
  );
}

/**
 * A button whose action needs a second click to confirm (cancelling a running
 * operation, removing something).  The question falls back after a few seconds.
 */
export function ConfirmButton({ question, confirmLabel = '确认', keepLabel = '不了', onConfirm, children, ...rest }: Omit<ButtonProps, 'onClick'> & {
  question: ReactNode; confirmLabel?: string; keepLabel?: string; onConfirm: () => void;
}) {
  const [asking, setAsking] = useState(false);
  useEffect(() => {
    if (!asking) return;
    const t = setTimeout(() => setAsking(false), 6000);
    return () => clearTimeout(t);
  }, [asking]);
  if (asking) {
    return (
      <span role="group" aria-label="确认" className="inline-flex flex-wrap items-center gap-1.5 text-xs text-fg">
        <span>{question}</span>
        <Button size="xs" variant="danger" autoFocus onClick={() => { setAsking(false); onConfirm(); }}>{confirmLabel}</Button>
        <Button size="xs" variant="ghost" onClick={() => setAsking(false)}>{keepLabel}</Button>
      </span>
    );
  }
  return <Button {...rest} onClick={() => setAsking(true)}>{children}</Button>;
}

// ------------------------------------------------------------------ Card

export function Card({ className, children, ...rest }: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('rounded-[var(--radius-card)] border border-line bg-surface shadow-[var(--shadow-card)]', className)} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({ title, description, icon, actions, className }: {
  title: ReactNode; description?: ReactNode; icon?: ReactNode; actions?: ReactNode; className?: string;
}) {
  return (
    <div className={cn('flex items-start gap-3 border-b border-line px-5 py-4', className)}>
      {icon && <div className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">{icon}</div>}
      <div className="min-w-0 flex-1">
        <h3 className="text-[15px] font-semibold leading-6">{title}</h3>
        {description && <p className="mt-0.5 text-[13px] leading-5 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('px-5 py-4', className)}>{children}</div>;
}

// ------------------------------------------------------------------ Page header

export function PageHeader({ eyebrow, title, description, actions }: {
  eyebrow?: ReactNode; title: ReactNode; description?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-accent">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1.5 max-w-3xl text-sm leading-6 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ Badge

type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'danger' | 'info';
const TONES: Record<Tone, string> = {
  neutral: 'bg-surface-2 text-muted border-line',
  accent: 'bg-accent-soft text-accent border-transparent',
  ok: 'bg-ok-soft text-ok border-transparent',
  warn: 'bg-warn-soft text-warn border-transparent',
  danger: 'bg-danger-soft text-danger border-transparent',
  info: 'bg-info-soft text-info border-transparent',
};

export function Badge({ tone = 'neutral', children, className, dot, title }: {
  tone?: Tone; children: ReactNode; className?: string; dot?: boolean; title?: string;
}) {
  return (
    <span title={title} className={cn('inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4', TONES[tone], className)}>
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

// ------------------------------------------------------------------ Callout

const CALLOUT_ICON = { info: Info, warn: AlertTriangle, danger: XCircle, ok: CheckCircle2 };
export function Callout({ tone = 'info', title, children, actions, className }: {
  tone?: 'info' | 'warn' | 'danger' | 'ok'; title?: ReactNode; children?: ReactNode; actions?: ReactNode; className?: string;
}) {
  const Icon = CALLOUT_ICON[tone];
  const color = { info: 'text-info bg-info-soft', warn: 'text-warn bg-warn-soft', danger: 'text-danger bg-danger-soft', ok: 'text-ok bg-ok-soft' }[tone];
  return (
    <div className={cn('flex gap-3 rounded-xl px-4 py-3 text-[13px] leading-5', color, className)}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 text-fg">
        {title && <div className="font-semibold">{title}</div>}
        {children && <div className={cn(title && 'mt-0.5', 'text-fg/80')}>{children}</div>}
        {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ form controls

const fieldBase = 'focus-ring w-full rounded-lg border border-line bg-surface px-3 text-sm text-fg placeholder:text-subtle transition hover:border-line-strong focus:border-accent disabled:opacity-50';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cn(fieldBase, 'h-9', className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cn(fieldBase, 'min-h-28 py-2 font-mono text-[13px] leading-6', className)} {...rest} />;
});

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(fieldBase, 'h-9 cursor-pointer appearance-none bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-8', className)}
      style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238b93a1' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")" }}
      {...rest}
    >
      {children}
    </select>
  );
}

/**
 * A labelled form row.  Around one input / select it is a <label> (clicking
 * the text focuses the input).  With `group` — for segmented buttons, several
 * buttons or anything that is not a single input — it is a labelled group
 * instead: a <label> would forward a click on its text to the first button
 * and silently change the choice.
 */
export function Field({ label, hint, children, className, group }: {
  label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string; group?: boolean;
}) {
  const id = useId();
  if (group) {
    return (
      <div role="group" aria-labelledby={`${id}-l`} aria-describedby={hint ? `${id}-h` : undefined} className={cn('flex flex-col gap-1.5', className)}>
        <span id={`${id}-l`} className="text-[13px] font-medium text-fg">{label}</span>
        {children}
        {hint && <span id={`${id}-h`} className="text-xs text-muted">{hint}</span>}
      </div>
    );
  }
  return (
    <label className={cn('flex flex-col gap-1.5', className)}>
      <span className="text-[13px] font-medium text-fg">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

/** Arrow keys (and Home / End) move between the radios / tabs inside `e.currentTarget` and select. */
export function arrowNav(e: React.KeyboardEvent<HTMLElement>, role: 'radio' | 'tab' = 'radio') {
  const next = ['ArrowRight', 'ArrowDown'];
  const prev = ['ArrowLeft', 'ArrowUp'];
  if (![...next, ...prev, 'Home', 'End'].includes(e.key)) return;
  const items = [...e.currentTarget.querySelectorAll<HTMLElement>(`[role="${role}"]`)]
    .filter((el) => !(el as HTMLButtonElement).disabled && el.getAttribute('aria-disabled') !== 'true');
  if (!items.length) return;
  const cur = items.indexOf(document.activeElement as HTMLElement);
  const i = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1
    : next.includes(e.key) ? (cur + 1) % items.length : (cur - 1 + items.length) % items.length;
  e.preventDefault();
  items[i].focus();
  items[i].click();
}

/** Integer input that commits on blur / Enter (never on every keystroke). */
export function NumberInput({ value, onCommit, suffix, className, step = 1, min, max, disabled }: {
  value: number | null; onCommit: (v: number | null) => void; suffix?: string; className?: string;
  step?: number; min?: number; max?: number; disabled?: boolean;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const cancelled = useRef(false);
  const shown = draft ?? (value === null || value === undefined ? '' : String(value));
  // read the element's value: the draft in this closure may be one render old
  const commit = (raw: string) => {
    if (cancelled.current) { cancelled.current = false; setDraft(null); return; }
    if (draft === null && raw === shown) return;
    const t = raw.trim();
    setDraft(null);
    if (t === '') return onCommit(null);
    const n = Number(t);
    if (Number.isFinite(n) && n !== value) onCommit(n);
  };
  return (
    <div className={cn('relative', className)}>
      <input
        type="number"
        inputMode="numeric"
        className={cn(fieldBase, 'tabular h-8 pr-9 font-mono text-[13px]', !suffix && 'pr-2')}
        value={shown}
        step={step}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={(e) => commit(e.currentTarget.value)}
        onKeyDown={(e) => {
          if (isEnter(e)) { e.currentTarget.blur(); }
          if (isEscape(e)) { cancelled.current = true; setDraft(null); e.currentTarget.blur(); }
        }}
      />
      {suffix && <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-xs text-subtle">{suffix}</span>}
    </div>
  );
}

export function Slider({ value, onChange, onCommit, min = 0, max = 100, step = 1, disabled, className, label }: {
  value: number; onChange: (v: number) => void; onCommit?: (v: number) => void; min?: number; max?: number;
  step?: number; disabled?: boolean; className?: string; label?: string;
}) {
  return (
    <RSlider.Root
      className={cn('relative flex h-5 w-full touch-none items-center select-none', disabled && 'opacity-40', className)}
      value={[value]} min={min} max={max} step={step} disabled={disabled}
      onValueChange={(v) => onChange(v[0])}
      onValueCommit={(v) => onCommit?.(v[0])}
      aria-label={label}
    >
      <RSlider.Track className="relative h-1.5 grow overflow-hidden rounded-full bg-surface-3">
        <RSlider.Range className="absolute h-full rounded-full bg-accent" />
      </RSlider.Track>
      <RSlider.Thumb className="focus-ring block size-4 rounded-full border-2 border-accent bg-surface shadow transition hover:scale-110" />
    </RSlider.Root>
  );
}

/**
 * Long slider plus a number that can be clicked and typed into.
 * `onChange` fires while dragging/typing (live preview); `onCommit` when the
 * user releases the thumb or confirms the typed value (persist here).
 */
export function SliderField({
  label, name, value, onChange, onCommit, min = 0, max = 100, step = 1, unit = '%', disabled, hint, className, trackClassName,
}: {
  label?: ReactNode; /** accessible name when `label` is not plain text */ name?: string;
  value: number; onChange: (v: number) => void; onCommit?: (v: number) => void;
  min?: number; max?: number; step?: number; unit?: string; disabled?: boolean; hint?: ReactNode;
  className?: string; trackClassName?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const cancelled = useRef(false);
  const decimals = step < 1 ? Math.min(3, String(step).split('.')[1]?.length ?? 2) : 0;
  const shown = draft ?? value.toFixed(decimals);
  const clamp = (v: number) => Math.min(max, Math.max(min, v));
  const commitDraft = (raw: string) => {
    if (cancelled.current) { cancelled.current = false; setDraft(null); return; }
    if (draft === null && raw === value.toFixed(decimals)) return;
    const n = Number(raw.replace('%', '').trim());
    setDraft(null);
    if (raw.trim() === '' || !Number.isFinite(n)) return;
    const v = clamp(Number((Math.round(n / step) * step).toFixed(decimals)));
    onChange(v);
    onCommit?.(v);
  };
  return (
    <div className={cn('flex min-w-0 items-center gap-3', disabled && 'opacity-50', className)}>
      {label && (
        <span className="shrink-0 text-[13px] font-medium whitespace-nowrap text-muted">
          {label}
          {hint && <span className="ml-1 font-normal text-subtle">{hint}</span>}
        </span>
      )}
      <Slider
        className={cn('min-w-40 flex-1', trackClassName)}
        value={value} min={min} max={max} step={step} disabled={disabled}
        onChange={onChange} onCommit={onCommit}
        label={typeof label === 'string' ? label : name}
      />
      <div className="relative shrink-0">
        <input
          type="text"
          inputMode="decimal"
          disabled={disabled}
          aria-label={`${typeof label === 'string' ? label : name ?? ''}（输入数值）`}
          title="点击直接输入数值，回车确认"
          className={cn(
            'focus-ring tabular h-8 w-[4.75rem] rounded-lg border border-line bg-surface pr-6 pl-2 text-right font-mono text-[13px] font-semibold',
            'cursor-text transition hover:border-line-strong focus:border-accent',
          )}
          value={shown}
          onFocus={(e) => { setDraft(value.toFixed(decimals)); requestAnimationFrame(() => e.target.select()); }}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={(e) => commitDraft(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (isEnter(e)) e.currentTarget.blur();
            if (isEscape(e)) { cancelled.current = true; setDraft(null); e.currentTarget.blur(); }
            if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
              e.preventDefault();
              const d = (e.key === 'ArrowUp' ? 1 : -1) * step * (e.shiftKey ? 10 : 1);
              const v = clamp(Number(((draft !== null ? Number(draft) || value : value) + d).toFixed(decimals)));
              setDraft(v.toFixed(decimals));
              onChange(v);
            }
          }}
        />
        <span className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-xs text-subtle">{unit}</span>
      </div>
    </div>
  );
}

export function Switch({ checked, onChange, label, disabled, ariaLabel }: {
  checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean;
  /** accessible name when there is no visible label */ ariaLabel?: string;
}) {
  return (
    <label className={cn('inline-flex cursor-pointer items-center gap-2 text-[13px]', disabled && 'cursor-not-allowed opacity-50')}>
      <RSwitch.Root
        checked={checked} onCheckedChange={onChange} disabled={disabled} aria-label={ariaLabel}
        className="focus-ring relative h-5 w-9 shrink-0 rounded-full bg-surface-3 transition data-[state=checked]:bg-accent"
      >
        <RSwitch.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow transition data-[state=checked]:translate-x-[18px]" />
      </RSwitch.Root>
      {label}
    </label>
  );
}

/** Pill-style segmented control. */
export function Segmented<T extends string>({ value, onChange, options, size = 'md', className, label }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[];
  size?: 'sm' | 'md'; className?: string; /** accessible name of the group */ label?: string;
}) {
  // one tab stop: the chosen option (or the first usable one); arrows move between options
  const tabStop = options.find((o) => o.value === value && !o.disabled)?.value ?? options.find((o) => !o.disabled)?.value;
  return (
    <div className={cn('inline-flex rounded-lg bg-surface-2 p-0.5 ring-1 ring-line', className)} role="radiogroup" aria-label={label}
      onKeyDown={(e) => arrowNav(e)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          tabIndex={o.value === tabStop ? 0 : -1}
          disabled={o.disabled}
          title={o.title}
          onClick={() => { if (o.value !== value) onChange(o.value); }}
          className={cn(
            'focus-ring rounded-md font-medium whitespace-nowrap transition disabled:opacity-40',
            size === 'sm' ? 'h-6 px-2 text-xs' : 'h-8 px-3 text-[13px]',
            value === o.value ? 'bg-surface text-fg shadow-sm ring-1 ring-line' : 'text-muted hover:text-fg',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Several choices at once, in the look of Segmented; the last one chosen cannot be taken off. */
export function MultiToggle<T extends string>({ value, onChange, options, size = 'md', className, label }: {
  value: T[]; onChange: (v: T[]) => void; options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[];
  size?: 'sm' | 'md'; className?: string; /** accessible name of the group */ label?: string;
}) {
  const toggle = (v: T) => {
    const next = value.includes(v) ? value.filter((x) => x !== v) : [...value, v];
    if (next.length) onChange(options.map((o) => o.value).filter((x) => next.includes(x)));
  };
  return (
    <div className={cn('inline-flex rounded-lg bg-surface-2 p-0.5 ring-1 ring-line', className)} role="group" aria-label={label}>
      {options.map((o) => {
        const on = value.includes(o.value);
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            disabled={o.disabled}
            title={o.title}
            onClick={() => toggle(o.value)}
            className={cn(
              'focus-ring inline-flex items-center gap-1 rounded-md font-medium whitespace-nowrap transition disabled:opacity-40',
              size === 'sm' ? 'h-6 px-2 text-xs' : 'h-8 px-3 text-[13px]',
              on ? 'bg-surface text-fg shadow-sm ring-1 ring-line' : 'text-muted hover:text-fg',
            )}
          >
            {on && <Check className={size === 'sm' ? 'size-3' : 'size-3.5'} aria-hidden />}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Tabs

export function Tabs({ value, onChange, tabs, className }: {
  value: string; onChange: (v: string) => void; tabs: { value: string; label: ReactNode; content: ReactNode }[]; className?: string;
}) {
  return (
    <RTabs.Root value={value} onValueChange={onChange} className={className}>
      <RTabs.List className="flex gap-1 border-b border-line">
        {tabs.map((t) => (
          <RTabs.Trigger
            key={t.value}
            value={t.value}
            className="focus-ring -mb-px inline-flex items-center gap-1.5 border-b-2 border-transparent px-3 py-2 text-[13px] font-medium text-muted transition hover:text-fg data-[state=active]:border-accent data-[state=active]:text-fg"
          >
            {t.label}
          </RTabs.Trigger>
        ))}
      </RTabs.List>
      {tabs.map((t) => (
        <RTabs.Content key={t.value} value={t.value} className="pt-4 outline-none">
          {t.content}
        </RTabs.Content>
      ))}
    </RTabs.Root>
  );
}

// ------------------------------------------------------------------ Tooltip

export function Tip({ content, children, side = 'top', keep }: {
  content: ReactNode; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right';
  /** keep the same element tree when `content` comes and goes (the child is not remounted) */
  keep?: boolean;
}) {
  if (!content && !keep) return <>{children}</>;
  return (
    <RTooltip.Root delayDuration={250} open={content ? undefined : false}>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content
          side={side}
          sideOffset={6}
          className="z-50 max-w-xs animate-in rounded-md bg-fg px-2 py-1 text-xs text-canvas shadow-[var(--shadow-pop)]"
        >
          {content}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

export const TooltipProvider = RTooltip.Provider;

// ------------------------------------------------------------------ Dialog

export function Dialog({ open, onOpenChange, title, description, children, footer, wide }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: ReactNode; description?: ReactNode;
  children?: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-40 animate-in bg-black/40 backdrop-blur-[2px]" />
        <RDialog.Content
          className={cn(
            'fixed top-1/2 left-1/2 z-50 max-h-[85vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-hidden',
            'flex flex-col animate-slide-up rounded-2xl border border-line bg-surface shadow-[var(--shadow-pop)]',
            wide ? 'max-w-3xl' : 'max-w-lg',
          )}
        >
          <div className="flex items-start gap-3 border-b border-line px-5 py-4">
            <div className="min-w-0 flex-1">
              <RDialog.Title className="text-base font-semibold">{title}</RDialog.Title>
              {description && <RDialog.Description className="mt-1 text-[13px] text-muted">{description}</RDialog.Description>}
            </div>
            <RDialog.Close asChild>
              <button className="focus-ring rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg" aria-label="关闭"><X className="size-4" /></button>
            </RDialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-line bg-surface-2/50 px-5 py-3">{footer}</div>}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}

// ------------------------------------------------------------------ misc

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('size-4 animate-spin text-muted', className)} />;
}

export function Progress({ value, className, tone = 'accent', label = '进度' }: {
  value: number; className?: string; tone?: 'accent' | 'ok' | 'danger'; label?: string;
}) {
  const color = { accent: 'bg-accent', ok: 'bg-ok', danger: 'bg-danger' }[tone];
  const pct = Math.round(Math.max(0, Math.min(1, value || 0)) * 100);
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
      className={cn('h-1.5 w-full overflow-hidden rounded-full bg-surface-3', className)}>
      <div className={cn('h-full rounded-full transition-[width] duration-300', color)} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line-strong bg-surface-2 px-1 font-mono text-[10px] font-medium text-muted shadow-[0_1px_0_var(--c-line-strong)]">{children}</kbd>;
}

export function EmptyState({ icon, title, description, action, className }: {
  icon?: ReactNode; title: ReactNode; description?: ReactNode; action?: ReactNode; className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong px-6 py-12 text-center', className)}>
      {icon && <div className="mb-3 grid size-11 place-items-center rounded-xl bg-surface-2 text-muted">{icon}</div>}
      <div className="text-sm font-semibold">{title}</div>
      {description && <p className="mt-1 max-w-sm text-[13px] text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone }: { label: ReactNode; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  const color = tone ? { neutral: 'text-fg', accent: 'text-accent', ok: 'text-ok', warn: 'text-warn', danger: 'text-danger', info: 'text-info' }[tone] : 'text-fg';
  return (
    <div className="rounded-xl border border-line bg-surface-2/60 px-4 py-3">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={cn('tabular mt-1 text-xl font-semibold tracking-tight', color)}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-subtle">{hint}</div>}
    </div>
  );
}

/** Key/value rows, e.g. calibration formula values. */
export function KV({ rows, className }: { rows: [ReactNode, ReactNode, ReactNode?][]; className?: string }) {
  return (
    <dl className={cn('divide-y divide-line rounded-xl border border-line', className)}>
      {rows.map(([k, v, hint], i) => (
        <div key={i} className="flex items-baseline gap-4 px-4 py-2.5">
          <dt className="w-44 shrink-0 text-[13px] text-muted">{k}</dt>
          <dd className="min-w-0 flex-1 text-sm">
            <div className="tabular font-medium">{v}</div>
            {hint && <div className="mt-0.5 text-xs text-subtle">{hint}</div>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Drag-and-drop or click-to-pick file zone. */
export function DropZone({ accept, onFile, title, hint, compact, disabled, busy }: {
  accept?: string; onFile: (f: File) => void; title: ReactNode; hint?: ReactNode; compact?: boolean; disabled?: boolean; busy?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled || undefined}
      aria-busy={busy || undefined}
      onClick={() => !disabled && ref.current?.click()}
      onKeyDown={(e) => {
        if (disabled || e.target !== e.currentTarget) return;
        if (isEnter(e) || e.key === ' ') { e.preventDefault(); ref.current?.click(); }
      }}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f && !disabled) onFile(f);
      }}
      className={cn(
        'focus-ring group flex cursor-pointer items-center gap-3 rounded-xl border-2 border-dashed border-line-strong text-left transition',
        'hover:border-accent/60 hover:bg-accent-soft/40',
        over && 'border-accent bg-accent-soft/60',
        compact ? 'px-3 py-2.5' : 'flex-col justify-center px-6 py-8 text-center',
        disabled && 'pointer-events-none opacity-50',
      )}
    >
      <div className={cn('grid shrink-0 place-items-center rounded-lg bg-surface-2 text-muted transition group-hover:text-accent', compact ? 'size-8' : 'size-11')}>
        {busy ? <Loader2 className="size-5 animate-spin" /> : <UploadCloud className={compact ? 'size-4' : 'size-5'} />}
      </div>
      <div className="min-w-0">
        <div className="text-[13px] font-medium">{title}</div>
        {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
      </div>
      <input
        ref={ref}
        type="file"
        accept={accept}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = '';
          if (f) onFile(f);
        }}
      />
    </div>
  );
}

/** Styled table wrapper; use <Th>/<Td> inside. */
export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('overflow-auto rounded-xl border border-line', className)}>
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  );
}
export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return <th className={cn('sticky top-0 z-[1] border-b border-line bg-surface-2 px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted', className)}>{children}</th>;
}
export function Td({ children, className, ...rest }: { children?: ReactNode; className?: string } & React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn('border-b border-line px-3 py-2 align-middle', className)} {...rest}>{children}</td>;
}

/**
 * Draggable divider. `axis="y"` resizes a height (drag up/down),
 * `axis="x"` a width. `invert` flips the direction (e.g. a panel below the
 * handle grows when dragging up). Double-click resets to `defaultValue`.
 */
export function ResizeHandle({ axis, value, onChange, min, max, invert, defaultValue, label, className }: {
  axis: 'x' | 'y'; value: number; onChange: (v: number) => void; min: number; max: number;
  invert?: boolean; defaultValue?: number; label: string; className?: string;
}) {
  const [dragging, setDragging] = useState(false);
  const clamp = (v: number) => Math.round(Math.min(max, Math.max(min, v)));
  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const origin = axis === 'y' ? e.clientY : e.clientX;
    const base = value;
    setDragging(true);
    const move = (ev: PointerEvent) => {
      const d = (axis === 'y' ? ev.clientY : ev.clientX) - origin;
      onChange(clamp(base + (invert ? -d : d)));
    };
    const up = () => {
      setDragging(false);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = axis === 'y' ? 'row-resize' : 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  const step = (e: React.KeyboardEvent) => {
    const k = axis === 'y' ? ['ArrowUp', 'ArrowDown'] : ['ArrowLeft', 'ArrowRight'];
    if (!k.includes(e.key)) return;
    e.preventDefault();
    const dir = e.key === k[0] ? -1 : 1;
    onChange(clamp(value + (invert ? -dir : dir) * (e.shiftKey ? 50 : 10)));
  };
  return (
    <div
      role="separator"
      aria-orientation={axis === 'y' ? 'horizontal' : 'vertical'}
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title={`${label}（拖动调整，双击恢复默认）`}
      onPointerDown={start}
      onKeyDown={step}
      onDoubleClick={() => defaultValue !== undefined && onChange(clamp(defaultValue))}
      className={cn(
        'focus-ring group relative z-10 flex shrink-0 touch-none items-center justify-center',
        axis === 'y' ? 'h-2 w-full cursor-row-resize' : 'w-3 cursor-col-resize self-stretch',
        className,
      )}
    >
      <span
        className={cn(
          'rounded-full bg-line-strong transition group-hover:bg-accent',
          axis === 'y' ? 'h-1 w-12' : 'h-12 w-1',
          dragging && 'bg-accent',
        )}
      />
    </div>
  );
}
