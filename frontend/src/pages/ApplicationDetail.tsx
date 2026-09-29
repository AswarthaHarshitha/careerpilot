import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle, CheckCircle2, ExternalLink, Eye, Loader2, Lock, Play, ShieldAlert, X,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { EventTimeline, PHASE_LABEL, PhaseSteps, RunnerCard } from '../components/AgentStatus'
import { Page } from '../components/AppShell'
import {
  Badge, Button, Card, CardHeader, Dialog, ErrorState, Field, Input, LoadingRows, Select, StatusBadge, Textarea, cx,
  errorText, useToast,
} from '../components/ui'
import { api, get, patch, post } from '../lib/api'
import { daysLabel, fmtDate, fmtDateTime, STATUS_LABEL, STATUS_ORDER } from '../lib/format'
import type { AppField, Application, AppStatus, ResumeVersion } from '../lib/types'

const ACTIVE = ['queued', 'running', 'action_required', 'awaiting_answers', 'filling', 'awaiting_review', 'submit_confirmed', 'submitting']

export default function ApplicationDetail() {
  const { id } = useParams()
  const q = useQuery({
    queryKey: ['application', id],
    queryFn: () => get<Application>(`/api/applications/${id}`),
    refetchInterval: (query) => (query.state.data?.session && ACTIVE.includes(query.state.data.session.state) ? 1500 : false),
  })
  if (q.isLoading) return <Page><LoadingRows rows={8} /></Page>
  if (q.error) return <Page><ErrorState error={q.error} onRetry={() => q.refetch()} /></Page>
  const a = q.data!
  return (
    <Page wide>
      <Header a={a} />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
          <AssistedApply a={a} />
        </div>
        <div className="space-y-6">
          <Tracking a={a} />
          <Timeline a={a} />
        </div>
      </div>
    </Page>
  )
}

