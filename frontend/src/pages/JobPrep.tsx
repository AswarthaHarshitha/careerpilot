import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, GraduationCap, Search } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { Page } from '../components/AppShell'
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Input, LoadingRows, PageHeader, TaskProgress } from '../components/ui'
import { get, post } from '../lib/api'
import { relative } from '../lib/format'
import type { Cited, JobDetail } from '../lib/types'
import { useTask } from '../lib/useTask'

export default function JobPrep() {
  const { id } = useParams()
  const q = useQuery({ queryKey: ['job', id], queryFn: () => get<JobDetail>(`/api/jobs/${id}`) })
  if (q.isLoading) return <Page><LoadingRows rows={8} /></Page>
  if (q.error) return <Page><ErrorState error={q.error} onRetry={() => q.refetch()} /></Page>
  const j = q.data!
  return (
    <Page wide>
      <PageHeader eyebrow={<Link to={`/jobs/${j.id}`} className="hover:text-ink">{j.company} · {j.role_title}</Link>}
        title="Research & interview prep"
        subtitle="Facts come from public pages and cite them. Everything else is marked as inference or suggested preparation." />
      <div className="grid gap-6 xl:grid-cols-2">
        <Research j={j} />
        <Interview j={j} />
      </div>
    </Page>
  )
}

function Source({ url }: { url: string }) {
  let label = url
  try { const u = new URL(url); label = u.hostname + (u.pathname !== '/' ? u.pathname : '') } catch { /* keep raw */ }
  return <a href={url} target="_blank" rel="noreferrer noopener" className="ml-1 inline-flex items-center gap-0.5 text-[11.5px] text-muted hover:text-accent">{label}<ExternalLink className="size-3" /></a>
}

function Facts({ title, items }: { title: string; items: Cited[] }) {
  if (!items.length) return null
  return (
    <div>
      <div className="mb-1 flex items-center gap-2 text-[12.5px] font-medium">{title}<Badge tone="strong">From the pages</Badge></div>
      <ul className="space-y-1 text-[13px] text-ink-2">{items.map((x, i) => <li key={i}>• {x.statement}<Source url={x.source} /></li>)}</ul>
    </div>
  )
}

function Inferred({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return (
    <div>
      <div className="mb-1 flex items-center gap-2 text-[12.5px] font-medium">{title}<Badge tone="verify">Inference</Badge></div>
      <ul className="space-y-1 text-[13px] text-ink-2">{items.map((x, i) => <li key={i}>• {x}</li>)}</ul>
    </div>
  )
}

function Research({ j }: { j: JobDetail }) {
  const qc = useQueryClient()
  const [site, setSite] = useState(j.company_website || j.research?.website || '')
  const [taskId, setTaskId] = useState<number | null>(null)
  const task = useTask(taskId, [['job', String(j.id)]])
  const run = useMutation({ mutationFn: () => post<{ task_id: number }>(`/api/jobs/${j.id}/research`, { website: site }), onSuccess: (r) => { setTaskId(r.task_id); qc.invalidateQueries({ queryKey: ['job', String(j.id)] }) } })
  const r = j.research
  return (
    <Card>
      <CardHeader title="Company research" subtitle={j.research_at ? `Updated ${relative(j.research_at)}${r?.sources.length ? ` · ${r.sources.length} page(s) read` : ''}` : 'Reads the company’s own public pages'} />
      <div className="space-y-4 p-4">
        <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); run.mutate() }}>
          <Field label="Company website" className="flex-1"><Input value={site} onChange={(e) => setSite(e.target.value)} placeholder="https://company.com" /></Field>
          <Button type="submit" variant="primary" icon={<Search className="size-3.5" />} loading={run.isPending || task?.status === 'running' || task?.status === 'queued'}>{r ? 'Research again' : 'Research company'}</Button>
        </form>
        {run.error && <ErrorState compact error={run.error} />}
        {task && task.status !== 'succeeded' && <TaskProgress steps={task.steps} status={task.status} error={task.error} />}
        {!r ? <p className="text-[13px] text-muted">No research yet.</p> : (
          <div className="space-y-4">
            {r.limitations.map((l) => <p key={l} className="rounded-md bg-partial-soft/60 px-3 py-2 text-[12.5px]">{l}</p>)}
            <Facts title="Overview" items={r.overview} />
            <Facts title="Recent relevant work" items={r.recent_work} />
            <Facts title="Engineering areas" items={r.engineering_areas} />
            <Facts title="Technology signals" items={r.technology_signals} />
            <Inferred title="Role context" items={r.role_context} />
            <Inferred title="Interview themes" items={r.interview_themes} />
            <Inferred title="Role-specific preparation" items={r.preparation} />
            {r.generated_by && <p className="text-[11px] text-faint">{r.generated_by}</p>}
          </div>
        )}
      </div>
    </Card>
  )
}

