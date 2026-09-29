import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { CommandPalette } from '../components/CommandPalette'
import { mockFetch, renderWithProviders } from './utils'

describe('command palette', () => {
  it('sends free text with the current page as context and shows what the agent did', async () => {
    const calls = mockFetch([{
      match: /\/api\/agent\/command$/, method: 'POST',
      body: { ok: true, tool: 'application_blockers', permission: 'READ', report: 'The application is waiting because 2 question(s) need your answer.', navigate: '/applications/7' },
    }])
    renderWithProviders(<CommandPalette open onClose={() => {}} />, '/applications/7')
    await userEvent.type(await screen.findByLabelText('Command'), 'why is my application stuck{Enter}')
    expect(await screen.findByText(/2 question\(s\) need your answer/)).toBeInTheDocument()
    const call = calls.find((c) => c.url.endsWith('/api/agent/command'))!
    expect(call.body).toEqual({ text: 'why is my application stuck', context: { application_id: '7' } })
    expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument()
  })

  it('lists commands and filters them as you type', async () => {
    mockFetch([])
    renderWithProviders(<CommandPalette open onClose={() => {}} />)
    expect(await screen.findByText('Apply to current job')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Command'), 'undo')
    await waitFor(() => expect(screen.queryByText('Apply to current job')).not.toBeInTheDocument())
    expect(screen.getByText('Undo last resume change')).toBeInTheDocument()
  })
})
