import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { BarChart3 } from 'lucide-react'
import { Page } from '../components/AppShell'
import { Card, CardHeader, EmptyState, ErrorState, LoadingRows, PageHeader } from '../components/ui'
import { get } from '../lib/api'
import { STATUS_LABEL } from '../lib/format'
import type { AppStatus } from '../lib/types'

type A = {
  applications_by_month: [string, number][]
  applications_this_month: number
  submitted_total: number
  interview_conversion: { interviews: number; submitted: number }
  status_counts: Record<string, number>
  most_requested: { skill: string; count: number }[]
  most_missing: { skill: string; count: number }[]
  by_company: { company: string; count: number }[]
  resume_versions: number
  jobs_analyzed: number
}

function Bars({ rows, tone = 'accent' }: { rows: { label: string; value: number }[]; tone?: 'accent' | 'missing' }) {
  const max = Math.max(1, ...rows.map((r) => r.value))
  return (
    <ul className="space-y-2 px-4 py-3">
      {rows.map((r, i) => (
        <li key={r.label} className="grid grid-cols-[120px_1fr_32px] items-center gap-3 text-[12.5px]">
          <span className="truncate text-ink-2">{r.label}</span>
          <div className="h-2 overflow-hidden rounded-full bg-surface-2">
            <motion.div initial={{ width: 0 }} animate={{ width: `${(r.value / max) * 100}%` }} transition={{ delay: i * 0.03, duration: 0.4 }}
              className={tone === 'accent' ? 'h-full rounded-full bg-accent' : 'h-full rounded-full bg-missing/70'} />
          </div>
          <span className="text-right tabular text-muted">{r.value}</span>
        </li>
      ))}
      {!rows.length && <li className="text-[12.5px] text-muted">No data yet.</li>}
    </ul>
  )
}

export default function Analytics() {
  const q = useQuery({ queryKey: ['analytics'], queryFn: () => get<A>('/api/analytics') })
  return (
    <Page>
      <PageHeader title="Analytics" subtitle="Your own workflow, measured. No predictions about hiring outcomes." />
      {q.isLoading ? <LoadingRows /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : q.data!.jobs_analyzed === 0 ? (
        <EmptyState icon={<BarChart3 className="size-7" />} title="Nothing to measure yet">Analytics appear once you've analyzed jobs and applied.</EmptyState>
      ) : (() => {
        const d = q.data!
        const conv = d.interview_conversion
        return (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line md:grid-cols-4">
              {[
                ['Applications this month', d.applications_this_month],
                ['Submitted in total', d.submitted_total],
                ['Reached interview', conv.submitted ? `${conv.interviews} of ${conv.submitted}` : '—'],
                ['Resume versions created', d.resume_versions],
              ].map(([l, v]) => (
                <div key={String(l)} className="bg-surface px-4 py-3.5">
                  <div className="text-[12px] text-muted">{l}</div>
                  <div className="mt-1 font-serif text-[26px] font-semibold leading-none tabular">{v}</div>
                </div>
              ))}
            </div>
            <div className="grid gap-6 lg:grid-cols-2">
              <Card><CardHeader title="Most requested skills" subtitle="Across the jobs you analyzed" /><Bars rows={d.most_requested.map((x) => ({ label: x.skill, value: x.count }))} /></Card>
              <Card><CardHeader title="Most common gaps" subtitle="Requested skills with no evidence in your profile" /><Bars tone="missing" rows={d.most_missing.map((x) => ({ label: x.skill, value: x.count }))} /></Card>
              <Card><CardHeader title="Applications by month" /><Bars rows={d.applications_by_month.map(([m, n]) => ({ label: new Date(m + '-01').toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }), value: n }))} /></Card>
              <Card><CardHeader title="By company" /><Bars rows={d.by_company.map((x) => ({ label: x.company, value: x.count }))} /></Card>
              <Card className="lg:col-span-2"><CardHeader title="Status breakdown" />
                <Bars rows={Object.entries(d.status_counts).map(([s, n]) => ({ label: STATUS_LABEL[s as AppStatus] ?? s, value: n }))} /></Card>
            </div>
          </div>
        )
      })()}
    </Page>
  )
}
