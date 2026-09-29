import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle, ArrowDown, ArrowUp, CheckCircle2, CircleDot, Cloud, CloudOff, Columns2, Download, Eye, FileCode2,
  History, Info, Loader2, PenLine, Plus, Redo2, RotateCcw, Send, Trash2, Undo2, Wand2, X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { Badge, Button, Card, Dialog, ErrorState, LoadingRows, Spinner, Textarea, cx, errorText, useToast } from '../components/ui'
import { ApiError, api, get, post } from '../lib/api'
import { fmtDateTime, relative } from '../lib/format'
import type { EditorBullet, EditorEntry, EditorSection, EditorState, QualityCheck } from '../lib/types'

type Op = Record<string, unknown> & { op: string; id?: string }
type SyncState = 'saved' | 'saving' | 'unsynced' | 'conflict'
type View = 'split' | 'edit' | 'preview'

const ORIGIN: Record<string, { label: string; cls: string }> = {
  profile: { label: 'Your profile', cls: 'bg-strong' },
  template: { label: 'Your resume', cls: 'bg-strong' },
  evidence: { label: 'Drafted from verified evidence', cls: 'bg-partial' },
  rewritten: { label: 'Reworded from your evidence', cls: 'bg-accent' },
  user: { label: 'Your edit', cls: 'bg-verify' },
}

export default function ResumeWorkspace() {
  const { id } = useParams()
  const q = useQuery({ queryKey: ['editor', id], queryFn: () => get<EditorState>(`/api/resumes/${id}/editor`), staleTime: Infinity })
  if (q.isLoading) return <div className="p-8"><LoadingRows rows={8} /></div>
  if (q.error) return <div className="p-8"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
  return <Editor initial={q.data!} vid={Number(id)} />
}

function pendingKey(vid: number) { return `cp-editor-pending-${vid}` }

