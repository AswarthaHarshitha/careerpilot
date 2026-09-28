import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { ToastProvider } from '../components/ui'

export function renderWithProviders(ui: ReactNode, path = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

type Route = { match: RegExp; method?: string; status?: number; body: unknown }

/** Replaces fetch with a router over canned JSON responses; returns the list of calls made. */
export function mockFetch(routes: Route[]) {
  const calls: { url: string; method: string; body?: unknown }[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const r = routes.find((x) => x.match.test(url) && (!x.method || x.method === method))
    if (!r) return new Response(JSON.stringify({ error: { code: 'not_found', message: 'Not found', hint: '', actions: [] } }), { status: 404, headers: { 'content-type': 'application/json' } })
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  return calls
}
