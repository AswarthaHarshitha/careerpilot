import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ClipboardCopy, KeyRound, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Page } from '../components/AppShell'
import {
  Badge, Button, Card, CardHeader, ErrorState, Field, Input, LoadingRows, PageHeader, Select, Tabs, errorText, useToast,
} from '../components/ui'
import { api, del, get, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDateTime, relative } from '../lib/format'
import type { SettingsData, Template } from '../lib/types'
import { AIUsagePanel, ProviderChoice, ProviderList } from '../components/AIProviders'
import { RunnerCard, useAgentStatus } from '../components/AgentStatus'

type S = SettingsData
type TabKey = 'resume' | 'github' | 'applications' | 'automation' | 'ai' | 'notifications' | 'security' | 'data'

export default function Settings() {
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as TabKey) || 'resume'
  const q = useQuery({ queryKey: ['settings'], queryFn: () => get<S>('/api/settings') })
  return (
    <Page>
      <PageHeader title="Settings" />
      <Tabs className="mb-6" value={tab} onChange={(v) => setParams({ tab: v })} tabs={[
        { value: 'resume', label: 'Resume' }, { value: 'github', label: 'GitHub' }, { value: 'applications', label: 'Applications' },
        { value: 'automation', label: 'Browser automation' }, { value: 'ai', label: 'AI providers' }, { value: 'notifications', label: 'Notifications' },
        { value: 'security', label: 'Security' }, { value: 'data', label: 'Data' },
      ]} />
      {q.isLoading ? <LoadingRows /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <div className="max-w-3xl space-y-6">
          {(tab === 'resume' || tab === 'applications' || tab === 'notifications') && <Preferences s={q.data!} tab={tab} />}
          {tab === 'github' && <GitHub s={q.data!} />}
          {tab === 'automation' && <Automation />}
          {tab === 'ai' && <AI s={q.data!} />}
          {tab === 'security' && <Security />}
          {tab === 'data' && <Data />}
        </div>
      )}
    </Page>
  )
}

function useSettingsSave<T>(path: string) {
  const qc = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: (body: T) => put<S>(path, body),
    onSuccess: (d) => { qc.setQueryData(['settings'], d); toast('Settings saved') },
    onError: (e) => toast(errorText(e), 'error'),
  })
}

function Preferences({ s, tab }: { s: S; tab: TabKey }) {
  const [f, setF] = useState(s)
  useEffect(() => setF(s), [s])
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => get<Template[]>('/api/templates'), enabled: tab === 'resume' })
  const qc = useQueryClient()
  const save = useSettingsSave<Partial<S>>('/api/settings/preferences')
  const setDefault = useMutation({ mutationFn: (id: number) => post(`/api/templates/${id}/default`), onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['templates'] }) } })
  const num = (k: keyof S) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: Number(e.target.value) })
  const body = { resume_max_pages: f.resume_max_pages, max_projects: f.max_projects, max_bullets_per_project: f.max_bullets_per_project, timezone: f.timezone, submit_confirmation_mode: f.submit_confirmation_mode, deadline_reminder_days: f.deadline_reminder_days, follow_up_after_days: f.follow_up_after_days, notify_in_app: f.notify_in_app, header_portfolio: f.header_portfolio, header_github: f.header_github, header_linkedin: f.header_linkedin }
  return (
    <Card>
      <CardHeader title={tab === 'resume' ? 'Resume defaults' : tab === 'applications' ? 'Application preferences' : 'Notifications'} />
      <form className="space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(body) }}>
        {tab === 'resume' && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Default template" hint={<Link to="/resumes/templates" className="text-accent hover:underline">Manage templates</Link>}>
              <Select value={s.default_template_id ?? ''} onChange={(e) => e.target.value && setDefault.mutate(Number(e.target.value))}>
                <option value="">None</option>
                {templates.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </Select>
            </Field>
            <Field label="Resume length target (pages)" hint="Least relevant content is trimmed to fit."><Input type="number" min={1} max={3} value={f.resume_max_pages} onChange={num('resume_max_pages')} /></Field>
            <Field label="Projects per resume"><Input type="number" min={1} max={6} value={f.max_projects} onChange={num('max_projects')} /></Field>
            <Field label="Bullets per project"><Input type="number" min={1} max={6} value={f.max_bullets_per_project} onChange={num('max_bullets_per_project')} /></Field>
            {(['header_portfolio', 'header_github', 'header_linkedin'] as const).map((k) => (
              <Field key={k} label={`${k === 'header_portfolio' ? 'Portfolio' : k === 'header_github' ? 'GitHub' : 'LinkedIn'} link in header (default)`}
                hint={k === 'header_portfolio' ? 'The portfolio is only ever a header link, never a project. A job’s instructions can override this.' : undefined}>
                <Select value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value as S['header_github'] })}>
                  <option value="template">As in my template</option><option value="include">Always include</option><option value="exclude">Leave out</option>
                </Select>
              </Field>
            ))}
          </div>
        )}
        {tab === 'applications' && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Final submission confirmation" hint="Every submission needs your explicit action; this only sets how.">
              <Select value={f.submit_confirmation_mode} onChange={(e) => setF({ ...f, submit_confirmation_mode: e.target.value as S['submit_confirmation_mode'] })}>
                <option value="type_company">Type the company name (recommended)</option>
                <option value="click">A single confirm click</option>
              </Select>
            </Field>
            <Field label="Time zone"><Input value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
            <Field label="Remind me before deadlines (days)"><Input type="number" min={0} max={30} value={f.deadline_reminder_days} onChange={num('deadline_reminder_days')} /></Field>
            <Field label="Follow up after applying (days)"><Input type="number" min={1} max={60} value={f.follow_up_after_days} onChange={num('follow_up_after_days')} /></Field>
          </div>
        )}
        {tab === 'notifications' && (
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={f.notify_in_app} onChange={(e) => setF({ ...f, notify_in_app: e.target.checked })} className="accent-[var(--accent)]" />Show deadline and follow-up reminders on the dashboard</label>
            <p className="text-[12.5px] text-muted">Reminders appear in CareerPilot. Email delivery is not configured in this deployment.</p>
          </div>
        )}
        <div className="flex justify-end"><Button type="submit" variant="primary" loading={save.isPending}>Save</Button></div>
      </form>
    </Card>
  )
}

