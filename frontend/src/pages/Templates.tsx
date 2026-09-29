import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Star, Trash2, Upload, UserRoundPlus } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link } from 'react-router'
import { Page } from '../components/AppShell'
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, LoadingRows, PageHeader, TaskProgress, cx, errorText, useToast } from '../components/ui'
import { api, del, get, post } from '../lib/api'
import { fmtDate } from '../lib/format'
import type { Template } from '../lib/types'
import { useTask } from '../lib/useTask'
import { PdfPreview } from './ResumeWorkspace'

export default function Templates() {
  const q = useQuery({ queryKey: ['templates'], queryFn: () => get<Template[]>('/api/templates') })
  const [selected, setSelected] = useState<number | null>(null)
  const [taskId, setTaskId] = useState<number | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()
  const upload = useMutation({
    mutationFn: (f: File) => { const form = new FormData(); form.append('file', f); return api<{ template: Template; task_id: number }>('/api/templates', { form }) },
    onSuccess: (r) => { setSelected(r.template.id); setTaskId(r.task_id); qc.invalidateQueries({ queryKey: ['templates'] }) },
  })
  const task = useTask(taskId, [['templates'], ['template', String(selected)], ['resumes']])
  const fromProfile = useMutation({
    mutationFn: () => post<{ template: Template; task_id: number }>('/api/templates/from-profile'),
    onSuccess: (r) => { setSelected(r.template.id); setTaskId(r.task_id); qc.invalidateQueries({ queryKey: ['templates'] }) },
  })
  const current = q.data?.find((t) => t.id === selected) ?? q.data?.[0]

  return (
    <Page wide>
      <PageHeader eyebrow={<Link to="/resumes" className="hover:text-ink">Resumes</Link>} title="Resume templates"
        subtitle="CareerPilot edits the content inside your template and keeps its layout, fonts, spacing and section order."
        actions={<>
          <input ref={fileRef} type="file" accept=".tex,.zip,.docx,.pdf,.txt,.md,.markdown" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = '' }} />
          <Button icon={<UserRoundPlus className="size-4" />} loading={fromProfile.isPending} onClick={() => fromProfile.mutate()}>Create from my profile</Button>
          <Button variant="primary" icon={<Upload className="size-4" />} loading={upload.isPending} onClick={() => fileRef.current?.click()}>Upload template</Button>
        </>} />
      {upload.error && <div className="mb-4"><ErrorState error={upload.error} /></div>}
      {fromProfile.error && <div className="mb-4"><ErrorState error={fromProfile.error} /></div>}
      {task && task.status !== 'succeeded' && <Card className="mb-4 p-4"><TaskProgress steps={task.steps} status={task.status} error={task.error} /></Card>}
      {q.isLoading ? <LoadingRows /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.length ? (
        <EmptyState icon={<Upload className="size-7" />} title="Upload your resume source"
          action={<Button variant="primary" onClick={() => fileRef.current?.click()}>Choose a file</Button>}>
          Supported: PDF · DOCX · LaTeX (.tex or a zipped Overleaf project) · TXT/Markdown. LaTeX gives the most exact results: on Overleaf use Menu → Download → Source. A PDF is analysed and rebuilt as an editable template that keeps its layout.
        </EmptyState>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
          <div className="space-y-2">
            {q.data.map((t) => (
              <button key={t.id} onClick={() => setSelected(t.id)}
                className={cx('w-full rounded-lg border p-3 text-left transition-colors', current?.id === t.id ? 'border-accent/40 bg-accent-soft/40' : 'border-line bg-surface hover:border-line-strong')}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13.5px] font-semibold">{t.name}</span>
                  {t.is_default && <Badge tone="accent"><Star className="size-3" />Default</Badge>}
                </div>
                <div className="mt-1 text-[12px] text-muted">{({ latex: 'LaTeX', docx: 'Word', pdf: 'PDF', text: 'Text' } as Record<string, string>)[t.format] ?? t.format} · {t.sections.length} sections · {t.page_count ? `${t.page_count} page${t.page_count > 1 ? 's' : ''}` : 'compiling…'} · {fmtDate(t.created_at)}</div>
                {t.render_error && <div className="mt-1.5 text-[12px] text-missing">Preview failed</div>}
              </button>
            ))}
          </div>
          {current && <TemplateDetail key={current.id} id={current.id} />}
        </div>
      )}
    </Page>
  )
}

