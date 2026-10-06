import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

export default function LoginPage() {
  const { login } = useAuth()
  const nav = useNavigate()
  const loc = useLocation()
  const [username, setUsername] = useState('demo')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async e => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
      nav(loc.state?.from?.pathname || '/console', { replace: true })
    } catch {
      setError('Wrong username or password.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="h-screen bg-sidebar flex items-center justify-center">
      <div className="bg-white border border-gray-200 rounded-2xl p-9 w-[360px] shadow-2xl">
        <div className="flex items-center gap-2.5 mb-7">
          <div className="w-8 h-8 rounded-lg bg-acc flex items-center justify-center text-white font-bold text-base flex-shrink-0">T</div>
          <div>
            <div className="font-bold text-[15px]">TrustGate</div>
            <div className="text-[11px] text-gray-400 uppercase tracking-widest">Autonomous commerce</div>
          </div>
        </div>
        <h2 className="text-[18px] font-bold mb-1">Sign in</h2>
        <p className="text-sm text-gray-400 mb-6">Approve or decline purchases your agent proposes.</p>
        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="text-[12px] font-semibold text-gray-500 mb-1 block">Username</span>
            <input
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-acc"
              value={username} onChange={e => setUsername(e.target.value)}
              autoComplete="username" autoFocus
            />
          </label>
          <label className="block">
            <span className="text-[12px] font-semibold text-gray-500 mb-1 block">Password</span>
            <input
              type="password"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-acc"
              value={password} onChange={e => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          {error && <p className="text-sm text-bad">{error}</p>}
          <button
            type="submit" disabled={loading}
            className="w-full bg-acc text-white font-semibold py-2.5 rounded-lg text-sm hover:bg-acc-dark transition-colors disabled:opacity-50"
          >
            {loading ? 'Signing in…' : 'Sign in →'}
          </button>
        </form>
        <p className="mt-4 text-[11px] text-gray-400 text-center">
          Demo credentials: <code className="bg-gray-100 px-1.5 py-0.5 rounded">demo</code> / printed in server console
        </p>
      </div>
    </div>
  )
}
