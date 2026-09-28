import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { EyeOff, FolderGit2, GitBranch, Globe, Hammer, Plus, RefreshCw, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { Page } from '../components/AppShell'
import {
  Badge, Button, Card, Dialog, EmptyState, ErrorState, Field, Input, LoadingRows, PageHeader, Tabs, TaskProgress,
  Textarea, cx,
} from '../components/ui'
import { get, post } from '../lib/api'
import { relative } from '../lib/format'
import type { Project, Suggestion } from '../lib/types'
import { useTask } from '../lib/useTask'

export default function Projects() {
  const [tab, setTab] = useState<'library' | 'suggested'>('library')
  const q = useQuery({ queryKey: ['projects'], queryFn: () => get<Project[]>('/api/projects') })
  const sugg = useQuery({ queryKey: ['suggestions'], queryFn: () => get<Suggestion[]>('/api/suggested-projects') })
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => get<{ github: { username: string; last_scan_at: string | null } }>('/api/settings') })
  const [taskId, setTaskId] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [showHidden, setShowHidden] = useState(false)
  const [adding, setAdding] = useState(false)
  const nav = useNavigate()
  const scan = useMutation({ mutationFn: () => post<{ task_id: number }>('/api/github/scan', {}), onSuccess: (r) => setTaskId(r.task_id) })
  const task = useTask(taskId, [['projects'], ['settings']])
  const running = task && (task.status === 'queued' || task.status === 'running')
  const rows = useMemo(() => {
    const s = search.toLowerCase()
    return (q.data ?? []).filter((p) => (showHidden || !p.hidden) && (!s || `${p.title} ${p.description} ${p.technologies.join(' ')}`.toLowerCase().includes(s)))
  }, [q.data, search, showHidden])
  const gh = settings.data?.github

  return (
    <Page wide>
      <PageHeader title="Projects" subtitle="Your engineering portfolio as evidence: what each repository verifiably demonstrates."
        actions={<>
          <Button icon={<Plus className="size-4" />} onClick={() => setAdding(true)}>Add project</Button>
          {gh?.username ? (
            <Button variant="primary" icon={<RefreshCw className={cx('size-4', running && 'animate-spin')} />} loading={scan.isPending} disabled={!!running} onClick={() => scan.mutate()}>Scan GitHub</Button>
          ) : (
            <Button variant="primary" icon={<GitBranch className="size-4" />} onClick={() => nav('/settings?tab=github')}>Connect GitHub</Button>
          )}
        </>} />
      {gh?.username && <p className="-mt-3 mb-4 text-[12.5px] text-muted">github.com/{gh.username}{gh.last_scan_at ? ` · last scanned ${relative(gh.last_scan_at)}` : ' · not scanned yet'}</p>}
      {scan.error && <div className="mb-4"><ErrorState error={scan.error} onAction={(a) => a === 'open_settings' && nav('/settings?tab=github')} /></div>}
      {task && task.status !== 'succeeded' && (
        <Card className="mb-4 p-4"><TaskProgress steps={task.steps} status={task.status} error={task.error} />
          {task.status === 'queued' && task.error && <p className="mt-2 text-[12.5px] text-partial">{task.error}</p>}</Card>
      )}
      <Tabs className="mb-4" value={tab} onChange={setTab} tabs={[
        { value: 'library', label: 'Library', count: q.data?.filter((p) => !p.hidden).length },
        { value: 'suggested', label: 'Suggested to build', count: sugg.data?.length },
      ]} />
      {tab === 'library' ? (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="relative w-full sm:w-72">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-faint" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search projects or technologies" className="pl-8" />
            </div>
            <label className="flex items-center gap-2 text-[12.5px] text-muted"><input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} className="accent-[var(--accent)]" />Show hidden</label>
          </div>
          {q.isLoading ? <LoadingRows /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : rows.length === 0 ? (
            <EmptyState icon={<FolderGit2 className="size-7" />} title="No projects yet"
              action={gh?.username ? <Button variant="primary" onClick={() => scan.mutate()}>Scan GitHub</Button> : <Button variant="primary" onClick={() => nav('/settings?tab=github')}>Connect GitHub</Button>}>
              CareerPilot reads each repository's README, dependencies and structure to record what it demonstrates, and labels every item as verified or inferred.
            </EmptyState>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {rows.map((p, i) => (
                <motion.div key={p.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 12) * 0.02 }}>
                  <Link to={`/projects/${p.id}`} className={cx('flex h-full flex-col rounded-lg border border-line bg-surface p-4 shadow-card transition-[border,transform] hover:-translate-y-px hover:border-line-strong', p.hidden && 'opacity-60')}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-[14.5px] font-semibold">{p.title}</div>
                        <div className="mt-0.5 flex items-center gap-2 text-[12px] text-muted">
                          {p.origin === 'github' ? <><GitBranch className="size-3" />{p.repo_full_name.split('/')[1]}</> : 'Added manually'}
                          {p.homepage_url && <><Globe className="size-3" />live</>}
                          {p.hidden && <><EyeOff className="size-3" />hidden</>}
                        </div>
                      </div>
                      {p.pushed_at && <span className="shrink-0 text-[11.5px] text-faint">{relative(p.pushed_at)}</span>}
                    </div>
                    <p className="mt-2 line-clamp-2 text-[12.5px] text-ink-2">{p.description || <span className="text-faint">No description</span>}</p>
                    <div className="mt-3 flex flex-wrap gap-1">{p.technologies.slice(0, 8).map((t) => <Badge key={t}>{t}</Badge>)}</div>
                    <div className="mt-auto flex items-center gap-3 pt-3 text-[11.5px] text-muted">
                      <span><span className="font-medium text-strong tabular">{p.evidence_counts.verified ?? 0}</span> verified</span>
                      {(p.evidence_counts.inferred ?? 0) > 0 && <span><span className="font-medium text-verify tabular">{p.evidence_counts.inferred}</span> to review</span>}
                      <span>{p.bullet_count ? `${p.bullet_count} bullets` : 'no bullets'}</span>
                    </div>
                  </Link>
                </motion.div>
              ))}
            </div>
          )}
        </>
      ) : (
        sugg.isLoading ? <LoadingRows /> : !sugg.data?.length ? (
          <EmptyState icon={<Hammer className="size-7" />} title="No suggested projects">
            When a job needs a skill your portfolio doesn't show, open the job and choose “Create supporting project plan”.
          </EmptyState>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {sugg.data.map((s) => (
              <Link key={s.id} to={`/suggestions/${s.id}`} className="rounded-lg border border-line bg-surface p-4 shadow-card hover:border-line-strong">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-[14px] font-semibold">{s.title}</span>
                  <Badge tone={s.status === 'verified' ? 'strong' : 'partial'}>{s.status === 'verified' ? 'Verified' : s.status === 'in_progress' ? 'In progress · not verified' : 'Not yet built'}</Badge>
                </div>
                <div className="mt-2 flex flex-wrap gap-1">{s.gap_skills.map((g) => <Badge key={g} tone="missing">{g}</Badge>)}</div>
              </Link>
            ))}
          </div>
        )
      )}
      <AddProject open={adding} onClose={() => setAdding(false)} onRepoTask={setTaskId} />
    </Page>
  )
}