function Editor({ initial, vid }: { initial: EditorState; vid: number }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [state, setState] = useState<EditorState>(initial)
  const [local, setLocal] = useState<Record<string, string>>({}) // text being typed, not yet sent
  const [sync, setSync] = useState<SyncState>('saved')
  const [view, setView] = useState<View>('split')
  const [panel, setPanel] = useState<'assistant' | 'quality' | 'history'>('assistant')
  const [selection, setSelection] = useState<string[]>([])
  const [flash, setFlash] = useState<string[]>([])
  const [conflict, setConflict] = useState<Op[] | null>(null)
  const queue = useRef<Op[]>([])
  const timer = useRef<number | undefined>(undefined)
  const inflight = useRef(false)
  const stateRef = useRef(state)
  stateRef.current = state

  const accept = useCallback((s: EditorState, changed?: string[]) => {
    setState(s)
    qc.setQueryData(['editor', String(vid)], s)
    qc.invalidateQueries({ queryKey: ['resumes'] })
    if (changed?.length) {
      setFlash(changed)
      window.setTimeout(() => setFlash([]), 1400)
    }
  }, [qc, vid])

  // restore edits that were not synced before a reload or network failure
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(pendingKey(vid)) || 'null') as { base: number; ops: Op[] } | null
      if (saved?.ops?.length) {
        queue.current = saved.ops
        setSync('unsynced')
        window.setTimeout(() => flush(), 200)
      }
    } catch { /* storage unavailable */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const persistQueue = () => {
    try {
      if (queue.current.length) localStorage.setItem(pendingKey(vid), JSON.stringify({ base: stateRef.current.revision, ops: queue.current }))
      else localStorage.removeItem(pendingKey(vid))
    } catch { /* ignore */ }
  }

  const flush = useCallback(async (): Promise<boolean> => {
    window.clearTimeout(timer.current)
    if (inflight.current) { await new Promise((r) => window.setTimeout(r, 150)); return flush() }
    if (!queue.current.length) return true
    const taken = queue.current.length
    const ops = coalesce(queue.current)
    inflight.current = true
    setSync('saving')
    try {
      const s = await post<EditorState & { changed: string[] }>(`/api/resumes/${vid}/ops`, {
        base_revision: stateRef.current.revision, ops, label: describe(ops),
      })
      // Edits typed while this save was in flight stay queued for the next one.
      queue.current = queue.current.slice(taken)
      persistQueue()
      const pending = new Set(queue.current.map((o) => o.id))
      setLocal((l) => { const n = { ...l }; ops.forEach((o) => { if (o.id && !pending.has(o.id)) delete n[o.id] }); return n })
      accept(s, s.changed)
      setSync('saved')
      return true
    } catch (e) {
      if (e instanceof ApiError && e.code === 'revision_conflict') {
        setConflict(ops)
        setSync('conflict')
      } else if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        queue.current = []
        persistQueue()
        setLocal({})
        setSync('saved')
        toast(e.message, 'error')
      } else {
        persistQueue()
        setSync('unsynced')
        window.setTimeout(() => flush(), 4000)
      }
      return false
    } finally {
      inflight.current = false
    }
  }, [accept, toast, vid])

  const enqueue = useCallback((op: Op, immediate = false) => {
    queue.current.push(op)
    persistQueue()
    setSync('saving')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => flush(), immediate ? 0 : 800)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flush])

  const editText = (elId: string, text: string) => {
    setLocal((l) => ({ ...l, [elId]: text }))
    enqueue({ op: 'update_text', id: elId, text })
  }

  const history = async (kind: 'undo' | 'redo' | 'restore', number?: number) => {
    await flush()
    setSync('saving')
    try {
      const s = await post<EditorState>(`/api/resumes/${vid}/${kind}`, { base_revision: stateRef.current.revision, number })
      accept(s, kind === 'restore' ? [] : ['all'])
      setSync('saved')
    } catch (e) {
      setSync('saved')
      if (e instanceof ApiError && e.code === 'revision_conflict') setConflict([])
      else toast(errorText(e), 'error')
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return // native text undo
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) { if (stateRef.current.can_redo) history('redo') } else if (stateRef.current.can_undo) history('undo')
      }
    }
    window.addEventListener('keydown', onKey)
    const beforeUnload = (e: BeforeUnloadEvent) => { if (queue.current.length) e.preventDefault() }
    window.addEventListener('beforeunload', beforeUnload)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('beforeunload', beforeUnload) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const download = async (kind: 'pdf' | 'source') => {
    await flush()
    try {
      const url = kind === 'pdf' ? `/api/resumes/${vid}/pdf?download=true` : `/api/resumes/${vid}/source`
      const res = await fetch(url, { credentials: 'same-origin' })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new ApiError(res.status, body?.error ?? { code: 'x', message: 'Download failed', hint: '', actions: [] })
      }
      const blob = await res.blob()
      const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') || '')?.[1] ?? 'resume.pdf'
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = name
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      toast(errorText(e) + (e instanceof ApiError && e.hint ? ` ${e.hint}` : ''), 'error')
      setPanel('quality')
    }
  }

  const approve = useMutation({
    mutationFn: async () => { await flush(); return post(`/api/resumes/${vid}/approve`) },
    onSuccess: async () => { accept(await get<EditorState>(`/api/resumes/${vid}/editor`)); toast('Approved') },
    onError: (e) => { toast(errorText(e), 'error'); setPanel('quality') },
  })

  const s = state
  const qr = s.quality_report
  const fails = qr?.counts?.fail ?? 0
  const sections = s.content.sections

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface/80 px-4 py-2.5 backdrop-blur md:px-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11.5px] font-medium uppercase tracking-[0.08em] text-muted">
            <Link to="/resumes" className="hover:text-ink">Resumes</Link>
            {s.job && <><span>/</span><Link to={`/jobs/${s.job.id}`} className="normal-case tracking-normal hover:text-ink">{s.job.company} · {s.job.role_title}</Link></>}
          </div>
          <div className="flex items-center gap-2">
            <h1 className="truncate font-serif text-[19px] font-semibold">Resume editor</h1>
            <Badge tone={s.status === 'approved' ? 'strong' : 'neutral'}>{s.status === 'approved' ? 'Approved' : 'Draft'}</Badge>
            <SyncBadge sync={sync} />
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Button size="sm" variant="ghost" icon={<Undo2 className="size-3.5" />} disabled={!s.can_undo} onClick={() => history('undo')} title="Undo (⌘Z)" aria-label="Undo" />
          <Button size="sm" variant="ghost" icon={<Redo2 className="size-3.5" />} disabled={!s.can_redo} onClick={() => history('redo')} title="Redo (⌘⇧Z)" aria-label="Redo" />
          <div className="mx-1 flex rounded-md border border-line bg-surface p-0.5 text-[12px]">
            {([['edit', PenLine, 'Edit'], ['split', Columns2, 'Split'], ['preview', Eye, 'Preview']] as const).map(([v, Icon, label]) => (
              <button key={v} onClick={() => setView(v)} className={cx('flex items-center gap-1 rounded px-2 py-0.5', view === v ? 'bg-surface-2 font-medium text-ink' : 'text-muted')}>
                <Icon className="size-3.5" />{label}
              </button>
            ))}
          </div>
          <span className={cx('text-[12px] tabular', s.page_count && s.page_count > s.page_target ? 'text-missing' : 'text-muted')}>
            {s.page_count ?? '—'} / {s.page_target} page{s.page_target > 1 ? 's' : ''}
          </span>
          <Button size="sm" variant="primary" icon={<CheckCircle2 className="size-3.5" />} loading={approve.isPending} disabled={s.export_blocked} onClick={() => approve.mutate()}>Approve</Button>
          <Button size="sm" icon={<Download className="size-3.5" />} disabled={s.export_blocked} onClick={() => download('pdf')}>PDF</Button>
          <Button size="sm" variant="ghost" icon={<FileCode2 className="size-3.5" />} disabled={s.export_blocked} onClick={() => download('source')} title="Download source">Source</Button>
          {s.job && <Link to={`/jobs/${s.job.id}`}><Button size="sm" variant="ghost" icon={<Send className="size-3.5" />}>Apply to job</Button></Link>}
        </div>
      </div>

      {(s.export_blocked || s.render_error) && (
        <div className="flex items-start gap-2 border-b border-missing/20 bg-missing-soft/60 px-6 py-2 text-[12.5px]">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-missing" />
          <span>{s.render_error || `Export is blocked until ${fails} check${fails !== 1 ? 's' : ''} pass.`} <button className="font-medium underline" onClick={() => setPanel('quality')}>See checks</button></span>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_340px] lg:grid-cols-[minmax(0,1fr)_320px]">
        {view !== 'preview' && (
          <div className={cx('min-h-0 overflow-y-auto border-r border-line bg-bg px-4 py-5 md:px-6', view === 'edit' && 'xl:col-span-2')}>
            <div className="mx-auto max-w-[760px] space-y-5">
              <HeaderEditor s={s} local={local} onText={editText} onLink={(kind, mode) => enqueue({ op: 'set_link', kind, mode }, true)} flash={flash} />
              {sections.map((sec) => (
                <SectionEditor key={sec.id} sec={sec} local={local} flash={flash} selection={selection} setSelection={setSelection}
                  onText={editText} onOp={(op) => enqueue(op, true)} />
              ))}
            </div>
          </div>
        )}
        {view !== 'edit' && (
          <div className={cx('min-h-0 border-r border-line bg-surface-3/60 p-3', view === 'preview' && 'xl:col-span-2', view === 'split' && 'hidden xl:block')}>
            <LivePdf vid={vid} revision={s.revision} pages={s.page_count} saving={sync === 'saving'} />
          </div>
        )}
        <aside className="min-h-0 overflow-y-auto border-t border-line bg-surface lg:border-t-0">
          <div className="sticky top-0 z-10 flex border-b border-line bg-surface">
            {([['assistant', Wand2, 'Assistant'], ['quality', CircleDot, `Checks${fails ? ` · ${fails}` : ''}`], ['history', History, 'History']] as const).map(([k, Icon, label]) => (
              <button key={k} onClick={() => setPanel(k)} className={cx('flex flex-1 items-center justify-center gap-1.5 py-2.5 text-[12.5px]', panel === k ? 'border-b-2 border-accent font-medium text-ink' : 'text-muted')}>
                <Icon className="size-3.5" />{label}
              </button>
            ))}
          </div>
          {panel === 'assistant' && <AssistantPanel vid={vid} state={s} selection={selection} clearSelection={() => setSelection([])}
            flush={flush} onApplied={(st, ch) => accept(st, ch)} />}
          {panel === 'quality' && <QualityPanel s={s} vid={vid} onResolved={(st) => accept(st, ['all'])} />}
          {panel === 'history' && <HistoryPanel s={s} onRestore={(n) => history('restore', n)} />}
        </aside>
      </div>
      <ConflictDialog open={conflict !== null} ops={conflict ?? []} vid={vid}
        onKeep={async () => {
          const latest = await get<EditorState>(`/api/resumes/${vid}/editor`)
          stateRef.current = latest
          setState(latest)
          setConflict(null)
          queue.current = conflict ?? []
          await flush()
        }}
        onNewer={async () => {
          queue.current = []
          persistQueue()
          setLocal({})
          accept(await get<EditorState>(`/api/resumes/${vid}/editor`))
          setConflict(null)
          setSync('saved')
        }} />
    </div>
  )
}

