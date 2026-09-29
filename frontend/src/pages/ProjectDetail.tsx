import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, EyeOff, GitBranch, Globe, Plus, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { Page } from '../components/AppShell'
import {
  Badge, Button, Card, CardHeader, ErrorState, EvidenceBadge, Field, Input, LoadingRows, PageHeader, Select, Textarea,
  errorText, useToast,
} from '../components/ui'
import { del, get, patch, post, put } from '../lib/api'
import { fmtDate, relative } from '../lib/format'
import type { Evidence, Project } from '../lib/types'

const KIND_ORDER = ['technology', 'concept', 'testing', 'feature', 'architecture', 'deployment']
const KIND_LABEL: Record<string, string> = {
  technology: 'Technologies', concept: 'Engineering concepts', testing: 'Testing', feature: 'Features (from README)',
  architecture: 'Architecture (from README)', deployment: 'Deployment',
}
const SOURCE_LABEL: Record<string, string> = {
  dependency: 'Dependency', file_tree: 'Repository file', language: 'Language share', readme: 'README',
  metadata: 'Repository settings', user: 'Added by you', llm: 'Model reading',
}

export default function ProjectDetail() {
  const { id } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['project', id], queryFn: () => get<Project>(`/api/projects/${id}`) })
  const set = (d: Project) => { qc.setQueryData(['project', id], d); qc.invalidateQueries({ queryKey: ['projects'] }) }
  const update = useMutation({ mutationFn: (body: Partial<Project>) => patch<Project>(`/api/projects/${id}`, body), onSuccess: set, onError: (e) => toast(errorText(e), 'error') })
  if (q.isLoading) return <Page><LoadingRows rows={8} /></Page>
  if (q.error) return <Page><ErrorState error={q.error} onRetry={() => q.refetch()} /></Page>
  const p = q.data!
  const grouped = KIND_ORDER.map((k) => ({ k, items: (p.evidence ?? []).filter((e) => e.kind === k) })).filter((g) => g.items.length)

  return (
    <Page>
      <PageHeader eyebrow={<Link to="/projects" className="hover:text-ink">Projects</Link>} title={p.title}
        subtitle={<span className="flex flex-wrap items-center gap-3">
          {p.repo_url && <a href={p.repo_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 hover:text-ink"><GitBranch className="size-3.5" />{p.repo_full_name || 'Repository'}</a>}
          {p.homepage_url && <a href={p.homepage_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 hover:text-ink"><Globe className="size-3.5" />Live</a>}
          {p.last_scanned_at && <span>Scanned {relative(p.last_scanned_at)}</span>}
        </span>}
        actions={<Button icon={<EyeOff className="size-3.5" />} onClick={() => update.mutate({ hidden: !p.hidden })}>{p.hidden ? 'Unhide' : 'Hide from library'}</Button>} />
      <Card className="mb-6 p-4">
        {p.asset_type === 'personal_profile' ? (
          <div className="text-[13px]">
            <div className="flex items-center gap-2 font-medium"><Badge tone="verify">Personal profile</Badge>Header link only</div>
            <p className="mt-1 text-ink-2">This is your portfolio/profile. It can appear only as a link in your resume header — never in Projects, never as evidence for a job requirement, and never counted in matching.</p>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-[13px]">
            <label className="flex items-center gap-2">
              <input type="checkbox" className="accent-[var(--accent)]" checked={!p.resume_eligible} onChange={(e) => update.mutate({ resume_eligible: !e.target.checked })} />
              Exclude from resume intelligence
            </label>
            <label className="flex items-center gap-2">Status
              <Select value={p.status} onChange={(e) => update.mutate({ status: e.target.value as Project['status'] })} className="h-8 w-auto text-[12.5px]">
                {['idea', 'planned', 'building', 'built', 'verified', 'deployed'].map((x) => <option key={x} value={x}>{x[0].toUpperCase() + x.slice(1)}</option>)}
              </Select>
            </label>
            <button type="button" className="text-[12.5px] text-muted underline-offset-2 hover:text-ink hover:underline"
              onClick={() => { if (confirm('Mark this repository as your portfolio/profile? It will never be used as a project.')) update.mutate({ asset_type: 'personal_profile' }) }}>
              This is my portfolio/profile
            </button>
            <span className="text-[12px] text-muted">{p.evidence_eligible ? 'Used as evidence for matching and resumes.' : 'Not used as evidence (only Built, Verified or Deployed projects are).'}</span>
          </div>
        )}
      </Card>
      {p.scan_error && <div className="mb-4 rounded-md border border-partial/30 bg-partial-soft px-3 py-2 text-[12.5px]">{p.scan_error}</div>}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader title="Engineering evidence" subtitle="Only Verified and User-provided items are used as resume claims. Review inferred items." />
            <div className="divide-y divide-line">
              {grouped.map((g) => (
                <section key={g.k}>
                  <div className="bg-surface-2/40 px-4 py-2 text-[11.5px] font-medium uppercase tracking-[0.06em] text-muted">{KIND_LABEL[g.k]}</div>
                  <ul className="divide-y divide-line">{g.items.map((e) => <EvidenceRow key={e.id} e={e} projectId={p.id} onChanged={() => q.refetch()} />)}</ul>
                </section>
              ))}
              {!grouped.length && <div className="px-4 py-6 text-[13px] text-muted">No evidence yet.</div>}
            </div>
            <AddEvidence projectId={p.id} onAdded={set} />
          </Card>
          <Bullets p={p} onSaved={set} />
        </div>
        <div className="space-y-6">
          <Card>
            <CardHeader title="Details" />
            <div className="space-y-3 p-4">
              <Field label="Display name"><Input defaultValue={p.display_name || p.name} onBlur={(e) => e.target.value !== p.display_name && update.mutate({ display_name: e.target.value })} /></Field>
              <Field label="Description"><Textarea rows={3} defaultValue={p.description} onBlur={(e) => e.target.value !== p.description && update.mutate({ description: e.target.value })} /></Field>
              {p.languages && p.languages.length > 0 && (
                <div>
                  <div className="mb-1.5 text-[12.5px] font-medium text-ink-2">Languages (measured)</div>
                  <div className="flex h-2 overflow-hidden rounded-full bg-surface-3">
                    {p.languages.map((l, i) => <div key={l.name} style={{ width: `${l.share}%`, opacity: 1 - i * 0.14 }} className="bg-accent" title={`${l.name} ${l.share}%`} />)}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-x-3 text-[11.5px] text-muted">{p.languages.map((l) => <span key={l.name}>{l.name} {l.share}%</span>)}</div>
                </div>
              )}
            </div>
          </Card>
          {p.analysis && p.analysis.analyzer_version && (
            <Card>
              <CardHeader title="Repository analysis" subtitle={`Cached from the last scan${p.last_scanned_at ? ' · ' + fmtDate(p.last_scanned_at) : ''}. Re-read when the repository changes, after 30 days, or when you rescan.`} />
              <dl className="grid gap-x-4 gap-y-2 px-4 py-3 text-[12.5px] sm:grid-cols-[140px_1fr]">
                {p.analysis.readme_summary && <><dt className="text-muted">Summary</dt><dd>{p.analysis.readme_summary}{p.analysis.summary_by && <span className="text-faint"> · read by {p.analysis.summary_by}</span>}</dd></>}
                <dt className="text-muted">Frameworks</dt><dd>{p.analysis.frameworks?.join(', ') || '—'}</dd>
                <dt className="text-muted">Dependencies</dt><dd className="text-ink-2">{p.analysis.dependencies?.length ? `${p.analysis.dependencies.slice(0, 18).join(', ')}${p.analysis.dependencies.length > 18 ? ` +${p.analysis.dependencies.length - 18}` : ''}` : '—'}</dd>
                <dt className="text-muted">Tests</dt><dd>{p.analysis.tests_detected ? `Yes · ${p.analysis.tests}` : 'None found'}</dd>
                <dt className="text-muted">Deployment</dt><dd>{p.analysis.deployment_detected ? p.analysis.deployment?.join(', ') : 'None found'}</dd>
                {!!p.analysis.features_detected?.length && <><dt className="text-muted">Features</dt><dd><ul className="space-y-0.5">{p.analysis.features_detected.slice(0, 6).map((f) => <li key={f}>• {f}</li>)}</ul></dd></>}
                <dt className="text-muted">Confidence</dt><dd className="capitalize">{p.analysis.confidence} · {p.analysis.evidence_items} evidence items</dd>
              </dl>
            </Card>
          )}
          {p.readme_excerpt && (
            <Card>
              <details>
                <summary className="cursor-pointer px-4 py-3 text-[13.5px] font-semibold">README excerpt</summary>
                <div className="prose-jd border-t border-line px-4 py-3 text-[12.5px]">{p.readme_excerpt}</div>
              </details>
            </Card>
          )}
        </div>
      </div>
    </Page>
  )
}

function EvidenceRow({ e, projectId, onChanged }: { e: Evidence; projectId: number; onChanged: () => void }) {
  const toast = useToast()
  const review = useMutation({
    mutationFn: (status: Evidence['status']) => patch(`/api/projects/${projectId}/evidence/${e.id}`, { status }),
    onSuccess: onChanged, onError: (err) => toast(errorText(err), 'error'),
  })
  const remove = useMutation({ mutationFn: () => del(`/api/projects/${projectId}/evidence/${e.id}`), onSuccess: onChanged })
  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <span className={e.status === 'unsupported' ? 'text-muted line-through' : 'font-medium'}>{e.label}</span>
          <EvidenceBadge status={e.status} />
          <span className="text-[11.5px] text-muted">{SOURCE_LABEL[e.source_type] ?? e.source_type}{e.source_detail ? ` · ${e.source_detail}` : ''}</span>
        </div>
        {e.snippet && <div className="mt-0.5 line-clamp-2 text-[12px] italic text-muted">“{e.snippet}”</div>}
      </div>
      <div className="flex shrink-0 gap-1">
        {e.status === 'inferred' && (
          <Button size="sm" variant="ghost" icon={<Check className="size-3.5" />} title="I confirm this is accurate" onClick={() => review.mutate('verified')}>Confirm</Button>
        )}
        {e.status !== 'unsupported' ? (
          <Button size="sm" variant="ghost" icon={<X className="size-3.5" />} title="Not accurate - never use this" onClick={() => review.mutate('unsupported')} aria-label="Mark unsupported" />
        ) : (
          <Button size="sm" variant="ghost" onClick={() => review.mutate('inferred')}>Restore</Button>
        )}
        {e.source_type === 'user' && <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => remove.mutate()} aria-label="Delete" />}
      </div>
    </li>
  )
}

function AddEvidence({ projectId, onAdded }: { projectId: number; onAdded: (p: Project) => void }) {
  const [label, setLabel] = useState('')
  const [kind, setKind] = useState('technology')
  const m = useMutation({
    mutationFn: () => post<Project>(`/api/projects/${projectId}/evidence`, { label, kind }),
    onSuccess: (p) => { setLabel(''); onAdded(p) },
  })
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (label.trim()) m.mutate() }} className="flex flex-wrap gap-2 border-t border-line p-3">
      <Select value={kind} onChange={(e) => setKind(e.target.value)} className="h-8.5 w-auto text-[13px]">
        {KIND_ORDER.map((k) => <option key={k} value={k}>{KIND_LABEL[k].replace(' (from README)', '')}</option>)}
      </Select>
      <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Add evidence you can vouch for, e.g. Redis caching" className="h-8.5 min-w-0 flex-1" />
      <Button size="md" type="submit" icon={<Plus className="size-3.5" />} loading={m.isPending} disabled={!label.trim()}>Add</Button>
    </form>
  )
}

