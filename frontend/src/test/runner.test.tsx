import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router'
import { describe, expect, it } from 'vitest'
import ApplicationDetail from '../pages/ApplicationDetail'
import { mockFetch, renderWithProviders } from './utils'

const runner = (connected: boolean, state = connected ? 'waiting' : 'offline') => ({
  connected, state, detail: connected ? 'Complete the CAPTCHA' : '', installed: true, ever_connected: true,
  runners: [{ name: 'laptop', host: 'my-mac', version: '1.1', online: connected, state, detail: '', last_seen_at: new Date().toISOString(), last_seen_seconds: connected ? 1 : 90 }],
})

function app(phase: string, state: string, online: boolean) {
  return {
    id: 7, status: 'in_progress', status_label: 'Application In Progress', job_id: 3, company: 'Acme', role_title: 'Backend Engineer',
    job_code: '', location: '', job_url: '', application_url: 'https://example.com/apply', deadline: null, days_remaining: null,
    discovered_at: null, date_applied: null, follow_up_date: null, submitted_at: null, submission_verified: false, notes: '',
    resume_version: { id: 9, label: 'Acme', version_number: 8 }, updated_at: '', events: [], answers: [], fields: [],
    runner: runner(online),
    session: { id: 2, state, phase, runner_online: online, fields_total: 19, fields_done: 17, resume_uploaded: true, submit_clicked_at: null,
      message: 'Complete the CAPTCHA / security check in the browser window.', current_url: '', step: 2, platform: 'generic', has_screenshot: false,
      log: [{ at: '2026-09-29T12:04:02Z', message: 'Runner connected and picked up the task' }, { at: '2026-09-29T12:04:21Z', message: 'Page 2 completed' }],
      answers_revision: 1, filled_revision: 1, submit_confirmed_at: null, result_text: '', resume_version_id: 9, last_heartbeat_at: null, created_at: '' },
  }
}

describe('runner status', () => {
  it('shows Runner Offline with actions instead of a vague waiting message', async () => {
    mockFetch([
      { match: /\/api\/applications\/7$/, body: app('RUNNER_OFFLINE', 'action_required', false) },
      { match: /\/api\/resumes\?job_id=3$/, body: [] },
    ])
    renderWithProviders(<Routes><Route path="/applications/:id" element={<ApplicationDetail />} /></Routes>, '/applications/7')
    expect(await screen.findByText(/CareerPilot Runner · Offline/)).toBeInTheDocument()
    expect(screen.getAllByText('Runner offline').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Retry connection/ })).toBeInTheDocument()
    expect(screen.queryByText(/Waiting for the apply agent/)).not.toBeInTheDocument()
  })

  it('shows what the agent is waiting for, progress, and a way to continue', async () => {
    mockFetch([
      { match: /\/api\/applications\/7$/, body: app('WAITING_FOR_USER', 'action_required', true) },
      { match: /\/api\/resumes\?job_id=3$/, body: [] },
    ])
    renderWithProviders(<Routes><Route path="/applications/:id" element={<ApplicationDetail />} /></Routes>, '/applications/7')
    expect(await screen.findByText(/Connected — Waiting for you/)).toBeInTheDocument()
    expect(screen.getByText('17 / 19 fields')).toBeInTheDocument()
    expect(screen.getByText(/v8 · uploaded/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue application' })).toBeInTheDocument()
    expect(screen.getByText('Page 2 completed')).toBeInTheDocument()
  })
})