function coalesce(ops: Op[]): Op[] {
  // consecutive text edits to the same element collapse into the last one
  const out: Op[] = []
  for (const op of ops) {
    const last = out[out.length - 1]
    if (last && op.op === 'update_text' && last.op === 'update_text' && last.id === op.id) out[out.length - 1] = op
    else out.push(op)
  }
  return out
}

function describe(ops: Op[]): string {
  if (ops.length === 1) {
    const o = ops[0]
    if (o.op === 'update_text') return 'Edited text'
    if (o.op === 'set_link') return `${o.mode === 'exclude' ? 'Removed' : o.mode === 'include' ? 'Added' : 'Reset'} ${o.kind} link`
    if (o.op === 'move_entry') return 'Moved an entry'
    if (o.op === 'move_bullet') return 'Moved a bullet'
    if (o.op === 'remove_bullet') return 'Removed a bullet'
    if (o.op === 'add_bullet') return 'Added a bullet'
    if (o.op === 'remove_section') return 'Removed a section'
    if (o.op === 'restore_section') return 'Restored a section'
    if (o.op === 'set_skills') return 'Edited skills'
  }
  return `${ops.length} edits`
}

function SyncBadge({ sync }: { sync: SyncState }) {
  if (sync === 'saving') return <span className="flex items-center gap-1 text-[11.5px] text-muted"><Loader2 className="size-3 animate-spin" />Saving…</span>
  if (sync === 'unsynced') return <span className="flex items-center gap-1 text-[11.5px] text-partial"><CloudOff className="size-3" />Not synced — retrying</span>
  if (sync === 'conflict') return <span className="flex items-center gap-1 text-[11.5px] text-missing"><AlertTriangle className="size-3" />Changed elsewhere</span>
  return <span className="flex items-center gap-1 text-[11.5px] text-muted"><Cloud className="size-3" />Saved</span>
}

// --- document editing --------------------------------------------------------------------------------

function Flash({ on, children, className }: { on: boolean; children: React.ReactNode; className?: string }) {
  return (
    <motion.div animate={on ? { backgroundColor: ['rgba(208,138,92,0.22)', 'rgba(208,138,92,0)'] } : {}} transition={{ duration: 1.3 }} className={cx('rounded', className)}>
      {children}
    </motion.div>
  )
}