function GitHub({ s }: { s: S }) {
  const [username, setUsername] = useState(s.github.username)
  const [token, setToken] = useState('')
  const [priv, setPriv] = useState(s.github.include_private_repos)
  const [forks, setForks] = useState(s.github.include_forks)
  const save = useSettingsSave<Record<string, unknown>>('/api/settings/github')
  return (
    <Card>
      <CardHeader title="GitHub connection" subtitle={s.github.last_scan_at ? `Last scanned ${relative(s.github.last_scan_at)}` : 'Not scanned yet'} />
      <form className="space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate({ username, token: token ? token : null, include_private_repos: priv, include_forks: forks }) }}>
        <Field label="GitHub username or profile URL"><Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="https://github.com/your-name" /></Field>
        <Field label="Personal access token (optional)"
          hint={<>Without a token GitHub allows 60 requests an hour (enough for about 50 repositories). A fine-grained, read-only token raises that and allows private repositories. {s.github.token && <>Stored: <span className="font-mono">{s.github.token}</span></>}</>}>
          <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={s.github.token ? 'Leave blank to keep the stored token' : 'github_pat_…'} />
        </Field>
        <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={priv} onChange={(e) => setPriv(e.target.checked)} className="accent-[var(--accent)]" />Include private repositories (needs a token)</label>
        <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={forks} onChange={(e) => setForks(e.target.checked)} className="accent-[var(--accent)]" />Include forks</label>
        <div className="flex items-center justify-between">
          {s.github.token ? <Button type="button" variant="ghost" size="sm" onClick={() => save.mutate({ username, token: '', include_private_repos: false, include_forks: forks })}>Remove token</Button> : <span />}
          <div className="flex gap-2"><Link to="/projects"><Button type="button">Open projects</Button></Link><Button type="submit" variant="primary" loading={save.isPending}>Save</Button></div>
        </div>
      </form>
    </Card>
  )
}

type Tok = { id: number; name: string; prefix: string; created_at: string; last_used_at: string | null }

