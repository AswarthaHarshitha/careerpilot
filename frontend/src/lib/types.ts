export type User = { id: number; email: string; display_name: string; csrf_token: string }

export type TaskStep = { label: string; state: 'pending' | 'running' | 'done' | 'failed' | 'skipped'; detail?: string }
export type Task = {
  id: number
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  steps: TaskStep[]
  result: Record<string, unknown> & { error?: { code: string; message: string; hint: string; actions: string[] } }
  error: string
}

export type MatchStatus = 'strong' | 'partial' | 'missing' | 'needs_verification'
export type Importance = 'required' | 'preferred' | 'keyword'

export type EvidenceRef = { type: string; id: number; label: string; status: string; detail?: string; via?: string }

export type Requirement = {
  id: number
  kind: 'skill' | 'experience' | 'education' | 'certification' | 'domain' | 'other'
  label: string
  canonical: string
  category: string
  importance: Importance
  snippet: string
  match: { status: MatchStatus; rationale: string; evidence: EvidenceRef[] } | null
}

export type MatchCounts = Partial<Record<MatchStatus, number>> & { required_total?: number; required_strong?: number }

export type AppStatus =
  | 'saved' | 'analyzing' | 'resume_ready' | 'ready_to_apply' | 'in_progress' | 'submitted' | 'assessment'
  | 'interview' | 'rejected' | 'withdrawn' | 'offer'

export type JobSummary = {
  id: number
  company: string
  role_title: string
  job_code: string
  location: string
  workplace_type: string
  employment_type: string
  deadline: string | null
  deadline_source: string
  days_remaining: number | null
  source_platform: string
  analysis_state: 'pending' | 'running' | 'ready' | 'failed'
  analysis_error: string
  created_at: string
  job_url: string
  application_url: string
  archived: boolean
  application: { id: number; status: AppStatus } | null
  match_counts: MatchCounts
}

export type RecommendedProject = {
  project_id: number
  title: string
  score: number
  rank: number
  matched: { requirement_id: number; label: string; importance: Importance; evidence_status: string; via?: string }[]
  selected: boolean
  repo_url: string
  has_bullets: boolean
}

export type JobDetail = JobSummary & {
  description_text: string
  responsibilities: string[]
  qualifications: string[]
  keywords: string[]
  experience_text: string
  min_years_experience: number | null
  education_text: string
  posted_date: string | null
  analysis_notes: string[]
  analyzed_with_llm: boolean
  company_careers_url: string
  ingest_method: string
  requirements: Requirement[]
  recommended_projects: RecommendedProject[]
  resumes: { id: number; label: string; version_number: number; status: string; created_at: string; page_count: number | null }[]
  suggested_projects: { id: number; title: string; status: string }[]
}

export type Bullet = { text: string; original: string; origin: string; rejected?: string[] }
export type ResumeEntry = {
  source_index: number | null
  fields: Record<string, string>
  ref?: { type: string; id: number } | null
  matched?: string[]
  bullets: Bullet[] | null
}
export type ResumeSection = {
  index: number
  title: string
  kind: string
  layout: 'paragraph' | 'entries' | 'skills' | 'items' | 'raw'
  unchanged?: boolean
  text?: string
  original_text?: string
  entries?: ResumeEntry[]
  categories?: { name: string; items: string[]; original?: string[] }[]
  items?: string[]
}
export type Finding = { severity: 'high' | 'medium' | 'low' | 'info'; title: string; detail: string }
export type AtsReport = {
  summary: { jd_coverage: string; keyword_alignment: string; formatting_risk: string; pages: number | null }
  coverage: Record<string, { covered: number; total: number; level: string }>
  missing_with_evidence: { label: string; importance: string; match: string }[]
  missing_without_evidence: { label: string; importance: string; match: string }[]
  terminology: { resume: string; posting: string }[]
  parsing_risks: string[]
  findings: Finding[]
}
export type RecruiterReport = {
  summary: {
    evidence_quality: string
    relevance: string
    bullet_quality: string
    technical_depth: string
    potential_gaps: number
    resume_length: string
  }
  gaps: string[]
  rejected_rewrites: number
  findings: Finding[]
}
export type ResumeVersion = {
  id: number
  label: string
  job_id: number | null
  template_id: number | null
  version_number: number
  is_base: boolean
  status: 'draft' | 'approved'
  page_count: number | null
  has_pdf: boolean
  has_source: boolean
  source_format: 'tex' | 'docx' | null
  render_error: string
  used_llm: boolean
  created_at: string
  approved_at: string | null
  parent_id: number | null
  summary: { ats: Partial<AtsReport['summary']>; recruiter: Partial<RecruiterReport['summary']> }
  job?: { id: number; company: string; role_title: string }
  content?: { sections: ResumeSection[]; focus: string[]; used_llm: boolean }
  changes?: { section: string; type: string; detail: string }[]
  ats_report?: AtsReport
  recruiter_report?: RecruiterReport
}

