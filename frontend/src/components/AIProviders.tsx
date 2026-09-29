import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cpu, HardDrive, Lock, PlugZap } from 'lucide-react'
import { useState } from 'react'
import { get, post, put } from '../lib/api'
import { fmtDateTime } from '../lib/format'
import type { AISettings, AIUsage, SettingsData } from '../lib/types'
import { Badge, Button, Card, CardHeader, Field, Input, cx, errorText, useToast } from './ui'

const CHOICES: { value: AISettings['provider']; label: string; hint: string }[] = [
  { value: 'auto', label: 'Automatic', hint: 'Use the configured providers in order' },
  { value: 'gemini', label: 'Gemini', hint: 'Google AI Studio' },
  { value: 'openrouter', label: 'OpenRouter', hint: 'Free models' },
  { value: 'ollama', label: 'Local Ollama', hint: 'Runs next to the API' },
  { value: 'none', label: 'Off', hint: 'Deterministic processing only' },
]

const KEY_HELP: Record<string, string> = {
  gemini: 'Create a key at aistudio.google.com → Get API key.',
  openrouter: 'Create a key at openrouter.ai → Keys. Free models need no credits.',
  ollama: 'Set OLLAMA_BASE_URL on the server where the API runs; Ollama must be reachable from it.',
}

export function useSaveAI() {
  const qc = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => put<SettingsData>('/api/settings/ai', body),
    onSuccess: (d) => { qc.setQueryData(['settings'], d); qc.invalidateQueries({ queryKey: ['ai-usage'] }); toast('AI settings saved') },
    onError: (e) => toast(errorText(e), 'error'),
  })
}

export function ProviderChoice({ ai, compact = false }: { ai: AISettings; compact?: boolean }) {
  const save = useSaveAI()
  const set = (body: Partial<{ provider: string; fallback_enabled: boolean; privacy_mode: boolean }>) =>
    save.mutate({ provider: ai.provider, fallback_enabled: ai.fallback_enabled, privacy_mode: ai.privacy_mode, ...body })
  return (
    <div className="space-y-4">
      <div role="radiogroup" aria-label="AI provider" className={cx('grid gap-2', compact ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-2 sm:grid-cols-5')}>
        {CHOICES.filter((c) => !compact || c.value !== 'auto').map((c) => {
          const p = ai.providers.find((x) => x.id === c.value)
          return (
            <button key={c.value} type="button" role="radio" aria-checked={ai.provider === c.value} onClick={() => set({ provider: c.value })}
              className={cx('rounded-md border px-3 py-2 text-left transition-colors',
                ai.provider === c.value ? 'border-accent/50 bg-accent-soft/50' : 'border-line bg-surface hover:border-line-strong')}>
              <div className="flex items-center gap-1.5 text-[13px] font-medium">
                {p && <span className={cx('size-1.5 rounded-full', p.configured ? 'bg-strong' : 'bg-faint')} aria-hidden />}
                {c.label}
              </div>
              <div className="text-[11.5px] text-muted">{p ? (p.configured ? 'Configured' : 'Not configured') : c.hint}</div>
            </button>
          )
        })}
      </div>
      <div className="space-y-2">
        <label className="flex items-start gap-2 text-[13px]">
          <input type="checkbox" className="mt-0.5 accent-[var(--accent)]" checked={ai.fallback_enabled} onChange={(e) => set({ fallback_enabled: e.target.checked })} />
          <span><span className="font-medium">Fallback</span> — when the provider hits its rate limit or is down, try the next configured provider.</span>
        </label>
        <label className="flex items-start gap-2 text-[13px]">
          <input type="checkbox" className="mt-0.5 accent-[var(--accent)]" checked={ai.privacy_mode} onChange={(e) => set({ privacy_mode: e.target.checked })} />
          <span><span className="font-medium">Privacy mode</span> — send resume and profile content only to a local model (Ollama). External providers are never used while this is on.</span>
        </label>
      </div>
      <div className="rounded-md border border-line bg-surface-2/50 px-3 py-2 text-[12.5px] text-ink-2">
        {ai.active_order.length
          ? <>In use: <span className="font-medium text-ink">{ai.active_order.join(' → ')}</span>{ai.privacy_mode && <> · <Lock className="inline size-3" /> local only</>}</>
          : <>No AI provider in use. CareerPilot keeps working with deterministic processing.</>}
        {ai.note && <div className="mt-0.5 text-muted">{ai.note}</div>}
      </div>
    </div>
  )
}

function ProviderRow({ p, onSaveKey }: { p: AISettings['providers'][number]; onSaveKey: (key: string | null) => void }) {
  const [key, setKey] = useState('')
  const toast = useToast()
  const test = useMutation({
    mutationFn: () => post<{ ok: boolean; provider: string; latency_ms: number }>('/api/settings/ai/test', { provider: p.id }),
    onSuccess: (r) => toast(`${r.provider} answered in ${(r.latency_ms / 1000).toFixed(1)}s`),
    onError: (e) => toast(errorText(e), 'error'),
  })
  const Icon = p.id === 'ollama' ? HardDrive : Cpu
  return (
    <li className="space-y-2 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Icon className="size-4 text-muted" />
        <span className="text-[13.5px] font-medium">{p.label}</span>
        <span className={cx('flex items-center gap-1 text-[12px]', p.configured ? 'text-strong' : 'text-muted')}>
          <span className={cx('size-1.5 rounded-full', p.configured ? 'bg-strong' : 'bg-faint')} />{p.configured ? `Configured${p.source === 'server' ? ' on the server' : ' with your key'}` : 'Not configured'}
        </span>
        {!p.external && <Badge>Local</Badge>}
        <span className="ml-auto text-[12px] text-muted">Model: {p.model}</span>
        <Button size="sm" icon={<PlugZap className="size-3.5" />} loading={test.isPending} disabled={!p.configured} onClick={() => test.mutate()}>Test connection</Button>
      </div>
      {p.id !== 'ollama' ? (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (key.trim()) { onSaveKey(key.trim()); setKey('') } }}>
          <Field label={`Your ${p.label} API key (optional)`} className="min-w-[240px] flex-1"
            hint={<>{p.your_key ? <>Stored encrypted: <span className="font-mono">{p.your_key}</span>. </> : ''}{KEY_HELP[p.id]} Keys stay on the server and are never shown again.</>}>
            <Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={p.your_key ? 'Replace the stored key' : p.source === 'server' ? 'Using the server key' : 'Paste a key'} />
          </Field>
          <Button type="submit" disabled={!key.trim()}>Save key</Button>
          {p.your_key && <Button type="button" variant="ghost" onClick={() => onSaveKey('')}>Remove</Button>}
        </form>
      ) : (
        <p className="text-[12px] text-muted">{p.configured ? `Server: ${p.base_url}` : KEY_HELP.ollama}</p>
      )}
    </li>
  )
}

