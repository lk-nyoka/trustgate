import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { api } from '../api'
import Chip from '../components/Chip'

const EVENT_LABELS = {
  INTENT_RECEIVED:    ['Purchase intent received from agent', 'acc'],
  FACTS_RESOLVED:     ['Trusted facts resolved from registry', 'acc'],
  CONTEXT_SCANNED:    ['Context evidence scanned', 'acc'],
  DECISION:           ['Policy evaluated', 'acc'],
  HELD_FOR_APPROVAL:  ['Approval required — held for human review', 'warn'],
  APPROVED:           ['Authenticated user approved', 'ok'],
  DECLINED:           ['User declined', 'bad'],
  PAYMENT_CAPTURED:   ['PayPal order created and captured', 'ok'],
  PAYMENT_FAILED:     ['Payment adapter failed', 'bad'],
  PAYMENT_MISMATCH:   ['PayPal returned mismatched data', 'bad'],
  APPROVAL_EXPIRED:   ['Approval window expired', 'bad'],
  APPROVAL_REFUSED_POLICY_CHANGED: ['Approval refused — policy changed', 'bad'],
  APPROVAL_REFUSED_FACTS_CHANGED:  ['Approval refused — facts changed', 'bad'],
  POLICY_REVOKED:     ['Policy revoked by user', 'bad'],
}

const DOT_CLS = {
  ok:   'bg-ok',
  warn: 'bg-warn',
  bad:  'bg-bad',
  acc:  'bg-acc',
}

function evDesc(e) {
  const d = e.data || {}
  switch (e.event) {
    case 'INTENT_RECEIVED': {
      const uc = d.agent_claimed_untrusted || {}
      return `${uc.merchant_reference} · ${uc.product_reference}`
    }
    case 'FACTS_RESOLVED': return `${d.merchant} · ${d.amount} ${d.currency}`
    case 'CONTEXT_SCANNED': return Array.isArray(d) ? (d.length ? `${d.length} flag(s) detected` : 'No flags — page is clean') : ''
    case 'DECISION': {
      const reasons = d.reasons || []
      return `${d.decision}${reasons.length ? ' · ' + reasons[0].replace(/_/g,' ') : ''}`
    }
    case 'HELD_FOR_APPROVAL': return `Expires ${d.expires_at?.slice(0,19).replace('T',' ')}`
    case 'APPROVED': return `By ${d.by}`
    case 'DECLINED': return `By ${d.by}`
    case 'PAYMENT_CAPTURED': return `Order ${d.order_id} · Capture ${d.capture_id}`
    case 'PAYMENT_FAILED': return d.error || ''
    default: return ''
  }
}

const TABS = [
  { key: 'all',      label: 'All events' },
  { key: 'policy',   label: 'Policy decisions' },
  { key: 'payments', label: 'Payment events' },
]

const POLICY_EVENTS = new Set(['DECISION','HELD_FOR_APPROVAL','APPROVAL_REFUSED_POLICY_CHANGED','APPROVAL_REFUSED_FACTS_CHANGED'])
const PAYMENT_EVENTS = new Set(['PAYMENT_CAPTURED','PAYMENT_FAILED','PAYMENT_MISMATCH','APPROVED','DECLINED'])

