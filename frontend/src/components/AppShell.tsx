import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import {
  BarChart3, Briefcase, FileText, FolderGit2, LayoutDashboard, LogOut, Moon, Plus, Send, Settings, Sun, User2,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { post } from '../lib/api'
import { useAuth } from '../lib/auth'
import type { JobSummary } from '../lib/types'
import { Button, Dialog, ErrorState, Input, Kbd, cx } from './ui'
import { SidebarAgentStatus } from './AgentStatus'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/jobs', label: 'Jobs', icon: Briefcase },
  { to: '/applications', label: 'Applications', icon: Send },
  { to: '/resumes', label: 'Resumes', icon: FileText },
  { to: '/projects', label: 'Projects', icon: FolderGit2 },
  { to: '/profile', label: 'Career Profile', icon: User2 },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/settings', label: 'Settings', icon: Settings },
]
const MOBILE_NAV = [NAV[0], NAV[1], NAV[2], NAV[3], NAV[5]]

function useTheme() {
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem('cp-theme')
      if (saved) return saved === 'dark'
    } catch { /* storage unavailable */ }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  })
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    try { localStorage.setItem('cp-theme', dark ? 'dark' : 'light') } catch { /* ignore */ }
  }, [dark])
  return [dark, setDark] as const
}

export function QuickImport({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [url, setUrl] = useState('')
  const nav = useNavigate()
  const qc = useQueryClient()
  const ref = useRef<HTMLInputElement>(null)
  const m = useMutation({
    mutationFn: (u: string) => post<{ job: JobSummary; task_id: number }>('/api/jobs', { url: u }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
      onClose()
      setUrl('')
      nav(`/jobs/${r.job.id}?task=${r.task_id}`)
    },
  })
  useEffect(() => {
    if (open) setTimeout(() => ref.current?.focus(), 50)
    else m.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  return (
    <Dialog open={open} onClose={onClose} title="Analyze a job">
      <form onSubmit={(e) => { e.preventDefault(); if (url.trim()) m.mutate(url.trim()) }} className="space-y-3">
        <p className="text-[13px] text-muted">Paste the job posting link. CareerPilot reads the posting, extracts the requirements and matches them against your evidence.</p>
        <Input ref={ref} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://company.com/careers/job/12345" inputMode="url" aria-label="Job URL" />
        {m.error && <ErrorState error={m.error} compact onRetry={() => m.mutate(url.trim())} onAction={(a) => { if (a === 'paste_description') { onClose(); nav('/jobs/new?mode=paste') } }} />}
        <div className="flex items-center justify-between gap-2 pt-1">
          <button type="button" className="text-[12.5px] text-muted underline-offset-2 hover:text-ink hover:underline" onClick={() => { onClose(); nav('/jobs/new?mode=paste') }}>
            Paste a description or upload a PDF instead
          </button>
          <Button variant="primary" type="submit" loading={m.isPending} disabled={!url.trim()}>Analyze</Button>
        </div>
      </form>
    </Dialog>
  )
}

export function AppShell() {
  const { user, logout } = useAuth()
  const [dark, setDark] = useTheme()
  const [quick, setQuick] = useState(false)
  const loc = useLocation()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setQuick(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="flex h-full">
      <aside className="hidden w-[232px] shrink-0 flex-col border-r border-line bg-surface/60 md:flex">
        <div className="flex h-14 items-center gap-2.5 px-5">
          <svg viewBox="0 0 32 32" className="size-6"><rect width="32" height="32" rx="7" fill="var(--ink)" /><path d="M9 21.5 16 8l7 13.5-7-3.2-7 3.2Z" fill="var(--accent)" /></svg>
          <span className="font-serif text-[17px] font-semibold tracking-[-0.01em]">CareerPilot</span>
        </div>
        <div className="px-3 pb-3">
          <button onClick={() => setQuick(true)} className="flex h-9 w-full items-center gap-2 rounded-md border border-line-strong bg-surface px-3 text-[13px] text-muted shadow-card transition-colors hover:text-ink">
            <Plus className="size-3.5" /> Analyze a job <span className="ml-auto"><Kbd>⌘K</Kbd></span>
          </button>
        </div>
        <nav className="flex-1 space-y-0.5 px-3" aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cx('group relative flex h-8.5 items-center gap-2.5 rounded-md px-2.5 text-[13.5px] transition-colors',
                isActive ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2 hover:bg-surface-2/70 hover:text-ink')}>
              {({ isActive }) => (
                <>
                  {isActive && <motion.span layoutId="nav-active" className="absolute inset-y-1.5 left-0 w-0.5 rounded bg-accent" />}
                  <n.icon className={cx('size-4', isActive ? 'text-accent' : 'text-muted group-hover:text-ink-2')} />
                  {n.label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <SidebarAgentStatus />
        <div className="border-t border-line p-3">
          <div className="flex items-center gap-2 px-1">
            <div className="flex size-7 items-center justify-center rounded-full bg-surface-3 text-[12px] font-semibold text-ink-2">
              {(user?.display_name || user?.email || '?').slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12.5px] font-medium">{user?.display_name || 'You'}</div>
              <div className="truncate text-[11.5px] text-muted">{user?.email}</div>
            </div>
            <button onClick={() => setDark(!dark)} className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Toggle theme">
              {dark ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
            </button>
            <button onClick={() => logout()} className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Sign out">
              <LogOut className="size-3.5" />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center justify-between border-b border-line bg-surface/80 px-4 backdrop-blur md:hidden">
          <span className="font-serif text-[16px] font-semibold">CareerPilot</span>
          <div className="flex items-center gap-1">
            <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setQuick(true)}>Job</Button>
            <NavLink to="/settings" className="rounded p-1.5 text-muted" aria-label="Settings"><Settings className="size-4" /></NavLink>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto pb-20 md:pb-0">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={loc.pathname.split('/').slice(0, 3).join('/')}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.16, ease: [0.2, 0.8, 0.2, 1] }}
              className="h-full"
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
        <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-surface/95 backdrop-blur md:hidden" aria-label="Mobile">
          {MOBILE_NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cx('flex flex-1 flex-col items-center gap-0.5 py-2 text-[10.5px]', isActive ? 'text-accent' : 'text-muted')}>
              <n.icon className="size-4.5" />
              {n.label.replace('Career ', '')}
            </NavLink>
          ))}
        </nav>
      </div>
      <QuickImport open={quick} onClose={() => setQuick(false)} />
    </div>
  )
}

export function Page({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return <div className={cx('mx-auto px-4 py-6 md:px-8 md:py-8', wide ? 'max-w-[1500px]' : 'max-w-[1180px]')}>{children}</div>
}
