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
  const setup = state.data?.needs_setup

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
            <h1 className="font-serif text-[26px] font-semibold leading-tight">{setup ? 'Set up your workspace' : 'Sign in'}</h1>
            <p className="mt-1.5 text-[13.5px] text-muted">
              {setup ? 'Create the owner account. Registration closes after this.' : 'Your job applications, evidence and resumes.'}
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
          </>
        )}
      </motion.div>
    </div>
  )
}