function AddProject({ open, onClose, onRepoTask }: { open: boolean; onClose: () => void; onRepoTask: (id: number) => void }) {
  const [mode, setMode] = useState<'repo' | 'manual'>('repo')
  const [repo, setRepo] = useState('')
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [url, setUrl] = useState('')
  const qc = useQueryClient()
  const nav = useNavigate()
  const m = useMutation({
    mutationFn: async () => mode === 'repo'
      ? post<{ task_id: number }>('/api/github/repos', { repo_url: repo })
      : post<Project>('/api/projects', { display_name: name, description: desc, homepage_url: url }),
    onSuccess: (r) => {
      onClose()
      if ('task_id' in r) onRepoTask(r.task_id)
      else { qc.invalidateQueries({ queryKey: ['projects'] }); nav(`/projects/${(r as Project).id}`) }
    },
  })
  return (
    <Dialog open={open} onClose={onClose} title="Add a project"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={mode === 'repo' ? !repo : !name} onClick={() => m.mutate()}>Add</Button></>}>
      <Tabs className="mb-4" value={mode} onChange={setMode} tabs={[{ value: 'repo', label: 'GitHub repository' }, { value: 'manual', label: 'Without a repository' }]} />
      {mode === 'repo' ? (
        <Field label="Repository URL" hint="Public, or private if you added a token in Settings."><Input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="https://github.com/you/project" /></Field>
      ) : (
        <div className="space-y-3">
          <Field label="Project name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Description"><Textarea rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
          <Field label="Live URL (optional)"><Input value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
          <p className="text-[12px] text-muted">Evidence you add for projects without a repository is recorded as user-provided.</p>
        </div>
      )}
      {m.error && <div className="mt-3"><ErrorState compact error={m.error} /></div>}
    </Dialog>
  )
}
