import clsx, { type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, Check, CircleDashed, ExternalLink, Loader2, RotateCw, X } from 'lucide-react'
import {
  createContext, forwardRef, useCallback, useContext, useEffect, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import { ApiError } from '../lib/api'
import type { AppStatus, MatchStatus, TaskStep } from '../lib/types'
import { STATUS_LABEL } from '../lib/format'

export const cx = (...args: ClassValue[]) => twMerge(clsx(args))

// --- buttons & inputs ----------------------------------------------------------------------------

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'quiet'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  icon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button(
  { variant = 'secondary', size = 'md', loading, icon, className, children, disabled, ...rest }, ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        'inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-[background,color,border,box-shadow] duration-150 disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' && 'h-7 px-2.5 text-[12.5px]',
        size === 'md' && 'h-8.5 px-3.5 text-[13px]',
        size === 'lg' && 'h-10 px-4.5 text-sm',
        variant === 'primary' && 'bg-accent text-accent-ink shadow-[inset_0_1px_0_rgb(255_255_255/0.12)] hover:bg-accent-hover',
        variant === 'secondary' && 'border border-line-strong bg-surface text-ink hover:bg-surface-2',
        variant === 'ghost' && 'text-ink-2 hover:bg-surface-2 hover:text-ink',
        variant === 'quiet' && 'text-muted hover:text-ink',
        variant === 'danger' && 'border border-missing/40 bg-surface text-missing hover:bg-missing-soft',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-3.5 animate-spin" /> : icon}
      {children}
    </button>
  )
})

const fieldCls =
  'w-full rounded-md border border-line-strong bg-surface px-3 text-[13.5px] text-ink placeholder:text-faint transition-colors focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-60'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest }, ref,
) {
  return <input ref={ref} className={cx(fieldCls, 'h-9', className)} {...rest} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...rest }, ref,
) {
  return <textarea ref={ref} className={cx(fieldCls, 'py-2 leading-relaxed', className)} {...rest} />
})

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(fieldCls, 'h-9 pr-8', className)} {...rest}>
      {children}
    </select>
  )
}

export function Field({ label, hint, children, className }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cx('block', className)}>
      <span className="mb-1.5 block text-[12.5px] font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[12px] text-muted">{hint}</span>}
    </label>
  )
}

// --- surfaces ------------------------------------------------------------------------------------