function AutoText({ value, onChange, className, placeholder, onFocus, selected, rows = 1 }: {
  value: string; onChange: (v: string) => void; className?: string; placeholder?: string; onFocus?: () => void; selected?: boolean; rows?: number
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) { el.style.height = '0px'; el.style.height = el.scrollHeight + 'px' }
  }, [value])
  return (
    <textarea ref={ref} rows={rows} value={value} placeholder={placeholder} onFocus={onFocus} onChange={(e) => onChange(e.target.value)}
      className={cx('block w-full resize-none overflow-hidden rounded border border-transparent bg-transparent px-1.5 py-0.5 leading-relaxed text-ink outline-none transition-colors hover:border-line focus:border-accent/50 focus:bg-surface', selected && 'border-accent/40 bg-accent-soft/30', className)} />
  )
}

function HeaderEditor({ s, local, onText, onLink, flash }: {
  s: EditorState; local: Record<string, string>; onText: (id: string, t: string) => void; onLink: (kind: string, mode: string) => void; flash: string[]
}) {
  const h = s.content.header
  const kinds = ['github', 'linkedin', 'portfolio'] as const
  return (
    <Card className="p-4">
      <Flash on={flash.includes('header.name')}>
        <AutoText value={local['header.name'] ?? h?.name ?? ''} onChange={(v) => onText('header.name', v)} className="text-center font-serif text-[22px] font-semibold" />
      </Flash>
      <div className="mt-2 flex flex-wrap items-center justify-center gap-1.5">
        {kinds.map((k) => {
          const lk = h?.links?.find((x) => x.kind === k)
          const url = lk?.url || s.profile_links[k]
          if (!url) return null
          const shown = lk ? (lk.mode === 'include' || (lk.mode === 'template' && lk.in_template)) : false
          return (
            <Flash key={k} on={flash.includes(`header.link.${k}`)}>
              <button onClick={() => onLink(k, shown ? 'exclude' : 'include')} title={shown ? `Remove the ${k} link` : `Show the ${k} link`}
                className={cx('flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[12px] transition-colors',
                  shown ? 'border-accent/30 bg-accent-soft/50 text-ink' : 'border-dashed border-line-strong text-muted line-through decoration-faint')}>
                {k === 'github' ? 'GitHub' : k === 'linkedin' ? 'LinkedIn' : 'Portfolio'}
                {shown ? <X className="size-3" /> : <Plus className="size-3" />}
              </button>
            </Flash>
          )
        })}
      </div>
      <p className="mt-2 text-center text-[11.5px] text-faint">Email and phone come from your template. Links toggle for this resume only.</p>
    </Card>
  )
}

function SectionEditor({ sec, local, flash, selection, setSelection, onText, onOp }: {
  sec: EditorSection; local: Record<string, string>; flash: string[]; selection: string[]
  setSelection: (s: string[]) => void; onText: (id: string, t: string) => void; onOp: (op: Op) => void
}) {
  const title = sec.title_text ?? sec.title
  if (sec.removed) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-dashed border-line-strong px-4 py-2.5 text-[13px] text-muted">
        <span className="line-through">{title}</span>
        <Button size="sm" variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={() => onOp({ op: 'restore_section', id: sec.id })}>Restore section</Button>
      </div>
    )
  }
  return (
    <Card className="p-4">
      <div className="mb-2 flex items-center gap-2 border-b border-line pb-1.5">
        <Flash on={flash.includes(sec.title_id)} className="flex-1">
          <input value={local[sec.title_id] ?? title} onChange={(e) => onText(sec.title_id, e.target.value)} aria-label="Section title"
            className="w-full rounded border border-transparent bg-transparent px-1 font-serif text-[15.5px] font-semibold tracking-wide outline-none hover:border-line focus:border-accent/50" />
        </Flash>
        {sec.unchanged && <Badge>As in template</Badge>}
        <button onClick={() => onOp({ op: 'remove_section', id: sec.id })} className="rounded p-1 text-faint hover:bg-missing-soft hover:text-missing" title="Remove section" aria-label="Remove section">
          <Trash2 className="size-3.5" />
        </button>
      </div>
      {sec.layout === 'paragraph' && sec.text_id && (
        <Flash on={flash.includes(sec.text_id)}>
          <AutoText value={local[sec.text_id] ?? sec.text ?? ''} onChange={(v) => onText(sec.text_id!, v)} className="text-[13.5px]"
            onFocus={() => setSelection([sec.text_id!])} selected={selection.includes(sec.text_id)} />
        </Flash>
      )}
      {sec.layout === 'entries' && (sec.entries ?? []).map((e, i) => (
        <EntryEditor key={e.id} e={e} i={i} count={sec.entries!.length} kind={sec.kind} local={local} flash={flash}
          selection={selection} setSelection={setSelection} onText={onText} onOp={onOp} />
      ))}
      {sec.layout === 'entries' && (sec.removed_entries ?? []).map((e) => (
        <div key={e.id} className="mt-1 flex items-center justify-between text-[12.5px] text-muted">
          <span className="line-through">{e.fields.primary}</span>
          <button className="underline" onClick={() => onOp({ op: 'restore_entry', id: e.id })}>Restore</button>
        </div>
      ))}
      {sec.layout === 'skills' && (sec.categories ?? []).map((c) => (
        <Flash key={c.id} on={flash.includes(c.id)}>
          <div className="grid items-start gap-1 py-0.5 sm:grid-cols-[150px_1fr]">
            <span className="px-1.5 pt-1 text-[12.5px] font-medium text-ink-2">{c.name}</span>
            <SkillsInput key={c.items.join('\u0000')} value={c.items} onCommit={(items) => onOp({ op: 'set_skills', id: c.id, items })} />
          </div>
        </Flash>
      ))}
      {sec.layout === 'items' && (sec.items ?? []).map((t, i) => {
        const iid = sec.item_ids?.[i] ?? `${sec.id}.i${i + 1}`
        return (
          <Flash key={iid} on={flash.includes(iid)}>
            <div className="flex gap-1.5"><span className="mt-2 size-1 shrink-0 rounded-full bg-ink-2" />
              <AutoText value={local[iid] ?? t} onChange={(v) => onText(iid, v)} className="text-[13px]" onFocus={() => setSelection([iid])} selected={selection.includes(iid)} />
            </div>
          </Flash>
        )
      })}
      {sec.layout === 'table' && <p className="text-[12.5px] text-muted">This table is kept exactly as in your template.</p>}
    </Card>
  )
}

