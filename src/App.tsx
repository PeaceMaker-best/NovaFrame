import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { AppShell } from './components/AppShell'

const OverviewPage = lazy(() => import('./pages/OverviewPage').then((module) => ({ default: module.OverviewPage })))
const StudioPage = lazy(() => import('./pages/StudioPage').then((module) => ({ default: module.StudioPage })))
const MatrixPage = lazy(() => import('./pages/MatrixPage').then((module) => ({ default: module.MatrixPage })))
const AssetsPage = lazy(() => import('./pages/AssetsPage').then((module) => ({ default: module.AssetsPage })))
const QueuePage = lazy(() => import('./pages/QueuePage').then((module) => ({ default: module.QueuePage })))
const ReviewPage = lazy(() => import('./pages/ReviewPage').then((module) => ({ default: module.ReviewPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))

function PageLoading() {
  return <div className="page-loading"><span /><strong>正在打开工作区</strong></div>
}

export default function App() {
  return (
    <AppShell>
      <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/studio" element={<StudioPage />} />
          <Route path="/matrix" element={<MatrixPage />} />
          <Route path="/assets" element={<AssetsPage />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </AppShell>
  )
}
