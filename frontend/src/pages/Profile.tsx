import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Page } from '../components/AppShell'
import {
  Badge, Button, Card, CardHeader, Dialog, ErrorState, Field, Input, LoadingRows, PageHeader, Select, Tabs, Textarea,
  errorText, useToast,
} from '../components/ui'
import { del, get, post, put } from '../lib/api'

type Personal = {
  full_name: string; first_name: string; last_name: string; email: string; phone: string; location: string
  street: string; address_line2: string; city: string; state: string; postal_code: string; country: string
  linkedin_url: string; github_url: string; portfolio_url: string; headline: string; summary: string
  work_authorization: string; requires_sponsorship: boolean | null; willing_to_relocate: boolean | null
  notice_period: string; salary_expectation: string; years_of_experience: number | null; earliest_start_date: string
  preferred_locations: string[]; preferred_job_types: string[]
}
type Exp = { id: number; organization: string; title: string; location: string; employment_type: string; start_date: string; end_date: string; is_current: boolean; bullets: string[] }
type Edu = { id: number; institution: string; degree: string; field_of_study: string; start_date: string; end_date: string; graduation_year: number | null; grade: string; location: string }
type Skill = { id: number; name: string; canonical: string; category: string; known: boolean }
type Cert = { id: number; name: string; issuer: string; date: string; url: string }
type Answer = { id: number; question_key: string; label: string; answer: string; sensitive: boolean }
type ProfileData = { personal: Personal; experiences: Exp[]; educations: Edu[]; skills: Skill[]; certifications: Cert[]; achievements: { id: number; text: string }[]; answers: Answer[]; completeness: { missing: string[] } }

type Tab = 'personal' | 'experience' | 'education' | 'skills' | 'certs' | 'preferences' | 'answers'

export default function Profile() {
  const q = useQuery({ queryKey: ['profile'], queryFn: () => get<ProfileData>('/api/profile') })
  const [tab, setTab] = useState<Tab>('personal')
  return (
    <Page>
      <PageHeader title="Career profile" subtitle="The single source of truth for every resume and application. Nothing here is guessed." />
      {q.data && q.data.completeness.missing.length > 0 && (
        <div className="mb-5 rounded-lg border border-line bg-surface-2/60 px-4 py-3 text-[13px]">
          <span className="font-medium">Missing: </span><span className="text-ink-2">{q.data.completeness.missing.join(', ')}.</span>
          <span className="text-muted"> Application answers that need these will ask you. </span>
          <Link to="/resumes/templates" className="text-accent hover:underline">Import from your resume template</Link>
        </div>
      )}
      <Tabs className="mb-5" value={tab} onChange={setTab} tabs={[
        { value: 'personal', label: 'Personal & links' },
        { value: 'experience', label: 'Experience', count: q.data?.experiences.length },
        { value: 'education', label: 'Education', count: q.data?.educations.length },
        { value: 'skills', label: 'Skills', count: q.data?.skills.length },
        { value: 'certs', label: 'Certifications & achievements' },
        { value: 'preferences', label: 'Application preferences' },
        { value: 'answers', label: 'Saved answers', count: q.data?.answers.length },
      ]} />
      {q.isLoading ? <LoadingRows /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <>
          {tab === 'personal' && <PersonalForm p={q.data!.personal} section="personal" />}
          {tab === 'preferences' && <PersonalForm p={q.data!.personal} section="preferences" />}
          {tab === 'experience' && <ExperienceList items={q.data!.experiences} />}
          {tab === 'education' && <EducationList items={q.data!.educations} />}
          {tab === 'skills' && <Skills items={q.data!.skills} />}
          {tab === 'certs' && <CertsAndAchievements certs={q.data!.certifications} achievements={q.data!.achievements} />}
          {tab === 'answers' && <Answers items={q.data!.answers} />}
        </>
      )}
    </Page>
  )
}

function useSave<T>(fn: (b: T) => Promise<unknown>, msg = 'Saved') {
  const qc = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['profile'] }); toast(msg) },
    onError: (e) => toast(errorText(e), 'error'),
  })
}

const triState = (v: boolean | null) => (v === null ? '' : v ? 'yes' : 'no')
const fromTri = (v: string) => (v === '' ? null : v === 'yes')