export function Card({ className, children, ...rest }: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx('rounded-lg border border-line bg-surface shadow-card', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardHeader({ title, subtitle, actions, className }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cx('flex items-start justify-between gap-3 border-b border-line px-4 py-3', className)}>
      <div className="min-w-0">
        <h3 className="text-[13.5px] font-semibold text-ink">{title}</h3>
        {subtitle && <p className="mt-0.5 text-[12.5px] text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

export function PageHeader({ eyebrow, title, subtitle, actions }: { eyebrow?: ReactNode; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1.5 text-[11.5px] font-medium uppercase tracking-[0.08em] text-muted">{eyebrow}</div>}
        <h1 className="font-serif text-[28px] font-semibold leading-tight tracking-[-0.01em] text-ink md:text-[32px]">{title}</h1>
        {subtitle && <div className="mt-1.5 text-[13.5px] text-muted">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line-strong bg-surface-2 px-1.5 py-px font-mono text-[10.5px] text-muted">{children}</kbd>
}

// --- badges --------------------------------------------------------------------------------------

type Tone = 'neutral' | 'accent' | 'strong' | 'partial' | 'missing' | 'verify'
const toneCls: Record<Tone, string> = {
  neutral: 'bg-surface-2 text-ink-2 border-line',
  accent: 'bg-accent-soft text-accent border-accent/20',
  strong: 'bg-strong-soft text-strong border-strong/20',
  partial: 'bg-partial-soft text-partial border-partial/25',
  missing: 'bg-missing-soft text-missing border-missing/20',
  verify: 'bg-verify-soft text-verify border-verify/20',
}

export function Badge({ tone = 'neutral', children, className, dot }: { tone?: Tone; children: ReactNode; className?: string; dot?: boolean }) {
  return (
    <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded border px-1.5 py-px text-[11.5px] font-medium', toneCls[tone], className)}>
      {dot && <span className="size-1.5 rounded-full bg-current opacity-80" />}
      {children}
    </span>
  )
}

export const MATCH_LABEL: Record<MatchStatus, string> = {
  strong: 'Strong evidence',
  partial: 'Partial evidence',
  missing: 'Missing',
  needs_verification: 'Needs verification',
}
const matchTone: Record<MatchStatus, Tone> = { strong: 'strong', partial: 'partial', missing: 'missing', needs_verification: 'verify' }

export function MatchBadge({ status }: { status: MatchStatus }) {
  return <Badge tone={matchTone[status]} dot>{MATCH_LABEL[status]}</Badge>
}

const evidenceTone: Record<string, Tone> = { verified: 'strong', user_provided: 'accent', inferred: 'verify', unsupported: 'missing' }
const evidenceLabel: Record<string, string> = { verified: 'Verified', user_provided: 'User-provided', inferred: 'Inferred', unsupported: 'Unsupported' }
export function EvidenceBadge({ status }: { status: string }) {
  return <Badge tone={evidenceTone[status] ?? 'neutral'}>{evidenceLabel[status] ?? status}</Badge>
}

const statusTone: Partial<Record<AppStatus, Tone>> = {
  resume_ready: 'accent', ready_to_apply: 'accent', in_progress: 'partial', submitted: 'strong', assessment: 'verify',
  interview: 'verify', offer: 'strong', rejected: 'missing', withdrawn: 'neutral',
}
export function StatusBadge({ status }: { status: AppStatus }) {
  return <Badge tone={statusTone[status] ?? 'neutral'} dot>{STATUS_LABEL[status]}</Badge>
}

export function LevelText({ level }: { level: string | number | null | undefined }) {
  const v = String(level ?? '—')
  const tone = /strong|low/i.test(v) ? 'text-strong' : /good/i.test(v) ? 'text-ink' : /fair|medium/i.test(v) ? 'text-partial' : /weak|high/i.test(v) ? 'text-missing' : 'text-ink'
  return <span className={cx('font-medium', tone)}>{v}</span>
}

// --- states --------------------------------------------------------------------------------------

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx('size-4 animate-spin text-muted', className)} />
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('skeleton', className)} />
}

export function LoadingRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2.5 p-1" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-11 w-full" />
      ))}
    </div>
  )
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line-strong px-6 py-12 text-center">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <h3 className="font-serif text-[18px] font-semibold text-ink">{title}</h3>
      {children && <div className="mt-1.5 max-w-md text-[13px] text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

const ACTION_LABELS: Record<string, string> = {
  retry: 'Retry',
  paste_description: 'Paste the description instead',
  open_manually: 'Open job manually',
  open_settings: 'Open settings',
  open_templates: 'Upload a template',
  generate_resume: 'Generate a resume',
  duplicate: 'Duplicate version',
}

export function ErrorState({
  error, onRetry, onAction, openUrl, compact,
}: { error: unknown; onRetry?: () => void; onAction?: (a: string) => void; openUrl?: string; compact?: boolean }) {
  const e = error instanceof ApiError ? error : null
  const message = e?.message ?? (error as Error)?.message ?? 'Something went wrong.'
  const actions = (e?.actions ?? ['retry']).filter((a) => ACTION_LABELS[a] && (a !== 'retry' || onRetry) && (a !== 'open_manually' || openUrl))
  return (
    <div role="alert" className={cx('rounded-lg border border-missing/25 bg-missing-soft/60', compact ? 'p-3' : 'p-4')}>
      <div className="flex gap-3">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-missing" />
        <div className="min-w-0">
          <p className="text-[13.5px] font-medium text-ink">{message}</p>
          {e?.hint && <p className="mt-1 text-[12.5px] text-ink-2"><span className="text-muted">Possible reason: </span>{e.hint}</p>}
          {actions.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {actions.map((a) =>
                a === 'retry' ? (
                  <Button key={a} size="sm" icon={<RotateCw className="size-3.5" />} onClick={onRetry}>Retry</Button>
                ) : a === 'open_manually' ? (
                  <a key={a} href={openUrl} target="_blank" rel="noreferrer noopener">
                    <Button size="sm" icon={<ExternalLink className="size-3.5" />}>Open job manually</Button>
                  </a>
                ) : (
                  <Button key={a} size="sm" onClick={() => onAction?.(a)}>{ACTION_LABELS[a]}</Button>
                ),
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export function TaskProgress({ steps, status, error }: { steps: TaskStep[]; status?: string; error?: string }) {
  return (
    <div className="space-y-1.5">
      {steps.map((s, i) => (
        <motion.div
          key={s.label}
          initial={{ opacity: 0, x: -4 }}
          animate={{ opacity: s.state === 'pending' ? 0.55 : 1, x: 0 }}
          transition={{ delay: i * 0.04, duration: 0.2 }}
          className="flex items-center gap-2.5 text-[13px]"
        >
          <span className="flex size-4 items-center justify-center">
            {s.state === 'done' && (
              <motion.span initial={{ scale: 0.4 }} animate={{ scale: 1 }} className="flex size-4 items-center justify-center rounded-full bg-strong text-surface">
                <Check className="size-2.5" strokeWidth={3} />
              </motion.span>
            )}
            {s.state === 'running' && <Loader2 className="size-4 animate-spin text-accent" />}
            {s.state === 'pending' && <CircleDashed className="size-3.5 text-faint" />}
            {s.state === 'failed' && <X className="size-4 text-missing" />}
            {s.state === 'skipped' && <span className="size-1.5 rounded-full bg-faint" />}
          </span>
          <span className={cx(s.state === 'running' ? 'text-ink' : s.state === 'done' ? 'text-ink-2' : 'text-muted')}>
            {s.label}{s.state === 'running' ? '…' : ''}
          </span>
          {s.detail && <span className="text-[12px] text-muted">· {s.detail}</span>}
        </motion.div>
      ))}
      {status === 'queued' && <p className="text-[12.5px] text-muted">Waiting to start…</p>}
      {status === 'failed' && error && <p className="pt-1 text-[12.5px] text-missing">{error}</p>}
    </div>
  )
}

// --- dialog --------------------------------------------------------------------------------------

export function Dialog({ open, onClose, title, children, footer, width = 'max-w-lg' }: {
  open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; width?: string
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-[#1c1b19]/35 backdrop-blur-[1px]" onClick={onClose} />
          <motion.div
            role="dialog"
            aria-modal="true"
            initial={{ opacity: 0, y: 12, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.99 }}
            transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
            className={cx('relative max-h-[92vh] w-full overflow-y-auto rounded-t-xl border border-line bg-surface shadow-float sm:rounded-xl', width)}
          >
            <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
              <h2 className="font-serif text-[18px] font-semibold">{title}</h2>
              <button onClick={onClose} className="rounded p-1 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Close">
                <X className="size-4" />
              </button>
            </div>
            <div className="px-5 py-4">{children}</div>
            {footer && <div className="flex justify-end gap-2 border-t border-line bg-surface-2/50 px-5 py-3">{footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

// --- tabs ----------------------------------------------------------------------------------------

export function Tabs<T extends string>({ tabs, value, onChange, className }: {
  tabs: { value: T; label: ReactNode; count?: number }[]; value: T; onChange: (v: T) => void; className?: string
}) {
  return (
    <div role="tablist" className={cx('flex gap-1 overflow-x-auto border-b border-line', className)}>
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cx('relative -mb-px whitespace-nowrap px-3 py-2 text-[13px] font-medium transition-colors', value === t.value ? 'text-ink' : 'text-muted hover:text-ink')}
        >
          {t.label}
          {t.count !== undefined && <span className="ml-1.5 text-[11.5px] text-faint tabular">{t.count}</span>}
          {value === t.value && <motion.span layoutId={'tab-underline-' + tabs.map((x) => x.value).join()} className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-accent" />}
        </button>
      ))}
    </div>
  )
}

// --- toasts --------------------------------------------------------------------------------------

type Toast = { id: number; text: string; tone: 'ok' | 'error' }
const ToastCtx = createContext<(text: string, tone?: 'ok' | 'error') => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const push = useCallback((text: string, tone: 'ok' | 'error' = 'ok') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-20 right-4 z-[60] flex flex-col items-end gap-2 md:bottom-5">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              className={cx('pointer-events-auto flex max-w-sm items-center gap-2 rounded-md border px-3.5 py-2.5 text-[13px] shadow-float',
                t.tone === 'ok' ? 'border-line bg-ink text-bg' : 'border-missing/30 bg-missing-soft text-ink')}
            >
              {t.tone === 'ok' ? <Check className="size-3.5 text-accent" /> : <AlertTriangle className="size-3.5 text-missing" />}
              {t.text}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  )
}

export const useToast = () => useContext(ToastCtx)

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : (e as Error)?.message ?? 'Something went wrong.'
}