function TemplateDetail({ id }: { id: number }) {
  const qc = useQueryClient()
  const toast = useToast()
  const t = useQuery({ queryKey: ['template', String(id)], queryFn: () => get<Template>(`/api/templates/${id}`) })
  const importProfile = useMutation({
    mutationFn: () => post<{ imported: Record<string, number> }>(`/api/templates/${id}/import-profile`),
    onSuccess: (r) => {
      const n = Object.values(r.imported).reduce((a, b) => a + b, 0)
      toast(n ? `Imported ${n} item${n > 1 ? 's' : ''} into your profile` : 'Your profile already has everything from this template')
      qc.invalidateQueries({ queryKey: ['profile'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const makeDefault = useMutation({ mutationFn: () => post(`/api/templates/${id}/default`), onSuccess: () => qc.invalidateQueries({ queryKey: ['templates'] }) })
  const remove = useMutation({
    mutationFn: () => del(`/api/templates/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['templates'] }),
    onError: (e) => toast(errorText(e), 'error'),
  })
  if (t.isLoading) return <LoadingRows />
  if (t.error) return <ErrorState error={t.error} onRetry={() => t.refetch()} />
  const d = t.data!
  if (d.analysis_pending && !d.render_error) {
    return <Card className="p-6 text-[13px] text-muted">Analysing the PDF and rebuilding it as an editable template…</Card>
  }
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        {d.fingerprint && (
          <Card>
            <CardHeader title="Template successfully analysed" subtitle={`Template lock: ON — content changes, the layout does not`} />
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 px-4 py-3 text-[12.5px]">
              {([['Pages', String(d.fingerprint.pages)], ['Layout', d.fingerprint.layout], ['Text', d.fingerprint.text],
                ['Font', d.fingerprint.font || '—'], ['Body size', `${d.fingerprint.body_size}pt`], ['Headings', d.fingerprint.heading_style || 'plain'],
                ['Margins', `${d.fingerprint.margins.left}pt / ${d.fingerprint.margins.right}pt`], ['Sections', String(d.fingerprint.section_order.length)]] as const).map(([k, v]) => (
                <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="text-ink-2">{v}</dd></div>
              ))}
            </dl>
            {d.comparison && (
              <div className="border-t border-line px-4 py-3 text-[12.5px]">
                <div className="mb-1 font-medium">How closely the rebuilt template matches</div>
                <ul className="space-y-0.5 text-ink-2">
                  <li>Pages: {d.comparison.pages_rebuilt} (original {d.comparison.pages_original})</li>
                  <li>Section headings found: {d.comparison.headings_found} of {d.comparison.headings_total}</li>
                  <li>Heading positions: within {d.comparison.mean_heading_offset_pt}pt on average (largest {d.comparison.max_heading_offset_pt}pt)</li>
                  <li>Text preserved: {d.comparison.text_preserved}%</li>
                </ul>
              </div>
            )}
          </Card>
        )}
        <Card>
          <CardHeader title="Parsed structure" subtitle={`${d.header.name || 'Header'}${d.header.email ? ' · ' + d.header.email : ''}`}
            actions={<>
              {!d.is_default && <Button size="sm" onClick={() => makeDefault.mutate()}>Make default</Button>}
              <Button size="sm" variant="ghost" aria-label="Delete template" icon={<Trash2 className="size-3.5" />} onClick={() => { if (confirm('Delete this template?')) remove.mutate() }} />
            </>} />
          <div className="divide-y divide-line">
            {d.structure?.map((s) => (
              <details key={s.index} className="group px-4 py-2.5">
                <summary className="flex cursor-pointer list-none items-center justify-between text-[13px]">
                  <span className="font-medium">{s.title}</span>
                  <span className="flex items-center gap-2 text-[12px] text-muted"><Badge>{s.kind}</Badge>{s.layout}</span>
                </summary>
                <div className="mt-2 space-y-1.5 text-[12.5px] text-ink-2">
                  {s.text && <p>{s.text}</p>}
                  {s.entries?.map((e, i) => (
                    <div key={i}>
                      <div className="font-medium text-ink">{e.fields.primary}{e.fields.secondary ? ` · ${e.fields.secondary}` : ''}{e.fields.dates ? <span className="text-muted"> · {e.fields.dates}</span> : ''}</div>
                      {e.bullets.map((b, j) => <div key={j} className="pl-3 text-muted">• {b}</div>)}
                    </div>
                  ))}
                  {s.skills?.map((c) => <div key={c.name}><span className="font-medium text-ink">{c.name}:</span> {c.items.join(', ')}</div>)}
                  {s.items?.map((it, i) => <div key={i}>• {it}</div>)}
                </div>
              </details>
            ))}
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-start gap-3">
            <UserRoundPlus className="mt-0.5 size-4 shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px] font-medium">Import into your career profile</div>
              <p className="mt-0.5 text-[12.5px] text-muted">Adds the experience, projects, skills, education and certifications from this resume. Only empty fields are filled; nothing is duplicated.</p>
            </div>
            <Button size="sm" loading={importProfile.isPending} onClick={() => importProfile.mutate()}>Import</Button>
          </div>
        </Card>
      </div>
      <div className="space-y-3">
        {d.render_error ? <ErrorState error={new Error(d.render_error)} /> : d.has_original_pdf ? (
          <div className="grid gap-3 2xl:grid-cols-2">
            <div><div className="mb-1 text-[12px] font-medium text-muted">Your uploaded PDF</div><PdfPreview url={`/api/templates/${id}/original.pdf`} height="h-[700px]" /></div>
            <div><div className="mb-1 text-[12px] font-medium text-muted">Rebuilt template (used for tailored resumes)</div><PdfPreview url={`/api/templates/${id}/preview.pdf`} available={d.has_preview} height="h-[700px]" /></div>
          </div>
        ) : <PdfPreview url={`/api/templates/${id}/preview.pdf`} available={d.has_preview} height="h-[760px]" />}
      </div>
    </div>
  )
}