function SkillsInput({ value, onCommit }: { value: string[]; onCommit: (items: string[]) => void }) {
  const [text, setText] = useState(value.join(', '))
  const commit = () => {
    const items = text.split(',').map((x) => x.trim()).filter(Boolean)
    if (items.join(', ') !== value.join(', ')) onCommit(items)
  }
  return <input value={text} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && commit()}
    className="w-full rounded border border-transparent bg-transparent px-1.5 py-0.5 text-[13px] outline-none hover:border-line focus:border-accent/50 focus:bg-surface" />
}

function EntryEditor({ e, i, count, kind, local, flash, selection, setSelection, onText, onOp }: {
  e: EditorEntry; i: number; count: number; kind: string; local: Record<string, string>; flash: string[]; selection: string[]
  setSelection: (s: string[]) => void; onText: (id: string, t: string) => void; onOp: (op: Op) => void
}) {
  const field = (role: string, cls: string, placeholder: string, fit = false) => e.fields[role] !== undefined && (
    <input value={local[`${e.id}.field.${role}`] ?? e.fields[role]} placeholder={placeholder} aria-label={placeholder}
      size={fit ? Math.max(8, (local[`${e.id}.field.${role}`] ?? e.fields[role]).length + 1) : undefined}
      onChange={(ev) => onText(`${e.id}.field.${role}`, ev.target.value)}
      className={cx('min-w-0 rounded border border-transparent bg-transparent px-1.5 py-0.5 outline-none hover:border-line focus:border-accent/50 focus:bg-surface', cls)} />
  )
  const contentBullets = (e.bullets ?? []).filter((b) => !b.tech_line)
  return (
    <motion.div layout transition={{ duration: 0.2 }} className="group/entry mt-3 first:mt-1">
      <Flash on={flash.includes(e.id) || flash.includes('all')}>
        <div className="flex items-center gap-1">
          {field('primary', 'min-w-0 flex-1 text-[13.5px] font-semibold', 'Title')}
          {(kind === 'projects' || kind === 'experience') && (
            <div className="flex opacity-0 transition-opacity group-hover/entry:opacity-100">
              <button disabled={i === 0} onClick={() => onOp({ op: 'move_entry', id: e.id, position: i - 1 })} className="rounded p-1 text-faint hover:text-ink disabled:opacity-30" aria-label="Move up"><ArrowUp className="size-3.5" /></button>
              <button disabled={i === count - 1} onClick={() => onOp({ op: 'move_entry', id: e.id, position: i + 1 })} className="rounded p-1 text-faint hover:text-ink disabled:opacity-30" aria-label="Move down"><ArrowDown className="size-3.5" /></button>
              {count > 1 && <button onClick={() => onOp({ op: 'remove_entry', id: e.id })} className="rounded p-1 text-faint hover:text-missing" aria-label="Remove entry"><Trash2 className="size-3.5" /></button>}
            </div>
          )}
        </div>
        {e.fields.secondary !== undefined && (
          <div className="flex text-[12.5px] italic text-ink-2">{field('secondary', 'min-w-0 flex-1', 'Role / degree')}</div>
        )}
        {(e.fields.dates !== undefined || e.fields.location !== undefined) && (
          <div className="flex flex-wrap items-center text-[12px] text-muted">
            {field('dates', 'max-w-full', 'Dates', true)}
            {e.fields.dates !== undefined && e.fields.location !== undefined && <span aria-hidden className="text-faint">·</span>}
            {field('location', 'max-w-full', 'Location', true)}
          </div>
        )}
      </Flash>
      {e.bullets && (
        <ul className="mt-1 space-y-0.5">
          <AnimatePresence initial={false}>
            {e.bullets.map((b, bi) => (
              <BulletEditor key={b.id} b={b} bi={bi} last={contentBullets.length} entryId={e.id} local={local} flash={flash}
                selected={selection.includes(b.id)} onSelect={() => setSelection([b.id])} onText={onText} onOp={onOp}
                canRemove={b.tech_line || contentBullets.length > 1} />
            ))}
          </AnimatePresence>
          <li>
            <button onClick={() => { const t = prompt('New bullet'); if (t?.trim()) onOp({ op: 'add_bullet', id: e.id, text: t.trim() }) }}
              className="ml-4 mt-0.5 flex items-center gap-1 text-[12px] text-faint opacity-0 hover:text-ink group-hover/entry:opacity-100">
              <Plus className="size-3" />Add bullet
            </button>
          </li>
        </ul>
      )}
    </motion.div>
  )
}

