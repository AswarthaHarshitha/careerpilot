import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { AlarmClock, ArrowRight, Check, CheckCircle2, CircleAlert, FileCheck2, Plus, Send } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { Page, QuickImport } from '../components/AppShell'
import { Badge, Button, Card, CardHeader, ErrorState, LoadingRows, StatusBadge, cx } from '../components/ui'
import { get, post } from '../lib/api'
import { daysLabel, fmtDate, greeting } from '../lib/format'
import type { AppStatus, Project, Template } from '../lib/types'

type Card_ = {
  application_id: number; job_id: number; company: string; role_title: string; location: string; status: AppStatus
  deadline: string | null; days_remaining: number | null; analysis_state: string
  match?: { required_total?: number; required_strong?: number; missing?: number }
}
type Dash = {
  greeting_name: string
  stats: { active_applications: number; resume_versions: number; upcoming_deadlines: number; interviews: number; needs_attention: number }
  actions: { kind: string; count: number; text: string; items: (Card_ & { reminder_id?: number; text?: string })[] }[]
  pipeline: { stage: string; count: number }[]
  recent_jobs: Card_[]
  upcoming_deadlines: Card_[]
  reminders: { id: number; kind: string; due_date: string; message: string; application_id: number | null; job_id: number | null }[]
  has_jobs: boolean
}

const STAGES: Record<string, string> = { saved: 'Saved', ready: 'Ready', applied: 'Applied', assessment: 'Assessment', interview: 'Interview', offer: 'Offer' }
const ACTION_ICON: Record<string, React.ReactNode> = {
  review: <CircleAlert className="size-4 text-partial" />,
  ready: <FileCheck2 className="size-4 text-accent" />,
  deadline: <AlarmClock className="size-4 text-missing" />,
  overdue: <AlarmClock className="size-4 text-missing" />,
  follow_up: <Send className="size-4 text-verify" />,
  failed: <CircleAlert className="size-4 text-missing" />,
}

export default function Dashboard() {
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => get<Dash>('/api/dashboard'), refetchInterval: 30_000 })
  const [quick, setQuick] = useState(false)
  const today = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date())

  return (
    <Page>
      <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[12px] font-medium uppercase tracking-[0.08em] text-muted">{today}</div>
          <h1 className="mt-1 font-serif text-[30px] font-semibold leading-tight tracking-[-0.01em] md:text-[34px]">
            {greeting()}{q.data?.greeting_name ? `, ${q.data.greeting_name}` : ''}
          </h1>
        </div>
        <Button variant="primary" size="lg" icon={<Plus className="size-4" />} onClick={() => setQuick(true)}>Analyze a job</Button>
      </div>
      {q.isLoading ? <LoadingRows rows={6} /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : q.data && (
        <DashboardBody d={q.data} onImport={() => setQuick(true)} />
      )}
      <QuickImport open={quick} onClose={() => setQuick(false)} />
    </Page>
  )
}

