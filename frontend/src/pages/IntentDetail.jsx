import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { api } from '../api'
import Chip from '../components/Chip'
import FlowDiagram from '../components/FlowDiagram'

const REASON_LABELS = {
  UNKNOWN_MERCHANT: 'Merchant is not recognised in the registry',
  UNKNOWN_PRODUCT: 'Product is not recognised in the registry',
  MERCHANT_NOT_IN_ALLOWLIST: 'Merchant is not included in your active policy',
  CATEGORY_NOT_ALLOWED: 'Category is not permitted under this policy',
  CURRENCY_NOT_ALLOWED: 'Currency is not in the allowed list',
  EXCEEDS_MAX_SINGLE_PURCHASE: 'Amount exceeds the single-purchase limit',
  EXCEEDS_TOTAL_BUDGET: 'Amount would exceed the total spending budget',
  HIDDEN_PAYMENT_INSTRUCTION: 'Hidden payment instruction detected in page metadata',
  PAYMENT_INSTRUCTION_IN_PAGE: 'Unexpected payment instruction in page content',
}
const rLabel = r => REASON_LABELS[r] || r.replace(/_/g, ' ')

export default function IntentDetailPage() {
  const { id } = useParams()
  const [intent, setIntent] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.intent(id).then(v => { setIntent(v); setLoading(false) }).catch(() => setLoading(false))
  }, [id])

  if (loading) return <div className="text-gray-400 p-8 text-sm">Loading…</div>
  if (!intent) return <div className="p-8"><p className="text-bad mb-3">Not found.</p><Link to="/intents" className="text-acc text-sm">← Back</Link></div>

  const { state, facts: f, decision, reasons = [], flags = [], order_id, capture_id } = intent
  const isCaptured = state === 'CAPTURED'
  const isBlocked = state === 'BLOCKED'

  return (
    <div>
      <div className="flex gap-2 mb-5">
        <Link to="/intents" className="text-[13px] text-gray-400 border border-gray-200 px-3 py-1.5 rounded-lg hover:bg-gray-50">← Purchase intents</Link>
        <Link to={`/audit/${id}`} className="text-[13px] text-gray-400 border border-gray-200 px-3 py-1.5 rounded-lg hover:bg-gray-50">View audit timeline</Link>
      </div>

      <h1 className="text-[26px] font-extrabold tracking-tight mb-1">
        {isCaptured ? 'Transaction record' : isBlocked ? 'Payment blocked' : `Purchase — ${state}`}
      </h1>
      <p className="text-[13px] text-gray-400 font-mono mb-5">{id}</p>

      <div className="grid grid-cols-2 gap-4">
        {/* Main card */}
        <div>
          {isBlocked && (
            <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              className="bg-bad-light border border-red-200 rounded-xl p-5">
              <div className="text-[10px] font-bold uppercase tracking-widest text-bad mb-3">
                🚫 Payment blocked before PayPal was called
              </div>
              <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-2 text-[13px] mb-4">
                <dt className="text-gray-400 text-[12px]">Merchant</dt>
                <dd>{f?.merchant || 'Unknown merchant'}</dd>
                <dt className="text-gray-400 text-[12px]">Amount</dt>
                <dd className="font-bold">{f ? `$${f.amount} ${f.currency}` : '—'}</dd>
                <dt className="text-gray-400 text-[12px]">Product</dt>
                <dd>{f?.product || '—'}</dd>
              </dl>

              {/* Primary reason */}
              <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-1.5">Primary reason</div>
              <p className="font-bold text-bad-dark text-[14px] mb-3">{rLabel(reasons[0])}</p>

              {/* Additional */}
              {reasons.slice(1).length > 0 && (
                <>
                  <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-1.5">Additional restrictions</div>
                  <ul className="list-disc list-inside text-[13px] text-gray-600 mb-3 space-y-0.5">
                    {reasons.slice(1).map(r => <li key={r}>{rLabel(r)}</li>)}
                  </ul>
                </>
              )}

              {/* Context evidence */}
              {flags.length > 0 && (
                <>
                  <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-1.5">Context evidence</div>
                  {flags.map((fl, i) => (
                    <div key={i} className="bg-white/60 border border-red-200 rounded-lg p-3 mb-2">
                      <div className="font-bold text-bad-dark text-[12px] mb-0.5">{fl.flag?.replace(/_/g, ' ')}</div>
                      <div className="text-[11px] text-bad-dark opacity-80">
                        <code className="bg-red-100 px-1 rounded text-[10px]">{fl.source}</code> — {fl.excerpt?.slice(0, 160)}
                      </div>
                    </div>
                  ))}
                </>
              )}

              <div className="border-t border-red-200 pt-4 mt-4">
                <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-2 text-[13px]">
                  <dt className="text-gray-400 text-[12px]">PayPal order</dt>
                  <dd className="text-gray-400">Not created</dd>
                  <dt className="text-gray-400 text-[12px]">Capture</dt>
                  <dd className="text-gray-400">Not created</dd>
                  <dt className="text-gray-400 text-[12px]">Payment authority</dt>
                  <dd className="font-bold text-bad-dark">Contained</dd>
                  <dt className="text-gray-400 text-[12px]">Audit state</dt>
                  <dd><Chip value="BLOCKED" /></dd>
                </dl>
              </div>
            </motion.div>
          )}

          {isCaptured && (
            <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              className="bg-ok-light border border-green-200 rounded-xl p-5">
              <div className="text-[10px] font-bold uppercase tracking-widest text-ok mb-3">✓ Payment captured</div>
              <div className="text-[32px] font-extrabold tabular mb-1">${f?.amount} <span className="text-[20px] font-semibold text-gray-400">{f?.currency}</span></div>
              <div className="text-[13px] text-gray-500 mb-4">{f?.product}</div>
              <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-2 text-[13px]">
                {[
                  ['Merchant', f?.merchant],
                  ['Payee', f?.payee_id],
                  ['Policy decision', null],
                  ['Approval', null],
                  ['Payment state', null],
                  ['PayPal order', order_id],
                  ['Capture ID', capture_id],
                ].map(([k, v]) => (
                  <>
                    <dt key={`k-${k}`} className="text-gray-400 text-[12px]">{k}</dt>
                    <dd key={`v-${k}`}>
                      {k === 'Policy decision' ? <Chip value={decision} /> :
                       k === 'Approval' ? <Chip value={intent.approval_status} /> :
                       k === 'Payment state' ? <Chip value={state} /> :
                       <span className="font-mono text-[11px]">{v}</span>}
                    </dd>
                  </>
                ))}
              </dl>
            </motion.div>
          )}

          {!isCaptured && !isBlocked && (
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-2 text-[13px]">
                <dt className="text-gray-400 text-[12px]">State</dt><dd><Chip value={state} /></dd>
                <dt className="text-gray-400 text-[12px]">Decision</dt><dd><Chip value={decision} /></dd>
              </dl>
            </div>
          )}
        </div>

        {/* Flow + checks */}
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <FlowDiagram intent={intent} />
          {intent.checks?.length > 0 && (
            <>
              <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mt-4 mb-2">Policy evaluation</div>
              <ul className="space-y-1">
                {intent.checks.map(([label, status], i) => (
                  <li key={i} className="flex items-center gap-2 text-[13px] py-1 border-b border-gray-100 last:border-0">
                    <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold flex-shrink-0
                      ${status === 'pass' ? 'bg-ok-light text-ok' : status === 'fail' ? 'bg-bad-light text-bad' : 'bg-warn-light text-warn'}`}>
                      {status === 'pass' ? '✓' : status === 'fail' ? '✕' : '!'}
                    </span>
                    {label}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