function BulletEditor({ b, bi, last, entryId, local, flash, selected, onSelect, onText, onOp, canRemove }: {
  b: EditorBullet; bi: number; last: number; entryId: string; local: Record<string, string>; flash: string[]; selected: boolean
  onSelect: () => void; onText: (id: string, t: string) => void; onOp: (op: Op) => void; canRemove: boolean
}) {
  const o = ORIGIN[b.origin] ?? ORIGIN.profile
  void entryId
  return (
    <motion.li layout initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="group/b flex items-start gap-1">
      <span className={cx('mt-[9px] size-1.5 shrink-0 rounded-full', o.cls)} title={o.label} />
      <Flash on={flash.includes(b.id) || flash.includes('all')} className="min-w-0 flex-1">
        <AutoText value={local[b.id] ?? b.text.replace(/\*\*/g, '')} onChange={(v) => onText(b.id, v)} onFocus={onSelect} selected={selected}
          className={cx('text-[13px]', b.tech_line && 'text-ink-2')} />
      </Flash>
      <div className="flex shrink-0 opacity-0 transition-opacity group-hover/b:opacity-100">
        {!b.tech_line && bi > 0 && <button onClick={() => onOp({ op: 'move_bullet', id: b.id, position: bi - 1 })} className="rounded p-0.5 text-faint hover:text-ink" aria-label="Move bullet up"><ArrowUp className="size-3" /></button>}
        {!b.tech_line && bi < last - 1 && <button onClick={() => onOp({ op: 'move_bullet', id: b.id, position: bi + 1 })} className="rounded p-0.5 text-faint hover:text-ink" aria-label="Move bullet down"><ArrowDown className="size-3" /></button>}
        {canRemove && <button onClick={() => onOp({ op: 'remove_bullet', id: b.id })} className="rounded p-0.5 text-faint hover:text-missing" aria-label="Remove bullet"><Trash2 className="size-3" /></button>}
      </div>
    </motion.li>
  )
}

// --- live preview --------------------------------------------------------------------------------

function LivePdf({ vid, revision, pages, saving }: { vid: number; revision: number; pages: number | null; saving: boolean }) {
  // Page images rendered from the actual PDF: identical output on every browser and phone.
  const [shown, setShown] = useState(revision)
  const loaded = useRef({ rev: revision, n: 0 })
  const count = Math.max(1, pages ?? 1)
  const onLoad = (rev: number) => {
    if (loaded.current.rev !== rev) loaded.current = { rev, n: 0 }
    loaded.current.n += 1
    if (loaded.current.n >= count) setShown(rev)
  }
  const src = (rev: number, p: number) => `/api/resumes/${vid}/preview/${p}.png?rev=${rev}`
  return (
    <div className="relative h-full overflow-y-auto rounded-md">
      <div className="mx-auto max-w-[820px] space-y-3">
        {Array.from({ length: count }).map((_, i) => (
          <div key={i} className="relative overflow-hidden rounded-sm bg-white shadow-float">
            <img src={src(shown, i + 1)} alt={`Page ${i + 1}`} className="block w-full" />
            {shown !== revision && <img src={src(revision, i + 1)} alt="" onLoad={() => onLoad(revision)} className="absolute inset-0 w-full opacity-0" />}
          </div>
        ))}
      </div>
      <AnimatePresence>{(saving || shown !== revision) && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="sticky bottom-2 ml-auto mr-2 flex w-fit items-center gap-1 rounded bg-ink/80 px-2 py-0.5 text-[11px] text-bg">
          <Loader2 className="size-3 animate-spin" />Updating preview
        </motion.div>
      )}</AnimatePresence>
      <a href={`/api/resumes/${vid}/pdf`} target="_blank" rel="noreferrer" className="sticky bottom-2 mt-2 block w-fit rounded bg-surface/90 px-2 py-0.5 text-[11px] text-muted shadow-card">Open PDF</a>
    </div>
  )
}

// --- assistant -----------------------------------------------------------------------------------

type AssistantResult = {
  applied: boolean; summary: string[]; message: string; refused: string[]; ops: Op[]; broad: boolean; ai?: string
  preview?: { id: string; before: string; after: string }[]; state?: EditorState; changed?: string[]
}

const SUGGESTIONS = ['Make the summary 2 lines', 'Put Resolve first', 'Remove the portfolio link', 'Make the projects section more concise', 'Make the resume exactly one page']

