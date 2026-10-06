import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { api } from '../api'
import Chip from '../components/Chip'
import FlowDiagram from '../components/FlowDiagram'

const BINDING_LABELS = [
  'Merchant verified',
  'Product verified',
  'Configured payee binding verified',
  'Amount verified',
  'Currency verified',
  'Policy version verified',
  'Approval window valid',
]

function useTimer(seconds) {
  const [left, setLeft] = useState(seconds ?? 0)
  useEffect(() => {
    setLeft(seconds ?? 0)
    if (!seconds || seconds <= 0) return
    const iv = setInterval(() => setLeft(s => Math.max(0, s - 1)), 1000)
    return () => clearInterval(iv)
  }, [seconds])
  const m = Math.floor(left / 60), s = left % 60
  return left > 0 ? `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : 'Expired'
}

export default function ApprovalPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const [intent, setIntent] = useState(null)
  const [loading, setLoading] = useState(true)
  const [approving, setApproving] = useState(false)
  const [bindingStep, setBindingStep] = useState(-1) // -1 = idle, 0-6 = animating
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const timerStr = useTimer(intent?.expires_seconds)

  const load = useCallback(async () => {
    try {
      const v = await api.intent(id)
      setIntent(v)
      if (v.state === 'CAPTURED') setDone(true)
    } catch { setError('Intent not found.') }
    finally { setLoading(false) }
  }, [id])

  useEffect(() => { load() }, [load])

  const handleApprove = async () => {
    setApproving(true)
    setBindingStep(0)
    // Animate binding checks
    for (let i = 0; i < BINDING_LABELS.length; i++) {
      await new Promise(r => setTimeout(r, 280))
      setBindingStep(i)
    }
    await new Promise(r => setTimeout(r, 300))
    try {
      const v = await api.approve(id)
      setIntent(v)
      setDone(true)
    } catch (e) {
      setError(e.data?.detail || 'Approval failed.')
      setApproving(false)
      setBindingStep(-1)
    }
  }

  const handleDecline = async () => {
    try {
      await api.decline(id)
      nav('/intents')
    } catch (e) { setError(e.data?.detail || 'Decline failed.') }
  }

  if (loading) return <div className="text-gray-400 text-sm p-8">Loading…</div>
  if (!intent || error) return (
    <div className="p-8">
      <p className="text-bad mb-4">{error || 'Not found.'}</p>
      <Link to="/intents" className="text-acc text-sm">← Back to intents</Link>
    </div>
  )

  const f = intent.facts
  const held = intent.state === 'HELD_FOR_APPROVAL'
  const captured = intent.state === 'CAPTURED'

  return (
    <div>
      <div className="flex items-center gap-2 mb-5">
        <Link to="/intents" className="text-[13px] text-gray-400 hover:text-gray-700 border border-gray-200 px-3 py-1.5 rounded-lg">
          ← Purchase intents
        </Link>
      </div>

      <h1 className="text-[26px] font-extrabold tracking-tight mb-1">
        {held ? 'Purchase awaiting approval' : captured ? 'Approved and captured' : 'Purchase'}
      </h1>
      <p className="text-[13px] text-gray-400 font-mono mb-5">{id}</p>

      <div className="grid grid-cols-2 gap-4">
        {/* Left: facts + actions */}
        <div className="space-y-3">
          <div className={`border rounded-xl p-5 ${held ? 'bg-warn-light border-amber-200' : captured ? 'bg-ok-light border-green-200' : 'bg-white border-gray-200'}`}>
            {held && <div className="text-[10px] font-bold uppercase tracking-widest text-warn mb-3">⚠ Awaiting your decision</div>}
            {captured && <div className="text-[10px] font-bold uppercase tracking-widest text-ok mb-3">✓ Payment captured</div>}

            <div className="text-[32px] font-extrabold tabular mb-1">${f?.amount} <span className="text-[20px] font-semibold text-gray-400">{f?.currency}</span></div>
            <div className="text-[13px] text-gray-500 mb-4">{f?.product}</div>

            <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-2 text-[13px]">
              {[
                ['Merchant', f?.merchant],
                ['Product', f?.product],
                ['Payee', f?.payee_id],
                ['Policy', `Travel delegation · v${intent.policy_version}`],
                ['Reason', intent.reasons?.map(r => r.replace(/_/g, ' ')).join('; ') || 'Above $250 threshold'],
                ...(held ? [['Expires in', timerStr]] : []),
              ].map(([k, v]) => (
                <>
                  <dt key={`k-${k}`} className="text-gray-400 text-[12px]">{k}</dt>
                  <dd key={`v-${k}`} className={`font-mono text-[11px] ${k === 'Expires in' ? 'text-warn font-bold' : ''}`}>{v}</dd>
                </>
              ))}
            </dl>

            {/* Binding animation */}
            <AnimatePresence>
              {approving && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  className="mt-4 border-t border-amber-200 pt-4"
                >
                  <div className="text-[12px] font-semibold text-gray-600 mb-2">
                    {done ? 'Approval complete' : 'Binding approval…'}
                  </div>
                  <ul className="space-y-1">
                    {BINDING_LABELS.map((label, i) => (
                      <motion.li
                        key={label}
                        initial={{ opacity: 0, x: -8 }}
                        animate={bindingStep >= i ? { opacity: 1, x: 0 } : {}}
                        className="flex items-center gap-2 text-[13px]"
                      >
                        <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0
                          ${bindingStep >= i ? 'bg-ok text-white' : 'bg-gray-200 text-gray-400'}`}>
                          {bindingStep >= i ? '✓' : ''}
                        </span>
                        {label}
                      </motion.li>
                    ))}
                  </ul>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Buttons */}
            {held && !approving && (
              <div className="flex gap-2 mt-5 pt-4 border-t border-amber-200">
                <button
                  onClick={handleDecline}
                  className="border border-bad text-bad-dark bg-bad-light hover:bg-red-100 font-semibold px-4 py-2 rounded-lg text-[13px] transition-colors"
                >
                  Decline
                </button>
                <button
                  onClick={handleApprove}
                  className="bg-acc text-white font-semibold px-5 py-2 rounded-lg text-[13px] hover:bg-acc-dark transition-colors"
                >
                  Approve ${f?.amount} {f?.currency} →
                </button>
              </div>
            )}

            {error && <p className="text-bad text-sm mt-3">{error}</p>}
          </div>

          {/* Receipt */}
          {captured && intent.order_id && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="bg-ok-light border border-green-200 rounded-xl p-5"
            >
              <div className="text-[11px] font-semibold uppercase tracking-widest text-ok-dark mb-3">Receipt</div>
              <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-2 text-[13px]">
                {[
                  ['Policy decision', null],
                  ['Approval', null],
                  ['Payment state', null],
                  ['PayPal order', intent.order_id],
                  ['Capture ID', intent.capture_id],
                ].map(([k, v]) => (
                  <>
                    <dt key={`k-${k}`} className="text-gray-400 text-[12px]">{k}</dt>
                    <dd key={`v-${k}`}>
                      {k === 'Policy decision' ? <Chip value={intent.decision} /> :
                       k === 'Approval' ? <Chip value={intent.approval_status} /> :
                       k === 'Payment state' ? <Chip value={intent.state} /> :
                       <code className="text-[11px]">{v}</code>}
                    </dd>
                  </>
                ))}
              </dl>
            </motion.div>
          )}
        </div>

        {/* Right: flow + checks */}
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <FlowDiagram intent={intent} />
          {intent.checks?.length > 0 && (
            <>
              <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mt-4 mb-2">Policy evaluation</div>
              <ul className="space-y-1">
                {intent.checks.map(([label, status], i) => (
                  <li key={i} className={`flex items-center gap-2 text-[13px] py-1 border-b border-gray-100 last:border-0`} style={{ animationDelay: `${i * 0.15}s` }}>
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
          {intent.binding_checks?.length > 0 && (
            <>
              <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mt-4 mb-2">Approval bound to</div>
              <ul className="space-y-1">
                {intent.binding_checks.map((label, i) => (
                  <li key={i} className="flex items-center gap-2 text-[13px] py-1 border-b border-gray-100 last:border-0">
                    <span className="w-4 h-4 rounded-full bg-ok-light text-ok flex items-center justify-center text-[9px] font-bold flex-shrink-0">✓</span>
                    {label}
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="mt-4 text-[12px]">
            <Link to={`/audit/${id}`} className="text-acc hover:underline">View audit timeline →</Link>
          </p>
        </div>
      </div>
    </div>
  )
}
