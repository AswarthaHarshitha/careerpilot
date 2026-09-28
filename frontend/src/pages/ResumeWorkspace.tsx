import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle, CheckCircle2, Copy, Download, Eye, FileCode2, GitCompare, Info, Lock, PenLine, RefreshCw, Send,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Page } from '../components/AppShell'
import {
  Badge, Button, Card, CardHeader, Dialog, ErrorState, Input, LevelText, LoadingRows, MatchBadge, Select, Spinner,
  Textarea, cx, errorText, useToast,
} from '../components/ui'
import { api, get, patch, post } from '../lib/api'
import { fmtDateTime } from '../lib/format'
import type { Finding, JobDetail, ResumeSection, ResumeVersion } from '../lib/types'

export default function ResumeWorkspace() {
  const { id } = useParams()
  const v = useQuery({ queryKey: ['resume', id], queryFn: () => get<ResumeVersion>(`/api/resumes/${id}`) })
  const job = useQuery({
    queryKey: ['job', String(v.data?.job_id)],
    queryFn: () => get<JobDetail>(`/api/jobs/${v.data!.job_id}`),
    enabled: !!v.data?.job_id,
  })
  if (v.isLoading) return <Page wide><LoadingRows rows={8} /></Page>
  if (v.error) return <Page wide><ErrorState error={v.error} onRetry={() => v.refetch()} /></Page>
  return <Workspace v={v.data!} job={job.data} />
}