function Automation() {
  const q = useQuery({ queryKey: ['runner-tokens'], queryFn: () => get<Tok[]>('/api/settings/runner-tokens') })
  const [name, setName] = useState('My laptop')
  const [created, setCreated] = useState<string | null>(null)
  const qc = useQueryClient()
  const toast = useToast()
  const create = useMutation({ mutationFn: () => post<{ token: string }>('/api/settings/runner-tokens', { name }), onSuccess: (r) => { setCreated(r.token); qc.invalidateQueries({ queryKey: ['runner-tokens'] }) } })
  const revoke = useMutation({ mutationFn: (id: number) => del(`/api/settings/runner-tokens/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ['runner-tokens'] }) })
  const origin = window.location.origin
  const cmd = `cd careerpilot/backend\nuv sync --extra automation && uv run playwright install chromium\nuv run careerpilot-runner install --api ${origin} --token ${created ?? '<token>'}`
  const agent = useAgentStatus()
  return (
    <>
      <Card>
        <CardHeader title="CareerPilot Runner" subtitle="Runs on your computer and fills application forms in a real browser window you can see." />
        <div className="space-y-3 p-5 text-[13px] text-ink-2">
          {agent.data && <RunnerCard runner={agent.data.runner} compact />}
          <p>Employer sites need a real browser, your own sign-ins, and sometimes a CAPTCHA only you can solve, so form filling runs on your machine rather than on the server. The runner never sees your employer-site passwords; your logins stay in its local browser profile.</p>
          <ol className="list-decimal space-y-1 pl-5">
            <li>Create a token below.</li>
            <li>Install the runner once. It starts with your computer and connects automatically:</li>
          </ol>
          <div className="relative">
            <pre className="overflow-x-auto rounded-md border border-line bg-surface-2 p-3 font-mono text-[12px] text-ink">{cmd}</pre>
            <Button size="sm" variant="ghost" className="absolute right-1.5 top-1.5" icon={<ClipboardCopy className="size-3.5" />} onClick={() => { navigator.clipboard.writeText(cmd); toast('Copied') }} aria-label="Copy command" />
          </div>
          <p>For development, run it in a terminal instead with <code className="font-mono text-[12px]">uv run careerpilot-runner --api {origin} --token …</code>. To stop starting it automatically: <code className="font-mono text-[12px]">uv run careerpilot-runner uninstall</code>.</p>
          <p>Then choose “Apply with CareerPilot” on any application. Greenhouse, Lever, Ashby, SmartRecruiters and standard HTML forms are filled directly; Workday, Taleo, Oracle, SuccessFactors and iCIMS need you to sign in inside the runner's window first. Multi-page forms are followed page by page, and questions only you can answer are left for you.</p>
        </div>
      </Card>
      <Card>
        <CardHeader title="Runner tokens" />
        <div className="p-5">
          {created && (
            <div className="mb-4 rounded-md border border-accent/30 bg-accent-soft p-3 text-[12.5px]">
              <div className="mb-1 font-medium">Copy this token now. It won't be shown again.</div>
              <div className="flex items-center gap-2"><code className="min-w-0 flex-1 truncate font-mono text-[12px]">{created}</code>
                <Button size="sm" icon={<ClipboardCopy className="size-3.5" />} onClick={() => { navigator.clipboard.writeText(created); toast('Copied') }}>Copy</Button></div>
            </div>
          )}
          <form className="mb-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. My laptop" />
            <Button type="submit" variant="primary" icon={<KeyRound className="size-3.5" />} loading={create.isPending}>Create token</Button>
          </form>
          <ul className="divide-y divide-line rounded-md border border-line">
            {q.data?.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-3 px-3 py-2 text-[13px]">
                <div><span className="font-medium">{t.name}</span> <span className="font-mono text-[12px] text-muted">{t.prefix}…</span>
                  <div className="text-[11.5px] text-muted">Created {fmtDateTime(t.created_at)} · {(() => { const r = agent.data?.runner.runners.find((x) => x.name === t.name); return r ? (r.online ? `connected${r.host ? ` from ${r.host}` : ''}` : `last seen ${relative(r.last_seen_at)}`) : t.last_used_at ? `last used ${relative(t.last_used_at)}` : 'never connected' })()}</div></div>
                <Button size="sm" variant="danger" onClick={() => revoke.mutate(t.id)}>Revoke</Button>
              </li>
            ))}
            {!q.data?.length && <li className="px-3 py-3 text-[12.5px] text-muted">No tokens.</li>}
          </ul>
        </div>
      </Card>
    </>
  )
}

function AI({ s }: { s: S }) {
  return (
    <>
      <Card>
        <CardHeader title="AI providers" subtitle="Used for reading job descriptions, wording and the editor assistant. Every output is checked; facts come only from your profile and verified evidence. Keys stay on the server." />
        <div className="p-5"><ProviderChoice ai={s.ai} /></div>
        <div className="border-t border-line"><ProviderList ai={s.ai} /></div>
      </Card>
      <AIUsagePanel />
    </>
  )
}

function Security() {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const toast = useToast()
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => get<{ id: string; created_at: string; user_agent: string; current: boolean }[]>('/api/auth/sessions') })
  const qc = useQueryClient()
  const change = useMutation({
    mutationFn: () => post('/api/auth/password', { current_password: cur, new_password: next }),
    onSuccess: () => { setCur(''); setNext(''); toast('Password changed; other sessions were signed out'); qc.invalidateQueries({ queryKey: ['sessions'] }) },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const revoke = useMutation({ mutationFn: (id: string) => del(`/api/auth/sessions/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }) })
  return (
    <>
      <Card>
        <CardHeader title="Change password" />
        <form className="grid gap-4 p-5 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); change.mutate() }}>
          <Field label="Current password"><Input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
          <Field label="New password" hint="At least 12 characters, mixed case and a digit."><Input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
          <div className="flex justify-end sm:col-span-2"><Button type="submit" variant="primary" loading={change.isPending} disabled={!cur || !next}>Change password</Button></div>
        </form>
      </Card>
      <Card>
        <CardHeader title="Active sessions" />
        <ul className="divide-y divide-line">
          {sessions.data?.map((x) => (
            <li key={x.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-[12.5px]">
              <div className="min-w-0"><div className="truncate">{x.user_agent || 'Unknown device'}</div><div className="text-muted">Signed in {fmtDateTime(x.created_at)}</div></div>
              {x.current ? <Badge tone="strong">This device</Badge> : <Button size="sm" variant="ghost" onClick={() => revoke.mutate(x.id)}>Sign out</Button>}
            </li>
          ))}
        </ul>
      </Card>
    </>
  )
}

