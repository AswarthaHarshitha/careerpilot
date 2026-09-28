import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { describe, expect, it } from 'vitest'
import ApplicationDetail from '../pages/ApplicationDetail'
import { mockFetch, renderWithProviders } from './utils'

const field = (over: Record<string, unknown>) => ({
  id: 1, label: 'First name', input_type: 'text', required: true, options: [], field_key: 'first_name', sensitive: false,
  value: 'Test', value_source: 'profile', status: 'filled', note: '', fill_result: 'ok', validation_message: '', step: 0, ...over,
})

function app(overrides: Record<string, unknown> = {}) {
  return {
    id: 7, status: 'in_progress', status_label: 'Application In Progress', job_id: 3, company: 'IBM', role_title: 'Software Engineer',
    job_code: '12345', location: 'Bengaluru', job_url: '', application_url: 'https://example.com/apply', deadline: null, days_remaining: null,
    discovered_at: null, date_applied: null, follow_up_date: null, submitted_at: null, submission_verified: false, notes: '',
    resume_version: { id: 9, label: 'IBM', version_number: 1 }, updated_at: '', events: [], answers: [],
    fields: [field({})],
    session: { id: 2, state: 'awaiting_review', message: 'Ready', current_url: '', step: 0, platform: '', has_screenshot: false, log: [],
      answers_revision: 1, filled_revision: 1, submit_confirmed_at: null, result_text: '', resume_version_id: 9, last_heartbeat_at: null, created_at: '' },
    ...overrides,
  }
}

describe('final submission', () => {
  it('requires typing the company name before the submit button enables', async () => {
    const calls = mockFetch([
      { match: /\/api\/applications\/7\/automation\/confirm-submit$/, method: 'POST', body: app({ session: { ...app().session, state: 'submit_confirmed' } }) },
      { match: /\/api\/applications\/7$/, body: app() },
      { match: /\/api\/resumes\?job_id=3$/, body: [] },
      { match: /\/api\/settings$/, body: { submit_confirmation_mode: 'type_company' } },
    ])
    renderWithProviders(<Routes><Route path="/applications/:id" element={<ApplicationDetail />} /></Routes>, '/applications/7')
    await userEvent.click(await screen.findByRole('button', { name: 'Submit application' }))
    const dialog = await screen.findByRole('dialog')
    const confirm = Array.from(dialog.querySelectorAll('button')).find((b) => b.textContent === 'Submit application')!
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Type IBM to confirm'), 'IB')
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Type IBM to confirm'), 'M')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/confirm-submit'))).toBe(true))
    expect(calls.find((c) => c.url.endsWith('/confirm-submit'))!.body).toEqual({ typed_confirmation: 'IBM' })
  })

  it('keeps submission disabled while a question still needs an answer', async () => {
    mockFetch([
      { match: /\/api\/applications\/7$/, body: app({ fields: [field({}), field({ id: 2, label: 'Why IBM?', field_key: 'why_company', input_type: 'textarea', value: '', status: 'needs_input', value_source: 'none' })] }) },
      { match: /\/api\/resumes\?job_id=3$/, body: [] },
    ])
    renderWithProviders(<Routes><Route path="/applications/:id" element={<ApplicationDetail />} /></Routes>, '/applications/7')
    expect(await screen.findByText('Why IBM?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit application' })).toBeDisabled()
    expect(screen.getByText('Needs your input')).toBeInTheDocument()
  })

  it('never shows a self-identification answer it did not get from the user', async () => {
    mockFetch([
      { match: /\/api\/applications\/7$/, body: app({ fields: [field({ id: 3, label: 'Gender', field_key: 'gender', sensitive: true, input_type: 'select', options: ['Female', 'Male'], value: '', status: 'needs_input', note: 'Self-identification question - CareerPilot never answers this for you.', value_source: 'none', required: false })] }) },
      { match: /\/api\/resumes\?job_id=3$/, body: [] },
    ])
    renderWithProviders(<Routes><Route path="/applications/:id" element={<ApplicationDetail />} /></Routes>, '/applications/7')
    expect(await screen.findByText(/never answers this for you/)).toBeInTheDocument()
    expect(screen.getByText('Self-identification')).toBeInTheDocument()
  })
})
