import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { api } from '../api'
import { useAuth } from '../hooks/useAuth'
import Chip from '../components/Chip'

function useTimer(seconds) {
  const [left, setLeft] = useState(seconds)
  useEffect(() => {
    setLeft(seconds)
    if (!seconds || seconds <= 0) return
    const iv = setInterval(() => setLeft(s => Math.max(0, s - 1)), 1000)
    return () => clearInterval(iv)
  }, [seconds])
  const m = Math.floor(left / 60), s = left % 60
  return left > 0 ? `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : 'Expired'
}

function PendingBanner({ intent, onRefresh }) {
  const f = intent.facts
  const timerStr = useTimer(intent.expires_seconds)
  const expired = timerStr === 'Expired'
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-warn-light border border-amber-200 rounded-xl p-4 mb-5 flex items-center justify-between gap-4 flex-wrap"
    >
      <div>
        <div className="text-[10px] font-bold uppercase tracking-widest text-warn mb-1.5">⚠ Action required</div>
        <div className="font-bold text-[16px] mb-0.5">{f?.product || '—'}</div>
        <div className="text-[12px] text-gray-500">{f?.merchant || '—'}</div>
      </div>
      <div className="text-right">
        <div className="text-[22px] font-extrabold tabular">${f?.amount} {f?.currency}</div>
        <div className="text-[12px] text-warn mt-0.5">
          Expires in <span className="font-bold font-mono">{timerStr}</span>
        </div>
      </div>
      {!expired && (
        <Link
          to={`/approvals/${intent.intent_id}`}
          className="bg-acc text-white font-semibold px-4 py-2.5 rounded-lg text-sm hover:bg-acc-dark transition-colors flex-shrink-0"
        >
          Review and approve →
        </Link>
      )}
    </motion.div>
  )
}

function StatCard({ label, value, sub, color }) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl px-5 py-4">
      <div className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-1.5">{label}</div>
      <div className={`text-2xl font-bold tabular ${color || ''}`}>{value}</div>
      <div className="text-[11px] text-gray-400 mt-1">{sub}</div>
    </div>
  )
}

const STORY = [
  { key: 'seed-180', label: 'Safe flight',     amt: '$180', color: 'text-ok',   sub: 'CPT → JNB economy · Demo Airlines · Auto-approved' },
  { key: 'seed-320', label: 'Flexible flight', amt: '$320', color: 'text-warn', sub: 'CPT → JNB flexible · Demo Airlines · Awaiting approval' },
  { key: 'seed-fee', label: 'Activation fee',  amt: '$3',   color: 'text-bad',  sub: 'Activation Services Demo · Not in travel policy' },
]

export default function ConsolePage() {
  const { stats, policy, refresh } = useAuth()
  const [intents, setIntents] = useState([])

  const load = useCallback(async () => {
    try { setIntents(await api.intents()) } catch {}
    refresh()
  }, [refresh])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const iv = setInterval(load, 10000)
    return () => clearInterval(iv)
  }, [load])

  const pending = intents.filter(v => v.state === 'HELD_FOR_APPROVAL' && v.approval_status === 'PENDING')
  const total = (stats.captured || 0) + (stats.awaiting || 0) + (stats.blocked || 0)

  // map story card key → real intent
  const storyIntents = {}
  STORY.forEach(s => {
    const match = intents.find(v => v.facts?.product?.toLowerCase().includes(
      s.key === 'seed-180' ? 'economy' : s.key === 'seed-320' ? 'flexible' : 'activation'
    ))
    if (match) storyIntents[s.key] = match
  })

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
          <span className="w-5 h-0.5 bg-acc inline-block" />
          Trust boundary / Live demo
        </div>
        <h1 className="text-[40px] font-extrabold leading-tight tracking-tight mb-2">
          The agent can propose.<br />
          <span className="text-acc">It cannot authorize.</span>
        </h1>
        <p className="text-[14px] text-gray-500 max-w-xl">
          TrustGate turns an AI recommendation into a governed purchase intent.
          Trusted facts, deterministic policy, and human authority decide what reaches PayPal.
        </p>
      </div>

      {/* Pending banner */}
      <AnimatePresence>
        {pending.map(p => <PendingBanner key={p.intent_id} intent={p} onRefresh={load} />)}
      </AnimatePresence>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        <StatCard label="Requests today" value={String(total).padStart(2,'0')} sub="all decisions visible" />
        <StatCard label="Payments captured" value={String(stats.captured||0).padStart(2,'0')} sub="via governed flow" color="text-ok" />
        <StatCard label="PayPal bypass attempts" value={String(stats.blocked||0).padStart(2,'0')} sub="blocked before order" color="text-bad" />
        <StatCard
          label="Active policy"
          value={policy ? `travel.v${policy.version}` : '—'}
          sub="updated this session"
        />
      </div>

      {/* Two-col */}
      <div className="grid grid-cols-2 gap-4">
        {/* Story cards */}
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-3">01 / Run the story</div>
          {STORY.map((s, i) => {
            const intent = storyIntents[s.key]
            const href = intent
              ? (intent.state === 'HELD_FOR_APPROVAL' ? `/approvals/${intent.intent_id}` : `/intents/${intent.intent_id}`)
              : '#'
            return (
              <Link
                key={s.key} to={href}
                className="block bg-white border border-gray-200 rounded-xl p-4 mb-2 hover:border-acc hover:shadow-sm transition-all no-underline"
              >
                <div className="flex justify-between items-center mb-1.5">
                  <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest">0{i+1}</span>
                  <span className={`w-2 h-2 rounded-full inline-block ${s.color === 'text-ok' ? 'bg-ok' : s.color === 'text-warn' ? 'bg-warn' : 'bg-bad'}`} />
                </div>
                <div className="font-bold text-[14px] mb-1">{s.label}</div>
                <div className="flex justify-between items-center">
                  <span className="text-[12px] text-gray-400">{s.sub}</span>
                  <span className="font-bold text-[15px] tabular">{s.amt}</span>
                </div>
                {intent && (
                  <div className="flex gap-1.5 mt-2">
                    <Chip value={intent.decision} />
                    <Chip value={intent.state} />
                  </div>
                )}
              </Link>
            )
          })}
        </div>

        {/* Policy panel */}
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-3">02 / Authority</div>
          {policy && (
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <div className="flex items-center justify-between mb-3">
                <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">Active policy</div>
                <span className="bg-acc-light text-acc-dark text-[10px] font-bold px-2 py-0.5 rounded">v{policy.version}.1</span>
              </div>
              <div className="bg-ok-light border border-green-200 rounded-lg px-3 py-2.5 mb-4">
                <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-widest text-ok-dark mb-0.5">
                  <span>✓</span> Bounded delegation
                </div>
                <div className="text-[11px] text-ok-dark opacity-80">Policy rules the authority. AI only supplies the suggestion.</div>
              </div>
              <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 text-[12px]">
                {[
                  ['Policy ID', policy.policy_id],
                  ['Merchant allowlist', policy.merchant_allowlist?.join(', ')],
                  ['Category', policy.category_allowlist?.join(', ')],
                  ['Auto-approve ≤', `$${policy.auto_approve_up_to}`],
                  ['Max single', `$${policy.max_single_purchase}`],
                  ['Total budget', `$${policy.max_total_spend}`],
                ].map(([k, v]) => (
                  <>
                    <dt key={`k-${k}`} className="text-gray-400">{k}</dt>
                    <dd key={`v-${k}`} className="font-mono text-[11px]">{v}</dd>
                  </>
                ))}
              </dl>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
