import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, type ReactNode } from 'react'
import { ApiError, get, post, setCsrf } from './api'
import type { User } from './types'

type AuthState = {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, name: string) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const me = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        const u = await get<User>('/api/auth/me')
        setCsrf(u.csrf_token)
        return u
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    staleTime: Infinity,
    retry: 1,
  })

  useEffect(() => {
    const onUnauthorized = () => qc.setQueryData(['me'], null)
    window.addEventListener('cp:unauthorized', onUnauthorized)
    return () => window.removeEventListener('cp:unauthorized', onUnauthorized)
  }, [qc])

  // Keep the ['me'] query (the auth observer is subscribed to it); drop everything cached for the previous session.
  const resetOthers = () => qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' })
  const accept = (u: User) => {
    setCsrf(u.csrf_token)
    resetOthers()
    qc.setQueryData(['me'], u)
  }

  const value: AuthState = {
    user: me.data ?? null,
    loading: me.isLoading,
    login: async (email, password) => accept(await post<User>('/api/auth/login', { email, password })),
    register: async (email, password, name) =>
      accept(await post<User>('/api/auth/register', { email, password, display_name: name })),
    logout: async () => {
      await post('/api/auth/logout').catch(() => undefined)
      resetOthers()
      qc.setQueryData(['me'], null)
    },
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