function AssistantPanel({ vid, state, selection, clearSelection, flush, onApplied }: {
  vid: number; state: EditorState; selection: string[]; clearSelection: () => void; flush: () => Promise<boolean>
  onApplied: (s: EditorState, changed?: string[]) => void
}) {
  const [text, setText] = useState('')
  const [result, setResult] = useState<(AssistantResult & { instruction: string }) | null>(null)
  const toast = useToast()
  const selected = selection[0] ? state.elements[selection[0]] : null
  const run = useMutation({
    mutationFn: async (instruction: string) => {
      await flush()
      return post<AssistantResult>(`/api/resumes/${vid}/assistant`, { instruction, selection, base_revision: state.revision })
    },
    onSuccess: (r, instruction) => {
      setResult({ ...r, instruction })
      if (r.applied && r.state) { onApplied(r.state, r.changed); setText('') }
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const confirm = useMutation({
    mutationFn: () => post<AssistantResult>(`/api/resumes/${vid}/assistant`, { instruction: result!.instruction, ops: result!.ops, base_revision: state.revision }),
    onSuccess: (r) => { if (r.state) onApplied(r.state, r.changed); setResult({ ...result!, applied: true, preview: undefined }); setText('') },
    onError: (e) => toast(errorText(e), 'error'),
  })
  return (
    <div className="space-y-3 p-4">
      <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) run.mutate(text.trim()) }}>
        <div className="mb-1.5 flex items-center justify-between text-[11.5px] text-muted">
          <span>{selected ? <>Scope: <span className="text-ink">selected {selected.type === 'paragraph' ? 'summary' : selected.type}</span></> : 'Scope: decided from your request'}</span>
          {selected && <button type="button" onClick={clearSelection} className="underline">Clear</button>}
        </div>
        <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Tell the editor what to change…"
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (text.trim()) run.mutate(text.trim()) } }} />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[11px] text-faint">Changes only what you ask. Your own edits are kept.</span>
          <Button type="submit" size="sm" variant="primary" loading={run.isPending} disabled={!text.trim()}>Make change</Button>
        </div>
      </form>
      {!result && (
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => <button key={s} onClick={() => run.mutate(s)} className="rounded border border-line px-2 py-0.5 text-[11.5px] text-muted hover:border-line-strong hover:text-ink">{s}</button>)}
        </div>
      )}
      <AnimatePresence mode="wait">
        {result && (
          <motion.div key={result.instruction + String(result.applied)} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="rounded-md border border-line bg-surface-2/40 p-3 text-[12.5px]">
            <div className="mb-1 text-[11.5px] text-muted">“{result.instruction}”</div>
            {result.applied ? (
              <div className="space-y-0.5">{result.summary.map((x) => <div key={x} className="flex items-center gap-1.5"><CheckCircle2 className="size-3.5 text-strong" />{x}</div>)}</div>
            ) : result.preview ? (
              <div>
                <div className="mb-1 font-medium text-ink">Changes proposed</div>
                <ul className="mb-2 space-y-0.5">{result.summary.map((x) => <li key={x}>• {x}</li>)}</ul>
                <div className="max-h-56 space-y-1.5 overflow-y-auto">
                  {result.preview.filter((p) => p.before !== p.after).slice(0, 12).map((p) => (
                    <div key={p.id} className="rounded bg-surface p-1.5">
                      {p.before && <div className="text-missing line-through decoration-missing/40">{p.before}</div>}
                      {p.after && <div className="text-ink">{p.after}</div>}
                    </div>
                  ))}
                </div>
                {result.message && <p className="mt-1.5 text-muted">{result.message}</p>}
                <div className="mt-2 flex justify-end gap-2">
                  <Button size="sm" variant="ghost" onClick={() => setResult(null)}>Cancel</Button>
                  <Button size="sm" variant="primary" loading={confirm.isPending} onClick={() => confirm.mutate()}>Apply changes</Button>
                </div>
              </div>
            ) : (
              <p className="text-ink-2">{result.message}</p>
            )}
            {result.refused.map((r) => <p key={r} className="mt-1 flex gap-1.5 text-verify"><Info className="mt-0.5 size-3.5 shrink-0" />{r}</p>)}
            {result.ai && <p className="mt-1.5 text-[11.5px] text-faint">{result.ai}</p>}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// --- quality / history / conflicts ----------------------------------------------------------------

const STATUS_STYLE: Record<QualityCheck['status'], { icon: React.ReactNode; tone: string }> = {
  pass: { icon: <CheckCircle2 className="size-3.5 text-strong" />, tone: 'text-strong' },
  review: { icon: <Info className="size-3.5 text-partial" />, tone: 'text-partial' },
  fail: { icon: <AlertTriangle className="size-3.5 text-missing" />, tone: 'text-missing' },
}

function QualityPanel({ s, vid, onResolved }: { s: EditorState; vid: number; onResolved: (st: EditorState) => void }) {
  const qr = s.quality_report
  const toast = useToast()
  const resolve = useMutation({
    mutationFn: async (option: string) => { await post(`/api/resumes/${vid}/resolve-length`, { option }); return get<EditorState>(`/api/resumes/${vid}/editor`) },
    onSuccess: onResolved,
    onError: (e) => toast(errorText(e), 'error'),
  })
  const order = useMemo(() => [...(qr?.checks ?? [])].sort((a, b) => ['fail', 'review', 'pass'].indexOf(a.status) - ['fail', 'review', 'pass'].indexOf(b.status)), [qr])
  if (!qr?.checks) return <p className="p-4 text-[12.5px] text-muted">No checks yet.</p>
  return (
    <div className="space-y-3 p-4">
      <div className={cx('rounded-md px-3 py-2 text-[12.5px]', qr.blocked ? 'bg-missing-soft/60 text-ink' : 'bg-strong-soft text-ink')}>
        {qr.blocked ? 'Export blocked until the failing checks pass.' : 'All required checks pass. The PDF can be exported.'}
      </div>
      {qr.length_conflict && (
        <div className="rounded-md border border-partial/30 bg-partial-soft p-3 text-[12.5px]">
          <div className="font-medium">{qr.length_conflict.target}-page limit: the resume is {qr.length_conflict.pages} pages</div>
          <div className="mt-1 text-ink-2">Choose how to fit it — nothing is removed without your choice.</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {qr.length_conflict.options.map((o) => <Button key={o.id} size="sm" loading={resolve.isPending && resolve.variables === o.id} onClick={() => resolve.mutate(o.id)}>{o.label}</Button>)}
          </div>
        </div>
      )}
      <ul className="space-y-2">
        {order.map((c) => (
          <li key={c.id} className="text-[12.5px]">
            <div className="flex items-center gap-1.5">{STATUS_STYLE[c.status].icon}<span className="font-medium text-ink">{c.label}</span>
              <span className={cx('ml-auto text-[11px] font-medium uppercase', STATUS_STYLE[c.status].tone)}>{c.status}</span></div>
            {c.detail && <div className="ml-5 text-ink-2">{c.detail}</div>}
            {c.items.length > 0 && <ul className="ml-5 mt-0.5 list-disc pl-4 text-muted">{c.items.slice(0, 6).map((i) => <li key={i}>{i}</li>)}</ul>}
          </li>
        ))}
      </ul>
      <p className="text-[11px] text-faint">These are concrete checks, not a score. No check predicts hiring outcomes.</p>
    </div>
  )
}

function HistoryPanel({ s, onRestore }: { s: EditorState; onRestore: (n: number) => void }) {
  return (
    <ol className="divide-y divide-line">
      {s.history.map((h) => (
        <li key={h.number} className={cx('flex items-start gap-2 px-4 py-2.5 text-[12.5px]', h.current && 'bg-accent-soft/40')}>
          <span className="mt-0.5 w-7 shrink-0 font-mono text-[11px] text-muted">v{h.number}</span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-ink">{h.label}{h.current && <span className="text-accent"> · current</span>}</div>
            <div className="text-[11px] text-faint">{h.source === 'assistant' ? 'Assistant' : h.source === 'user' ? 'You' : h.source === 'restore' ? 'Restore' : 'Generated'} · {relative(h.created_at)}{h.page_count ? ` · ${h.page_count}p` : ''}</div>
          </div>
          {!h.current && <button onClick={() => onRestore(h.number)} className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[11px] text-muted hover:text-ink" title={fmtDateTime(h.created_at)}>Restore</button>}
        </li>
      ))}
    </ol>
  )
}

function ConflictDialog({ open, ops, vid, onKeep, onNewer }: { open: boolean; ops: Op[]; vid: number; onKeep: () => void; onNewer: () => void }) {
  const [compare, setCompare] = useState(false)
  const latest = useQuery({ queryKey: ['editor-latest', vid, open], queryFn: () => get<EditorState>(`/api/resumes/${vid}/editor`), enabled: open && compare })
  return (
    <Dialog open={open} onClose={() => undefined} title="This resume was changed somewhere else"
      footer={<><Button variant="ghost" onClick={() => setCompare(!compare)}>Compare changes</Button><Button onClick={onNewer}>Use newer version</Button><Button variant="primary" onClick={onKeep}>Keep my changes</Button></>}>
      <p className="text-[13px] text-ink-2">Another tab or device saved a newer version. Keep your changes (they are applied on top of the newer version) or discard them and load the newer version.</p>
      {compare && (
        <div className="mt-3 grid gap-3 text-[12px] sm:grid-cols-2">
          <div><div className="mb-1 font-medium">Your unsaved changes</div><ul className="space-y-1">{ops.map((o, i) => <li key={i} className="rounded bg-surface-2 p-1.5">{o.op}{o.text ? `: ${String(o.text).slice(0, 80)}` : ''}</li>)}</ul></div>
          <div><div className="mb-1 font-medium">Newer version</div>{latest.data ? <ul className="space-y-1">{latest.data.history.slice(0, 5).map((h) => <li key={h.number} className="rounded bg-surface-2 p-1.5">v{h.number}: {h.label}</li>)}</ul> : <Spinner />}</div>
        </div>
      )}
    </Dialog>
  )
}

export function PdfPreview({ url, available = true, height = 'h-[760px]' }: { url: string; available?: boolean; height?: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => {
    if (!available) return
    let made: string | null = null
    const ctrl = new AbortController()
    api<Blob>(url, { signal: ctrl.signal }).then((b) => { made = URL.createObjectURL(b); setBlobUrl(made) }).catch((e) => { if ((e as Error).name !== 'AbortError') setErr(e) })
    return () => { ctrl.abort(); if (made) URL.revokeObjectURL(made) }
  }, [url, available])
  if (!available) return <div className={cx('flex items-center justify-center rounded-lg border border-dashed border-line-strong text-[13px] text-muted', height)}>No PDF yet.</div>
  if (err) return <ErrorState error={err} />
  return (
    <div className={cx('overflow-hidden rounded-lg border border-line bg-surface-3 shadow-card', height)}>
      {blobUrl ? <iframe title="PDF preview" src={blobUrl + '#view=FitH&toolbar=0'} className="h-full w-full" /> : <div className="flex h-full items-center justify-center"><Spinner /></div>}
    </div>
  )
}
