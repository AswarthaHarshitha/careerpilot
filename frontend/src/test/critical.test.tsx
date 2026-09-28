import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ErrorState, MATCH_LABEL } from '../components/ui'
import { ApiError } from '../lib/api'
import { AuthProvider } from '../lib/auth'
import Login from '../pages/Login'
import { mockFetch, renderWithProviders } from './utils'

describe('first-run setup and sign-in', () => {
  it('offers owner setup when no account exists and registers', async () => {
    const calls = mockFetch([
      { match: /\/api\/auth\/me$/, status: 401, body: { error: { code: 'unauthorized', message: 'Please sign in', hint: '', actions: [] } } },
      { match: /\/api\/auth\/state$/, body: { needs_setup: true, registration_open: true } },
      { match: /\/api\/auth\/register$/, method: 'POST', body: { id: 1, email: 'me@example.com', display_name: 'Me', csrf_token: 't' } },
    ])
    renderWithProviders(<AuthProvider><Login /></AuthProvider>)
    expect(await screen.findByText('Set up your workspace')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Your name'), 'Me')
    await userEvent.type(screen.getByLabelText('Email'), 'me@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'Strong-Pass-2026')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/api/auth/register'))).toBe(true))
    const reg = calls.find((c) => c.url.endsWith('/api/auth/register'))!
    expect(reg.body).toEqual({ email: 'me@example.com', password: 'Strong-Pass-2026', display_name: 'Me' })
  })

  it('shows sign-in with a readable error when credentials are wrong', async () => {
    mockFetch([
      { match: /\/api\/auth\/me$/, status: 401, body: { error: { code: 'unauthorized', message: 'x', hint: '', actions: [] } } },
      { match: /\/api\/auth\/state$/, body: { needs_setup: false, registration_open: false } },
      { match: /\/api\/auth\/login$/, method: 'POST', status: 401, body: { error: { code: 'unauthorized', message: 'Email or password is incorrect.', hint: '', actions: [] } } },
    ])
    renderWithProviders(<AuthProvider><Login /></AuthProvider>)
    await userEvent.type(await screen.findByLabelText('Email'), 'me@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Email or password is incorrect.')).toBeInTheDocument()
  })
})

describe('error states', () => {
  it('explains the failure, the likely reason and offers the next actions', async () => {
    const retry = vi.fn()
    const onAction = vi.fn()
    const err = new ApiError(422, {
      code: 'fetch_failed', message: 'The job page could not be analyzed.',
      hint: 'The company requires authentication or uses an unsupported application portal.',
      actions: ['retry', 'open_manually', 'paste_description'],
    })
    renderWithProviders(<ErrorState error={err} onRetry={retry} onAction={onAction} openUrl="https://example.com/job" />)
    expect(screen.getByText('The job page could not be analyzed.')).toBeInTheDocument()
    expect(screen.getByText(/unsupported application portal/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalled()
    expect(screen.getByRole('link')).toHaveAttribute('href', 'https://example.com/job')
    await userEvent.click(screen.getByRole('button', { name: 'Paste the description instead' }))
    expect(onAction).toHaveBeenCalledWith('paste_description')
  })
})

describe('evidence vocabulary', () => {
  it('uses evidence statuses, not scores', () => {
    expect(Object.values(MATCH_LABEL)).toEqual(['Strong evidence', 'Partial evidence', 'Missing', 'Needs verification'])
  })
})
