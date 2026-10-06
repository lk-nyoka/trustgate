import { NavLink } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

const NAV = [
  { to: '/console',      icon: '▦', label: 'Command center' },
  { to: '/intents',      icon: '→', label: 'Purchase intents' },
  { to: '/audit',        icon: '≡', label: 'Audit trail' },
  { to: '/architecture', icon: '◈', label: 'Architecture' },
  { to: '/api-explorer', icon: '</>', label: 'API Explorer' },
  { to: '/evaluation',   icon: '✓', label: 'Evaluation' },
]

export default function Sidebar() {
  const { stats, policy, paypalMode, logout } = useAuth()
  const total = (stats.captured || 0) + (stats.awaiting || 0) + (stats.blocked || 0)
  const pending = stats.awaiting || 0

  return (
    <aside className="w-[200px] flex-shrink-0 bg-sidebar border-r border-sidebar-border flex flex-col overflow-y-auto">
      {/* Brand */}
      <div className="px-4 py-5 border-b border-sidebar-border">
        <div className="flex items-center gap-2.5 mb-1">
          <div className="w-7 h-7 rounded-[7px] bg-acc flex items-center justify-center text-white font-bold text-sm flex-shrink-0">T</div>
          <span className="text-[13px] font-bold text-slate-200">TrustGate</span>
        </div>
        <div className="text-[10px] uppercase tracking-widest text-slate-500 pl-[38px]">Autonomous commerce</div>
      </div>

      {/* Nav */}
      <div className="px-2.5 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-slate-500">Control plane</div>
      <ul className="px-2 flex flex-col gap-0.5">
        {NAV.map(({ to, icon, label }) => (
          <li key={to}>
            <NavLink
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] font-medium transition-colors no-underline
                 ${isActive
                   ? 'bg-[#1e2640] text-slate-200'
                   : 'text-slate-500 hover:bg-[#1a1f2e] hover:text-slate-200'}`
              }
            >
              <span className="w-[18px] text-center text-sm flex-shrink-0">{icon}</span>
              {label}
              {label === 'Purchase intents' && total > 0 && (
                <span className="ml-auto bg-[#1e2a45] text-slate-200 text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[20px] text-center">
                  {total}
                </span>
              )}
              {label === 'Audit trail' && total > 0 && (
                <span className="ml-auto bg-[#1e2a45] text-slate-200 text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[20px] text-center">
                  {total}
                </span>
              )}
              {label === 'Command center' && pending > 0 && (
                <span className="ml-auto bg-[#78350f] text-amber-200 text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[20px] text-center">
                  {pending}
                </span>
              )}
            </NavLink>
          </li>
        ))}
      </ul>

      {/* Bottom */}
      <div className="mt-auto border-t border-sidebar-border p-2.5 space-y-2">
        {/* PayPal mode */}
        <div className="bg-[#1a1f2e] border border-sidebar-border rounded-lg px-3 py-2">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">Payment mode</div>
          <div className="flex items-center gap-1.5 text-[12px] font-semibold text-slate-200">
            <span className={`w-1.5 h-1.5 rounded-full ${paypalMode === 'sandbox' ? 'bg-ok animate-pulse' : 'bg-slate-500'}`} />
            {paypalMode === 'sandbox' ? 'PayPal Sandbox' : 'PayPal (fake)'}
          </div>
          <div className="text-[10px] text-slate-500 mt-0.5">
            {paypalMode === 'sandbox' ? 'Governed-only mode' : 'Offline / fake adapter'}
          </div>
        </div>

        {/* User */}
        <div className="flex items-center gap-2.5 px-2 py-1">
          <div className="w-7 h-7 rounded-full bg-acc flex items-center justify-center text-white font-bold text-xs flex-shrink-0">A</div>
          <div className="min-w-0">
            <div className="text-[12px] font-semibold text-slate-200 truncate">Alex Morgan</div>
            <div className="text-[10px] text-slate-500">Policy owner</div>
          </div>
        </div>

        <button
          onClick={logout}
          className="w-full text-left text-[11px] text-slate-500 hover:text-slate-300 px-2 py-1 transition-colors"
        >
          Sign out
        </button>
      </div>
    </aside>
  )
}
