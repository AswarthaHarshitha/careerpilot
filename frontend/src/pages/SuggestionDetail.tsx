import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ClipboardCopy, GitBranch, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Page } from '../components/AppShell'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Input, LoadingRows, PageHeader, TaskProgress, useToast } from '../components/ui'
import { api, del, get, post } from '../lib/api'
import type { Suggestion } from '../lib/types'
import { useTask } from '../lib/useTask'
import { SUGGESTION_STATUS } from './JobDetail'

function List({ title, items }: { title: string; items: string[] }) {
  return (
    <Card><CardHeader title={title} /><ul className="space-y-1.5 px-4 py-3 text-[13px] text-ink-2">{items.map((x, i) => <li key={i} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-faint" />{x}</li>)}</ul></Card>
  )
}

export default function SuggestionDetail() {
  const { id } = useParams()
  const q = useQuery({ queryKey: ['suggestion', id], queryFn: () => get<Suggestion>(`/api/suggested-projects/${id}`) })
  const [repo, setRepo] = useState('')
  const [taskId, setTaskId] = useState<number | null>(null)
  const qc = useQueryClient()
  const toast = useToast()
  const nav = useNavigate()
  const link = useMutation({
    mutationFn: () => post<{ suggestion: Suggestion; task_id: number }>(`/api/suggested-projects/${id}/link`, { repo_url: repo }),
    onSuccess: (r) => { qc.setQueryData(['suggestion', id], r.suggestion); setTaskId(r.task_id) },
  })
  const task = useTask(taskId, [['suggestion', id], ['projects'], ['suggestions']])
  const setStatus = useMutation({
    mutationFn: (status: string) => api<Suggestion>(`/api/suggested-projects/${id}`, { method: 'PATCH', body: { status } }),
    onSuccess: (d) => qc.setQueryData(['suggestion', id], d),
  })
  const remove = useMutation({ mutationFn: () => del(`/api/suggested-projects/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['suggestions'] }); nav('/projects') } })
  if (q.isLoading) return <Page><LoadingRows rows={8} /></Page>
  if (q.error) return <Page><ErrorState error={q.error} onRetry={() => q.refetch()} /></Page>
  const s = q.data!
  const p = s.plan
  return (
    <Page>
      <PageHeader eyebrow={<Link to="/projects" className="hover:text-ink">Suggested project</Link>} title={p.title}
        subtitle={p.summary}
        actions={<Button variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => remove.mutate()}>Discard</Button>} />
      <div className={['verified', 'deployed'].includes(s.status) ? 'mb-6 flex items-start gap-3 rounded-lg border border-strong/25 bg-strong-soft px-4 py-3' : 'mb-6 flex items-start gap-3 rounded-lg border border-partial/30 bg-partial-soft px-4 py-3'}>
        {['verified', 'deployed'].includes(s.status) ? <CheckCircle2 className="mt-0.5 size-4 text-strong" /> : <AlertTriangle className="mt-0.5 size-4 text-partial" />}
        <div className="text-[13px]">
          <div className="font-semibold">{['verified', 'deployed'].includes(s.status) ? 'Verified from your repository' : s.status === 'built' ? 'Built — waiting for verified evidence' : `NOT YET BUILT · ${SUGGESTION_STATUS[s.status]}`}</div>
          <div className="text-ink-2">
            {['verified', 'deployed'].includes(s.status) ? s.verification_notes : 'This is a plan, not experience. It is never shown on a resume. Build it, then link the repository: it becomes evidence only when the scan verifies the skills below.'}
          </div>
          {['building', 'built'].includes(s.status) && s.verification_notes && <div className="mt-1 text-muted">{s.verification_notes}</div>}
        </div>
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
          {p.why && <Card className="p-4 text-[13px] text-ink-2"><span className="font-medium text-ink">Why this project: </span>{p.why}</Card>}
          {s.verification.length > 0 && (
            <Card>
              <CardHeader title="Project verification" subtitle="From the linked repository" />
              <ul className="divide-y divide-line">
                {s.verification.map((x) => (
                  <li key={x.skill} className="flex items-center justify-between px-4 py-2 text-[13px]">
                    <span>{x.label}{x.gap && <span className="text-faint"> · gap</span>}</span>
                    <span className={x.verified ? 'text-strong' : 'text-missing'}>{x.verified ? `✓ ${x.source}` : '✗ not found'}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <List title="Architecture" items={p.architecture} />
          <List title="Features" items={p.features} />
          <Card>
            <CardHeader title="Implementation phases" />
            <ol className="divide-y divide-line">
              {p.phases.map((ph) => (
                <li key={ph.name} className="px-4 py-3">
                  <div className="text-[13px] font-semibold">{ph.name}</div>
                  <ul className="mt-1 space-y-0.5 text-[12.5px] text-ink-2">{ph.tasks.map((t, i) => <li key={i}>– {t}</li>)}</ul>
                </li>
              ))}
            </ol>
          </Card>
          <div className="grid gap-6 md:grid-cols-2">
            <List title="Tests" items={p.tests} />
            <List title="Deployment" items={p.deployment} />
            <List title="README outline" items={p.readme_outline} />
            <List title="GitHub setup" items={p.github_setup} />
            {p.interview_points && <List title="Interview talking points" items={p.interview_points} />}
          </div>
          <Card>
            <CardHeader title="Repository structure" />
            <pre className="overflow-x-auto px-4 py-3 font-mono text-[12px] text-ink-2">{p.repo_structure}</pre>
          </Card>
          <Card>
            <CardHeader title="Implementation prompt" subtitle="Paste into your coding assistant to start the build."
              actions={<Button size="sm" icon={<ClipboardCopy className="size-3.5" />} onClick={() => { navigator.clipboard.writeText(p.coding_prompt); toast('Copied') }}>Copy</Button>} />
            <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap px-4 py-3 font-mono text-[12px] leading-relaxed text-ink-2">{p.coding_prompt}</pre>
          </Card>
        </div>
        <div className="space-y-6">
          <Card>
            <CardHeader title="Closes these gaps" />
            <div className="space-y-3 px-4 py-3">
              <div className="flex flex-wrap gap-1">{s.gap_skills.map((g) => <Badge key={g} tone="missing">{g}</Badge>)}</div>
              {s.existing_skills.length > 0 && <div className="text-[12.5px] text-muted">Builds on: {s.existing_skills.join(', ')}</div>}
              {p.for_role && <div className="text-[12.5px] text-muted">For: {p.for_role}{s.job_id && <> · <Link to={`/jobs/${s.job_id}`} className="text-accent hover:underline">view job</Link></>}</div>}
              <div className="flex flex-wrap gap-1">{p.stack.map((t) => <Badge key={t}>{t}</Badge>)}</div>
            </div>
          </Card>
          {!['verified', 'deployed'].includes(s.status) && ['idea', 'planned', 'building'].includes(s.status) && (
            <Card>
              <CardHeader title="Status" />
              <div className="flex gap-1 p-3">
                {(['idea', 'planned', 'building'] as const).map((x) => (
                  <Button key={x} size="sm" variant={s.status === x ? 'primary' : 'secondary'} onClick={() => setStatus.mutate(x)}>{SUGGESTION_STATUS[x]}</Button>
                ))}
              </div>
            </Card>
          )}
          {!['verified', 'deployed'].includes(s.status) && (
            <Card>
              <CardHeader title="Built it?" subtitle="Link the repository to verify it." />
              <form className="space-y-2.5 p-4" onSubmit={(e) => { e.preventDefault(); if (repo) link.mutate() }}>
                <Field label="Repository URL"><Input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="https://github.com/you/project" /></Field>
                <Button type="submit" className="w-full" icon={<GitBranch className="size-3.5" />} loading={link.isPending} disabled={!repo}>Link and verify</Button>
                {link.error && <ErrorState compact error={link.error} />}
                {task && <TaskProgress steps={task.steps} status={task.status} error={task.error} />}
              </form>
            </Card>
          )}
        </div>
      </div>
    </Page>
  )
}