function Workspace({ v, job }: { v: ResumeVersion; job?: JobDetail }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const toast = useToast()
  const [mode, setMode] = useState<'preview' | 'edit'>('preview')
  const [pdfNonce, setPdfNonce] = useState(0)
  const [compare, setCompare] = useState(false)
  const locked = v.status === 'approved' || v.is_base
  const refresh = (data: ResumeVersion) => {
    qc.setQueryData(['resume', String(v.id)], data)
    qc.invalidateQueries({ queryKey: ['resumes'] })
    setPdfNonce((n) => n + 1)
  }
  const approve = useMutation({
    mutationFn: () => post(`/api/resumes/${v.id}/approve`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['resume', String(v.id)] }); qc.invalidateQueries({ queryKey: ['job'] }); toast('Version approved') },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const duplicate = useMutation({
    mutationFn: () => post<ResumeVersion>(`/api/resumes/${v.id}/duplicate`),
    onSuccess: (d) => nav(`/resumes/${d.id}`),
  })
  const regen = useMutation({
    mutationFn: (index: number) => post<ResumeVersion>(`/api/resumes/${v.id}/regenerate-section`, { section_index: index }),
    onSuccess: (d) => { refresh(d); toast('Section regenerated') },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const sections = v.content?.sections ?? []
  const editable = sections.filter((s) => !s.unchanged)

  return (
    <div className="mx-auto max-w-[1560px] px-4 py-5 md:px-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-2 text-[12px] font-medium uppercase tracking-[0.08em] text-muted">
            <Link to="/resumes" className="hover:text-ink">Resumes</Link><span>/</span>
            {job ? <Link to={`/jobs/${job.id}`} className="normal-case tracking-normal hover:text-ink">{job.company} · {job.role_title}</Link> : <span>Original</span>}
          </div>
          <h1 className="font-serif text-[24px] font-semibold leading-tight">{v.is_base ? v.label : `Version ${v.version_number}`}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
            <Badge tone={v.status === 'approved' ? 'strong' : 'neutral'} dot>{v.status === 'approved' ? 'Approved' : 'Draft'}</Badge>
            <span>{fmtDateTime(v.created_at)}</span>
            {v.page_count && <span>· {v.page_count} page{v.page_count > 1 ? 's' : ''}</span>}
            {v.used_llm && <span>· wording assisted, truth-checked</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!locked && editable.length > 0 && (
            <Select aria-label="Regenerate section" value="" disabled={regen.isPending} onChange={(e) => e.target.value && regen.mutate(Number(e.target.value))} className="h-8.5 w-auto text-[13px]">
              <option value="">{regen.isPending ? 'Regenerating…' : 'Regenerate section'}</option>
              {editable.map((s) => <option key={s.index} value={s.index}>{s.title}</option>)}
            </Select>
          )}
          <Button icon={<GitCompare className="size-3.5" />} onClick={() => setCompare(true)}>Compare</Button>
          {locked ? (
            <Button icon={<Copy className="size-3.5" />} loading={duplicate.isPending} onClick={() => duplicate.mutate()}>Duplicate to edit</Button>
          ) : (
            <Button variant="primary" icon={<CheckCircle2 className="size-3.5" />} loading={approve.isPending} onClick={() => approve.mutate()} disabled={!v.has_pdf}>Approve</Button>
          )}
          <a href={`/api/resumes/${v.id}/pdf?download=true`}><Button icon={<Download className="size-3.5" />} disabled={!v.has_pdf}>PDF</Button></a>
          {v.has_source && <a href={`/api/resumes/${v.id}/source`}><Button icon={<FileCode2 className="size-3.5" />}>.{v.source_format}</Button></a>}
          {job?.application && <Link to={`/applications/${job.application.id}`}><Button icon={<Send className="size-3.5" />}>Apply</Button></Link>}
        </div>
      </div>

      {v.render_error && <div className="mb-4"><ErrorState compact error={new Error(v.render_error)} /></div>}

      <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)_340px] lg:grid-cols-[minmax(0,1fr)_320px]">
        <aside className="order-2 space-y-4 xl:order-1">
          <LeftPanel v={v} job={job} />
        </aside>
        <section className="order-1 min-w-0 lg:order-1 xl:order-2">
          <div className="mb-2 flex items-center gap-1 rounded-md border border-line bg-surface p-0.5 text-[12.5px] shadow-card w-fit">
            <button onClick={() => setMode('preview')} className={cx('flex items-center gap-1.5 rounded px-2.5 py-1', mode === 'preview' ? 'bg-surface-2 font-medium text-ink' : 'text-muted')}><Eye className="size-3.5" />Preview</button>
            <button onClick={() => setMode('edit')} className={cx('flex items-center gap-1.5 rounded px-2.5 py-1', mode === 'edit' ? 'bg-surface-2 font-medium text-ink' : 'text-muted')}>
              {locked ? <Lock className="size-3.5" /> : <PenLine className="size-3.5" />}Content
            </button>
          </div>
          <AnimatePresence mode="wait">
            {mode === 'preview' ? (
              <motion.div key="p" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <PdfPreview url={`/api/resumes/${v.id}/pdf`} nonce={pdfNonce} available={v.has_pdf} />
              </motion.div>
            ) : (
              <motion.div key="e" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <ContentEditor v={v} locked={locked} onSaved={(d) => { refresh(d); setMode('preview') }} />
              </motion.div>
            )}
          </AnimatePresence>
        </section>
        <aside className="order-3 space-y-4">
          <ReviewPanel v={v} />
        </aside>
      </div>
      <CompareDialog open={compare} onClose={() => setCompare(false)} v={v} />
    </div>
  )
}

export function PdfPreview({ url, nonce = 0, available = true, height = 'h-[calc(100vh-190px)] min-h-[560px]' }: { url: string; nonce?: number; available?: boolean; height?: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => {
    if (!available) return
    let revoked: string | null = null
    const ctrl = new AbortController()
    setBlobUrl(null)
    setErr(null)
    api<Blob>(url, { signal: ctrl.signal })
      .then((b) => { revoked = URL.createObjectURL(b); setBlobUrl(revoked) })
      .catch((e) => { if ((e as Error).name !== 'AbortError') setErr(e) })
    return () => { ctrl.abort(); if (revoked) URL.revokeObjectURL(revoked) }
  }, [url, nonce, available])
  if (!available) return <div className={cx('flex items-center justify-center rounded-lg border border-dashed border-line-strong text-[13px] text-muted', height)}>No PDF for this version.</div>
  if (err) return <ErrorState error={err} />
  return (
    <div className={cx('overflow-hidden rounded-lg border border-line bg-surface-3 shadow-card', height)}>
      {blobUrl ? (
        <>
          <iframe title="Resume preview" src={blobUrl + '#view=FitH&toolbar=0'} className="hidden h-full w-full sm:block" />
          <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-[13px] text-muted sm:hidden">
            PDF previews open in your browser's viewer on mobile.
            <a href={blobUrl} target="_blank" rel="noreferrer"><Button>Open PDF</Button></a>
          </div>
        </>
      ) : <div className="flex h-full items-center justify-center"><Spinner /></div>}
    </div>
  )
}

