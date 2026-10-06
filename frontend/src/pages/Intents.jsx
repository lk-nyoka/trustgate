import { useState, useEffect, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import Chip from '../components/Chip'

const STATE_DOT = {
  CAPTURED: 'bg-ok', HELD_FOR_APPROVAL: 'bg-warn', BLOCKED: 'bg-bad',
}
const STATE_SUB = {
  CAPTURED: 'governed payment path', HELD_FOR_APPROVAL: 'awaiting human approval',
  BLOCKED: 'context risk flagged',
}

export default function IntentsPage() {
  const [intents, setIntents] = useState([])
  const [selected, setSelected] = useState(null)
  const nav = useNavigate()

  const load = useCallback(async () => {
    const data = await api.intents()
    setIntents(data)
    if (!selected && data.length) setSelected(data[0])
  }, [selected])

  useEffect(() => { load() }, [])

  const sel = selected ? intents.find(v => v.intent_id === selected.intent_id) || selected : null

  return (
    <div>
      <div className="flex items-end justify-between mb-5">
        <div>
          <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
            <span className="w-5 h-0.5 bg-acc inline-block" />
            Request queue / {intents.length} intent{intents.length !== 1 ? 's' : ''}
          </div>
          <h1 className="text-[32px] font-extrabold tracking-tight mb-1">Every proposal gets a verdict.</h1>
          <p className="text-[14px] text-gray-500">Inspect the exact input, server-resolved facts, policy result, and payment state for each request.</p>
        </div>
        <button onClick={load} className="text-[12px] text-acc border border-acc-light px-3 py-1.5 rounded-lg hover:bg-acc-light transition-colors">
          ↺ Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Left: list */}
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">Live requests</span>
            <span className="flex items-center gap-1.5 text-[11px] text-gray-400">
              <span className="w-1.5 h-1.5 rounded-full bg-ok animate-pulse inline-block" /> streaming
            </span>
          </div>
          <div className="text-[13px] font-semibold px-4 py-2 text-gray-700">Purchase intents</div>
          {intents.map(v => {
            const f = v.facts
            const isSel = sel?.intent_id === v.intent_id
            const dot = STATE_DOT[v.state] || 'bg-gray-300'
            const sub = STATE_SUB[v.state] || v.state.toLowerCase().replace(/_/g, ' ')
            return (
              <button
                key={v.intent_id}
                onClick={() => setSelected(v)}
                className={`w-full text-left px-4 py-3.5 border-b border-gray-100 last:border-0 transition-colors
                  ${isSel ? 'bg-acc-light border-l-[3px] border-l-acc' : 'hover:bg-gray-50'}`}
              >
                <div className="flex justify-between items-center mb-1">
                  <span className="font-semibold text-[13px] truncate">
                    {f ? `${f.merchant} · ${f.product}` : v.intent_id}
                  </span>
                  <span className="font-bold text-[14px] tabular">{f ? `$${f.amount}` : '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-gray-400 flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full inline-block ${dot}`} />{sub}
                  </span>
                  <Chip value={v.state} />
                </div>
              </button>
            )
          })}
          {!intents.length && <div className="px-4 py-8 text-[13px] text-gray-400 text-center">No intents yet.</div>}
        </div>

        {/* Right: detail */}
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          {sel ? (
            <>
              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
                <span className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">Selected intent</span>
                <Chip value={sel.state} />
              </div>
              <div className="p-5">
                <div className="font-bold text-[16px] mb-4">
                  {sel.facts ? `${sel.facts.merchant} · ${sel.facts.product}` : sel.intent_id}
                </div>
                {sel.facts && (
                  <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-2 text-[13px] mb-4">
                    {[
                      ['Merchant', sel.facts.merchant],
                      ['Product reference', sel.facts.product],
                      ['Verified payee', sel.facts.payee_id],
                      ['Amount / currency', `${sel.facts.amount} ${sel.facts.currency}`],
                      ['Policy version', `travel.v${sel.policy_version}`],
                      ['Policy decision', sel.decision],
                      ...(sel.order_id ? [['PayPal order', sel.order_id], ['Capture ID', sel.capture_id]] : []),
                    ].map(([k, v]) => (
                      <>
                        <dt key={`k-${k}`} className="text-gray-400 text-[12px]">{k}</dt>
                        <dd key={`v-${k}`} className="font-mono text-[11px] break-all">{v}</dd>
                      </>
                    ))}
                  </dl>
                )}
                {sel.flags?.length > 0 && (
                  <div className="mb-4">
                    <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-2">Context evidence</div>
                    {sel.flags.map((fl, i) => (
                      <div key={i} className="bg-bad-light border border-red-200 rounded-lg p-3 mb-2">
                        <div className="font-bold text-bad-dark text-[12px] mb-0.5">{fl.flag?.replace(/_/g,' ')}</div>
                        <div className="text-[11px] text-bad-dark opacity-80">
                          <code className="bg-red-100 px-1 rounded">{fl.source}</code> — {fl.excerpt?.slice(0, 120)}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex gap-2 mt-2">
                  {sel.state === 'HELD_FOR_APPROVAL' ? (
                    <Link to={`/approvals/${sel.intent_id}`}
                      className="bg-acc text-white font-semibold px-4 py-2 rounded-lg text-[13px] hover:bg-acc-dark transition-colors">
                      Run this intent in console →
                    </Link>
                  ) : (
                    <Link to={`/intents/${sel.intent_id}`}
                      className="border border-gray-200 text-gray-700 font-medium px-4 py-2 rounded-lg text-[13px] hover:bg-gray-50 transition-colors">
                      View {sel.state === 'BLOCKED' ? 'security decision' : 'transaction'} →
                    </Link>
                  )}
                  <Link to={`/audit/${sel.intent_id}`}
                    className="border border-gray-200 text-gray-700 font-medium px-4 py-2 rounded-lg text-[13px] hover:bg-gray-50 transition-colors">
                    Audit →
                  </Link>
                </div>
              </div>
            </>
          ) : (
            <div className="p-8 text-[13px] text-gray-400">Select a request to inspect it.</div>
          )}
        </div>
      </div>
    </div>
  )
}
