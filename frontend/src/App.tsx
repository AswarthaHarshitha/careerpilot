import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { AppShell } from './components/AppShell'
import { Spinner } from './components/ui'
import { get } from './lib/api'
import { useAuth } from './lib/auth'
import type { SettingsData } from './lib/types'
import Login from './pages/Login'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const Jobs = lazy(() => import('./pages/Jobs'))
const NewJob = lazy(() => import('./pages/NewJob'))
const JobDetail = lazy(() => import('./pages/JobDetail'))
const JobPrep = lazy(() => import('./pages/JobPrep'))
const Applications = lazy(() => import('./pages/Applications'))
const ApplicationDetail = lazy(() => import('./pages/ApplicationDetail'))
const Resumes = lazy(() => import('./pages/Resumes'))
const ResumeWorkspace = lazy(() => import('./pages/ResumeWorkspace'))
const Templates = lazy(() => import('./pages/Templates'))
const Projects = lazy(() => import('./pages/Projects'))
const ProjectDetail = lazy(() => import('./pages/ProjectDetail'))
const SuggestionDetail = lazy(() => import('./pages/SuggestionDetail'))
const Profile = lazy(() => import('./pages/Profile'))
const Analytics = lazy(() => import('./pages/Analytics'))
const SettingsPage = lazy(() => import('./pages/Settings'))
const NotFound = lazy(() => import('./pages/NotFound'))
const Setup = lazy(() => import('./pages/Setup'))

const fallback = (
  <div className="flex h-full items-center justify-center"><Spinner /></div>
)
const wrap = (el: React.ReactNode) => <Suspense fallback={fallback}>{el}</Suspense>

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: wrap(<Dashboard />) },
      { path: 'jobs', element: wrap(<Jobs />) },
      { path: 'jobs/new', element: wrap(<NewJob />) },
      { path: 'jobs/:id', element: wrap(<JobDetail />) },
      { path: 'jobs/:id/prep', element: wrap(<JobPrep />) },
      { path: 'applications', element: wrap(<Applications />) },
      { path: 'applications/:id', element: wrap(<ApplicationDetail />) },
      { path: 'resumes', element: wrap(<Resumes />) },
      { path: 'resumes/templates', element: wrap(<Templates />) },
      { path: 'resumes/:id', element: wrap(<ResumeWorkspace />) },
      { path: 'projects', element: wrap(<Projects />) },
      { path: 'projects/:id', element: wrap(<ProjectDetail />) },
      { path: 'suggestions/:id', element: wrap(<SuggestionDetail />) },
      { path: 'profile', element: wrap(<Profile />) },
      { path: 'analytics', element: wrap(<Analytics />) },
      { path: 'settings', element: wrap(<SettingsPage />) },
      { path: '*', element: wrap(<NotFound />) },
    ],
  },
])

export default function App() {
  const { user, loading } = useAuth()
  if (loading) return fallback
  if (!user) return <Login />
  return <Workspace />
}

function Workspace() {
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => get<SettingsData>('/api/settings') })
  if (settings.isLoading) return fallback
  if (settings.data && !settings.data.onboarded) return wrap(<Setup settings={settings.data} />)
  return <RouterProvider router={router} />
}
