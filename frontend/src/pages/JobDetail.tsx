import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import {
  Archive, ChevronDown, ExternalLink, FileText, Hammer, Pencil, RefreshCw, Send, Sparkles,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { Page } from '../components/AppShell'
import {
  Badge, Button, Card, CardHeader, Dialog, ErrorState, EvidenceBadge, Field, Input, LoadingRows, MatchBadge, Select,
  StatusBadge, TaskProgress, cx, errorText, useToast,
} from '../components/ui'
import { ApiError, del, get, patch, post, put } from '../lib/api'
import { daysLabel, fmtDate, STATUS_LABEL, STATUS_ORDER } from '../lib/format'
import type { AppStatus, JobDetail as Job, Requirement, Suggestion, Template } from '../lib/types'
import { useTask } from '../lib/useTask'

export default function JobDetail() {
  const { id } = useParams()
  const [params, setParams] = useSearchParams()
  const taskId = Number(params.get('task')) || null
  const qc = useQueryClient()
  const job = useQuery({
    queryKey: ['job', id],
    queryFn: () => get<Job>(`/api/jobs/${id}`),
    refetchInterval: (q) => (q.state.data && ['pending', 'running'].includes(q.state.data.analysis_state) ? 1500 : false),
  })
  const task = useTask(taskId, [['job', id], ['jobs'], ['dashboard']], (t) => {
    if (t.status === 'succeeded') setTimeout(() => setParams({}, { replace: true }), 900)
  })

  if (job.isLoading) return <Page><LoadingRows rows={8} /></Page>
  if (job.error) return <Page><ErrorState error={job.error} onRetry={() => job.refetch()} /></Page>
  const j = job.data!
  const analyzing = j.analysis_state === 'pending' || j.analysis_state === 'running' || (task && task.status !== 'succeeded' && task.status !== 'failed')

  return (
    <Page>
      <JobHeader j={j} />
      <AnimatePresence>
        {analyzing && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="mb-6 overflow-hidden">
            <Card className="p-5">
              <div className="mb-3 text-[13.5px] font-semibold">Analyzing job…</div>
              <TaskProgress steps={task?.steps ?? [{ label: 'Reading the job', state: 'running' }]} status={task?.status} />
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
      {j.analysis_state === 'failed' && (
        <div className="mb-6">
          <ErrorState
            error={new ApiError(422, { code: 'analysis_failed', message: 'The job page could not be analyzed.', hint: j.analysis_error, actions: ['retry', 'open_manually', 'paste_description'] })}
            onRetry={async () => { const r = await post<{ task_id: number }>(`/api/jobs/${j.id}/reanalyze`); setParams({ task: String(r.task_id) }); qc.invalidateQueries({ queryKey: ['job', id] }) }}
            openUrl={j.job_url || undefined}
            onAction={(a) => a === 'paste_description' && (window.location.href = '/jobs/new?mode=paste')}
          />
        </div>
      )}
      {j.analysis_state === 'ready' && <Analysis j={j} />}
    </Page>
  )
}

function JobHeader({ j }: { j: Job }) {
  const [editing, setEditing] = useState(false)
  const qc = useQueryClient()
  const nav = useNavigate()
  const toast = useToast()
  const status = useMutation({
    mutationFn: (s: AppStatus) => patch(`/api/applications/${j.application!.id}`, { status: s }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['job', String(j.id)] }); qc.invalidateQueries({ queryKey: ['dashboard'] }) },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const archive = useMutation({
    mutationFn: () => del(`/api/jobs/${j.id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['jobs'] }); nav('/jobs') },
  })
  const meta = [
    j.job_code && { label: 'Job ID', value: <span className="font-mono text-[12.5px]">{j.job_code}</span> },
    { label: 'Location', value: <>{j.location || '—'}{j.workplace_type ? <span className="text-muted"> · {j.workplace_type}</span> : ''}</> },
    j.employment_type && { label: 'Type', value: j.employment_type },
    {
      label: 'Deadline', value: j.deadline
        ? <span title={j.deadline_source} className={cx((j.days_remaining ?? 99) <= 3 ? 'text-missing' : '')}>{fmtDate(j.deadline)} <span className="text-muted">· {daysLabel(j.days_remaining)}</span></span>
        : <span className="text-muted">Not specified</span>,
    },
  ].filter(Boolean) as { label: string; value: React.ReactNode }[]

  return (
    <div className="mb-6">
      <div className="mb-1.5 flex items-center gap-2 text-[12px] font-medium uppercase tracking-[0.08em] text-muted">
        <Link to="/jobs" className="hover:text-ink">Jobs</Link><span>/</span><span className="normal-case tracking-normal">{j.company || 'Unknown company'}</span>
      </div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-serif text-[28px] font-semibold leading-tight tracking-[-0.01em] md:text-[32px]">{j.role_title || 'Untitled role'}</h1>
          <div className="mt-1 text-[15px] text-ink-2">{j.company}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {j.application && (
            <Select aria-label="Application status" value={j.application.status} onChange={(e) => status.mutate(e.target.value as AppStatus)} className="h-8.5 w-auto text-[13px]">
              {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
            </Select>
          )}
          <Button size="md" variant="ghost" icon={<Pencil className="size-3.5" />} onClick={() => setEditing(true)}>Edit</Button>
          {(j.application_url || j.job_url) && (
            <a href={j.application_url || j.job_url} target="_blank" rel="noreferrer noopener">
              <Button icon={<ExternalLink className="size-3.5" />}>Open application</Button>
            </a>
          )}
          <Button variant="ghost" icon={<Archive className="size-3.5" />} onClick={() => archive.mutate()} aria-label="Archive job" />
        </div>
      </div>
      <dl className="mt-4 flex flex-wrap gap-x-7 gap-y-2 border-y border-line py-3">
        {meta.map((m) => (
          <div key={m.label} className="min-w-0">
            <dt className="text-[11.5px] uppercase tracking-[0.06em] text-muted">{m.label}</dt>
            <dd className="mt-0.5 text-[13.5px]">{m.value}</dd>
          </div>
        ))}
        {j.application && <div><dt className="text-[11.5px] uppercase tracking-[0.06em] text-muted">Status</dt><dd className="mt-1"><StatusBadge status={j.application.status} /></dd></div>}
      </dl>
      <EditJobDialog j={j} open={editing} onClose={() => setEditing(false)} />
    </div>
  )
}

function EditJobDialog({ j, open, onClose }: { j: Job; open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ company: j.company, role_title: j.role_title, job_code: j.job_code, location: j.location, application_url: j.application_url, deadline: j.deadline ?? '' })
  const m = useMutation({
    mutationFn: () => patch(`/api/jobs/${j.id}`, {
      company: form.company, role_title: form.role_title, job_code: form.job_code, location: form.location,
      application_url: form.application_url || null, deadline: form.deadline || null, clear_deadline: !form.deadline && !!j.deadline,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['job', String(j.id)] }); onClose() },
  })
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value })
  return (
    <Dialog open={open} onClose={onClose} title="Edit job details"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
      <div className="grid gap-3.5 sm:grid-cols-2">
        <Field label="Company"><Input value={form.company} onChange={set('company')} /></Field>
        <Field label="Role"><Input value={form.role_title} onChange={set('role_title')} /></Field>
        <Field label="Job ID"><Input value={form.job_code} onChange={set('job_code')} /></Field>
        <Field label="Location"><Input value={form.location} onChange={set('location')} /></Field>
        <Field label="Application URL" className="sm:col-span-2"><Input value={form.application_url} onChange={set('application_url')} /></Field>
        <Field label="Deadline" hint={j.deadline_source ? `Current source: ${j.deadline_source}` : 'Only set a deadline the posting actually states.'} className="sm:col-span-2">
          <Input type="date" value={form.deadline} onChange={set('deadline')} />
        </Field>
      </div>
      {m.error && <div className="mt-3"><ErrorState error={m.error} compact /></div>}
    </Dialog>
  )
}

const GROUPS: { key: string; title: string; filter: (r: Requirement) => boolean }[] = [
  { key: 'required', title: 'Must have', filter: (r) => r.kind === 'skill' && r.importance === 'required' },
  { key: 'preferred', title: 'Good to have', filter: (r) => r.kind === 'skill' && r.importance === 'preferred' },
  { key: 'other', title: 'Experience, education & certifications', filter: (r) => ['experience', 'education', 'certification'].includes(r.kind) },
  { key: 'keyword', title: 'Also mentioned', filter: (r) => (r.kind === 'skill' && r.importance === 'keyword') || r.kind === 'domain' },
]

function Analysis({ j }: { j: Job }) {
  const req = j.requirements.filter((r) => r.importance === 'required' && r.match)
  const strong = req.filter((r) => r.match!.status === 'strong').length
  const missing = j.requirements.filter((r) => r.match?.status === 'missing' && r.kind === 'skill' && r.importance !== 'keyword')
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0 space-y-6">
        <Card>
          <CardHeader title="JD analysis" subtitle={req.length ? `${strong} of ${req.length} required items are backed by your evidence.` : 'No required skills were recognised in this posting.'}
            actions={<RematchButton jobId={j.id} />} />
          {j.analysis_notes.length > 0 && (
            <div className="border-b border-line bg-surface-2/40 px-4 py-2 text-[12.5px] text-muted">
              {j.analysis_notes.map((n, i) => <div key={i}>{n}</div>)}
            </div>
          )}
          <div className="divide-y divide-line">
            {GROUPS.map((g) => {
              const rows = j.requirements.filter(g.filter)
              if (!rows.length) return null
              return (
                <section key={g.key}>
                  <div className="bg-surface-2/40 px-4 py-2 text-[11.5px] font-medium uppercase tracking-[0.06em] text-muted">{g.title} <span className="text-faint">· {rows.length}</span></div>
                  <ul className="divide-y divide-line">{rows.map((r) => <RequirementRow key={r.id} r={r} />)}</ul>
                </section>
              )
            })}
          </div>
        </Card>
        <Card>
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3">
              <span className="text-[13.5px] font-semibold">Job description</span>
              <ChevronDown className="size-4 text-muted transition-transform group-open:rotate-180" />
            </summary>
            <div className="prose-jd border-t border-line px-4 py-4 text-[13px]">{j.description_text}</div>
          </details>
        </Card>
      </div>
      <div className="space-y-6">
        <ResumePanel j={j} />
        <ProjectsPanel j={j} />
        <GapsPanel j={j} missing={missing} />
        {j.responsibilities.length > 0 && (
          <Card>
            <CardHeader title="Responsibilities" />
            <ul className="space-y-1.5 px-4 py-3 text-[13px] text-ink-2">
              {j.responsibilities.slice(0, 8).map((r, i) => <li key={i} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-faint" />{r}</li>)}
            </ul>
          </Card>
        )}
      </div>
    </div>
  )
}

function RematchButton({ jobId }: { jobId: number }) {
  const qc = useQueryClient()
  const m = useMutation({ mutationFn: () => post(`/api/jobs/${jobId}/rematch`), onSuccess: () => qc.invalidateQueries({ queryKey: ['job', String(jobId)] }) })
  return <Button size="sm" variant="ghost" icon={<RefreshCw className={cx('size-3.5', m.isPending && 'animate-spin')} />} onClick={() => m.mutate()} title="Re-check against your latest profile and projects">Re-check</Button>
}

function RequirementRow({ r }: { r: Requirement }) {
  const [open, setOpen] = useState(false)
  return (
    <li>
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-surface-2/40" aria-expanded={open}>
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-medium text-ink">{r.label}</div>
          {r.match && <div className="mt-0.5 text-[12.5px] text-muted">{r.match.rationale}</div>}
        </div>
        <div className="shrink-0 pt-0.5">{r.match && <MatchBadge status={r.match.status} />}</div>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18 }} className="overflow-hidden">
            <div className="space-y-2.5 px-4 pb-3.5">
              {r.snippet && <blockquote className="border-l-2 border-line-strong pl-3 text-[12.5px] italic text-muted">“{r.snippet}”</blockquote>}
              {r.match?.evidence.length ? (
                <ul className="space-y-1">
                  {r.match.evidence.map((e, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-2 text-[12.5px]">
                      <EvidenceBadge status={e.status} />
                      {e.type === 'project' ? <Link to={`/projects/${e.id}`} className="font-medium hover:text-accent">{e.label}</Link> : <span className="font-medium">{e.label}</span>}
                      {e.detail && <span className="text-muted">{e.detail}</span>}
                      {e.via && <span className="text-muted">via {e.via}</span>}
                    </li>
                  ))}
                </ul>
              ) : <div className="text-[12.5px] text-muted">No evidence on file.</div>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  )
}

function ResumePanel({ j }: { j: Job }) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => get<Template[]>('/api/templates') })
  const [taskId, setTaskId] = useState<number | null>(null)
  const [tpl, setTpl] = useState<string>('')
  const gen = useMutation({
    mutationFn: () => post<{ task_id: number }>(`/api/jobs/${j.id}/resumes`, tpl ? { template_id: Number(tpl) } : {}),
    onSuccess: (r) => setTaskId(r.task_id),
  })
  const task = useTask(taskId, [['job', String(j.id)], ['resumes'], ['dashboard']], (t) => {
    if (t.status === 'succeeded' && t.result.resume_version_id) nav(`/resumes/${t.result.resume_version_id}`)
  })
  const running = !!task && (task.status === 'queued' || task.status === 'running')
  const hasTemplate = (templates.data?.length ?? 0) > 0
  return (
    <Card>
      <CardHeader title="Tailored resume" subtitle={j.resumes.length ? `${j.resumes.length} version${j.resumes.length > 1 ? 's' : ''} for this job` : 'Generated in your own template.'} />
      <div className="space-y-3 px-4 py-3.5">
        {j.resumes.slice(0, 4).map((v) => (
          <Link key={v.id} to={`/resumes/${v.id}`} className="flex items-center justify-between gap-2 rounded-md border border-line px-3 py-2 text-[13px] hover:border-line-strong hover:bg-surface-2/40">
            <span className="flex min-w-0 items-center gap-2"><FileText className="size-3.5 shrink-0 text-muted" /><span className="truncate">Version {v.version_number}</span></span>
            <span className="flex items-center gap-2 text-[12px] text-muted">{v.page_count ? `${v.page_count}p` : ''}<Badge tone={v.status === 'approved' ? 'strong' : 'neutral'}>{v.status === 'approved' ? 'Approved' : 'Draft'}</Badge></span>
          </Link>
        ))}
        {running ? (
          <div className="rounded-md border border-line bg-surface-2/40 p-3"><TaskProgress steps={task!.steps} status={task!.status} /></div>
        ) : !hasTemplate && !templates.isLoading ? (
          <div className="rounded-md border border-dashed border-line-strong p-3 text-[12.5px] text-muted">
            Upload your resume template first. <Link to="/resumes/templates" className="font-medium text-accent hover:underline">Upload template</Link>
          </div>
        ) : (
          <div className="flex gap-2">
            {(templates.data?.length ?? 0) > 1 && (
              <Select value={tpl} onChange={(e) => setTpl(e.target.value)} className="h-8.5 min-w-0 flex-1 text-[13px]" aria-label="Template">
                <option value="">Default template</option>
                {templates.data!.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </Select>
            )}
            <Button variant="primary" className="flex-1" icon={<Sparkles className="size-3.5" />} loading={gen.isPending} onClick={() => gen.mutate()}>
              {j.resumes.length ? 'Generate new version' : 'Generate resume'}
            </Button>
          </div>
        )}
        {task?.status === 'failed' && <ErrorState compact error={new ApiError(400, task.result.error ?? { code: 'failed', message: task.error, hint: '', actions: ['retry'] })} onRetry={() => gen.mutate()} />}
        {gen.error && <ErrorState compact error={gen.error} onAction={(a) => a === 'open_templates' && nav('/resumes/templates')} />}
        {j.application && ['resume_ready', 'ready_to_apply', 'in_progress'].includes(j.application.status) && (
          <Link to={`/applications/${j.application.id}`} onClick={() => qc.invalidateQueries({ queryKey: ['application'] })}>
            <Button className="w-full" icon={<Send className="size-3.5" />}>Prepare application</Button>
          </Link>
        )}
      </div>
    </Card>
  )
}

function ProjectsPanel({ j }: { j: Job }) {
  const qc = useQueryClient()
  const initial = useMemo(() => new Set(j.recommended_projects.filter((p) => p.selected).map((p) => p.project_id)), [j.recommended_projects])
  const [sel, setSel] = useState<Set<number> | null>(null)
  const current = sel ?? initial
  const dirty = sel !== null && (sel.size !== initial.size || [...sel].some((x) => !initial.has(x)))
  const save = useMutation({
    mutationFn: () => put(`/api/jobs/${j.id}/projects`, { project_ids: [...current] }),
    onSuccess: () => { setSel(null); qc.invalidateQueries({ queryKey: ['job', String(j.id)] }) },
  })
  const toggle = (id: number) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSel(next)
  }
  return (
    <Card>
      <CardHeader title="Relevant projects" subtitle="Ranked by verified evidence for this job's requirements."
        actions={dirty && <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>} />
      {j.recommended_projects.length === 0 ? (
        <div className="px-4 py-4 text-[12.5px] text-muted">No project shows evidence for these requirements. <Link to="/projects" className="text-accent hover:underline">Review your projects</Link></div>
      ) : (
        <ul className="divide-y divide-line">
          {j.recommended_projects.map((p) => (
            <li key={p.project_id} className="flex gap-3 px-4 py-3">
              <input type="checkbox" className="mt-1 size-3.5 accent-[var(--accent)]" checked={current.has(p.project_id)} onChange={() => toggle(p.project_id)} aria-label={`Use ${p.title}`} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <Link to={`/projects/${p.project_id}`} className="truncate text-[13.5px] font-medium hover:text-accent">{p.title}</Link>
                  <span className="shrink-0 text-[11.5px] text-faint">#{p.rank}</span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {p.matched.slice(0, 7).map((m) => (
                    <Badge key={m.requirement_id} tone={m.evidence_status === 'inferred' ? 'verify' : m.importance === 'required' ? 'strong' : 'neutral'}>
                      {m.label}{m.evidence_status === 'inferred' ? ' ?' : ''}
                    </Badge>
                  ))}
                </div>
                {!p.has_bullets && <div className="mt-1.5 text-[11.5px] text-muted">No resume bullets yet; drafts will come from repository evidence.</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function GapsPanel({ j, missing }: { j: Job; missing: Requirement[] }) {
  const nav = useNavigate()
  const [picked, setPicked] = useState<string[]>([])
  const m = useMutation({
    mutationFn: () => post<Suggestion>(`/api/jobs/${j.id}/suggested-projects`, { gap_keys: picked }),
    onSuccess: (s) => nav(`/suggestions/${s.id}`),
  })
  if (!missing.length && !j.suggested_projects.length) return null
  return (
    <Card>
      <CardHeader title="Gaps" subtitle="Requirements with no evidence. These are not added to your resume." />
      <div className="space-y-2 px-4 py-3">
        {missing.map((r) => (
          <label key={r.id} className="flex items-center gap-2.5 text-[13px]">
            <input type="checkbox" className="size-3.5 accent-[var(--accent)]" checked={picked.includes(r.canonical)}
              onChange={(e) => setPicked(e.target.checked ? [...picked, r.canonical].slice(0, 3) : picked.filter((x) => x !== r.canonical))} />
            <span className="flex-1">{r.label}</span>
            <Badge tone={r.importance === 'required' ? 'missing' : 'neutral'}>{r.importance}</Badge>
          </label>
        ))}
        {missing.length > 0 && (
          <Button className="mt-1 w-full" icon={<Hammer className="size-3.5" />} disabled={!picked.length} loading={m.isPending} onClick={() => m.mutate()}>
            Create supporting project plan
          </Button>
        )}
        {m.error && <ErrorState compact error={m.error} />}
        {j.suggested_projects.map((s) => (
          <Link key={s.id} to={`/suggestions/${s.id}`} className="flex items-center justify-between rounded-md border border-line px-3 py-2 text-[12.5px] hover:bg-surface-2/40">
            <span className="truncate">{s.title}</span>
            <Badge tone={s.status === 'verified' ? 'strong' : 'partial'}>{s.status === 'verified' ? 'Verified' : 'Not yet built'}</Badge>
          </Link>
        ))}
      </div>
    </Card>
  )
}