export default function AuditPage() {
  const { id: selectedId } = useParams()
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('all')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!selectedId) { setLoading(false); return }
    api.audit(selectedId).then(d => { setData(d); setLoading(false) }).catch(() => setLoading(false))
  }, [selectedId])

  const events = data?.events || []
  const filtered = tab === 'policy' ? events.filter(e => POLICY_EVENTS.has(e.event))
    : tab === 'payments' ? events.filter(e => PAYMENT_EVENTS.has(e.event))
    : events

  const chainOk = data?.chain_valid
  const lastHash = events.length ? events[events.length-1].hash : null
  const hashSnip = lastHash ? `${lastHash.slice(0,8)}…${lastHash.slice(-4)}` : '—'

  if (!selectedId) return (
    <div>
      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
        <span className="w-5 h-0.5 bg-acc inline-block" /> Evidence / Append only
      </div>
      <h1 className="text-[36px] font-extrabold tracking-tight mb-2">Nothing disappears after the verdict.</h1>
      <p className="text-[14px] text-gray-500 mb-6">Select an intent from <Link to="/intents" className="text-acc">Purchase Intents</Link> to view its audit timeline.</p>
    </div>
  )

  return (
    <div>
      <div className="flex items-end justify-between mb-5">
        <div>
          <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
            <span className="w-5 h-0.5 bg-acc inline-block" /> Evidence / Append only
          </div>
          <h1 className="text-[32px] font-extrabold tracking-tight mb-1">Nothing disappears after the verdict.</h1>
          <p className="text-[13px] text-gray-400 font-mono">{selectedId}</p>
        </div>
        <div className={`border rounded-lg px-4 py-2.5 text-right ${chainOk ? 'bg-ok-light border-green-200' : 'bg-bad-light border-red-200'}`}>
          <div className={`text-[10px] font-bold uppercase tracking-widest mb-1 ${chainOk ? 'text-ok' : 'text-bad'}`}>
            {chainOk ? '✓ Chain intact' : '⚠ Chain invalid'}
          </div>
          <div className={`text-[11px] font-mono ${chainOk ? 'text-ok-dark' : 'text-bad-dark'}`}>hash: {hashSnip}</div>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        {/* Tabs */}
        <div className="flex items-center gap-6 px-4 py-3 border-b border-gray-100">
          {TABS.map(t => {
            const count = t.key === 'all' ? events.length
              : t.key === 'policy' ? events.filter(e => POLICY_EVENTS.has(e.event)).length
              : events.filter(e => PAYMENT_EVENTS.has(e.event)).length
            return (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={`text-[13px] font-semibold pb-1 transition-colors ${tab === t.key
                  ? 'text-acc border-b-2 border-acc'
                  : 'text-gray-500 hover:text-gray-700'}`}>
                {t.label} <span className="bg-gray-100 text-gray-500 text-[10px] px-1.5 py-0.5 rounded ml-1">{count}</span>
              </button>
            )
          })}
          <span className="ml-auto text-[11px] text-gray-400 font-mono">hash: {hashSnip}</span>
        </div>

        {/* Table */}
        {loading ? (
          <div className="p-8 text-gray-400 text-sm text-center">Loading…</div>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr className="bg-[#f8f9fb]">
                {['Event', 'Action / Source', 'State', 'Event ID'].map(h => (
                  <th key={h} className="text-left text-[10px] font-bold uppercase tracking-widest text-gray-400 px-4 py-2.5 border-b border-gray-100">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, i) => {
                const [label, hint] = EVENT_LABELS[e.event] || [e.event.replace(/_/g,' '), 'acc']
                const dot = DOT_CLS[hint] || 'bg-acc'
                const desc = evDesc(e)
                const actor =
                  e.event === 'INTENT_RECEIVED' ? 'shopping-agent / scoped key' :
                  e.event === 'FACTS_RESOLVED' ? 'registry / product + merchant' :
                  e.event === 'PAYMENT_CAPTURED' || e.event === 'PAYMENT_FAILED' ? 'trust-middleware+' :
                  'policy-engine / travel.v1'
                const stateChip =
                  PAYMENT_EVENTS.has(e.event) && e.event.includes('CAPTURED') ? 'CAPTURED' :
                  e.event === 'HELD_FOR_APPROVAL' ? 'HELD' :
                  PAYMENT_EVENTS.has(e.event) && !e.event.includes('CAPTURED') ? 'BLOCKED' :
                  'RECEIVED'

                return (
                  <motion.tr key={i}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.04 }}
                    className="border-b border-gray-50 last:border-0 hover:bg-gray-50/50 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-start gap-2">
                        <span className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${dot}`} />
                        <div>
                          <div className="font-semibold text-[13px]">{label}</div>
                          <div className="text-[11px] text-gray-400">{e.ts?.slice(11,19)} · append only</div>
                          {desc && <div className="text-[11px] text-gray-500 mt-0.5">{desc}</div>}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-[12px] text-gray-500">{actor}</td>
                    <td className="px-4 py-3"><Chip value={stateChip} /></td>
                    <td className="px-4 py-3 font-mono text-[10px] text-gray-400">evt_{String(e.seq).padStart(4,'0')}</td>
                  </motion.tr>
                )
              })}
              {!filtered.length && (
                <tr><td colSpan={4} className="px-4 py-8 text-gray-400 text-sm text-center">No events.</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
