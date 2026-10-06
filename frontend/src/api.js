/**
 * API client — thin wrapper around fetch().
 * All calls go to the FastAPI backend (proxied in dev via vite.config.js).
 * CSRF token is injected from the /api/me response and stored in module scope.
 */

let _csrf = ''
export function setCSRF(token) { _csrf = token }
export function getCSRF() { return _csrf }

async function req(method, path, body) {
  const headers = { 'Content-Type': 'application/json' }
  if (_csrf) headers['X-CSRF-Token'] = _csrf
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers,
    body: body !== undefined ? JSON.stringify({ ...body, csrf: _csrf }) : undefined,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw Object.assign(new Error(err.detail || 'Request failed'), { status: res.status, data: err })
  }
  return res.json()
}

export const api = {
  login: (username, password) => req('POST', '/api/login', { username, password }),
  logout: () => req('POST', '/api/logout', {}),
  me: () => fetch('/api/me', { credentials: 'include' }).then(r => r.ok ? r.json() : null),
  intents: () => req('GET', '/api/intents'),
  intent: id => req('GET', `/api/intents/${id}`),
  approve: id => req('POST', `/api/intents/${id}/approve`, {}),
  decline: id => req('POST', `/api/intents/${id}/decline`, {}),
  audit: id => req('GET', `/api/intents/${id}/audit`),
  revoke: () => req('POST', '/api/policy/revoke', {}),
}
