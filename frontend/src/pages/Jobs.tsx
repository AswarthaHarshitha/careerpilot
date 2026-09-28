import { useQuery } from '@tanstack/react-query'
import { Briefcase, Plus, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Page } from '../components/AppShell'
import { Badge, Button, EmptyState, ErrorState, Input, LoadingRows, PageHeader, StatusBadge, Tabs, cx } from '../components/ui'
import { get } from '../lib/api'
import { daysLabel, fmtDate, relative } from '../lib/format'
import type { JobSummary } from '../lib/types'

export default function Jobs() {
  const [archived, setArchived] = useState<'active' | 'archived'>('active')
  const [q, setQ] = useState('')
  const jobs = useQuery({
    queryKey: ['jobs', archived],
    queryFn: () => get<JobSummary[]>(`/api/jobs?archived=${archived === 'archived'}`),
    refetchInterval: (query) => (query.state.data?.some((j) => j.analysis_state === 'pending' || j.analysis_state === 'running') ? 2000 : false),
  })
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase()
    return (jobs.data ?? []).filter((j) => !s || `${j.company} ${j.role_title} ${j.location} ${j.job_code}`.toLowerCase().includes(s))
  }, [jobs.data, q])

  return (
    <Page>
      <PageHeader title="Jobs" subtitle="Every posting you have analyzed, with the evidence behind each match."
        actions={<Link to="/jobs/new"><Button variant="primary" icon={<Plus className="size-4" />}>Add job</Button></Link>} />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Tabs value={archived} onChange={setArchived} tabs={[{ value: 'active', label: 'Active' }, { value: 'archived', label: 'Archived' }]} className="border-b-0" />
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-faint" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search company, role, job ID" className="pl-8" />
        </div>
      </div>
      {jobs.isLoading ? <LoadingRows /> : jobs.error ? <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} /> :
        rows.length === 0 ? (
          <EmptyState icon={<Briefcase className="size-7" />} title={q ? 'No matching jobs' : 'No jobs yet'}
            action={!q && <Link to="/jobs/new"><Button variant="primary">Analyze your first job</Button></Link>}>
            {q ? 'Try a different search.' : 'Paste a job URL or description. CareerPilot extracts the requirements and checks them against your evidence.'}
          </EmptyState>
        ) : (
          <div className="overflow-hidden rounded-lg border border-line bg-surface shadow-card">
            <table className="w-full text-left text-[13px]">
              <thead className="hidden border-b border-line bg-surface-2/60 text-[11.5px] uppercase tracking-[0.05em] text-muted md:table-header-group">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Role</th>
                  <th className="px-3 py-2.5 font-medium">Evidence</th>
                  <th className="px-3 py-2.5 font-medium">Deadline</th>
                  <th className="px-3 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 text-right font-medium">Added</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((j) => (
                  <tr key={j.id} className="group block cursor-pointer transition-colors hover:bg-surface-2/50 md:table-row">
                    <td className="block px-4 pt-3 md:table-cell md:py-3">
                      <Link to={`/jobs/${j.id}`} className="block">
                        <div className="font-semibold text-ink">{j.role_title || (j.analysis_state === 'failed' ? 'Could not analyze' : 'Analyzing…')}</div>
                        <div className="mt-0.5 text-[12.5px] text-muted">
                          {j.company || '—'}{j.location ? ` · ${j.location}` : ''}{j.job_code ? <span className="font-mono text-[11.5px]"> · {j.job_code}</span> : ''}
                        </div>
                      </Link>
                    </td>
                    <td className="block px-4 py-1 md:table-cell md:px-3 md:py-3"><EvidenceSummary j={j} /></td>
                    <td className={cx('block px-4 py-1 text-[12.5px] md:table-cell md:px-3', j.deadline ? ((j.days_remaining ?? 99) <= 3 ? 'text-missing' : 'text-ink-2') : 'text-faint')}>
                      {j.deadline ? <>{fmtDate(j.deadline)}<div className="text-[11.5px] text-muted">{daysLabel(j.days_remaining)}</div></> : 'Not specified'}
                    </td>
                    <td className="block px-4 py-1 md:table-cell md:px-3">{j.application && <StatusBadge status={j.application.status} />}</td>
                    <td className="block px-4 pb-3 text-[12px] text-muted md:table-cell md:py-3 md:text-right">{relative(j.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </Page>
  )
}

function EvidenceSummary({ j }: { j: JobSummary }) {
  if (j.analysis_state === 'failed') return <Badge tone="missing">Analysis failed</Badge>
  if (j.analysis_state !== 'ready') return <Badge>Analyzing…</Badge>
  const c = j.match_counts
  if (!c.required_total) return <span className="text-[12px] text-muted">No skills recognised</span>
  return (
    <div className="flex items-center gap-2">
      <div className="flex h-1.5 w-20 overflow-hidden rounded-full bg-surface-3" aria-hidden>
        <div className="bg-strong" style={{ width: `${((c.strong ?? 0) / Math.max(1, (c.strong ?? 0) + (c.partial ?? 0) + (c.missing ?? 0) + (c.needs_verification ?? 0))) * 100}%` }} />
        <div className="bg-partial" style={{ width: `${((c.partial ?? 0) / Math.max(1, (c.strong ?? 0) + (c.partial ?? 0) + (c.missing ?? 0) + (c.needs_verification ?? 0))) * 100}%` }} />
      </div>
      <span className="text-[12px] text-ink-2 tabular">{c.required_strong}/{c.required_total} required</span>
      {(c.missing ?? 0) > 0 && <span className="text-[12px] text-missing tabular">{c.missing} missing</span>}
    </div>
  )
}
