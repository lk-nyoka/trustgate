import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

const NODES = [
  {
    id: 'agent', label: 'AI Agent', color: 'border-acc text-acc-dark bg-acc-light',
    dot: 'bg-acc',
    does: 'Discovers products, plans purchases, and submits purchase intents via the API.',
    trusts: 'Nothing. It receives a narrow propose_purchase() tool only.',
    never: 'Cannot supply prices, payees, categories, user IDs, or approvals. Cannot call PayPal directly.',
    records: 'The agent_claimed_untrusted payload is logged at INTENT_RECEIVED.',
  },
  {
    id: 'api', label: 'Purchase Intent API', color: 'border-acc text-acc-dark bg-acc-light',
    dot: 'bg-acc',
    does: 'Receives merchant and product references from the agent. Validates structure. Rejects extra fields.',
    trusts: 'The agent API key (scoped to one user + one policy). Nothing else from the request.',
    never: 'Cannot accept amount, payee, user_id, or approver from the agent. Extra fields → HTTP 422.',
    records: 'Agent key, request ID, merchant_reference, product_reference at intake.',
  },
  {
    id: 'facts', label: 'Trusted Fact Resolution', color: 'border-acc text-acc-dark bg-acc-light',
    dot: 'bg-acc',
    does: 'Replaces agent references with server-verified facts: merchant name, payee, category, price, currency.',
    trusts: 'Only the server-side merchant and product registry.',
    never: 'Never trusts agent-supplied prices, payees, or categories.',
    records: 'Full TrustedFacts set at FACTS_RESOLVED.',
  },
  {
    id: 'policy', label: 'Deterministic Policy Engine', color: 'border-acc text-acc-dark bg-acc-light',
    dot: 'bg-acc',
    does: 'Evaluates 8 checks: merchant allowlist, category allowlist, currency, single-purchase limit, budget cap, auto-approve threshold, context flags.',
    trusts: 'Trusted facts only. Returns ALLOW, APPROVAL_REQUIRED, or BLOCK.',
    never: 'Never uses an LLM for the authorization decision. No probabilistic output.',
    records: 'Decision, reasons, all 8 check results, policy version at DECISION.',
  },
  {
    id: 'context', label: 'Context Safety Layer', color: 'border-acc text-acc-dark bg-acc-light',
    dot: 'bg-acc',
    does: 'DOM scanner checks for hidden JSON-LD instructions, CSS-hidden text, and suspicious payment phrases. Can only add friction — never grant authority.',
    trusts: 'Nothing from the agent. The middleware fetches the source URL directly.',
    never: 'Context flags cannot override a deterministic BLOCK. They can escalate ALLOW → APPROVAL_REQUIRED.',
    records: 'Flag type, detector source, excerpt at CONTEXT_SCANNED.',
  },
  {
    id: 'approval', label: 'Human Approval Service', color: 'border-warn text-warn-dark bg-warn-light',
    dot: 'bg-warn',
    does: 'Shows server-derived facts to the authenticated human. Accepts APPROVE or DECLINE via CSRF-protected form.',
    trusts: 'The session cookie only. Agent bearer tokens are rejected with HTTP 403.',
    never: 'Approver identity never comes from the request body. Cannot approve an expired or revoked intent.',
    records: 'Approver user ID, binding checks (7 items), timestamp at APPROVED.',
  },
  {
    id: 'paypal', label: 'PayPal Execution Adapter', color: 'border-ok text-ok-dark bg-ok-light',
    dot: 'bg-ok',
    does: 'Creates a PayPal Sandbox order, captures payment, reads the order back for independent verification.',
    trusts: 'Only calls PayPal after ALLOW or human APPROVE. Vault token held server-side.',
    never: 'Never called for BLOCKED or HELD_FOR_APPROVAL requests. Agent never sees the vault token.',
    records: 'PayPal order ID, capture ID, verified amount/payee at PAYMENT_CAPTURED.',
  },
  {
    id: 'merchant', label: 'Merchant', color: 'border-ok text-ok-dark bg-ok-light',
    dot: 'bg-ok',
    does: 'Receives payment only after all prior checks pass.',
    trusts: 'PayPal payment confirmation.',
    never: 'Never receives payment for BLOCKED or unapproved HELD requests.',
    records: 'PayPal order and capture IDs visible in the audit record.',
  },
]

export default function ArchitecturePage() {
  const [active, setActive] = useState(null)

  return (
    <div>
      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
        <span className="w-5 h-0.5 bg-acc inline-block" />
        System design
      </div>
      <h1 className="text-[36px] font-extrabold tracking-tight mb-2">Authority moves through policy, not prompts.</h1>
      <p className="text-[14px] text-gray-500 mb-6 max-w-xl">Click any component to see what it does, what it trusts, what it can never do, and what evidence it records.</p>

      <div className="grid grid-cols-[320px_1fr] gap-6">
        {/* Pipeline */}
        <div>
          {NODES.map((n, i) => (
            <div key={n.id}>
              <button
                onClick={() => setActive(active?.id === n.id ? null : n)}
                className={`w-full text-left border rounded-xl px-4 py-3.5 transition-all
                  ${active?.id === n.id ? n.color + ' shadow-sm' : 'bg-white border-gray-200 hover:border-acc hover:shadow-sm'}`}
              >
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${n.dot}`} />
                  <span className="font-semibold text-[13px]">{n.label}</span>
                  <span className="ml-auto text-gray-300 text-[11px]">{active?.id === n.id ? '▲' : '▼'}</span>
                </div>
              </button>
              {i < NODES.length - 1 && (
                <div className="flex justify-center my-1">
                  <span className="text-acc text-[18px] font-bold leading-none">↓</span>
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Detail */}
        <AnimatePresence mode="wait">
          {active ? (
            <motion.div key={active.id}
              initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -8 }}
              className={`border rounded-xl p-6 ${active.color}`}
            >
              <h2 className="text-[20px] font-bold mb-4">{active.label}</h2>
              {[
                ['What it does', active.does],
                ['What it trusts', active.trusts],
                ['What it can never do', active.never],
                ['What evidence it records', active.records],
              ].map(([heading, body]) => (
                <div key={heading} className="mb-4">
                  <div className="text-[11px] font-bold uppercase tracking-widest text-gray-500 mb-1">{heading}</div>
                  <p className="text-[13px] text-gray-700 leading-relaxed">{body}</p>
                </div>
              ))}
            </motion.div>
          ) : (
            <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="bg-white border border-gray-200 rounded-xl p-8 flex items-center justify-center text-gray-400 text-[13px]">
              Select a component to inspect it
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="mt-6 bg-white border border-gray-200 rounded-xl p-5">
        <h3 className="font-bold text-[15px] mb-3">Design principles</h3>
        <ul className="space-y-1.5 text-[13px] text-gray-600">
          {[
            'The agent can only recommend. Only policy can authorize.',
            'Prices, payees and categories come from the server registry, never the agent.',
            'PayPal is never called for BLOCKED or unapproved HELD requests.',
            'Every decision is written to a hash-chained, tamper-detectable audit log.',
            'Human approval uses session auth + CSRF. Bearer tokens are rejected at the approval endpoint.',
            'Context flags can only add friction — they cannot override a deterministic BLOCK or grant authority.',
          ].map((p, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className="text-acc mt-0.5">→</span>{p}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