function PersonalForm({ p, section }: { p: Personal; section: 'personal' | 'preferences' }) {
  const [f, setF] = useState(p)
  useEffect(() => setF(p), [p])
  const save = useSave((body: Personal) => put('/api/profile', body))
  const s = (k: keyof Personal) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value })
  return (
    <Card className="p-5">
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(f) }} className="space-y-5">
        {section === 'personal' ? (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Full name"><Input value={f.full_name} onChange={s('full_name')} /></Field>
              <Field label="First name"><Input value={f.first_name} onChange={s('first_name')} /></Field>
              <Field label="Last name"><Input value={f.last_name} onChange={s('last_name')} /></Field>
              <Field label="Email"><Input type="email" value={f.email} onChange={s('email')} /></Field>
              <Field label="Phone"><Input value={f.phone} onChange={s('phone')} /></Field>
              <Field label="Location"><Input value={f.location} onChange={s('location')} placeholder="City, Country" /></Field>
              <Field label="LinkedIn"><Input value={f.linkedin_url} onChange={s('linkedin_url')} /></Field>
              <Field label="GitHub"><Input value={f.github_url} onChange={s('github_url')} /></Field>
              <Field label="Portfolio"><Input value={f.portfolio_url} onChange={s('portfolio_url')} /></Field>
            </div>
            <div>
              <div className="mb-2 text-[12.5px] font-medium text-ink-2">Address <span className="font-normal text-muted">— used only to fill application forms that ask for it</span></div>
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Street address" className="sm:col-span-2"><Input value={f.street ?? ''} onChange={s('street')} autoComplete="address-line1" /></Field>
                <Field label="Address line 2"><Input value={f.address_line2 ?? ''} onChange={s('address_line2')} autoComplete="address-line2" /></Field>
                <Field label="City"><Input value={f.city ?? ''} onChange={s('city')} autoComplete="address-level2" /></Field>
                <Field label="State"><Input value={f.state ?? ''} onChange={s('state')} autoComplete="address-level1" /></Field>
                <Field label="PIN / postal code"><Input value={f.postal_code ?? ''} onChange={s('postal_code')} autoComplete="postal-code" /></Field>
                <Field label="Country"><Input value={f.country ?? ''} onChange={s('country')} autoComplete="country-name" /></Field>
              </div>
            </div>
            <Field label="Headline"><Input value={f.headline} onChange={s('headline')} placeholder="e.g. Software engineer - backend and full-stack" /></Field>
            <Field label="Summary" hint="Used when a resume or answer needs a summary. Tailoring can reword it, never add to it."><Textarea rows={4} value={f.summary} onChange={s('summary')} /></Field>
          </>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Work authorization" hint="Your own words, e.g. “Indian citizen, authorized to work in India”."><Input value={f.work_authorization} onChange={s('work_authorization')} /></Field>
            <Field label="Requires visa sponsorship?">
              <Select value={triState(f.requires_sponsorship)} onChange={(e) => setF({ ...f, requires_sponsorship: fromTri(e.target.value) })}>
                <option value="">Not provided - ask me</option><option value="no">No</option><option value="yes">Yes</option>
              </Select>
            </Field>
            <Field label="Willing to relocate?">
              <Select value={triState(f.willing_to_relocate)} onChange={(e) => setF({ ...f, willing_to_relocate: fromTri(e.target.value) })}>
                <option value="">Not provided - ask me</option><option value="yes">Yes</option><option value="no">No</option>
              </Select>
            </Field>
            <Field label="Years of professional experience" hint="Leave empty to be asked each time.">
              <Input type="number" step="0.5" min="0" value={f.years_of_experience ?? ''} onChange={(e) => setF({ ...f, years_of_experience: e.target.value === '' ? null : Number(e.target.value) })} />
            </Field>
            <Field label="Notice period"><Input value={f.notice_period} onChange={s('notice_period')} placeholder="e.g. Immediate / 30 days" /></Field>
            <Field label="Earliest start date"><Input value={f.earliest_start_date} onChange={s('earliest_start_date')} /></Field>
            <Field label="Salary expectation" hint="Only used where a form asks."><Input value={f.salary_expectation} onChange={s('salary_expectation')} /></Field>
            <Field label="Preferred locations" hint="Comma-separated"><Input value={f.preferred_locations.join(', ')} onChange={(e) => setF({ ...f, preferred_locations: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} /></Field>
            <Field label="Preferred job types" hint="e.g. Full-time, Internship"><Input value={f.preferred_job_types.join(', ')} onChange={(e) => setF({ ...f, preferred_job_types: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} /></Field>
          </div>
        )}
        <div className="flex justify-end"><Button variant="primary" type="submit" loading={save.isPending}>Save</Button></div>
      </form>
    </Card>
  )
}

function ExperienceList({ items }: { items: Exp[] }) {
  const [editing, setEditing] = useState<Partial<Exp> | null>(null)
  const remove = useSave((id: number) => del(`/api/profile/experiences/${id}`), 'Removed')
  return (
    <div className="space-y-3">
      <div className="flex justify-end"><Button icon={<Plus className="size-3.5" />} onClick={() => setEditing({ bullets: [] })}>Add experience</Button></div>
      {items.map((e) => (
        <Card key={e.id} className="p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[14px] font-semibold">{e.title || 'Role'} <span className="font-normal text-muted">· {e.organization}</span></div>
              <div className="text-[12.5px] text-muted">{[e.start_date, e.is_current ? 'Present' : e.end_date].filter(Boolean).join(' – ')}{e.location ? ` · ${e.location}` : ''}</div>
            </div>
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" icon={<Pencil className="size-3.5" />} onClick={() => setEditing(e)} aria-label="Edit" />
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => confirm('Remove this experience?') && remove.mutate(e.id)} aria-label="Delete" />
            </div>
          </div>
          <ul className="mt-2 space-y-1 text-[13px] text-ink-2">{e.bullets.map((b, i) => <li key={i} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-faint" />{b}</li>)}</ul>
        </Card>
      ))}
      {!items.length && <p className="text-[13px] text-muted">No experience yet. Internships count.</p>}
      {editing && <ExperienceDialog value={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

function ExperienceDialog({ value, onClose }: { value: Partial<Exp>; onClose: () => void }) {
  const [f, setF] = useState<Partial<Exp>>({ organization: '', title: '', location: '', employment_type: '', start_date: '', end_date: '', is_current: false, ...value, bullets: value.bullets ?? [] })
  const save = useSave((b: Partial<Exp>) => (value.id ? put(`/api/profile/experiences/${value.id}`, b) : post('/api/profile/experiences', b)))
  const s = (k: keyof Exp) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value })
  return (
    <Dialog open onClose={onClose} title={value.id ? 'Edit experience' : 'Add experience'} width="max-w-2xl"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.organization} onClick={() => save.mutate(f, { onSuccess: onClose })}>Save</Button></>}>
      <div className="grid gap-3.5 sm:grid-cols-2">
        <Field label="Organization"><Input value={f.organization} onChange={s('organization')} /></Field>
        <Field label="Title"><Input value={f.title} onChange={s('title')} /></Field>
        <Field label="Location"><Input value={f.location} onChange={s('location')} /></Field>
        <Field label="Type"><Input value={f.employment_type} onChange={s('employment_type')} placeholder="Internship, Full-time…" /></Field>
        <Field label="Start"><Input value={f.start_date} onChange={s('start_date')} placeholder="Jan 2026" /></Field>
        <Field label="End"><Input value={f.end_date} onChange={s('end_date')} disabled={f.is_current} placeholder="Mar 2026" /></Field>
      </div>
      <label className="mt-3 flex items-center gap-2 text-[13px]"><input type="checkbox" checked={f.is_current} onChange={(e) => setF({ ...f, is_current: e.target.checked })} className="accent-[var(--accent)]" />I currently work here</label>
      <Field label="Bullets (one per line)" className="mt-4" hint="Write what you actually did. Tailoring reorders and may reword, but never adds facts.">
        <Textarea rows={6} value={(f.bullets ?? []).join('\n')} onChange={(e) => setF({ ...f, bullets: e.target.value.split('\n') })} />
      </Field>
    </Dialog>
  )
}

function EducationList({ items }: { items: Edu[] }) {
  const [editing, setEditing] = useState<Partial<Edu> | null>(null)
  const remove = useSave((id: number) => del(`/api/profile/educations/${id}`), 'Removed')
  return (
    <div className="space-y-3">
      <div className="flex justify-end"><Button icon={<Plus className="size-3.5" />} onClick={() => setEditing({})}>Add education</Button></div>
      {items.map((e) => (
        <Card key={e.id} className="flex items-start justify-between gap-3 p-4">
          <div>
            <div className="text-[14px] font-semibold">{e.institution}</div>
            <div className="text-[13px] text-ink-2">{[e.degree, e.field_of_study].filter(Boolean).join(', ')}{e.grade ? ` · ${e.grade}` : ''}</div>
            <div className="text-[12.5px] text-muted">{[e.start_date, e.end_date].filter(Boolean).join(' – ')}{e.graduation_year ? ` · Class of ${e.graduation_year}` : ''}</div>
          </div>
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" icon={<Pencil className="size-3.5" />} onClick={() => setEditing(e)} aria-label="Edit" />
            <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => confirm('Remove?') && remove.mutate(e.id)} aria-label="Delete" />
          </div>
        </Card>
      ))}
      {editing && <EducationDialog value={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

function EducationDialog({ value, onClose }: { value: Partial<Edu>; onClose: () => void }) {
  const [f, setF] = useState<Partial<Edu>>({ institution: '', degree: '', field_of_study: '', start_date: '', end_date: '', graduation_year: null, grade: '', location: '', ...value })
  const save = useSave((b: Partial<Edu>) => (value.id ? put(`/api/profile/educations/${value.id}`, b) : post('/api/profile/educations', b)))
  const s = (k: keyof Edu) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value })
  return (
    <Dialog open onClose={onClose} title={value.id ? 'Edit education' : 'Add education'}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.institution} onClick={() => save.mutate(f, { onSuccess: onClose })}>Save</Button></>}>
      <div className="grid gap-3.5 sm:grid-cols-2">
        <Field label="Institution" className="sm:col-span-2"><Input value={f.institution} onChange={s('institution')} /></Field>
        <Field label="Degree"><Input value={f.degree} onChange={s('degree')} placeholder="B.Tech" /></Field>
        <Field label="Field of study"><Input value={f.field_of_study} onChange={s('field_of_study')} placeholder="Computer Science and Engineering" /></Field>
        <Field label="Start"><Input value={f.start_date} onChange={s('start_date')} /></Field>
        <Field label="End"><Input value={f.end_date} onChange={s('end_date')} /></Field>
        <Field label="Graduation year"><Input type="number" value={f.graduation_year ?? ''} onChange={(e) => setF({ ...f, graduation_year: e.target.value ? Number(e.target.value) : null })} /></Field>
        <Field label="Grade"><Input value={f.grade} onChange={s('grade')} placeholder="CGPA 8.5/10" /></Field>
      </div>
    </Dialog>
  )
}

function Skills({ items }: { items: Skill[] }) {
  const [name, setName] = useState('')
  const [cat, setCat] = useState('')
  const add = useSave((b: { name: string; category: string }) => post('/api/profile/skills', b), 'Skill added')
  const remove = useSave((id: number) => del(`/api/profile/skills/${id}`), 'Removed')
  const cats = [...new Set(items.map((s) => s.category || 'Other'))]
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate({ name, category: cat }, { onSuccess: () => setName('') }) }}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Skill, e.g. PostgreSQL" className="min-w-0 flex-1" />
          <Input value={cat} onChange={(e) => setCat(e.target.value)} placeholder="Category (e.g. Databases)" className="sm:w-56" list="skill-cats" />
          <datalist id="skill-cats">{cats.map((c) => <option key={c} value={c} />)}</datalist>
          <Button type="submit" variant="primary" loading={add.isPending}>Add</Button>
        </form>
        <p className="mt-2 text-[12px] text-muted">Skills you list are user-provided. Matching still distinguishes “listed” from “demonstrated in a project”.</p>
      </Card>
      {cats.map((c) => (
        <Card key={c}>
          <CardHeader title={c} />
          <div className="flex flex-wrap gap-1.5 p-4">
            {items.filter((s) => (s.category || 'Other') === c).map((s) => (
              <span key={s.id} className="group inline-flex items-center gap-1 rounded border border-line bg-surface-2 py-0.5 pl-2 pr-1 text-[12.5px]">
                {s.name}{!s.known && <span className="text-[10.5px] text-faint" title="Not in CareerPilot's skill list; matched by name only">·</span>}
                <button onClick={() => remove.mutate(s.id)} className="rounded p-0.5 text-faint hover:text-missing" aria-label={`Remove ${s.name}`}><Trash2 className="size-3" /></button>
              </span>
            ))}
          </div>
        </Card>
      ))}
    </div>
  )
}

