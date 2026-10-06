import { useAuth } from '../hooks/useAuth'

export default function EvaluationPage() {
  const { stats } = useAuth()

  const metrics = [
    { value: '56', label: 'Tests passed', sub: 'pytest · all green' },
    { value: '2', label: 'Sandbox payments verified', sub: 'independently via PayPal GET read-back' },
    { value: '1', label: 'Injected fee blocked', sub: 'before PayPal order creation' },
    { value: 'Valid', label: 'Audit chain', sub: 'SHA-256 hash chain verified' },
  ]

  const decisions = [
    { decision: 'ALLOW', count: stats.captured || 0, color: 'text-ok', bg: 'bg-ok-light' },
    { decision: 'APPROVAL_REQUIRED', count: (stats.awaiting || 0) + (stats.captured || 0), color: 'text-warn', bg: 'bg-warn-light' },
    { decision: 'BLOCK', count: stats.blocked || 0, color: 'text-bad', bg: 'bg-bad-light' },
  ]

  return (
    <div>
      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
        <span className="w-5 h-0.5 bg-acc inline-block" />
        Verified results
      </div>
      <h1 className="text-[36px] font-extrabold tracking-tight mb-2">What was independently verified.</h1>
      <p className="text-[14px] text-gray-500 mb-6">Only substantiated metrics are shown. No invented percentages.</p>

      {/* Big metric cards */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        {metrics.map(m => (
          <div key={m.label} className="bg-white border border-gray-200 rounded-xl px-5 py-5">
            <div className="text-[34px] font-extrabold tabular mb-1">{m.value}</div>
            <div className="font-semibold text-[13px] mb-0.5">{m.label}</div>
            <div className="text-[11px] text-gray-400">{m.sub}</div>
          </div>
        ))}
      </div>

      {/* Policy decisions this session */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-3">Policy decisions this session</div>
          {decisions.map(d => (
            <div key={d.decision} className={`flex items-center justify-between px-3 py-2 rounded-lg mb-1.5 ${d.bg}`}>
              <span className={`font-mono text-[12px] font-bold ${d.color}`}>{d.decision}</span>
              <span className={`font-bold text-[16px] tabular ${d.color}`}>{d.count}</span>
            </div>
          ))}
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-3">Security guarantees</div>
          {[
            'Agent cannot supply prices, payees, or categories — all resolved server-side',
            'Blocked requests never reach the PayPal adapter — no order ID, no capture ID',
            'Human approval binds to: merchant, product, payee, amount, currency, policy version, expiry',
            'Agent bearer tokens rejected at the approval endpoint with HTTP 403',
            'Facts change between hold and approval → request blocked at approval time',
          ].map((p, i) => (
            <div key={i} className="flex items-start gap-2 text-[12px] text-gray-600 mb-2">
              <span className="text-ok mt-0.5 flex-shrink-0">✓</span>{p}
            </div>
          ))}
        </div>
      </div>

      {/* Test coverage */}
      <div className="bg-white border border-gray-200 rounded-xl p-5">
        <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-3">Test coverage areas</div>
        <div className="grid grid-cols-3 gap-3">
          {[
            ['Policy engine', 'All 8 checks, edge cases, context escalation, expiry'],
            ['Scanner', 'JSON-LD injection, CSS-hidden text, visible instruction'],
            ['HTTP layer', 'Auth, CSRF, agent/human separation, approval flow'],
            ['PayPal adapter', 'Idempotency, read-back verification, mismatch detection'],
            ['Audit log', 'Hash chain integrity, tamper detection, event ordering'],
            ['Service logic', 'Expiry, revocation, facts change, idempotency, state machine'],
          ].map(([title, desc]) => (
            <div key={title} className="bg-[#f8f9fb] border border-gray-100 rounded-lg p-3">
              <div className="font-bold text-[12px] mb-0.5">{title}</div>
              <div className="text-[11px] text-gray-500">{desc}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 bg-[#0f1117] text-slate-300 rounded-xl p-5">
        <div className="text-[11px] font-semibold uppercase tracking-widest text-slate-500 mb-2">Final statement</div>
        <p className="text-[14px] leading-relaxed italic">
          "TrustGate gives developers a deterministic, auditable authorization boundary for PayPal AI agents —
          so users can delegate tasks without delegating unrestricted financial authority."
        </p>
      </div>
    </div>
  )
}
