import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ClipboardCopy, Download, MonitorSmartphone, Play, RefreshCw } from 'lucide-react'
import { Link } from 'react-router'
import { get } from '../lib/api'
import type { AgentStatus, AutomationSession, RunnerStatus } from '../lib/types'
import { Button, cx, useToast } from './ui'

export function useAgentStatus() {
  return useQuery({ queryKey: ['agent-status'], queryFn: () => get<AgentStatus>('/api/agent/status'), refetchInterval: 4000 })
}

export const PHASE_LABEL: Record<string, string> = {
  CREATED: 'Created', QUEUED: 'Queued for the runner', RUNNER_OFFLINE: 'Runner offline', RUNNER_CONNECTED: 'Runner connected',
  OPENING_PAGE: 'Opening application page', INSPECTING_PAGE: 'Inspecting the page', MAPPING_FORM: 'Mapping the form',
  FILLING_FORM: 'Filling the form', UPLOADING_RESUME: 'Uploading resume', WAITING_FOR_USER: 'Waiting for you',
  RESUMING: 'Resuming', VALIDATING_FORM: 'Validating the form', READY_TO_SUBMIT: 'Ready to submit', SUBMITTING: 'Submitting',
  VERIFYING_SUBMISSION: 'Verifying submission', SUBMITTED: 'Submitted', SUBMISSION_UNCERTAIN: 'Submission status uncertain',
  BLOCKED: 'Blocked', FAILED: 'Failed', CANCELLED: 'Cancelled',
}

const RUNNER_LABEL: Record<string, string> = {
  idle: 'Connected — Ready', working: 'Connected — Working', waiting: 'Connected — Waiting for you', blocked: 'Connected — Blocked', offline: 'Offline',
}

export function Dot({ on, tone }: { on: boolean; tone?: 'attention' | 'bad' }) {
  return (
    <span className={cx('relative inline-flex size-2 shrink-0 rounded-full', !on ? 'border border-faint' : tone === 'attention' ? 'bg-partial' : tone === 'bad' ? 'bg-missing' : 'bg-strong')}>
      {on && !tone && <span className="absolute inset-0 animate-ping rounded-full bg-strong/40 [animation-duration:2.4s]" />}
    </span>
  )
}

export function runnerTone(r: RunnerStatus): 'attention' | 'bad' | undefined {
  return r.state === 'waiting' ? 'attention' : r.state === 'blocked' ? 'bad' : undefined
}

/** Compact status for the sidebar: always visible, links to what the agent is doing. */
export function SidebarAgentStatus() {
  const q = useAgentStatus()
  if (!q.data) return null
  const { runner, task } = q.data
  return (
    <Link to={task ? `/applications/${task.application_id}` : '/settings?tab=automation'}
      className="mx-3 mb-2 block rounded-md border border-line px-2.5 py-2 text-[12px] hover:border-line-strong" aria-label="Agent status">
      <div className="flex items-center gap-1.5 font-medium text-ink">
        <Dot on={runner.connected} tone={runnerTone(runner)} />Runner · {runner.connected ? RUNNER_LABEL[runner.state].replace('Connected — ', '') : 'Offline'}
      </div>
      {task ? (
        <div className="mt-1 text-muted">
          <div className="truncate text-ink-2">{task.role_title} — {task.company}</div>
          <div className="truncate">{PHASE_LABEL[task.phase] ?? task.phase}{task.fields_total ? ` · ${task.fields_done}/${task.fields_total} fields` : ''}</div>
        </div>
      ) : !runner.connected && <div className="mt-0.5 text-muted">{runner.installed ? 'Not running on this computer' : 'Not set up yet'}</div>}
    </Link>
  )
}

