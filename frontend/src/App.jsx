import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './hooks/useAuth'
import Shell from './components/Shell'
import LoginPage from './pages/Login'
import ConsolePage from './pages/Console'
import IntentsPage from './pages/Intents'
import ApprovalPage from './pages/Approval'
import IntentDetailPage from './pages/IntentDetail'
import AuditPage from './pages/Audit'
import ArchitecturePage from './pages/Architecture'
import ApiExplorerPage from './pages/ApiExplorer'
import EvaluationPage from './pages/Evaluation'

function Guard({ children }) {
  const { user } = useAuth()
  const loc = useLocation()
  if (user === undefined) return (
    <div className="h-screen bg-sidebar flex items-center justify-center text-slate-400 text-sm">
      Loading…
    </div>
  )
  if (!user) return <Navigate to="/login" state={{ from: loc }} replace />
  return <Shell>{children}</Shell>
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<Navigate to="/console" replace />} />
        <Route path="/console" element={<Guard><ConsolePage /></Guard>} />
        <Route path="/intents" element={<Guard><IntentsPage /></Guard>} />
        <Route path="/intents/:id" element={<Guard><IntentDetailPage /></Guard>} />
        <Route path="/approvals/:id" element={<Guard><ApprovalPage /></Guard>} />
        <Route path="/audit" element={<Guard><AuditPage /></Guard>} />
        <Route path="/audit/:id" element={<Guard><AuditPage /></Guard>} />
        <Route path="/architecture" element={<Guard><ArchitecturePage /></Guard>} />
        <Route path="/api-explorer" element={<Guard><ApiExplorerPage /></Guard>} />
        <Route path="/evaluation" element={<Guard><EvaluationPage /></Guard>} />
      </Routes>
    </AuthProvider>
  )
}
