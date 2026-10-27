import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { useState } from 'react'
import { ErrorState, Button, Field, Input, Spinner } from '../components/ui'
import { get } from '../lib/api'
import { useAuth } from '../lib/auth'

export default function Login() {
  const { login, register } = useAuth()
  const state = useQuery({ queryKey: ['auth-state'], queryFn: () => get<{ needs_setup: boolean; registration_open: boolean }>('/api/auth/state') })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState<'signin' | 'register'>('signin')
  const setup = state.data?.needs_setup || mode === 'register'
  const canRegister = !state.data?.needs_setup && state.data?.registration_open

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (setup) await register(email, password, name)
      else await login(email, password)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-bg px-4 py-10">
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }} className="w-full max-w-[380px]">
        <div className="mb-8 flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" className="size-7"><rect width="32" height="32" rx="7" fill="var(--ink)" /><path d="M9 21.5 16 8l7 13.5-7-3.2-7 3.2Z" fill="var(--accent)" /></svg>
          <span className="font-serif text-[20px] font-semibold">CareerPilot</span>
        </div>
        {state.isLoading ? (
          <Spinner />
        ) : state.error ? (
          <ErrorState error={state.error} onRetry={() => state.refetch()} />
        ) : (
          <>
            <h1 className="font-serif text-[26px] font-semibold leading-tight">{state.data?.needs_setup ? 'Set up your workspace' : setup ? 'Create an account' : 'Sign in'}</h1>
            <p className="mt-1.5 text-[13.5px] text-muted">
              {state.data?.needs_setup ? 'Create the owner account. Registration closes after this.' : setup ? 'Your data is private to your account.' : 'Your job applications, evidence and resumes.'}
            </p>
            <form onSubmit={submit} className="mt-6 space-y-3.5">
              {setup && (
                <Field label="Your name">
                  <Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
                </Field>
              )}
              <Field label="Email">
                <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
              </Field>
              <Field label="Password" hint={setup ? 'At least 12 characters, mixed case and a digit.' : undefined}>
                <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={setup ? 'new-password' : 'current-password'} required />
              </Field>
              {error ? <ErrorState error={error} compact /> : null}
              <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>
                {setup ? 'Create account' : 'Sign in'}
              </Button>
            </form>
            {canRegister && (
              <button type="button" onClick={() => { setMode(mode === 'register' ? 'signin' : 'register'); setError(null) }}
                className="mt-4 text-[13px] text-muted underline-offset-2 hover:text-ink hover:underline">
                {mode === 'register' ? 'I already have an account' : 'Create an account'}
              </button>
            )}
            {!setup && <p className="mt-4 text-[12px] text-faint">Forgot your password? Whoever runs this CareerPilot server can reset it with <code className="font-mono">careerpilot-admin reset-password</code>.</p>}
          </>
        )}
      </motion.div>
    </div>
  )
}
