import type { AppStatus } from './types'

const dateFmt = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
})

export function fmtDate(v: string | null | undefined): string {
  if (!v) return '—'
  const d = v.length === 10 ? new Date(v + 'T00:00:00') : new Date(v)
  return Number.isNaN(d.getTime()) ? '—' : dateFmt.format(d)
}

export function fmtDateTime(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '—' : dateTimeFmt.format(d)
}

export function relative(v: string | null | undefined): string {
  if (!v) return ''
  const diff = (Date.now() - new Date(v).getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`
  const days = Math.floor(diff / 86400)
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`
  return fmtDate(v)
}

export function daysLabel(days: number | null | undefined): string {
  if (days === null || days === undefined) return ''
  if (days < 0) return `${Math.abs(days)} day${days === -1 ? '' : 's'} overdue`
  if (days === 0) return 'Due today'
  if (days === 1) return '1 day remaining'
  return `${days} days remaining`
}

export const STATUS_LABEL: Record<AppStatus, string> = {
  saved: 'Saved',
  analyzing: 'Analyzing',
  resume_ready: 'Resume Ready',
  ready_to_apply: 'Ready to Apply',
  in_progress: 'Application In Progress',
  submitted: 'Submitted',
  assessment: 'Assessment',
  interview: 'Interview',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
  offer: 'Offer',
}

export const STATUS_ORDER: AppStatus[] = [
  'saved', 'analyzing', 'resume_ready', 'ready_to_apply', 'in_progress', 'submitted', 'assessment', 'interview',
  'offer', 'rejected', 'withdrawn',
]

export function greeting(): string {
  const h = new Date().getHours()
  if (h < 5) return 'Good evening'
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

export function plural(n: number, word: string, pluralWord?: string) {
  return `${n} ${n === 1 ? word : (pluralWord ?? word + 's')}`
}