export type Template = {
  id: number
  name: string
  format: 'latex' | 'docx'
  original_filename: string
  created_at: string
  page_count: number | null
  render_error: string
  has_preview: boolean
  is_default: boolean
  sections: { index: number; title: string; kind: string; layout: string; count: number }[]
  header: { name?: string; email?: string; phone?: string; links?: { url: string; text: string; kind: string }[] }
  structure?: {
    index: number
    title: string
    kind: string
    layout: string
    entries?: { fields: Record<string, string>; bullets: string[] }[]
    skills?: { name: string; items: string[] }[]
    items?: string[]
    text?: string
  }[]
}

export type Evidence = {
  id: number
  kind: string
  label: string
  canonical: string
  category: string
  status: 'verified' | 'user_provided' | 'inferred' | 'unsupported'
  source_type: string
  source_detail: string
  snippet: string
  user_reviewed: boolean
}
export type Project = {
  id: number
  name: string
  title: string
  display_name: string
  origin: 'github' | 'manual'
  description: string
  repo_full_name: string
  repo_url: string
  homepage_url: string
  is_private: boolean
  is_fork: boolean
  archived: boolean
  primary_language: string
  stars: number
  hidden: boolean
  pushed_at: string | null
  last_scanned_at: string | null
  scan_error: string
  technologies: string[]
  evidence_counts: Record<string, number>
  bullet_count: number
  topics: string[]
  evidence?: Evidence[]
  bullets?: { id: number; text: string; source: string }[]
  languages?: { name: string; share: number }[]
  readme_excerpt?: string
  file_count?: number | null
}

export type Suggestion = {
  id: number
  title: string
  job_id: number | null
  gap_skills: string[]
  gap_keys: string[]
  existing_skills: string[]
  status: 'not_built' | 'in_progress' | 'verified'
  plan: {
    title: string
    summary: string
    demonstrates: string[]
    builds_on: string[]
    for_role: string
    stack: string[]
    architecture: string[]
    features: string[]
    phases: { name: string; tasks: string[] }[]
    tests: string[]
    deployment: string[]
    repo_structure: string
    readme_outline: string[]
    github_setup: string[]
    coding_prompt: string
  }
  linked_project_id: number | null
  verification_notes: string
  created_at: string
}

export type AppField = {
  id: number
  label: string
  input_type: string
  required: boolean
  options: string[]
  field_key: string
  sensitive: boolean
  value: string
  value_source: string
  status: 'filled' | 'needs_input' | 'needs_confirmation' | 'confirmed' | 'skipped' | 'unsupported'
  note: string
  fill_result: string
  validation_message: string
  step: number
}
export type AutomationSession = {
  id: number
  state: string
  message: string
  current_url: string
  step: number
  platform: string
  has_screenshot: boolean
  log: { at: string; message: string }[]
  answers_revision: number
  filled_revision: number
  submit_confirmed_at: string | null
  result_text: string
  resume_version_id: number | null
  last_heartbeat_at: string | null
  created_at: string
}
export type Application = {
  id: number
  status: AppStatus
  status_label: string
  job_id: number
  company: string
  role_title: string
  job_code: string
  location: string
  job_url: string
  application_url: string
  deadline: string | null
  days_remaining: number | null
  discovered_at: string | null
  date_applied: string | null
  follow_up_date: string | null
  submitted_at: string | null
  submission_verified: boolean
  notes: string
  resume_version: { id: number; label: string; version_number: number } | null
  updated_at: string
  events?: { id: number; kind: string; message: string; data: Record<string, unknown>; created_at: string }[]
  answers?: { question: string; answer: string; source: string; confirmed: boolean }[]
  fields?: AppField[]
  session?: AutomationSession | null
  confirmation_text?: string
}