function Header({ a }: { a: Application }) {
  const qc = useQueryClient()
  const toast = useToast()
  const status = useMutation({
    mutationFn: (s: AppStatus) => patch(`/api/applications/${a.id}`, { status: s }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['application', String(a.id)] }); qc.invalidateQueries({ queryKey: ['dashboard'] }) },
    onError: (e) => toast(errorText(e), 'error'),
  })
  return (
    <div className="mb-6">
      <div className="mb-1.5 flex items-center gap-2 text-[12px] font-medium uppercase tracking-[0.08em] text-muted">
        <Link to="/applications" className="hover:text-ink">Applications</Link><span>/</span>
        <Link to={`/jobs/${a.job_id}`} className="normal-case tracking-normal hover:text-ink">View job analysis</Link>
      </div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-serif text-[28px] font-semibold leading-tight md:text-[32px]">{a.role_title || 'Untitled role'}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[14px] text-ink-2">
            <span>{a.company}</span>
            {a.job_code && <span className="font-mono text-[12.5px] text-muted">Job ID {a.job_code}</span>}
            {a.deadline ? <span className={cx('text-[13px]', (a.days_remaining ?? 99) <= 3 ? 'text-missing' : 'text-muted')}>Deadline {fmtDate(a.deadline)} · {daysLabel(a.days_remaining)}</span>
              : <span className="text-[13px] text-faint">Deadline: Not specified</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={a.status} />
          <Select aria-label="Status" value={a.status} onChange={(e) => status.mutate(e.target.value as AppStatus)} className="h-8.5 w-auto text-[13px]">
            {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
          </Select>
        </div>
      </div>
      {a.status === 'submitted' && (
        <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
          className={cx('mt-4 flex items-start gap-3 rounded-lg border px-4 py-3', a.submission_verified ? 'border-strong/25 bg-strong-soft' : 'border-partial/25 bg-partial-soft')}>
          {a.submission_verified ? <CheckCircle2 className="mt-0.5 size-4 text-strong" /> : <AlertTriangle className="mt-0.5 size-4 text-partial" />}
          <div className="text-[13px]">
            <div className="font-semibold">{a.submission_verified ? 'Application submitted successfully' : 'Marked as submitted'}</div>
            <div className="text-ink-2">
              {a.company} · {a.role_title}{a.job_code ? ` · Job ID ${a.job_code}` : ''}{a.confirmation_id ? ` · Application ID ${a.confirmation_id}` : ''} · Submitted {fmtDateTime(a.submitted_at)}
              {a.resume_version && <> · Resume <Link to={`/resumes/${a.resume_version.id}`} className="underline">{a.company} {a.role_title} v{a.resume_version.version_number}</Link></>}
            </div>
            {!a.submission_verified && <div className="mt-0.5 text-muted">Set by hand; CareerPilot did not see a confirmation from the employer's site.</div>}
          </div>
        </motion.div>
      )}
    </div>
  )
}

function AssistedApply({ a }: { a: Application }) {
  const qc = useQueryClient()
  const toast = useToast()
  const s = a.session
  const runner = a.runner
  const resumes = useQuery({ queryKey: ['resumes', a.job_id], queryFn: () => get<ResumeVersion[]>(`/api/resumes?job_id=${a.job_id}`) })
  const [resumeId, setResumeId] = useState<string>('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const refresh = (d: Application) => { qc.setQueryData(['application', String(a.id)], d); qc.invalidateQueries({ queryKey: ['agent-status'] }) }
  const start = useMutation({
    mutationFn: () => post<Application>(`/api/applications/${a.id}/automation`, { resume_version_id: resumeId ? Number(resumeId) : null }),
    onSuccess: refresh,
  })
  const cancel = useMutation({ mutationFn: () => post<Application>(`/api/applications/${a.id}/automation/cancel`), onSuccess: refresh })
  const resume = useMutation({ mutationFn: () => post<Application>(`/api/applications/${a.id}/automation/continue`), onSuccess: (d) => { refresh(d); toast('The agent is looking at the page again') }, onError: (e) => toast(errorText(e), 'error') })
  const active = s && ACTIVE.includes(s.state)
  const fields = a.fields ?? []
  const pending = fields.filter((f) => (f.status === 'needs_input' && (f.required || f.field_key === 'consent')) || f.status === 'needs_confirmation')
  const readyToSubmit = s?.state === 'awaiting_review' && pending.length === 0 && s.filled_revision >= s.answers_revision
  const rv = a.resume_version
  const waitingHuman = s?.phase === 'WAITING_FOR_USER' && s.state === 'action_required'

  return (
    <>
      <Card>
        <CardHeader title="Apply with CareerPilot"
          subtitle="The runner opens the employer's application in a browser on your computer, fills it from your profile, and pauses whenever a person is needed. Final submission always needs your confirmation."
          actions={active && <Button size="sm" variant="ghost" icon={<X className="size-3.5" />} loading={cancel.isPending} onClick={() => cancel.mutate()}>Cancel</Button>} />
        <div className="space-y-4 p-4">
          {runner && (!s || active) && <RunnerCard runner={runner} />}
          {s && (
            <div className="space-y-3">
              <PhaseSteps phase={s.phase} />
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-md border border-line bg-surface-2/40 px-3 py-2.5 text-[12.5px] sm:grid-cols-4">
                <div className="col-span-2"><dt className="text-muted">Current task</dt>
                  <dd className="flex items-center gap-1.5 font-medium">{active && s.phase !== 'WAITING_FOR_USER' && s.phase !== 'RUNNER_OFFLINE' && <Loader2 className="size-3.5 animate-spin text-accent" />}{PHASE_LABEL[s.phase] ?? s.phase}</dd></div>
                <div><dt className="text-muted">Resume</dt><dd>{rv ? `v${rv.version_number}` : '—'}{s.resume_uploaded ? ' · uploaded' : ''}</dd></div>
                <div><dt className="text-muted">Progress</dt><dd className="tabular-nums">{s.fields_total ? `${s.fields_done} / ${s.fields_total} fields` : '—'}</dd></div>
                <div className="col-span-2 sm:col-span-4"><dt className="text-muted">{s.phase === 'WAITING_FOR_USER' ? 'Waiting for' : 'Status'}</dt>
                  <dd className={cx(s.phase === 'WAITING_FOR_USER' && 'text-ink', ['BLOCKED', 'FAILED', 'SUBMISSION_UNCERTAIN'].includes(s.phase) && 'text-missing')}>
                    {s.phase === 'WAITING_FOR_USER' && pending.length > 0 && s.state !== 'action_required'
                      ? `${pending.length} answer${pending.length > 1 ? 's' : ''} from you: ${pending.slice(0, 2).map((f) => f.label).join('; ')}${pending.length > 2 ? '…' : ''}`
                      : s.message}
                  </dd></div>
              </dl>
              {waitingHuman && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-partial/30 bg-partial-soft/50 px-3 py-2.5 text-[13px]">
                  <div className="flex items-start gap-2"><ShieldAlert className="mt-0.5 size-4 shrink-0 text-partial" /><span>The browser is open and waiting. Finish that step in the browser window, then continue.</span></div>
                  <Button size="sm" variant="primary" loading={resume.isPending} onClick={() => resume.mutate()}>Continue application</Button>
                </div>
              )}
              {s.has_screenshot && <Screenshot appId={a.id} nonce={s.answers_revision + (s.log?.length ?? 0)} />}
              <details open={!!active}>
                <summary className="cursor-pointer text-[12.5px] font-medium text-ink-2">Activity</summary>
                <div className="mt-2"><EventTimeline s={s} /></div>
              </details>
            </div>
          )}
          {(!s || !active) && a.status !== 'submitted' && (
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Resume to upload" className="min-w-[220px] flex-1">
                <Select value={resumeId || String(a.resume_version?.id ?? '')} onChange={(e) => setResumeId(e.target.value)}>
                  {!a.resume_version && <option value="">Choose a version…</option>}
                  {resumes.data?.filter((r) => r.has_pdf).map((r) => <option key={r.id} value={r.id}>Version {r.version_number}{r.status === 'approved' ? ' (approved)' : ''}</option>)}
                </Select>
              </Field>
              <Button variant="primary" icon={<Play className="size-3.5" />} loading={start.isPending} onClick={() => start.mutate()}>
                {s ? 'Start again' : 'Apply with CareerPilot'}
              </Button>
              {a.application_url && <a href={a.application_url} target="_blank" rel="noreferrer noopener"><Button icon={<ExternalLink className="size-3.5" />}>Apply manually</Button></a>}
            </div>
          )}
          {start.error && <ErrorState compact error={start.error} />}
          {!s && <p className="text-[12px] text-muted">Greenhouse, Lever, Ashby, SmartRecruiters and standard HTML forms are filled directly. Workday, Taleo, Oracle and SuccessFactors need you to sign in first in the runner's browser; CareerPilot continues after that. CAPTCHAs and sign-ins are always left to you.</p>}
        </div>
      </Card>

      {fields.length > 0 && (
        <Card>
          <CardHeader title="Review application" subtitle={`${fields.length} fields found · ${pending.length} need you`}
            actions={s && ['awaiting_review', 'awaiting_answers', 'filling'].includes(s.state) && (
              <Button variant="primary" disabled={!readyToSubmit} onClick={() => setConfirmOpen(true)} title={readyToSubmit ? '' : 'Answer every highlighted field first'}>
                Review application
              </Button>
            )} />
          <ul className="divide-y divide-line">
            {fields.map((f) => <FieldRow key={f.id} f={f} appId={a.id} locked={!s || !['awaiting_answers', 'awaiting_review', 'filling', 'action_required'].includes(s.state)} onUpdated={refresh} onError={(e) => toast(errorText(e), 'error')} />)}
          </ul>
        </Card>
      )}
      <ConfirmSubmit open={confirmOpen} onClose={() => setConfirmOpen(false)} a={a} onDone={refresh} />
    </>
  )
}

function Screenshot({ appId, nonce }: { appId: number; nonce: number }) {
  const [open, setOpen] = useState(false)
  const src = `/api/applications/${appId}/automation/screenshot.png?n=${nonce}`
  return (
    <>
      <button onClick={() => setOpen(true)} className="group relative block w-full overflow-hidden rounded-md border border-line">
        <img src={src} alt="What the agent sees" className="max-h-[260px] w-full object-cover object-top" />
        <span className="absolute bottom-2 right-2 flex items-center gap-1 rounded bg-ink/80 px-2 py-1 text-[11.5px] text-bg opacity-0 transition-opacity group-hover:opacity-100"><Eye className="size-3" />View</span>
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Browser view" width="max-w-5xl"><img src={src} alt="Browser view" className="w-full rounded" /></Dialog>
    </>
  )
}

const PROVENANCE_LABEL: Record<string, string> = { USER_CONFIRMED: 'Your answer', CAREER_PROFILE: 'Career profile', JOB_SPECIFIC: 'This job', AI_SUGGESTED: 'Suggested draft — not a fact until you confirm' }
const CATEGORY_LABEL: Record<string, string> = { VERIFIED_PROFILE_REQUIRED: 'Profile fact', JOB_SPECIFIC_DECISION: 'Your decision', SENSITIVE: 'Sensitive' }

function FieldRow({ f, appId, locked, onUpdated, onError }: { f: AppField; appId: number; locked: boolean; onUpdated: (a: Application) => void; onError: (e: unknown) => void }) {
  const [value, setValue] = useState(f.value)
  const [dirty, setDirty] = useState(false)
  const [remember, setRemember] = useState(false)
  const m = useMutation({
    mutationFn: (body: { value?: string | null; confirm?: boolean; skip?: boolean }) => patch<Application>(`/api/applications/${appId}/fields/${f.id}`, { ...body, remember }),
    onSuccess: (d) => { setDirty(false); onUpdated(d) },
    onError,
  })
  const needs = f.status === 'needs_input' || f.status === 'needs_confirmation'
  const choice = f.options.length > 0 && ['select', 'radio', 'combobox'].includes(f.input_type)
  return (
    <li className={cx('px-4 py-3', needs && (f.required || f.status === 'needs_confirmation') && 'bg-partial-soft/35')}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5 text-[13px] font-medium">
            {f.label}{f.required && <span className="text-missing">*</span>}
            {f.sensitive && <Badge tone="verify"><Lock className="size-3" />Self-identification</Badge>}
            {!f.sensitive && CATEGORY_LABEL[f.category] && <Badge tone="verify">{CATEGORY_LABEL[f.category]}</Badge>}
          </div>
          {f.note && <div className="mt-0.5 text-[12px] text-muted">{f.note}</div>}
          {f.validation_message && <div className="mt-0.5 text-[12px] text-missing">{f.validation_message}</div>}
        </div>
        <div className="flex items-center gap-1.5">
          {f.value && PROVENANCE_LABEL[f.provenance] && <span className={cx('text-[11.5px]', f.provenance === 'AI_SUGGESTED' ? 'text-verify' : 'text-faint')}>{PROVENANCE_LABEL[f.provenance]}</span>}
          <Badge tone={f.status === 'filled' || f.status === 'confirmed' ? 'strong' : f.status === 'skipped' ? 'neutral' : 'partial'}>
            {f.status === 'needs_input' ? 'Needs your input' : f.status === 'needs_confirmation' ? 'Confirm' : f.status === 'confirmed' ? 'Confirmed' : f.status === 'skipped' ? 'Left blank' : f.fill_result === 'failed' ? 'Fill in browser' : 'Filled'}
          </Badge>
        </div>
      </div>
      {f.input_type !== 'file' && (
        <div className="mt-2 flex flex-wrap items-start gap-2">
          {f.field_key === 'consent' ? (
            <span className="text-[12.5px] text-ink-2">Accepting this is your decision.</span>
          ) : choice ? (
            <Select disabled={locked} value={value} onChange={(e) => { setValue(e.target.value); setDirty(true) }} className="h-8.5 w-auto min-w-[200px] text-[13px]">
              <option value="">Choose…</option>
              {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
            </Select>
          ) : f.input_type === 'textarea' ? (
            <Textarea disabled={locked} rows={3} value={value} onChange={(e) => { setValue(e.target.value); setDirty(true) }} className="min-w-0 flex-1" />
          ) : (
            <Input disabled={locked} value={value} onChange={(e) => { setValue(e.target.value); setDirty(true) }} className="h-8.5 min-w-0 flex-1" />
          )}
          {!locked && (
            <div className="flex gap-1.5">
              {dirty && <Button size="sm" loading={m.isPending} onClick={() => m.mutate({ value })}>Save</Button>}
              {(needs || dirty) && <Button size="sm" variant="primary" loading={m.isPending} disabled={f.field_key !== 'consent' && !value}
                onClick={() => m.mutate({ value: f.field_key === 'consent' ? null : value, confirm: true })}>{f.field_key === 'consent' ? 'I accept' : 'Confirm'}</Button>}
              {needs && !f.required && f.field_key !== 'consent' && <Button size="sm" variant="ghost" onClick={() => m.mutate({ skip: true })}>Leave blank</Button>}
            </div>
          )}
          {!locked && (needs || dirty) && f.field_key !== 'consent' && !f.sensitive && (
            <label className="flex basis-full items-center gap-1.5 text-[12px] text-muted">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-[var(--accent)]" />
              Remember this answer for future applications
            </label>
          )}
        </div>
      )}
      {f.input_type === 'file' && <div className="mt-1 text-[12.5px] text-ink-2">{f.value ? 'Your tailored resume PDF' : '—'}</div>}
    </li>
  )
}

function ConfirmSubmit({ open, onClose, a, onDone }: { open: boolean; onClose: () => void; a: Application; onDone: (d: Application) => void }) {
  const [typed, setTyped] = useState('')
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => get<{ submit_confirmation_mode: string }>('/api/settings'), enabled: open })
  const needType = settings.data?.submit_confirmation_mode !== 'click' && !!a.company
  const m = useMutation({
    mutationFn: () => post<Application>(`/api/applications/${a.id}/automation/confirm-submit`, { typed_confirmation: typed }),
    onSuccess: (d) => { onDone(d); onClose(); setTyped('') },
  })
  const fields = a.fields ?? []
  const filled = fields.filter((f) => f.value && f.input_type !== 'file')
  const required = fields.filter((f) => f.required)
  const unresolved = fields.filter((f) => (f.status === 'needs_input' && (f.required || f.field_key === 'consent')) || f.status === 'needs_confirmation')
  return (
    <Dialog open={open} onClose={onClose} title="Application ready" width="max-w-xl"
      footer={<>
        <Button variant="ghost" onClick={onClose}>Not yet</Button>
        <Button variant="primary" loading={m.isPending} disabled={unresolved.length > 0 || (needType && typed.trim().toLowerCase() !== a.company.trim().toLowerCase())} onClick={() => m.mutate()}>Confirm &amp; Submit</Button>
      </>}>
      <dl className="mb-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
        <dt className="text-muted">Company</dt><dd className="font-medium">{a.company || '—'}</dd>
        <dt className="text-muted">Role</dt><dd className="font-medium">{a.role_title || '—'}</dd>
        <dt className="text-muted">Resume</dt><dd>{a.resume_version ? `Resume v${a.resume_version.version_number}` : 'None attached'}</dd>
        <dt className="text-muted">Required fields</dt><dd>{required.length ? `${Math.round(100 * required.filter((f) => f.value || f.status === 'confirmed').length / required.length)}% complete` : 'None'}</dd>
        <dt className="text-muted">Unresolved questions</dt><dd className={unresolved.length ? 'font-medium text-missing' : ''}>{unresolved.length}</dd>
        <dt className="text-muted">Resume uploaded</dt><dd>{a.session?.resume_uploaded ? 'Yes' : 'Not confirmed by the form'}</dd>
      </dl>
      <p className="text-[13px] text-ink-2">Only after you confirm will the agent click the employer's submit button for <span className="font-semibold text-ink">{a.role_title}</span> at <span className="font-semibold text-ink">{a.company}</span>. This cannot be undone.</p>
      <div className="mt-3 max-h-[220px] overflow-y-auto rounded-md border border-line bg-surface-2/40 p-3 text-[12.5px]">
        {filled.map((f) => <div key={f.id} className="flex gap-2 py-0.5"><span className="w-2/5 shrink-0 truncate text-muted">{f.label}</span><span className="truncate">{f.field_key === 'consent' ? 'Accepted' : f.value}</span></div>)}
        {a.resume_version && <div className="flex gap-2 py-0.5"><span className="w-2/5 shrink-0 text-muted">Resume</span><span>Version {a.resume_version.version_number}</span></div>}
      </div>
      {needType && (
        <Field label={`Type ${a.company} to confirm`} className="mt-4">
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
        </Field>
      )}
      {m.error && <div className="mt-3"><ErrorState compact error={m.error} /></div>}
    </Dialog>
  )
}

function Tracking({ a }: { a: Application }) {
  const qc = useQueryClient()
  const [notes, setNotes] = useState(a.notes)
  const m = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch<Application>(`/api/applications/${a.id}`, body),
    onSuccess: (d) => qc.setQueryData(['application', String(a.id)], d),
  })
  return (
    <Card>
      <CardHeader title="Tracking" />
      <div className="space-y-3 p-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Date applied"><Input type="date" value={a.date_applied ?? ''} onChange={(e) => m.mutate({ date_applied: e.target.value || null })} /></Field>
          <Field label="Follow up on"><Input type="date" value={a.follow_up_date ?? ''} onChange={(e) => m.mutate(e.target.value ? { follow_up_date: e.target.value } : { clear_follow_up: true })} /></Field>
        </div>
        <Field label="Notes">
          <Textarea rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} onBlur={() => notes !== a.notes && m.mutate({ notes })} placeholder="Recruiter name, referral, interview prep…" />
        </Field>
        <dl className="space-y-1 text-[12.5px]">
          <div className="flex justify-between"><dt className="text-muted">Discovered</dt><dd>{fmtDate(a.discovered_at)}</dd></div>
          <div className="flex justify-between"><dt className="text-muted">Resume</dt><dd>{a.resume_version ? <Link className="hover:text-accent" to={`/resumes/${a.resume_version.id}`}>Version {a.resume_version.version_number}</Link> : '—'}</dd></div>
          {a.job_url && <div className="flex justify-between"><dt className="text-muted">Job posting</dt><dd><a className="hover:text-accent" href={a.job_url} target="_blank" rel="noreferrer noopener">Open ↗</a></dd></div>}
        </dl>
        {a.answers && a.answers.length > 0 && (
          <details className="rounded-md border border-line">
            <summary className="cursor-pointer px-3 py-2 text-[12.5px] font-medium">Submitted answers ({a.answers.length})</summary>
            <div className="space-y-1.5 border-t border-line px-3 py-2 text-[12px]">
              {a.answers.map((x, i) => <div key={i}><div className="text-muted">{x.question}</div><div>{x.answer}</div></div>)}
            </div>
          </details>
        )}
      </div>
    </Card>
  )
}

