import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Kanban, Rows3, Send } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { Page } from '../components/AppShell'
import { EmptyState, ErrorState, LoadingRows, PageHeader, Select, StatusBadge, cx } from '../components/ui'
import { get, patch } from '../lib/api'
import { daysLabel, fmtDate, STATUS_LABEL, STATUS_ORDER } from '../lib/format'
import type { Application, AppStatus } from '../lib/types'

const COLUMNS: { title: string; statuses: AppStatus[] }[] = [
  { title: 'Saved', statuses: ['saved', 'analyzing'] },
  { title: 'Ready', statuses: ['resume_ready', 'ready_to_apply'] },
  { title: 'In progress', statuses: ['in_progress'] },
  { title: 'Applied', statuses: ['submitted'] },
  { title: 'Assessment', statuses: ['assessment'] },
  { title: 'Interview', statuses: ['interview'] },
  { title: 'Offer', statuses: ['offer'] },
  { title: 'Closed', statuses: ['rejected', 'withdrawn'] },
]

export default function Applications() {
  const [view, setView] = useState<'board' | 'table'>(() => {
    try { return (localStorage.getItem('cp-app-view') as 'board' | 'table') || 'board' } catch { return 'board' }
  })
  const q = useQuery({ queryKey: ['applications'], queryFn: () => get<Application[]>('/api/applications') })
  const qc = useQueryClient()
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: AppStatus }) => patch(`/api/applications/${id}`, { status }),
    onMutate: async ({ id, status }) => {
      await qc.cancelQueries({ queryKey: ['applications'] })
      const prev = qc.getQueryData<Application[]>(['applications'])
      qc.setQueryData<Application[]>(['applications'], (old) => old?.map((a) => (a.id === id ? { ...a, status } : a)))
      return { prev }
    },
    onError: (_e, _v, ctx) => ctx?.prev && qc.setQueryData(['applications'], ctx.prev),
    onSettled: () => { qc.invalidateQueries({ queryKey: ['applications'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }) },
  })
  const choose = (v: 'board' | 'table') => { setView(v); try { localStorage.setItem('cp-app-view', v) } catch { /* ignore */ } }

  return (
    <Page wide>
      <PageHeader title="Applications" subtitle="Where every job stands, from saved to offer."
        actions={
          <div className="flex rounded-md border border-line bg-surface p-0.5 shadow-card">
            <button onClick={() => choose('board')} className={cx('flex items-center gap-1.5 rounded px-2.5 py-1 text-[12.5px]', view === 'board' ? 'bg-surface-2 font-medium' : 'text-muted')}><Kanban className="size-3.5" />Board</button>
            <button onClick={() => choose('table')} className={cx('flex items-center gap-1.5 rounded px-2.5 py-1 text-[12.5px]', view === 'table' ? 'bg-surface-2 font-medium' : 'text-muted')}><Rows3 className="size-3.5" />Table</button>
          </div>
        } />
      {q.isLoading ? <LoadingRows /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.length ? (
        <EmptyState icon={<Send className="size-7" />} title="No applications yet">Every job you analyze is tracked here automatically.</EmptyState>
      ) : view === 'board' ? (
        <div className="-mx-4 flex gap-3 overflow-x-auto px-4 pb-4 md:-mx-8 md:px-8">
          {COLUMNS.map((col) => {
            const items = q.data!.filter((a) => col.statuses.includes(a.status))
            return (
              <div key={col.title} className="w-[250px] shrink-0">
                <div className="mb-2 flex items-center justify-between px-1 text-[12px] font-medium uppercase tracking-[0.06em] text-muted">
                  {col.title}<span className="tabular text-faint">{items.length}</span>
                </div>
                <div className="min-h-[120px] space-y-2 rounded-lg bg-surface-2/50 p-2">
                  {items.map((a) => (
                    <motion.div layout key={a.id} className="rounded-md border border-line bg-surface p-3 shadow-card">
                      <Link to={`/applications/${a.id}`} className="block">
                        <div className="text-[12px] font-medium text-muted">{a.company || '—'}</div>
                        <div className="mt-0.5 line-clamp-2 text-[13.5px] font-semibold leading-snug">{a.role_title || 'Untitled role'}</div>
                        {a.deadline && !['submitted', 'assessment', 'interview', 'offer', 'rejected', 'withdrawn'].includes(a.status) && (
                          <div className={cx('mt-1.5 text-[11.5px]', (a.days_remaining ?? 99) <= 3 ? 'text-missing' : 'text-muted')}>{daysLabel(a.days_remaining)}</div>
                        )}
                        {a.date_applied && <div className="mt-1.5 text-[11.5px] text-muted">Applied {fmtDate(a.date_applied)}</div>}
                      </Link>
                      <Select aria-label="Move to" value={a.status} onChange={(e) => setStatus.mutate({ id: a.id, status: e.target.value as AppStatus })} className="mt-2 h-7 text-[12px]">
                        {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
                      </Select>
                    </motion.div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line bg-surface shadow-card">
          <table className="w-full min-w-[900px] text-left text-[13px]">
            <thead className="border-b border-line bg-surface-2/60 text-[11.5px] uppercase tracking-[0.05em] text-muted">
              <tr>{['Company', 'Role', 'Job ID', 'Location', 'Deadline', 'Applied', 'Follow-up', 'Resume', 'Status'].map((h) => <th key={h} className="px-3 py-2.5 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-line">
              {q.data.map((a) => (
                <tr key={a.id} className="hover:bg-surface-2/40">
                  <td className="px-3 py-2.5 font-medium"><Link to={`/applications/${a.id}`} className="hover:text-accent">{a.company || '—'}</Link></td>
                  <td className="px-3 py-2.5">{a.role_title}</td>
                  <td className="px-3 py-2.5 font-mono text-[12px] text-muted">{a.job_code || '—'}</td>
                  <td className="px-3 py-2.5 text-muted">{a.location || '—'}</td>
                  <td className="px-3 py-2.5 tabular">{a.deadline ? fmtDate(a.deadline) : <span className="text-faint">Not specified</span>}</td>
                  <td className="px-3 py-2.5 tabular">{fmtDate(a.date_applied)}</td>
                  <td className="px-3 py-2.5 tabular">{fmtDate(a.follow_up_date)}</td>
                  <td className="px-3 py-2.5">{a.resume_version ? <Link className="hover:text-accent" to={`/resumes/${a.resume_version.id}`}>v{a.resume_version.version_number}</Link> : '—'}</td>
                  <td className="px-3 py-2.5"><StatusBadge status={a.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  )
}
