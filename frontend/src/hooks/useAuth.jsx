import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { api, setCSRF } from '../api'

const AuthCtx = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined)
  const [stats, setStats] = useState({})
  const [policy, setPolicy] = useState(null)
  const [paypalMode, setPaypalMode] = useState('fake')

  const refresh = useCallback(async () => {
    const data = await api.me()
    if (data?.authenticated) {
      setCSRF(data.csrf)
      setUser(data.user_id)
      setStats(data.stats || {})
      setPolicy(data.policy || null)
      setPaypalMode(data.paypal_mode || 'fake')
    } else {
      setUser(null)
    }
    return data
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const login = async (username, password) => {
    const data = await api.login(username, password)
    setCSRF(data.csrf)
    await refresh()
  }

  const logout = async () => {
    await api.logout()
    setUser(null)
  }

  return (
    <AuthCtx.Provider value={{ user, stats, policy, paypalMode, refresh, login, logout }}>
      {children}
    </AuthCtx.Provider>
  )
}

export const useAuth = () => useContext(AuthCtx)