function DashboardBody({ d, onImport }: { d: Dash; onImport: () => void }) {
  const stats = [
    { label: 'Active applications', value: d.stats.active_applications, to: '/applications' },
    { label: 'Resume versions', value: d.stats.resume_versions, to: '/resumes' },
    { label: 'Upcoming deadlines', value: d.stats.upcoming_deadlines, to: '/applications' },
    { label: 'Interviews', value: d.stats.interviews, to: '/applications' },
    { label: 'Needs attention', value: d.stats.needs_attention, to: '/applications', alert: d.stats.needs_attention > 0 },
  ]
  return (
    <div className="space-y-6">
      {!d.has_jobs && <SetupChecklist onImport={onImport} />}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-3 lg:grid-cols-5">
        {stats.map((s, i) => (
          <Link key={s.label} to={s.to} className={cx('group bg-surface px-4 py-3.5 transition-colors hover:bg-surface-2/60', i === stats.length - 1 && 'col-span-2 sm:col-span-1')}>
            <div className="text-[12px] text-muted">{s.label}</div>
            <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
              className={cx('mt-1 font-serif text-[28px] font-semibold leading-none tabular', s.alert ? 'text-accent' : 'text-ink')}>
              {s.value}
            </motion.div>
          </Link>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]">
        <Card>
          <CardHeader title="Today's actions" subtitle={d.actions.length ? undefined : 'Nothing needs you right now.'} />
          {d.actions.length === 0 ? (
            <div className="flex items-center gap-2 px-4 py-6 text-[13px] text-muted"><CheckCircle2 className="size-4 text-strong" /> You're up to date.</div>
          ) : (
            <ul className="divide-y divide-line">
              {d.actions.map((a) => (
                <li key={a.kind} className="px-4 py-3">
                  <div className="flex items-center gap-2.5 text-[13.5px] font-medium">{ACTION_ICON[a.kind]}{a.text}</div>
                  <ul className="mt-2 space-y-1 pl-6.5">
                    {a.items.map((it, i) => (
                      <li key={i} className="text-[13px]">
                        {it.reminder_id ? (
                          <ReminderRow id={it.reminder_id} text={it.text ?? ''} appId={it.application_id} />
                        ) : (
                          <Link to={a.kind === 'review' || a.kind === 'ready' ? `/applications/${it.application_id}` : `/jobs/${it.job_id}`}
                            className="group flex items-center gap-2 text-ink-2 hover:text-ink">
                            <span className="truncate">{it.role_title || 'Untitled role'} <span className="text-muted">· {it.company || 'Unknown company'}</span></span>
                            {it.days_remaining !== null && it.days_remaining !== undefined && (a.kind === 'deadline' || a.kind === 'overdue') &&
                              <span className="shrink-0 text-[12px] text-missing">{daysLabel(it.days_remaining)}</span>}
                            <ArrowRight className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
                          </Link>
                        )}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader title="Pipeline" actions={<Link to="/applications" className="text-[12.5px] text-muted hover:text-ink">Open tracker</Link>} />
          <div className="px-4 py-4">
            <div className="flex items-stretch">
              {d.pipeline.map((p, i) => (
                <div key={p.stage} className="flex min-w-0 flex-1 items-center">
                  <div className="min-w-0 flex-1 text-center">
                    <motion.div initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ delay: 0.05 * i, duration: 0.3 }}
                      className={cx('mx-auto flex h-11 w-full max-w-[64px] origin-bottom items-center justify-center rounded-md border font-serif text-[18px] font-semibold tabular',
                        p.count ? 'border-accent/25 bg-accent-soft text-accent' : 'border-line bg-surface-2 text-faint')}>
                      {p.count}
                    </motion.div>
                    <div className="mt-1.5 truncate text-[11.5px] text-muted">{STAGES[p.stage]}</div>
                  </div>
                  {i < d.pipeline.length - 1 && <div className="mb-5 h-px w-2 shrink-0 bg-line-strong sm:w-3" />}
                </div>
              ))}
            </div>
            {d.upcoming_deadlines.length > 0 && (
              <div className="mt-5 border-t border-line pt-4">
                <div className="mb-2 text-[12px] font-medium uppercase tracking-[0.06em] text-muted">Upcoming deadlines</div>
                <ul className="space-y-1.5">
                  {d.upcoming_deadlines.map((c) => (
                    <li key={c.application_id} className="flex items-center justify-between gap-3 text-[13px]">
                      <Link to={`/jobs/${c.job_id}`} className="truncate text-ink-2 hover:text-ink">{c.company} · {c.role_title}</Link>
                      <span className={cx('shrink-0 tabular text-[12px]', (c.days_remaining ?? 99) <= 3 ? 'text-missing' : 'text-muted')}>
                        {fmtDate(c.deadline)} · {daysLabel(c.days_remaining)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Card>
      </div>

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-serif text-[20px] font-semibold">Recent jobs</h2>
          <Link to="/jobs" className="text-[12.5px] text-muted hover:text-ink">All jobs</Link>
        </div>
        {d.recent_jobs.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line-strong px-5 py-8 text-center text-[13px] text-muted">
            Jobs you analyze appear here. <button className="font-medium text-accent hover:underline" onClick={onImport}>Analyze your first job</button>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {d.recent_jobs.map((j, i) => (
              <motion.div key={j.job_id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}>
                <Link to={`/jobs/${j.job_id}`} className="block h-full rounded-lg border border-line bg-surface p-3.5 shadow-card transition-[border,transform] hover:-translate-y-px hover:border-line-strong">
                  <div className="text-[12px] font-medium text-muted">{j.company || 'Unknown company'}</div>
                  <div className="mt-0.5 line-clamp-2 text-[14px] font-semibold leading-snug">{j.role_title || (j.analysis_state === 'ready' ? 'Untitled role' : 'Analyzing…')}</div>
                  <div className="mt-1 truncate text-[12px] text-muted">{j.location || '—'}</div>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    <StatusBadge status={j.status} />
                    {j.match?.required_total ? (
                      <Badge tone={j.match.required_strong === j.match.required_total ? 'strong' : 'neutral'}>
                        {j.match.required_strong}/{j.match.required_total} required backed
                      </Badge>
                    ) : null}
                  </div>
                  <div className={cx('mt-2 text-[12px]', j.deadline ? ((j.days_remaining ?? 99) <= 3 ? 'text-missing' : 'text-ink-2') : 'text-faint')}>
                    {j.deadline ? `Deadline ${fmtDate(j.deadline)} · ${daysLabel(j.days_remaining)}` : 'Deadline: Not specified'}
                  </div>
                </Link>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ReminderRow({ id, text, appId }: { id: number; text: string; appId: number | null }) {
  const qc = useQueryClient()
  const done = useMutation({ mutationFn: () => post(`/api/reminders/${id}/done`), onSuccess: () => qc.invalidateQueries({ queryKey: ['dashboard'] }) })
  return (
    <span className="flex items-center gap-2 text-ink-2">
      {appId ? <Link to={`/applications/${appId}`} className="hover:text-ink">{text}</Link> : text}
      <button onClick={() => done.mutate()} className="rounded border border-line px-1.5 text-[11px] text-muted hover:text-ink">Done</button>
    </span>
  )
}

function SetupChecklist({ onImport }: { onImport: () => void }) {
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => get<Template[]>('/api/templates') })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => get<Project[]>('/api/projects') })
  const profile = useQuery({ queryKey: ['profile'], queryFn: () => get<{ completeness: { missing: string[] } }>('/api/profile') })
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => get<{ github: { username: string } }>('/api/settings') })
  const steps = [
    { done: (templates.data?.length ?? 0) > 0, title: 'Upload your resume template', text: 'Your .tex or .docx resume. Generated resumes keep its exact layout.', to: '/resumes/templates' },
    { done: (profile.data?.completeness.missing.length ?? 99) <= 3, title: 'Complete your career profile', text: 'Import it from your template, then add application details.', to: '/profile' },
    { done: !!settings.data?.github.username && (projects.data?.length ?? 0) > 0, title: 'Connect GitHub', text: 'CareerPilot reads your repositories to build verified project evidence.', to: '/settings?tab=github' },
  ]
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Set up CareerPilot" subtitle="Three steps before your first tailored resume." />
      <ol className="divide-y divide-line">
        {steps.map((s, i) => (
          <li key={s.title}>
            <Link to={s.to} className="flex items-center gap-3 px-4 py-3 hover:bg-surface-2/50">
              <span className={cx('flex size-6 shrink-0 items-center justify-center rounded-full border text-[12px] font-semibold',
                s.done ? 'border-strong bg-strong text-surface' : 'border-line-strong text-muted')}>
                {s.done ? <Check className="size-3.5" strokeWidth={3} /> : i + 1}
              </span>
              <div className="min-w-0">
                <div className={cx('text-[13.5px] font-medium', s.done && 'text-muted line-through decoration-faint')}>{s.title}</div>
                <div className="text-[12.5px] text-muted">{s.text}</div>
              </div>
              <ArrowRight className="ml-auto size-4 text-faint" />
            </Link>
          </li>
        ))}
        <li className="flex items-center gap-3 px-4 py-3">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full border border-line-strong text-[12px] font-semibold text-muted">4</span>
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-medium">Analyze a job</div>
            <div className="text-[12.5px] text-muted">Paste a job URL to see your evidence, gaps and a tailored resume.</div>
          </div>
          <Button size="sm" variant="primary" onClick={onImport}>Analyze a job</Button>
        </li>
      </ol>
    </Card>
  )
}
