import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Circle } from 'lucide-react'
import { useState } from 'react'
import { ProviderChoice, ProviderList } from '../components/AIProviders'
import { Button, Card, ErrorState, Field, Input, cx, errorText, useToast } from '../components/ui'
import { get, post, put } from '../lib/api'
import type { SettingsData } from '../lib/types'

function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: React.ReactNode }) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2">
        {done ? <CheckCircle2 className="size-4 text-strong" /> : <Circle className="size-4 text-faint" />}
        <span className="text-[12px] font-medium text-muted">Step {n}</span>
        <h2 className="text-[15px] font-semibold">{title}</h2>
      </div>
      {children}
    </Card>
  )
}

export default function Setup({ settings }: { settings: SettingsData }) {
  const qc = useQueryClient()
  const toast = useToast()
  const health = useQuery({ queryKey: ['health'], queryFn: () => get<{ database: string; latex_engine: string }>('/api/health') })
  const [username, setUsername] = useState(settings.github.username)
  const [token, setToken] = useState('')
  const [githubSkipped, setGithubSkipped] = useState(false)
  const github = useMutation({
    mutationFn: () => put<SettingsData>('/api/settings/github', { username, token: token || null, include_private_repos: false, include_forks: false }),
    onSuccess: (d) => { qc.setQueryData(['settings'], d); setToken(''); toast('GitHub connected') },
  })
  const finish = useMutation({
    mutationFn: () => post<SettingsData>('/api/settings/onboarding/complete'),
    onSuccess: (d) => qc.setQueryData(['settings'], d),
    onError: (e) => toast(errorText(e), 'error'),
  })
  const ai = settings.ai
  const aiDone = ai.active_order.length > 0 || ai.provider === 'none'
  const githubDone = !!settings.github.username || githubSkipped

  return (
    <div className="min-h-full bg-bg px-4 py-10">
      <div className="mx-auto max-w-3xl space-y-5">
        <div>
          <h1 className="font-serif text-[28px] font-semibold tracking-tight">Welcome to CareerPilot</h1>
          <p className="mt-1 text-[14px] text-ink-2">Let's configure your workspace. Everything here can be changed later in Settings.</p>
        </div>

        <Step n={1} title="AI provider" done={aiDone}>
          <p className="mb-3 text-[13px] text-ink-2">Choose which provider reads job descriptions and helps with wording. Without one, CareerPilot still works using deterministic processing.</p>
          <ProviderChoice ai={ai} compact />
          <div className="-mx-5 mt-4 border-t border-line"><ProviderList ai={ai} /></div>
        </Step>

        <Step n={2} title="GitHub" done={githubDone}>
          <p className="mb-3 text-[13px] text-ink-2">Your repositories are the evidence for your resume. Public repositories need only your username.</p>
          {settings.github.username ? (
            <div className="text-[13px]">Connected as <span className="font-medium">{settings.github.username}</span>. Scan your repositories from Projects.</div>
          ) : (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (username.trim()) github.mutate() }}>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="GitHub username or profile URL"><Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="https://github.com/your-name" /></Field>
                <Field label="Read-only token (optional)" hint="Raises GitHub's rate limit; needed only for private repositories.">
                  <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="github_pat_…" />
                </Field>
              </div>
              {github.error && <ErrorState compact error={github.error} />}
              <div className="flex gap-2">
                <Button type="submit" variant="primary" loading={github.isPending} disabled={!username.trim()}>Connect GitHub</Button>
                <Button type="button" variant="ghost" onClick={() => setGithubSkipped(true)} className={cx(githubSkipped && 'text-strong')}>{githubSkipped ? 'Skipped' : 'Skip for now'}</Button>
              </div>
            </form>
          )}
        </Step>

        <Step n={3} title="Database" done={health.data?.database === 'ok'}>
          <p className="text-[13px] text-ink-2">
            {health.isLoading ? 'Checking…' : health.data?.database === 'ok' ? '✓ Configured and reachable.' : 'The database is not reachable. Check DATABASE_URL on the server.'}
          </p>
        </Step>

        <Step n={4} title="Resume templates" done>
          <p className="text-[13px] text-ink-2">
            Upload later from Resumes → Templates: LaTeX (a .tex file or zipped Overleaf project), Word, PDF or text. If you have no template, “Create from my profile” there builds one in the standard layout.
          </p>
        </Step>

        <div className="flex justify-end">
          <Button variant="primary" loading={finish.isPending} onClick={() => finish.mutate()}>Finish setup</Button>
        </div>
      </div>
    </div>
  )
}