function Timeline({ a }: { a: Application }) {
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const add = useMutation({
    mutationFn: () => api<Application>(`/api/applications/${a.id}/events`, { method: 'POST', body: { message: note } }),
    onSuccess: (d) => { qc.setQueryData(['application', String(a.id)], d); setNote('') },
  })
  return (
    <Card>
      <CardHeader title="Timeline" />
      <div className="p-4">
        <form onSubmit={(e) => { e.preventDefault(); if (note.trim()) add.mutate() }} className="mb-4 flex gap-2">
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add an update…" className="h-8.5" />
          <Button size="md" type="submit" disabled={!note.trim()} loading={add.isPending}>Add</Button>
        </form>
        <ol className="relative space-y-3 border-l border-line pl-4">
          <AnimatePresence initial={false}>
            {(a.events ?? []).map((e) => (
              <motion.li key={e.id} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} className="relative text-[12.5px]">
                <span className={cx('absolute -left-[21px] top-1 size-2.5 rounded-full border-2 border-surface',
                  e.kind === 'submission' ? 'bg-strong' : e.kind === 'status' ? 'bg-accent' : e.kind === 'note' ? 'bg-verify' : 'bg-faint')} />
                <div className="text-ink">{e.message}</div>
                <div className="text-[11.5px] text-faint">{fmtDateTime(e.created_at)}</div>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      </div>
    </Card>
  )
}