export function ProviderList({ ai }: { ai: AISettings }) {
  const save = useSaveAI()
  const base = { provider: ai.provider, fallback_enabled: ai.fallback_enabled, privacy_mode: ai.privacy_mode }
  return (
    <ul className="divide-y divide-line">
      {ai.providers.map((p) => (
        <ProviderRow key={p.id} p={p} onSaveKey={(key) => save.mutate({ ...base, [`${p.id}_key`]: key })} />
      ))}
    </ul>
  )
}

export function AIUsagePanel() {
  const q = useQuery({ queryKey: ['ai-usage'], queryFn: () => get<AIUsage>('/api/settings/ai/usage'), refetchInterval: 30_000 })
  if (!q.data) return null
  const u = q.data
  return (
    <Card>
      <CardHeader title="AI usage" subtitle="Application-side usage: requests CareerPilot sent. Provider quotas are shown only where the provider reports them." />
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 border-b border-line px-4 py-3 text-[12.5px] sm:grid-cols-4">
        <div><dt className="text-muted">Current provider</dt><dd className="font-medium">{u.current_provider}</dd></div>
        <div><dt className="text-muted">Requests today</dt><dd className="font-medium tabular-nums">{u.providers.reduce((a, p) => a + p.requests_today, 0)}</dd></div>
        <div><dt className="text-muted">Requests this month</dt><dd className="font-medium tabular-nums">{u.providers.reduce((a, p) => a + p.requests_month, 0)}</dd></div>
        <div><dt className="text-muted">Fallback</dt><dd className="font-medium">{u.fallback}</dd></div>
      </dl>
      <table className="w-full text-[12.5px]">
        <thead className="text-left text-muted">
          <tr><th className="px-4 py-2 font-normal">Provider</th><th className="px-2 font-normal">Today</th><th className="px-2 font-normal">Month</th><th className="px-2 font-normal">Rate-limited</th><th className="px-4 font-normal">Last error</th></tr>
        </thead>
        <tbody className="divide-y divide-line">
          {u.providers.filter((p) => p.configured || p.requests_month).map((p) => (
            <tr key={p.id} className="align-top">
              <td className="px-4 py-2">
                <div className="font-medium">{p.label}</div>
                {p.cooling_down_seconds > 0 && <div className="text-partial">Paused {Math.ceil(p.cooling_down_seconds / 60)} min after a rate limit</div>}
                {p.reported && p.reported.free_requests_daily_limit != null && (
                  <div className="text-muted">Reported by OpenRouter: {p.reported.free_requests_today} of {p.reported.free_requests_daily_limit} free requests today</div>
                )}
              </td>
              <td className="px-2 py-2 tabular-nums">{p.requests_today}</td>
              <td className="px-2 py-2 tabular-nums">{p.requests_month}</td>
              <td className="px-2 py-2 tabular-nums">{p.rate_limited_month}</td>
              <td className="max-w-[260px] px-4 py-2 text-muted">{p.last_error ? <><div className="line-clamp-2">{p.last_error}</div><div className="text-faint">{p.last_error_at && fmtDateTime(p.last_error_at)}</div></> : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
