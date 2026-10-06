export default function FlowDiagram({ intent }) {
  const { state, decision, order_id } = intent

  let mid, midCls, lane, end, endCls
  if (order_id) {
    mid = 'Authorized'; midCls = 'border-ok text-ok-dark'; lane = 'bg-ok'; end = 'Order captured'; endCls = 'border-ok text-ok-dark'
  } else if (state === 'HELD_FOR_APPROVAL') {
    mid = 'Held for approval'; midCls = 'border-warn text-warn-dark'; lane = 'border-warn'; end = 'Not reached yet'; endCls = 'border-gray-200 text-gray-400'
  } else if (decision === 'BLOCK') {
    mid = 'Blocked'; midCls = 'border-bad text-bad-dark'; lane = null; end = 'Never called'; endCls = 'border-gray-200 text-gray-400'
  } else {
    mid = state; midCls = 'border-bad text-bad-dark'; lane = null; end = 'Not reached'; endCls = 'border-gray-200 text-gray-400'
  }

  return (
    <div className="flex items-center gap-1.5 my-3">
      <Node label="AI agent" sub="proposed" cls="border-acc" />
      <Lane cls="bg-acc" />
      <Node label="TrustGate" sub={mid} cls={midCls} />
      {lane
        ? <Lane cls={lane} dashed={state === 'HELD_FOR_APPROVAL'} />
        : <Lane cls="bg-gray-200" />}
      <Node label="PayPal" sub={end} cls={endCls} />
    </div>
  )
}

function Node({ label, sub, cls }) {
  return (
    <div className={`flex-shrink-0 min-w-[110px] text-center px-3 py-2.5 border-[1.5px] rounded-lg bg-white ${cls}`}>
      <div className="font-semibold text-xs">{label}</div>
      <div className="text-[11px] text-gray-400 mt-0.5">{sub}</div>
    </div>
  )
}

function Lane({ cls, dashed }) {
  if (dashed) {
    return <div className="flex-1 h-0.5 bg-[repeating-linear-gradient(90deg,#d97706_0_6px,transparent_6px_12px)]" />
  }
  return <div className={`flex-1 h-0.5 ${cls}`} />
}