function LeftPanel({ v, job }: { v: ResumeVersion; job?: JobDetail }) {
  const projects = v.content?.sections.find((s) => s.kind === 'projects')
  const reqs = job?.requirements.filter((r) => r.kind === 'skill' && r.importance !== 'keyword' && r.match) ?? []
  return (
    <>
      {job && (
        <Card>
          <CardHeader title="JD requirements" subtitle={`${reqs.filter((r) => r.match!.status === 'strong').length}/${reqs.length} demonstrated`} />
          <ul className="max-h-[320px] divide-y divide-line overflow-y-auto">
            {reqs.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-2 px-4 py-1.5 text-[12.5px]">
                <span className="truncate">{r.label}{r.importance === 'preferred' && <span className="text-faint"> · pref</span>}</span>
                <span className="shrink-0 scale-90"><MatchBadge status={r.match!.status} /></span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {projects?.entries && (
        <Card>
          <CardHeader title="Selected projects" />
          <ul className="divide-y divide-line">
            {projects.entries.map((e, i) => (
              <li key={i} className="px-4 py-2.5">
                <div className="text-[13px] font-medium">{e.fields.short_name || e.fields.primary}</div>
                {e.matched && e.matched.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{e.matched.slice(0, 6).map((m) => <Badge key={m} tone="strong">{m}</Badge>)}</div>}
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card>
        <CardHeader title="Resume sections" subtitle="From your template, in its order." />
        <ul className="px-4 py-2 text-[12.5px]">
          {v.content?.sections.map((s) => (
            <li key={s.index} className="flex items-center justify-between py-1">
              <span>{s.title}</span>
              <span className="text-faint">{s.unchanged ? 'as in template' : s.layout}</span>
            </li>
          ))}
        </ul>
      </Card>
    </>
  )
}

const sevIcon = (s: Finding['severity']) =>
  s === 'high' ? <AlertTriangle className="size-3.5 text-missing" /> : s === 'medium' ? <AlertTriangle className="size-3.5 text-partial" /> : <Info className="size-3.5 text-muted" />

function ReviewPanel({ v }: { v: ResumeVersion }) {
  const ats = v.ats_report
  const rec = v.recruiter_report
  const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div className="flex items-center justify-between py-1 text-[13px]"><span className="text-muted">{label}</span>{children}</div>
  )
  return (
    <>
      <Card>
        <CardHeader title="ATS check" subtitle="What an applicant-tracking system reads from the PDF." />
        {ats?.summary ? (
          <div className="px-4 py-3">
            <Row label="JD coverage"><LevelText level={ats.summary.jd_coverage} /></Row>
            <Row label="Keyword alignment"><LevelText level={ats.summary.keyword_alignment} /></Row>
            <Row label="Formatting risk"><LevelText level={ats.summary.formatting_risk} /></Row>
            {ats.coverage?.required && <Row label="Required skills on resume"><span className="tabular">{ats.coverage.required.covered}/{ats.coverage.required.total}</span></Row>}
            <Findings items={ats.findings} />
          </div>
        ) : <div className="px-4 py-3 text-[12.5px] text-muted">Not available.</div>}
      </Card>
      <Card>
        <CardHeader title="Recruiter review" />
        {rec?.summary ? (
          <div className="px-4 py-3">
            <Row label="Evidence quality"><LevelText level={rec.summary.evidence_quality} /></Row>
            <Row label="Project relevance"><LevelText level={rec.summary.relevance} /></Row>
            <Row label="Bullet quality"><LevelText level={rec.summary.bullet_quality} /></Row>
            <Row label="Technical depth"><LevelText level={rec.summary.technical_depth} /></Row>
            <Row label="Potential gaps"><span className="font-medium tabular">{rec.summary.potential_gaps}</span></Row>
            <Row label="Resume length"><span className="font-medium">{rec.summary.resume_length}</span></Row>
            <Findings items={rec.findings} />
          </div>
        ) : <div className="px-4 py-3 text-[12.5px] text-muted">Not available.</div>}
      </Card>
      {ats && (ats.missing_with_evidence.length > 0 || ats.missing_without_evidence.length > 0) && (
        <Card>
          <CardHeader title="Missing evidence" />
          <div className="space-y-2.5 px-4 py-3 text-[12.5px]">
            {ats.missing_with_evidence.length > 0 && (
              <div><div className="mb-1 font-medium text-ink">You have evidence, but it's not on the resume</div>
                <div className="flex flex-wrap gap-1">{ats.missing_with_evidence.map((m) => <Badge key={m.label} tone="accent">{m.label}</Badge>)}</div></div>
            )}
            {ats.missing_without_evidence.length > 0 && (
              <div><div className="mb-1 font-medium text-ink">Gaps (not added)</div>
                <div className="flex flex-wrap gap-1">{ats.missing_without_evidence.map((m) => <Badge key={m.label} tone="missing">{m.label}</Badge>)}</div></div>
            )}
          </div>
        </Card>
      )}
      {v.changes && v.changes.length > 0 && (
        <Card>
          <CardHeader title="Changes made" />
          <ul className="max-h-[300px] space-y-2 overflow-y-auto px-4 py-3 text-[12.5px]">
            {v.changes.map((c, i) => (
              <li key={i} className="flex gap-2">
                <Badge tone={c.type === 'kept' ? 'verify' : c.type === 'removed' || c.type === 'trimmed' ? 'neutral' : c.type === 'drafted' ? 'partial' : 'accent'}>{c.type}</Badge>
                <span className="text-ink-2">{c.detail}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  )
}

function Findings({ items }: { items: Finding[] }) {
  if (!items?.length) return <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-strong"><CheckCircle2 className="size-3.5" />No issues found.</p>
  return (
    <ul className="mt-2.5 space-y-2 border-t border-line pt-2.5">
      {items.map((f, i) => (
        <li key={i} className="flex gap-2 text-[12.5px]">
          <span className="mt-0.5">{sevIcon(f.severity)}</span>
          <span><span className="font-medium text-ink">{f.title}.</span> <span className="text-ink-2">{f.detail}</span></span>
        </li>
      ))}
    </ul>
  )
}

function ContentEditor({ v, locked, onSaved }: { v: ResumeVersion; locked: boolean; onSaved: (d: ResumeVersion) => void }) {
  const [sections, setSections] = useState<ResumeSection[]>(() => structuredClone(v.content?.sections ?? []))
  const toast = useToast()
  const save = useMutation({
    mutationFn: () => patch<ResumeVersion>(`/api/resumes/${v.id}`, { content: { ...v.content, sections } }),
    onSuccess: (d) => { toast('Saved and re-rendered'); onSaved(d) },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const update = (i: number, fn: (s: ResumeSection) => void) => setSections((prev) => { const next = structuredClone(prev); fn(next[i]); return next })

  return (
    <Card className="p-4">
      {locked && <div className="mb-3 flex items-center gap-2 rounded-md bg-surface-2 px-3 py-2 text-[12.5px] text-muted"><Lock className="size-3.5" />This version is locked. Duplicate it to make changes.</div>}
      <div className="space-y-6">
        {sections.map((s, si) => s.unchanged ? null : (
          <section key={s.index}>
            <h3 className="mb-2 font-serif text-[16px] font-semibold">{s.title}</h3>
            {s.layout === 'paragraph' && (
              <Textarea rows={4} disabled={locked} value={s.text ?? ''} onChange={(e) => update(si, (x) => { x.text = e.target.value })} />
            )}
            {s.layout === 'skills' && s.categories?.map((c, ci) => (
              <div key={c.name} className="mb-2 grid items-center gap-2 sm:grid-cols-[140px_1fr]">
                <span className="text-[12.5px] font-medium text-ink-2">{c.name}</span>
                <Input disabled={locked} value={c.items.join(', ')} onChange={(e) => update(si, (x) => { x.categories![ci].items = e.target.value.split(',').map((t) => t.trim()).filter(Boolean) })} />
              </div>
            ))}
            {s.layout === 'items' && s.items?.map((it, ii) => (
              <Textarea key={ii} rows={2} disabled={locked} className="mb-2" value={it} onChange={(e) => update(si, (x) => { x.items![ii] = e.target.value })} />
            ))}
            {s.layout === 'entries' && s.entries?.map((e, ei) => (
              <div key={ei} className="mb-3 rounded-md border border-line p-3">
                <div className="mb-2 text-[13px] font-semibold">{e.fields.primary}{e.fields.secondary ? <span className="font-normal text-muted"> · {e.fields.secondary}</span> : ''}</div>
                {e.bullets === null ? <div className="text-[12.5px] text-muted">Kept exactly as in your template.</div> : e.bullets.map((b, bi) => (
                  <div key={bi} className="mb-2">
                    <div className="mb-1 flex items-center gap-1.5">
                      <Badge tone={b.origin === 'rewritten' ? 'accent' : b.origin === 'evidence' ? 'partial' : 'neutral'}>
                        {b.origin === 'rewritten' ? 'Reworded' : b.origin === 'evidence' ? 'Drafted from evidence' : b.origin === 'user' ? 'Your edit' : 'Your wording'}
                      </Badge>
                      {b.rejected && b.rejected.length > 0 && <span className="text-[11.5px] text-verify" title={b.rejected.join('; ')}>A rewrite was rejected: {b.rejected[0]}</span>}
                    </div>
                    <Textarea rows={2} disabled={locked} value={b.text} onChange={(ev) => update(si, (x) => { x.entries![ei].bullets![bi].text = ev.target.value })} />
                    {b.origin === 'rewritten' && b.original !== b.text && <div className="mt-1 text-[11.5px] text-muted">Original: {b.original}</div>}
                  </div>
                ))}
              </div>
            ))}
          </section>
        ))}
      </div>
      {!locked && (
        <div className="sticky bottom-0 mt-4 flex justify-end gap-2 border-t border-line bg-surface pt-3">
          <Button variant="primary" icon={<RefreshCw className="size-3.5" />} loading={save.isPending} onClick={() => save.mutate()}>Save and re-render</Button>
        </div>
      )}
    </Card>
  )
}

function CompareDialog({ open, onClose, v }: { open: boolean; onClose: () => void; v: ResumeVersion }) {
  const all = useQuery({ queryKey: ['resumes'], queryFn: () => get<ResumeVersion[]>('/api/resumes'), enabled: open })
  const [other, setOther] = useState<string>('')
  const diff = useQuery({
    queryKey: ['compare', v.id, other],
    queryFn: () => get<{ differences: { section: string; removed: string[]; added: string[] }[] }>(`/api/resumes/compare?a=${other}&b=${v.id}`),
    enabled: open && !!other,
  })
  const candidates = (all.data ?? []).filter((x) => x.id !== v.id && (x.job_id === v.job_id || x.is_base))
  return (
    <Dialog open={open} onClose={onClose} title="Compare versions" width="max-w-3xl">
      <Select value={other} onChange={(e) => setOther(e.target.value)} aria-label="Version to compare">
        <option value="">Choose a version to compare with…</option>
        {candidates.map((x) => <option key={x.id} value={x.id}>{x.is_base ? x.label : `Version ${x.version_number} · ${fmtDateTime(x.created_at)}`}</option>)}
      </Select>
      <div className="mt-4">
        {diff.isLoading && other ? <Spinner /> : diff.data && (diff.data.differences.length === 0 ? (
          <p className="text-[13px] text-muted">The content is identical.</p>
        ) : diff.data.differences.map((d) => (
          <div key={d.section} className="mb-4">
            <h4 className="mb-1.5 text-[13px] font-semibold">{d.section}</h4>
            <div className="space-y-1 font-mono text-[12px]">
              {d.removed.map((t, i) => <div key={'r' + i} className="rounded bg-missing-soft px-2 py-1 text-ink-2">− {t}</div>)}
              {d.added.map((t, i) => <div key={'a' + i} className="rounded bg-strong-soft px-2 py-1 text-ink">+ {t}</div>)}
            </div>
          </div>
        )))}
      </div>
    </Dialog>
  )
}
