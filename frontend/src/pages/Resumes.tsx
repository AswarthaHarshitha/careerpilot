import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, LayoutTemplate, Trash2 } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router'
import { Page } from '../components/AppShell'
import { Badge, Button, Card, EmptyState, ErrorState, LevelText, LoadingRows, PageHeader, errorText, useToast } from '../components/ui'
import { del, get } from '../lib/api'
import { fmtDate } from '../lib/format'
import type { ResumeVersion } from '../lib/types'

export default function Resumes() {
  const q = useQuery({ queryKey: ['resumes'], queryFn: () => get<ResumeVersion[]>('/api/resumes') })
  const qc = useQueryClient()
  const toast = useToast()
  const remove = useMutation({
    mutationFn: (id: number) => del(`/api/resumes/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['resumes'] }); toast('Version deleted') },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const groups = useMemo(() => {
    const out = new Map<string, { title: string; sub: string; jobId: number | null; items: ResumeVersion[] }>()
    for (const v of q.data ?? []) {
      const key = v.is_base ? 'base' : String(v.job_id ?? 'none')
      if (!out.has(key)) out.set(key, v.is_base
        ? { title: 'Original resumes', sub: 'Your templates as uploaded. Never overwritten.', jobId: null, items: [] }
        : { title: v.job?.company ?? 'Unlinked', sub: v.job?.role_title ?? '', jobId: v.job_id, items: [] })
      out.get(key)!.items.push(v)
    }
    return [...out.values()].sort((a, b) => (a.jobId === null ? 1 : 0) - (b.jobId === null ? 1 : 0))
  }, [q.data])

  return (
    <Page>
      <PageHeader title="Resumes" subtitle="Every generated version, kept per job. Approved versions are locked."
        actions={<Link to="/resumes/templates"><Button icon={<LayoutTemplate className="size-4" />}>Templates</Button></Link>} />
      {q.isLoading ? <LoadingRows /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.length ? (
        <EmptyState icon={<FileText className="size-7" />} title="No resumes yet"
          action={<Link to="/resumes/templates"><Button variant="primary">Upload your template</Button></Link>}>
          Upload your resume template, then analyze a job and generate a tailored version in the same layout.
        </EmptyState>
      ) : (
        <div className="space-y-5">
          {groups.map((g) => (
            <Card key={g.title + g.jobId}>
              <div className="flex items-baseline justify-between border-b border-line px-4 py-3">
                <div>
                  {g.jobId ? <Link to={`/jobs/${g.jobId}`} className="text-[14px] font-semibold hover:text-accent">{g.title}</Link> : <span className="text-[14px] font-semibold">{g.title}</span>}
                  <span className="ml-2 text-[13px] text-muted">{g.sub}</span>
                </div>
                <span className="text-[12px] text-faint">{g.items.length} version{g.items.length > 1 ? 's' : ''}</span>
              </div>
              <ul className="divide-y divide-line">
                {g.items.map((v) => (
                  <li key={v.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-[13px] hover:bg-surface-2/40">
                    <Link to={`/resumes/${v.id}`} className="flex min-w-0 flex-1 items-center gap-2">
                      <FileText className="size-3.5 shrink-0 text-muted" />
                      <span className="truncate font-medium">{v.is_base ? v.label : `Version ${v.version_number}`}</span>
                      <Badge tone={v.status === 'approved' ? 'strong' : 'neutral'}>{v.status === 'approved' ? 'Approved' : 'Draft'}</Badge>
                    </Link>
                    <span className="hidden text-[12.5px] text-muted sm:inline">JD coverage <LevelText level={v.summary.ats.jd_coverage ?? '—'} /></span>
                    <span className="text-[12.5px] text-muted">{v.page_count ? `${v.page_count}p · ` : ''}{fmtDate(v.created_at)}</span>
                    <a href={`/api/resumes/${v.id}/pdf?download=true`} className="text-[12.5px] text-muted hover:text-ink">PDF</a>
                    {!v.is_base && (
                      <button className="rounded p-1 text-faint hover:bg-missing-soft hover:text-missing" aria-label="Delete version"
                        onClick={() => { if (confirm('Delete this version? This cannot be undone.')) remove.mutate(v.id) }}>
                        <Trash2 className="size-3.5" />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          ))}
        </div>
      )}
    </Page>
  )
}