function Interview({ j }: { j: JobDetail }) {
  const qc = useQueryClient()
  const [taskId, setTaskId] = useState<number | null>(null)
  const task = useTask(taskId, [['job', String(j.id)]])
  const run = useMutation({ mutationFn: () => post<{ task_id: number }>(`/api/jobs/${j.id}/interview-prep`), onSuccess: (r) => { setTaskId(r.task_id); qc.invalidateQueries({ queryKey: ['job', String(j.id)] }) } })
  const p = j.interview_prep
  return (
    <Card>
      <CardHeader title="Interview workspace" subtitle={p ? `Likely / suggested preparation${p.resume_version ? ` · based on resume v${p.resume_version}` : ''}` : 'Prepared automatically after you submit; you can start it any time'}
        actions={<Button size="sm" variant={p ? 'secondary' : 'primary'} icon={<GraduationCap className="size-3.5" />} loading={run.isPending} onClick={() => run.mutate()}>{p ? 'Refresh' : 'Prepare'}</Button>} />
      <div className="space-y-4 p-4">
        {task && task.status !== 'succeeded' && <TaskProgress steps={task.steps} status={task.status} error={task.error} />}
        {!p ? <EmptyState icon={<GraduationCap className="size-7" />} title="No preparation yet">Questions are built from your resume, verified projects and this job — never presented as known interview questions.</EmptyState> : (
          <div className="space-y-5 text-[13px]">
            <p className="rounded-md border border-line bg-surface-2/50 px-3 py-2 text-[12.5px] text-ink-2">{p.label}</p>
            <List title="Likely technical areas" items={p.technical_areas} />
            {!!p.project_questions.length && (
              <div>
                <div className="mb-1.5 text-[12.5px] font-medium">Project deep-dives</div>
                <ul className="space-y-2.5">{p.project_questions.map((x, i) => (
                  <li key={i}><div className="text-ink">{x.question}</div><div className="text-[12.5px] text-muted">{x.project} · {x.answer_outline}</div></li>
                ))}</ul>
              </div>
            )}
            {!!p.resume_questions.length && (
              <div>
                <div className="mb-1.5 text-[12.5px] font-medium">Questions about your resume</div>
                <ul className="space-y-2.5">{p.resume_questions.map((x, i) => (
                  <li key={i}><div className="text-[12.5px] italic text-muted">“{x.item}”</div><div className="text-ink">{x.question}</div><div className="text-[12.5px] text-muted">{x.answer_outline}</div></li>
                ))}</ul>
              </div>
            )}
            <List title="Role-specific questions" items={p.role_questions} />
            <List title="Behavioral" items={p.behavioral} />
            <List title="Coding topics to revise" items={p.coding_topics} />
            {p.generated_by && <p className="text-[11px] text-faint">{p.generated_by}</p>}
          </div>
        )}
      </div>
    </Card>
  )
}

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return <div><div className="mb-1 text-[12.5px] font-medium">{title}</div><ul className="space-y-0.5 text-ink-2">{items.map((x, i) => <li key={i}>• {x}</li>)}</ul></div>
}