function Bullets({ p, onSaved }: { p: Project; onSaved: (p: Project) => void }) {
  const [items, setItems] = useState<string[]>(() => (p.bullets ?? []).map((b) => b.text))
  const toast = useToast()
  const m = useMutation({ mutationFn: () => put<Project>(`/api/projects/${p.id}/bullets`, { bullets: items }), onSuccess: (d) => { onSaved(d); toast('Bullets saved') } })
  return (
    <Card>
      <CardHeader title="Resume bullets" subtitle="Your own wording for this project. Tailoring reorders and may reword these, never adding facts."
        actions={<Button size="sm" variant="primary" loading={m.isPending} onClick={() => m.mutate()}>Save</Button>} />
      <div className="space-y-2 p-4">
        {items.map((b, i) => (
          <div key={i} className="flex gap-2">
            <Textarea rows={2} value={b} onChange={(e) => setItems(items.map((x, j) => (j === i ? e.target.value : x)))} />
            <Button variant="ghost" aria-label="Remove bullet" icon={<Trash2 className="size-3.5" />} onClick={() => setItems(items.filter((_, j) => j !== i))} />
          </div>
        ))}
        {items.length < 8 && <Button size="sm" icon={<Plus className="size-3.5" />} onClick={() => setItems([...items, ''])}>Add bullet</Button>}
        {!items.length && <p className="text-[12.5px] text-muted">Without bullets, CareerPilot drafts them from the description and verified evidence and marks them for your review.</p>}
      </div>
      {p.bullets?.some((b) => b.source === 'template_import') && <div className="border-t border-line px-4 py-2 text-[11.5px] text-muted"><Badge>Imported</Badge> Some bullets came from your resume template.</div>}
    </Card>
  )
}
