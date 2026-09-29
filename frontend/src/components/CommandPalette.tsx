import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowRight, CornerDownLeft, Loader2, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { post } from '../lib/api'
import type { JobSummary } from '../lib/types'
import { Button, cx, errorText } from './ui'

type CommandResult = {
  ok: boolean; report: string; navigate?: string; tool: string | null; permission?: string
  options?: string[]; actions?: { label: string; command?: string; href?: string }[]
}

const COMMANDS: { label: string; text: string; hint: string; prefill?: boolean }[] = [
  { label: 'Edit current resume', text: 'open resume', hint: 'Resume' },
  { label: 'Apply to current job', text: 'apply for this job', hint: 'Runner' },
  { label: 'Continue application', text: 'continue the application', hint: 'Runner' },
  { label: 'What is stopping the application?', text: 'what is stopping the application?', hint: 'Status' },
  { label: 'Generate tailored resume', text: 'generate tailored resume', hint: 'Resume' },
  { label: 'Run ATS check', text: 'run ATS check', hint: 'Resume' },
  { label: 'Undo last resume change', text: 'undo last resume change', hint: 'Resume' },
  { label: 'Show skill gaps', text: 'show skill gaps', hint: 'Job' },
  { label: 'Open job notes', text: 'open job notes', hint: 'Job' },
  { label: 'Open application', text: 'open application', hint: 'Application' },
  { label: 'Add career idea…', text: 'Add career idea: ', hint: 'Notes', prefill: true },
  { label: 'Scan GitHub', text: 'rescan my github', hint: 'Evidence' },
]

function pageContext(path: string) {
  const m = path.match(/^\/(jobs|applications|resumes)\/(\d+)/)
  if (!m) return {}
  return { jobs: { job_id: m[2] }, applications: { application_id: m[2] }, resumes: { resume_id: m[2] } }[m[1] as 'jobs']
}

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [text, setText] = useState('')
  const [active, setActive] = useState(0)
  const [result, setResult] = useState<CommandResult | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const loc = useLocation()
  const qc = useQueryClient()
  const isUrl = /^https?:\/\/\S+$/i.test(text.trim())
  const matches = useMemo(() => {
    const t = text.trim().toLowerCase()
    return t ? COMMANDS.filter((c) => c.label.toLowerCase().includes(t)) : COMMANDS
  }, [text])

  const importJob = useMutation({
    mutationFn: (u: string) => post<{ job: JobSummary; task_id: number }>('/api/jobs', { url: u }),
    onSuccess: (r) => { qc.invalidateQueries(); close(); nav(`/jobs/${r.job.id}?task=${r.task_id}`) },
    onError: (e) => setResult({ ok: false, report: errorText(e), tool: null }),
  })
  const run = useMutation({
    mutationFn: (t: string) => post<CommandResult>('/api/agent/command', { text: t, context: pageContext(loc.pathname) }),
    onSuccess: (r) => {
      qc.invalidateQueries()
      if (r.tool === 'navigate' && r.navigate) { close(); nav(r.navigate); return }
      setResult(r)
    },
    onError: (e) => setResult({ ok: false, report: errorText(e), tool: null }),
  })
  const busy = run.isPending || importJob.isPending

  const close = () => { onClose(); setText(''); setResult(null); setActive(0) }
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 30) }, [open])

  const submit = (override?: string) => {
    const t = (override ?? text).trim()
    if (!t || busy) return
    setResult(null)
    if (/^https?:\/\/\S+$/i.test(t)) { importJob.mutate(t); return }
    run.mutate(t)
  }
  const pick = (i: number) => {
    const c = matches[i]
    if (!c) return submit()
    if (c.prefill) { setText(c.text); inputRef.current?.focus(); return }
    setText(c.label)
    submit(c.text)
  }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); close() }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, matches.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    if (e.key === 'Enter') {
      e.preventDefault()
      const exact = matches[active] && (text.trim() === '' || matches[active].label.toLowerCase().startsWith(text.trim().toLowerCase()))
      if (exact && !isUrl) pick(active)
      else submit()
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-start justify-center bg-ink/30 px-4 pt-[12vh] backdrop-blur-[2px]"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={close}>
          <motion.div role="dialog" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}
            initial={{ y: -6, scale: 0.99 }} animate={{ y: 0, scale: 1 }} exit={{ y: -4, opacity: 0 }} transition={{ duration: 0.14 }}
            className="w-full max-w-[640px] overflow-hidden rounded-xl border border-line bg-surface shadow-float">
            <div className="flex items-center gap-2.5 border-b border-line px-4">
              {busy ? <Loader2 className="size-4 animate-spin text-accent" /> : <Search className="size-4 text-muted" />}
              <input ref={inputRef} value={text} onChange={(e) => { setText(e.target.value); setActive(0); setResult(null) }} onKeyDown={onKey}
                placeholder="Tell CareerPilot what to do, or paste a job link" aria-label="Command"
                className="h-13 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-faint" />
              <kbd className="rounded border border-line px-1.5 text-[11px] text-muted">esc</kbd>
            </div>
            {result ? (
              <div className="space-y-3 p-4 text-[13.5px]">
                <p className={cx('whitespace-pre-line', result.ok ? 'text-ink' : 'text-ink-2')}>{result.report}</p>
                {!!result.options?.length && (
                  <div className="flex flex-wrap gap-1.5">
                    {result.options.map((o) => <button key={o} onClick={() => { setText(o); submit(o) }} className="rounded border border-line-strong px-2 py-0.5 text-[12.5px] hover:border-accent/50">{o}</button>)}
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span className="text-[11.5px] text-faint">{result.permission === 'HIGH_IMPACT' ? 'Needs your explicit confirmation' : result.permission === 'EXTERNAL' ? 'Acts through your runner' : ''}</span>
                  <div className="flex gap-2">
                    {result.actions?.filter((a) => a.command).map((a) => <Button key={a.label} size="sm" onClick={() => { setText(a.label); submit(a.command) }}>{a.label}</Button>)}
                    {result.navigate && <Button size="sm" variant="primary" icon={<ArrowRight className="size-3.5" />} onClick={() => { close(); nav(result.navigate!) }}>Open</Button>}
                  </div>
                </div>
              </div>
            ) : isUrl ? (
              <div className="flex items-center justify-between px-4 py-3 text-[13px] text-ink-2">
                Analyze this job posting<span className="flex items-center gap-1 text-[11.5px] text-muted"><CornerDownLeft className="size-3" />Enter</span>
              </div>
            ) : (
              <ul className="max-h-[360px] overflow-y-auto py-1.5" role="listbox">
                {matches.map((c, i) => (
                  <li key={c.label} role="option" aria-selected={i === active} onMouseEnter={() => setActive(i)} onClick={() => pick(i)}
                    className={cx('flex cursor-pointer items-center justify-between px-4 py-2 text-[13.5px]', i === active ? 'bg-surface-2 text-ink' : 'text-ink-2')}>
                    <span>{c.label}</span><span className="text-[11.5px] text-faint">{c.hint}</span>
                  </li>
                ))}
                {text.trim() && (
                  <li onClick={() => submit()} className={cx('flex cursor-pointer items-center justify-between px-4 py-2 text-[13.5px]', !matches.length ? 'bg-surface-2 text-ink' : 'text-ink-2')}>
                    <span>Do: “{text.trim()}”</span><span className="flex items-center gap-1 text-[11.5px] text-faint"><CornerDownLeft className="size-3" />Enter</span>
                  </li>
                )}
              </ul>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