function CertsAndAchievements({ certs, achievements }: { certs: Cert[]; achievements: { id: number; text: string }[] }) {
  const [cert, setCert] = useState({ name: '', issuer: '', date: '', url: '' })
  const [ach, setAch] = useState('')
  const addCert = useSave((b: typeof cert) => post('/api/profile/certifications', b), 'Certification added')
  const delCert = useSave((id: number) => del(`/api/profile/certifications/${id}`), 'Removed')
  const addAch = useSave((text: string) => post('/api/profile/achievements', { text }), 'Achievement added')
  const delAch = useSave((id: number) => del(`/api/profile/achievements/${id}`), 'Removed')
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card>
        <CardHeader title="Certifications" />
        <ul className="divide-y divide-line">
          {certs.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-2 px-4 py-2.5 text-[13px]">
              <span>{c.name}{c.issuer && <span className="text-muted"> · {c.issuer}</span>}{c.date && <span className="text-muted"> · {c.date}</span>}</span>
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => delCert.mutate(c.id)} aria-label="Delete" />
            </li>
          ))}
        </ul>
        <form className="grid gap-2 border-t border-line p-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); if (cert.name) addCert.mutate(cert, { onSuccess: () => setCert({ name: '', issuer: '', date: '', url: '' }) }) }}>
          <Input value={cert.name} onChange={(e) => setCert({ ...cert, name: e.target.value })} placeholder="Certification name" className="sm:col-span-2" />
          <Input value={cert.issuer} onChange={(e) => setCert({ ...cert, issuer: e.target.value })} placeholder="Issuer" />
          <Input value={cert.date} onChange={(e) => setCert({ ...cert, date: e.target.value })} placeholder="Date" />
          <div className="sm:col-span-2 flex justify-end"><Button type="submit" size="sm" disabled={!cert.name}>Add certification</Button></div>
        </form>
      </Card>
      <Card>
        <CardHeader title="Achievements" />
        <ul className="divide-y divide-line">
          {achievements.map((a) => (
            <li key={a.id} className="flex items-start justify-between gap-2 px-4 py-2.5 text-[13px]">
              <span>{a.text}</span>
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => delAch.mutate(a.id)} aria-label="Delete" />
            </li>
          ))}
        </ul>
        <form className="flex gap-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); if (ach.trim()) addAch.mutate(ach, { onSuccess: () => setAch('') }) }}>
          <Input value={ach} onChange={(e) => setAch(e.target.value)} placeholder="Publication, award, ranking…" />
          <Button type="submit" size="md" disabled={!ach.trim()}>Add</Button>
        </form>
      </Card>
    </div>
  )
}

