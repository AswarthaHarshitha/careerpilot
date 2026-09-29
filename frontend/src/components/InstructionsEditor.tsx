import { useMutation } from '@tanstack/react-query'
import { AlertTriangle, Check, Plus, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { post } from '../lib/api'
import type { InstructionControls, ResumeInstructions } from '../lib/types'
import { Button, Input, cx } from './ui'

type Interp = {
  applied: string[]
  conflicts: { instruction: string; reason: string; options: string[] }[]
  unrecognized: string[]
  ambiguous: string[]
}

const EXAMPLES = ['Include portfolio link', 'Keep to one page', 'Put Resolve first', 'Remove certifications', 'Emphasize backend']

function Segmented<T extends string | number>({ label, value, options, onChange }: {
  label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 py-1.5">
      <span className="text-[12.5px] text-ink-2">{label}</span>
      <div className="flex rounded-md border border-line bg-surface-2/60 p-0.5" role="radiogroup" aria-label={label}>
        {options.map((o) => (
          <button key={String(o.value)} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
            className={cx('rounded px-2 py-0.5 text-[12px] transition-colors', value === o.value ? 'bg-surface font-medium text-ink shadow-card' : 'text-muted hover:text-ink')}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export function InstructionsEditor({ value, onChange, compact }: {
  value: ResumeInstructions; onChange: (v: ResumeInstructions) => void; compact?: boolean
}) {
  const c = value.controls
  const [draft, setDraft] = useState('')
  const [interp, setInterp] = useState<Interp | null>(null)
  const parse = useMutation({ mutationFn: (text: string) => post<Interp>('/api/jobs/instructions/parse', { text }), onSuccess: setInterp })
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => {
    window.clearTimeout(timer.current)
    const all = [...value.custom, draft].filter((x) => x.trim()).join('\n')
    if (!all) { setInterp(null); return }
    timer.current = window.setTimeout(() => parse.mutate(all), 450)
    return () => window.clearTimeout(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.custom.join('\n'), draft])

  const set = (patch: Partial<InstructionControls>) => onChange({ ...value, controls: { ...c, ...patch } })
  const add = (text: string) => {
    const t = text.trim()
    if (!t) return
    onChange({ ...value, custom: [...value.custom, t] })
    setDraft('')
  }
  const tri = [{ value: 'default' as const, label: 'Default' }, { value: 'include' as const, label: 'Include' }, { value: 'exclude' as const, label: 'Exclude' }]
  const auto = [{ value: 'auto' as const, label: 'Auto' }, { value: 'include' as const, label: 'Include' }, { value: 'exclude' as const, label: 'Exclude' }]

  return (
    <div className="space-y-3">
      <div className={cx('grid gap-x-6', compact ? '' : 'sm:grid-cols-2')}>
        <Segmented label="Portfolio link" value={c.portfolio ?? 'default'} options={tri} onChange={(v) => set({ portfolio: v })} />
        <Segmented label="GitHub link" value={c.github ?? 'default'} options={tri} onChange={(v) => set({ github: v })} />
        <Segmented label="LinkedIn link" value={c.linkedin ?? 'default'} options={tri} onChange={(v) => set({ linkedin: v })} />
        <Segmented label="Length" value={String(c.length ?? 'default')} options={[{ value: 'default', label: 'Default' }, { value: '1', label: '1 page' }, { value: '2', label: '2 pages' }]}
          onChange={(v) => set({ length: v === 'default' ? 'default' : (Number(v) as 1 | 2) })} />
        <Segmented label="Projects" value={String(c.projects ?? 'auto')} options={[{ value: 'auto', label: 'Auto' }, { value: '2', label: '2' }, { value: '3', label: '3' }, { value: '4', label: '4' }]}
          onChange={(v) => set({ projects: v === 'auto' ? 'auto' : (Number(v) as 2 | 3 | 4) })} />
        <Segmented label="Summary" value={c.summary ?? 'auto'} options={auto} onChange={(v) => set({ summary: v })} />
        <Segmented label="Certifications" value={c.certifications ?? 'auto'} options={auto} onChange={(v) => set({ certifications: v })} />
      </div>
      <div>
        <div className="mb-1.5 text-[12.5px] font-medium text-ink-2">Additional instructions</div>
        <ul className="space-y-1">
          {value.custom.map((line, i) => (
            <li key={i} className="flex items-center gap-2 rounded-md border border-line bg-surface-2/40 px-2.5 py-1.5 text-[13px]">
              <span className="flex-1">{line}</span>
              <button type="button" aria-label="Remove instruction" onClick={() => onChange({ ...value, custom: value.custom.filter((_, j) => j !== i) })}
                className="rounded p-0.5 text-faint hover:text-missing"><X className="size-3.5" /></button>
            </li>
          ))}
        </ul>
        <div className="mt-1.5 flex gap-2">
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="e.g. Put Resolve above EventSphere and emphasize backend"
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(draft) } }} className="h-8.5" />
          <Button type="button" size="md" icon={<Plus className="size-3.5" />} onClick={() => add(draft)} disabled={!draft.trim()}>Add</Button>
        </div>
        {value.custom.length === 0 && !draft && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" onClick={() => add(ex)} className="rounded border border-line px-2 py-0.5 text-[11.5px] text-muted hover:border-line-strong hover:text-ink">{ex}</button>
            ))}
          </div>
        )}
      </div>
      {interp && (interp.applied.length > 0 || interp.conflicts.length > 0 || interp.unrecognized.length > 0) && (
        <div className="rounded-md border border-line bg-surface-2/40 p-3 text-[12.5px]">
          <div className="mb-1 font-medium text-ink">How CareerPilot reads this</div>
          <ul className="space-y-0.5">
            {interp.applied.map((a) => <li key={a} className="flex items-center gap-1.5 text-ink-2"><Check className="size-3 text-strong" />{a}</li>)}
            {interp.unrecognized.map((u) => <li key={u} className="text-muted">“{u}” — used as guidance for wording only</li>)}
            {interp.ambiguous.map((u) => <li key={u} className="text-partial">“{u}” — unclear which project you mean</li>)}
          </ul>
          {interp.conflicts.map((cf) => (
            <div key={cf.instruction} className="mt-2 rounded border border-missing/25 bg-missing-soft/50 p-2">
              <div className="flex items-center gap-1.5 font-medium text-ink"><AlertTriangle className="size-3.5 text-missing" />Not applied: “{cf.instruction}”</div>
              <div className="mt-0.5 text-ink-2">{cf.reason}</div>
              <div className="mt-1 text-muted">You can: {cf.options.join(' · ')}</div>
            </div>
          ))}
          <div className="mt-2 text-[11.5px] text-muted">These instructions affect this job's resume only. Your master profile does not change.</div>
        </div>
      )}
    </div>
  )
}

export const emptyInstructions = (): ResumeInstructions => ({ controls: {}, custom: [] })
