import { useState } from 'react'

const ENDPOINTS = [
  {
    method: 'POST', path: '/v1/purchase-intents',
    auth: 'Agent — Bearer API key', authColor: 'bg-acc-light text-acc-dark',
    desc: 'Agent proposes a purchase. Returns intent ID, state, decision, and approval status.',
    request: {
      merchant_reference: 'merchant_demo_airlines',
      product_reference: 'cpt-jnb-flex-320',
      quantity: 1,
      source_url: 'https://demo-airlines.test/checkout',
      request_id: 'req-unique-001',
    },
    response: {
      intent_id: 'pi_a3f8c291d4e0',
      state: 'HELD_FOR_APPROVAL',
      decision: 'APPROVAL_REQUIRED',
      approval_status: 'PENDING',
      reason_codes: ['AMOUNT_EXCEEDS_AUTO_APPROVAL_LIMIT'],
      trusted_purchase: {
        merchant: 'Demo Airlines',
        payee_id: 'paypal_sandbox_demo_airlines',
        category: 'travel',
        product: 'CPT-JNB flexible',
        amount: '320.00',
        currency: 'USD',
      },
    },
  },
  {
    method: 'POST', path: '/v1/approvals/{intent_id}',
    auth: 'Human — session cookie + CSRF', authColor: 'bg-warn-light text-warn-dark',
    desc: 'Human approves or declines a held purchase. Agent bearer tokens are rejected with HTTP 403.',
    request: { decision: 'APPROVE', csrf: '<csrf-token>' },
    response: { state: 'CAPTURED', decision: 'APPROVAL_REQUIRED', approval_status: 'APPROVED' },
  },
  {
    method: 'GET', path: '/v1/intents/{intent_id}',
    auth: 'Agent or human', authColor: 'bg-ok-light text-ok-dark',
    desc: 'Poll intent state. Agent sees trusted_purchase but never payment credentials.',
    request: null,
    response: {
      intent_id: 'pi_a3f8c291d4e0',
      state: 'CAPTURED',
      decision: 'APPROVAL_REQUIRED',
      approval_status: 'APPROVED',
      order_id: 'PAYPAL-ORDER-XXXX',
      capture_id: 'CAPTURE-YYYY',
    },
  },
  {
    method: 'GET', path: '/v1/intents/{intent_id}/audit',
    auth: 'Human session or admin key', authColor: 'bg-ok-light text-ok-dark',
    desc: 'Returns the full hash-chained audit trail for an intent. chain_valid: true means untampered.',
    request: null,
    response: {
      chain_valid: true,
      events: [
        { seq: 0, event: 'INTENT_RECEIVED', ts: '2024-01-01T10:00:00Z' },
        { seq: 1, event: 'FACTS_RESOLVED', ts: '2024-01-01T10:00:00Z' },
        { seq: 2, event: 'DECISION', ts: '2024-01-01T10:00:00Z' },
      ],
    },
  },
]

function CopyBtn({ text }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
      className="text-[11px] text-gray-400 hover:text-gray-700 border border-gray-200 px-2 py-0.5 rounded transition-colors"
    >
      {copied ? '✓ Copied' : 'Copy'}
    </button>
  )
}

export default function ApiExplorerPage() {
  const [active, setActive] = useState(0)
  const ep = ENDPOINTS[active]

  const curlCmd = ep.method === 'GET'
    ? `curl http://localhost:8000${ep.path.replace('{intent_id}','<intent_id>')} \\\n  -H "Cookie: tm_session=<session>"`
    : `curl -X ${ep.method} http://localhost:8000${ep.path.replace('{intent_id}','<intent_id>')} \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer <api-key>" \\\n  -d '${JSON.stringify(ep.request, null, 2)}'`

  return (
    <div>
      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
        <span className="w-5 h-0.5 bg-acc inline-block" />
        Developer surface / V1
      </div>
      <h1 className="text-[36px] font-extrabold tracking-tight mb-2">Authority has an API boundary.</h1>
      <p className="text-[14px] text-gray-500 mb-6">The agent receives a proposal endpoint. Only an authenticated human can call the approval endpoint. PayPal credentials never cross this surface.</p>

      <div className="grid grid-cols-[300px_1fr] gap-4">
        {/* Endpoint list */}
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          {ENDPOINTS.map((e, i) => (
            <button key={i} onClick={() => setActive(i)}
              className={`w-full text-left px-4 py-4 border-b border-gray-100 last:border-0 transition-colors
                ${active === i ? 'bg-acc-light' : 'hover:bg-gray-50'}`}
            >
              <div className="flex items-center gap-2 mb-1.5">
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded font-mono
                  ${e.method === 'GET' ? 'bg-ok text-white' : 'bg-acc text-white'}`}>
                  {e.method}
                </span>
                <code className="text-[12px] text-gray-700 truncate">{e.path}</code>
              </div>
              <div className="text-[11px] text-gray-500 truncate">{e.desc}</div>
            </button>
          ))}
        </div>

        {/* Detail */}
        <div className="space-y-3">
          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <div className="flex items-center gap-3 mb-3">
              <span className={`text-[11px] font-bold px-2.5 py-1 rounded font-mono
                ${ep.method === 'GET' ? 'bg-ok text-white' : 'bg-acc text-white'}`}>
                {ep.method}
              </span>
              <code className="text-[14px] font-semibold">{ep.path}</code>
            </div>
            <p className="text-[13px] text-gray-600 mb-3">{ep.desc}</p>
            <span className={`text-[11px] font-semibold px-2.5 py-1 rounded ${ep.authColor}`}>
              🔒 {ep.auth}
            </span>
          </div>

          {ep.request && (
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <div className="flex items-center justify-between mb-2">
                <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">Request body</div>
                <CopyBtn text={JSON.stringify(ep.request, null, 2)} />
              </div>
              <pre className="bg-[#f8f9fb] border border-gray-100 rounded-lg p-3 text-[11px] font-mono overflow-x-auto">
                {JSON.stringify(ep.request, null, 2)}
              </pre>
            </div>
          )}

          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">Response</div>
              <CopyBtn text={JSON.stringify(ep.response, null, 2)} />
            </div>
            <pre className="bg-[#f8f9fb] border border-gray-100 rounded-lg p-3 text-[11px] font-mono overflow-x-auto">
              {JSON.stringify(ep.response, null, 2)}
            </pre>
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[11px] font-semibold uppercase tracking-widest text-gray-400">cURL</div>
              <CopyBtn text={curlCmd} />
            </div>
            <pre className="bg-[#0f1117] text-slate-300 rounded-lg p-3 text-[11px] font-mono overflow-x-auto">
              {curlCmd}
            </pre>
          </div>
        </div>
      </div>

      <div className="mt-4 bg-warn-light border border-amber-200 rounded-xl p-5">
        <div className="font-bold text-warn-dark mb-2">The contract: recommendation is not authorization</div>
        <p className="text-[13px] text-warn-dark opacity-90">
          A manipulated agent may still recommend the $3 fee. It cannot turn that recommendation into a PayPal order.
          The middleware resolves facts from registered records, evaluates the active policy version,
          and returns a decision that downstream payment code cannot override.
        </p>
      </div>
    </div>
  )
}
