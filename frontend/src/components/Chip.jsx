const CLASSES = {
  ALLOW:            'bg-ok-light text-ok-dark',
  CAPTURED:         'bg-ok-light text-ok-dark',
  APPROVED:         'bg-ok-light text-ok-dark',
  NOT_REQUIRED:     'bg-ok-light text-ok-dark',
  valid:            'bg-ok-light text-ok-dark',
  APPROVAL_REQUIRED:'bg-warn-light text-warn-dark',
  HELD_FOR_APPROVAL:'bg-warn-light text-warn-dark',
  PENDING:          'bg-warn-light text-warn-dark',
  HELD:             'bg-warn-light text-warn-dark',
  BLOCK:            'bg-bad-light text-bad-dark',
  BLOCKED:          'bg-bad-light text-bad-dark',
  DECLINED:         'bg-bad-light text-bad-dark',
  EXPIRED:          'bg-bad-light text-bad-dark',
  REFUSED:          'bg-bad-light text-bad-dark',
  invalid:          'bg-bad-light text-bad-dark',
  RECEIVED:         'bg-acc-light text-acc-dark',
  VERIFIED:         'bg-acc-light text-acc-dark',
}

export default function Chip({ value, className = '' }) {
  const cls = CLASSES[value] || 'bg-gray-100 text-gray-600'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold tracking-wide whitespace-nowrap ${cls} ${className}`}>
      {value}
    </span>
  )
}