/** Full runner card: what state it is in and what to do about it. Never just "waiting". */
export function RunnerCard({ runner, compact = false }: { runner: RunnerStatus; compact?: boolean }) {
  const qc = useQueryClient()
  const toast = useToast()
  const origin = window.location.origin
  const copy = (t: string) => { navigator.clipboard.writeText(t); toast('Command copied') }
  if (runner.connected) {
    const r = runner.runners.find((x) => x.online)
    return (
      <div className="flex items-start gap-2.5 rounded-md border border-line bg-surface px-3 py-2.5 text-[13px]">
        <span className="mt-1.5"><Dot on tone={runnerTone(runner)} /></span>
        <div className="min-w-0">
          <div className="font-medium">CareerPilot Runner · {RUNNER_LABEL[runner.state]}</div>
          <div className="text-[12px] text-muted">{r?.host || r?.name}{runner.detail ? ` · ${runner.detail}` : ''}</div>
        </div>
      </div>
    )
  }
  return (
    <div className="rounded-md border border-partial/30 bg-partial-soft/50 px-3 py-3 text-[13px]">
      <div className="flex items-center gap-2 font-medium"><Dot on={false} />CareerPilot Runner · Offline</div>
      <p className="mt-1 text-ink-2">
        {runner.ever_connected
          ? 'The runner is not running on this computer, so nothing can happen in the browser right now. Your application is saved and continues when it reconnects.'
          : 'The runner is the small program that opens the employer’s site in a browser on your computer and fills the form while you watch. Install it once; it then starts with your computer.'}
      </p>
      {!compact && (
        <div className="mt-2.5 flex flex-wrap gap-2">
          {runner.ever_connected ? (
            <Button size="sm" icon={<Play className="size-3.5" />} onClick={() => copy('cd careerpilot/backend && uv run careerpilot-runner start')}>Copy start command</Button>
          ) : (
            <Link to="/settings?tab=automation"><Button size="sm" variant="primary" icon={<Download className="size-3.5" />}>Install Runner</Button></Link>
          )}
          <Button size="sm" icon={<RefreshCw className="size-3.5" />} onClick={() => { qc.invalidateQueries({ queryKey: ['agent-status'] }); qc.invalidateQueries({ queryKey: ['application'] }) }}>Retry connection</Button>
          <Button size="sm" variant="ghost" icon={<ClipboardCopy className="size-3.5" />} onClick={() => copy(`cd careerpilot/backend && uv run careerpilot-runner --api ${origin} --token <your token>`)}>Run manually</Button>
        </div>
      )}
    </div>
  )
}

const FLOW = ['OPENING_PAGE', 'MAPPING_FORM', 'FILLING_FORM', 'READY_TO_SUBMIT', 'SUBMITTING', 'SUBMITTED']
const FLOW_OF: Record<string, number> = {
  QUEUED: -1, RUNNER_OFFLINE: -1, RESUMING: 0, OPENING_PAGE: 0, INSPECTING_PAGE: 0, MAPPING_FORM: 1, FILLING_FORM: 2,
  UPLOADING_RESUME: 2, VALIDATING_FORM: 2, WAITING_FOR_USER: 2, READY_TO_SUBMIT: 3, SUBMITTING: 4, VERIFYING_SUBMISSION: 4,
  SUBMITTED: 5, SUBMISSION_UNCERTAIN: 4,
}

export function PhaseSteps({ phase }: { phase: string }) {
  const at = FLOW_OF[phase] ?? 0
  return (
    <ol className="flex flex-wrap items-center gap-x-1 gap-y-1 text-[11.5px]" aria-label="Application progress">
      {FLOW.map((p, i) => (
        <li key={p} className="flex items-center gap-1">
          <span className={cx('rounded px-1.5 py-0.5', i < at ? 'text-strong' : i === at ? 'bg-accent-soft font-medium text-accent' : 'text-faint')}>
            {PHASE_LABEL[p].replace('Opening application page', 'Open').replace('Mapping the form', 'Map').replace('Filling the form', 'Fill').replace('Ready to submit', 'Review')}
          </span>
          {i < FLOW.length - 1 && <span className="text-faint">›</span>}
        </li>
      ))}
    </ol>
  )
}

export function EventTimeline({ s }: { s: AutomationSession }) {
  const items = [...s.log].reverse()
  return (
    <ol className="max-h-[320px] space-y-1 overflow-y-auto font-mono text-[11.5px]" aria-label="Agent activity">
      {items.map((e, i) => (
        <li key={`${e.at}-${i}`} className={cx('flex gap-2', i === 0 ? 'text-ink' : 'text-muted')}>
          <span className="shrink-0 tabular-nums text-faint">{new Date(e.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
          <span className="font-sans text-[12px]">{e.message}</span>
        </li>
      ))}
    </ol>
  )
}

export function RunnerIcon() { return <MonitorSmartphone className="size-4" /> }
