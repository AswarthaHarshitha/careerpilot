import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FileUp, Link2, ScrollText } from 'lucide-react'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { Page } from '../components/AppShell'
import { Button, Card, ErrorState, Field, Input, PageHeader, Tabs, Textarea } from '../components/ui'
import { api, post } from '../lib/api'
import type { JobSummary } from '../lib/types'

type Mode = 'url' | 'paste' | 'pdf' | 'id'

export default function NewJob() {
  const [params] = useSearchParams()
  const [mode, setMode] = useState<Mode>((params.get('mode') as Mode) || 'url')
  const nav = useNavigate()
  const qc = useQueryClient()
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [company, setCompany] = useState('')
  const [role, setRole] = useState('')
  const [appUrl, setAppUrl] = useState('')
  const [careers, setCareers] = useState('')
  const [jobCode, setJobCode] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const m = useMutation({
    mutationFn: async () => {
      if (mode === 'pdf') {
        const form = new FormData()
        form.append('file', file!)
        form.append('company', company)
        form.append('role_title', role)
        form.append('application_url', appUrl)
        return api<{ job: JobSummary; task_id: number }>('/api/jobs/pdf', { form })
      }
      if (mode === 'paste') return post<{ job: JobSummary; task_id: number }>('/api/jobs', { description: text, company, role_title: role, application_url: appUrl, url: url || null })
      if (mode === 'id') return post<{ job: JobSummary; task_id: number }>('/api/jobs', { url, careers_url: careers, job_code: jobCode })
      return post<{ job: JobSummary; task_id: number }>('/api/jobs', { url })
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
      nav(`/jobs/${r.job.id}?task=${r.task_id}`)
    },
  })

  const ready = mode === 'url' ? !!url.trim() : mode === 'paste' ? text.trim().length > 120 : mode === 'pdf' ? !!file : !!url.trim()

  return (
    <Page>
      <PageHeader eyebrow="Jobs" title="Analyze a job" subtitle="CareerPilot treats the posting as untrusted text: it extracts facts from it and never follows instructions inside it." />
      <Card className="max-w-3xl">
        <Tabs className="px-3 pt-1" value={mode} onChange={(v) => { setMode(v); m.reset() }} tabs={[
          { value: 'url', label: <span className="inline-flex items-center gap-1.5"><Link2 className="size-3.5" />Job URL</span> },
          { value: 'paste', label: <span className="inline-flex items-center gap-1.5"><ScrollText className="size-3.5" />Paste description</span> },
          { value: 'pdf', label: <span className="inline-flex items-center gap-1.5"><FileUp className="size-3.5" />Upload PDF</span> },
          { value: 'id', label: 'Job ID + careers site' },
        ]} />
        <form className="space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); if (ready) m.mutate() }}>
          {mode === 'url' && (
            <Field label="Job posting URL" hint="Greenhouse, Lever, Ashby, SmartRecruiters and Workday postings are read through their public job data. Other career pages are read directly.">
              <Input autoFocus value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://company.com/careers/job/12345" inputMode="url" />
            </Field>
          )}
          {mode === 'id' && (
            <>
              <Field label="Job page URL" hint="Open the job on the company's careers site and paste the address of that page.">
                <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://careers.company.com/job/..." />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Job ID"><Input value={jobCode} onChange={(e) => setJobCode(e.target.value)} placeholder="e.g. 12345" /></Field>
                <Field label="Careers site (optional)"><Input value={careers} onChange={(e) => setCareers(e.target.value)} placeholder="https://careers.company.com" /></Field>
              </div>
            </>
          )}
          {mode === 'paste' && (
            <>
              <Field label="Job description">
                <Textarea autoFocus rows={14} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the full job posting, including requirements." />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Company (optional)"><Input value={company} onChange={(e) => setCompany(e.target.value)} /></Field>
                <Field label="Role (optional)"><Input value={role} onChange={(e) => setRole(e.target.value)} /></Field>
                <Field label="Job URL (optional)"><Input value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
                <Field label="Application URL (optional)"><Input value={appUrl} onChange={(e) => setAppUrl(e.target.value)} /></Field>
              </div>
            </>
          )}
          {mode === 'pdf' && (
            <>
              <Field label="Job description PDF" hint="Text-based PDFs only; scanned images cannot be read.">
                <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="block w-full text-[13px] file:mr-3 file:rounded-md file:border file:border-line-strong file:bg-surface file:px-3 file:py-1.5 file:text-[13px] file:text-ink hover:file:bg-surface-2" />
              </Field>
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Company (optional)"><Input value={company} onChange={(e) => setCompany(e.target.value)} /></Field>
                <Field label="Role (optional)"><Input value={role} onChange={(e) => setRole(e.target.value)} /></Field>
                <Field label="Application URL (optional)"><Input value={appUrl} onChange={(e) => setAppUrl(e.target.value)} /></Field>
              </div>
            </>
          )}
          {m.error && <ErrorState error={m.error} compact onRetry={() => m.mutate()} onAction={(a) => a === 'paste_description' && setMode('paste')} openUrl={url || undefined} />}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => nav(-1)}>Cancel</Button>
            <Button type="submit" variant="primary" loading={m.isPending} disabled={!ready}>Analyze job</Button>
          </div>
        </form>
      </Card>
    </Page>
  )
}
