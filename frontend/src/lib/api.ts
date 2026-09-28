export type ErrorBody = { code: string; message: string; hint: string; actions: string[] }

export class ApiError extends Error {
  status: number
  code: string
  hint: string
  actions: string[]

  constructor(status: number, body: ErrorBody) {
    super(body.message)
    this.status = status
    this.code = body.code
    this.hint = body.hint
    this.actions = body.actions ?? []
  }
}

let csrfToken = ''
export function setCsrf(token: string) {
  csrfToken = token
}

type Opts = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal }

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined || opts.form ? 'POST' : 'GET')
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken
  let body: BodyInit | undefined
  if (opts.form) body = opts.form
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  let res: Response
  try {
    res = await fetch(path, { method, headers, body, credentials: 'same-origin', signal: opts.signal })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, {
      code: 'network',
      message: 'CareerPilot could not reach the server.',
      hint: 'Check your connection. A server that was idle can take up to a minute to wake up.',
      actions: ['retry'],
    })
  }
  if (res.status === 204) return undefined as T
  const type = res.headers.get('content-type') ?? ''
  if (!res.ok) {
    let err: ErrorBody = { code: 'http_' + res.status, message: `Request failed (HTTP ${res.status}).`, hint: '', actions: ['retry'] }
    if (type.includes('json')) {
      const data = await res.json().catch(() => null)
      if (data?.error) err = data.error
    }
    if (res.status === 401 && path !== '/api/auth/me') window.dispatchEvent(new Event('cp:unauthorized'))
    throw new ApiError(res.status, err)
  }
  if (type.includes('json')) return (await res.json()) as T
  return (await res.blob()) as T
}

export const get = <T,>(path: string) => api<T>(path)
export const post = <T,>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body })
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body })
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body })
export const del = <T,>(path: string) => api<T>(path, { method: 'DELETE' })