function ImportCard() {
  const qc = useQueryClient()
  const toast = useToast()
  const ref = useRef<HTMLInputElement>(null)
  const [result, setResult] = useState<Record<string, number> | null>(null)
  const run = useMutation({
    mutationFn: (f: File) => { const form = new FormData(); form.append('file', f); return api<{ imported: Record<string, number> }>('/api/data/import', { form }) },
    onSuccess: (r) => { setResult(r.imported); qc.invalidateQueries(); toast('Import finished') },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const labels: Record<string, string> = { profile_fields: 'profile details', experiences: 'experiences', educations: 'education entries', skills: 'skills', certifications: 'certifications', achievements: 'achievements', answers: 'saved answers', projects: 'projects', templates: 'templates' }
  return (
    <Card>
      <CardHeader title="Import" subtitle="Bring in your profile, experience, education, skills, saved answers, preferences and resume templates from another CareerPilot's export. Existing details are kept; nothing is duplicated. GitHub projects are re-read from GitHub." />
      <div className="space-y-3 p-5">
        <input ref={ref} type="file" accept=".json,application/json" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) run.mutate(f); e.target.value = '' }} />
        <Button loading={run.isPending} onClick={() => ref.current?.click()}>Import an export file</Button>
        {result && (
          <p className="text-[13px] text-ink-2">
            Imported {Object.entries(result).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${labels[k] ?? k}`).join(', ') || 'nothing new — everything was already here'}.
          </p>
        )}
      </div>
    </Card>
  )
}

function Data() {
  const { logout } = useAuth()
  const [pw, setPw] = useState('')
  const [confirmText, setConfirmText] = useState('')
  const toast = useToast()
  const remove = useMutation({ mutationFn: () => post('/api/account/delete', { password: pw, confirm: confirmText }), onSuccess: () => logout(), onError: (e) => toast(errorText(e), 'error') })
  return (
    <>
      <Card>
        <CardHeader title="Export" subtitle="Everything CareerPilot stores about you, as JSON. Resume PDFs download from each version." />
        <div className="p-5"><a href="/api/data/export"><Button>Download export</Button></a></div>
      </Card>
      <ImportCard />
      <Card>
        <CardHeader title="Delete account" subtitle="Permanently deletes your profile, jobs, resumes, applications and settings." />
        <form className="grid gap-4 p-5 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); remove.mutate() }}>
          <Field label="Password"><Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
          <Field label="Type DELETE"><Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} /></Field>
          <div className="flex justify-end sm:col-span-2"><Button type="submit" variant="danger" icon={<Trash2 className="size-3.5" />} disabled={confirmText !== 'DELETE' || !pw} loading={remove.isPending}>Delete everything</Button></div>
        </form>
      </Card>
    </>
  )
}
