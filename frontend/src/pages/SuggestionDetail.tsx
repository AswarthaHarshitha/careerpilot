import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ClipboardCopy, GitBranch, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Page } from '../components/AppShell'
import { Badge, Button, Card, CardHeader, ErrorState, Field, Input, LoadingRows, PageHeader, TaskProgress, useToast } from '../components/ui'
import { del, get, post } from '../lib/api'
import type { Suggestion } from '../lib/types'
import { useTask } from '../lib/useTask'

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
  const remove = useMutation({ mutationFn: () => del(`/api/suggested-projects/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['suggestions'] }); nav('/projects') } })
  if (q.isLoading) return <Page><LoadingRows rows={8} /></Page>
  if (q.error) return <Page><ErrorState error={q.error} onRetry={() => q.refetch()} /></Page>
  const s = q.data!
  const p = s.plan
  const List = ({ title, items }: { title: string; items: string[] }) => (
    <Card><CardHeader title={title} /><ul className="space-y-1.5 px-4 py-3 text-[13px] text-ink-2">{items.map((x, i) => <li key={i} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-faint" />{x}</li>)}</ul></Card>
  )
  return (
    <Page>
      <PageHeader eyebrow={<Link to="/projects" className="hover:text-ink">Suggested project</Link>} title={p.title}
        subtitle={p.summary}
        actions={<Button variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => remove.mutate()}>Discard</Button>} />
      <div className={s.status === 'verified' ? 'mb-6 flex items-start gap-3 rounded-lg border border-strong/25 bg-strong-soft px-4 py-3' : 'mb-6 flex items-start gap-3 rounded-lg border border-partial/30 bg-partial-soft px-4 py-3'}>
        {s.status === 'verified' ? <CheckCircle2 className="mt-0.5 size-4 text-strong" /> : <AlertTriangle className="mt-0.5 size-4 text-partial" />}
        <div className="text-[13px]">
          <div className="font-semibold">{s.status === 'verified' ? 'Verified from your repository' : 'NOT YET BUILT'}</div>
          <div className="text-ink-2">
            {s.status === 'verified' ? s.verification_notes : 'This is a plan, not experience. It is never shown on a resume. Build it, then link the repository: it becomes evidence only when the scan verifies the skills below.'}
          </div>
          {s.status === 'in_progress' && s.verification_notes && <div className="mt-1 text-muted">{s.verification_notes}</div>}
        </div>
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
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
          {s.status !== 'verified' && (
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
