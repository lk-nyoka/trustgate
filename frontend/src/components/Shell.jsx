import { useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'

const TITLES = {
  '/console':      'Live Console',
  '/intents':      'Purchase Intents',
  '/audit':        'Audit Trail',
  '/architecture': 'Architecture',
  '/api-explorer': 'API Explorer',
  '/evaluation':   'Evaluation',
}

export default function Shell({ children }) {
  const loc = useLocation()
  const title = TITLES[loc.pathname] || loc.pathname.split('/').filter(Boolean).pop()?.replace(/-/g, ' ') || 'TrustGate'

  return (
    <div className="flex h-screen overflow-hidden bg-[#f4f5f7]">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Topbar */}
        <div className="h-12 flex-shrink-0 bg-white border-b border-gray-200 flex items-center justify-between px-7">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest text-gray-400">
            <span>TrustGate</span>
            <span className="text-gray-200">/</span>
            <span className="text-gray-700">{title.toUpperCase()}</span>
          </div>
          <div className="flex items-center gap-4 text-[12px] text-gray-400">
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-ok animate-pulse-dot inline-block" />
              Governed demo
            </span>
            <span className="text-gray-200">·</span>
            <span>Every proposal is checked before PayPal</span>
          </div>
        </div>
        {/* Content */}
        <div className="flex-1 overflow-y-auto p-8">
          {children}
        </div>
      </div>
    </div>
  )
}