const ANSWER_OPTIONS: { key: string; label: string; sensitive?: boolean }[] = [
  { key: 'how_heard', label: 'How did you hear about us?' },
  { key: 'referral', label: 'Referral / referred by' },
  { key: 'why_company', label: 'Default “why this company” note' },
  { key: 'additional_info', label: 'Additional information' },
  { key: 'country', label: 'Country of residence' },
  { key: 'gender', label: 'Gender (self-identification)', sensitive: true },
  { key: 'date_of_birth', label: 'Date of birth (YYYY-MM-DD)', sensitive: true },
  { key: 'race', label: 'Race / ethnicity (self-identification)', sensitive: true },
  { key: 'veteran', label: 'Veteran status (self-identification)', sensitive: true },
  { key: 'disability', label: 'Disability status (self-identification)', sensitive: true },
]

function Answers({ items }: { items: Answer[] }) {
  const [key, setKey] = useState('how_heard')
  const [answer, setAnswer] = useState('')
  const save = useSave((b: { question_key: string; label: string; answer: string }) => put('/api/profile/answers', b))
  const remove = useSave((id: number) => del(`/api/profile/answers/${id}`), 'Removed')
  const opt = ANSWER_OPTIONS.find((o) => o.key === key)!
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <p className="mb-3 text-[12.5px] text-muted">Answers to questions that come up on many forms. Self-identification answers are never submitted automatically: each application asks you to confirm them.</p>
        <form className="grid gap-2 sm:grid-cols-[260px_1fr_auto]" onSubmit={(e) => { e.preventDefault(); save.mutate({ question_key: key, label: opt.label, answer }, { onSuccess: () => setAnswer('') }) }}>
          <Select value={key} onChange={(e) => setKey(e.target.value)}>{ANSWER_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</Select>
          <Input value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder={opt.sensitive ? 'e.g. Prefer not to say' : 'Your answer'} />
          <Button type="submit" variant="primary" loading={save.isPending}>Save</Button>
        </form>
      </Card>
      <Card>
        <ul className="divide-y divide-line">
          {items.map((a) => (
            <li key={a.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-[13px]">
              <div className="min-w-0"><div className="text-muted">{a.label || a.question_key}{a.sensitive && <Badge tone="verify" className="ml-2">Confirm per application</Badge>}</div><div className="truncate">{a.answer}</div></div>
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => remove.mutate(a.id)} aria-label="Delete" />
            </li>
          ))}
          {!items.length && <li className="px-4 py-4 text-[13px] text-muted">No saved answers.</li>}
        </ul>
      </Card>
    </div>
  )
}
