const LOCK_REASON_HUMAN = {
  'spelade A-match innan nagon B-match': 'Spelade A-match innan B',
  'tre A-matcher i rad': 'Tre A-matcher i rad',
}

function formatDate(dateStr) {
  if (!dateStr) return null
  const [y, m, d] = dateStr.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('sv-SE', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function Badge({ children, variant }) {
  const styles = {
    warning: 'bg-amber-500 text-white',
    green: 'bg-green-600 text-white',
    yellow: 'bg-yellow-400 text-yellow-900',
    blue: 'bg-blue-600 text-white',
    red: 'bg-red-600 text-white',
  }
  return (
    <span
      className={`shrink-0 inline-block px-2.5 py-1 rounded-lg text-sm font-bold
                  leading-5 whitespace-nowrap ${styles[variant]}`}
    >
      {children}
    </span>
  )
}

function buildUnderlag(player, variant) {
  if (variant === 'locked') {
    const reason = LOCK_REASON_HUMAN[player.lock_orsak] ?? player.lock_orsak ?? 'Okänd orsak'
    const date = formatDate(player.lock_datum)
    return date ? `${reason} – ${date}` : reason
  }

  if (variant === 'must-sit') {
    return 'Nästa A-match låser'
  }

  if (player.maste_spela_b_forst) {
    return 'Måste spela B-match före A'
  }

  if ((player.consecutive_a ?? 0) > 0) {
    const n = player.consecutive_a
    return `${n} A-match${n > 1 ? 'er' : ''} i rad`
  }

  return null
}

function buildBadge(player, variant) {
  if (variant === 'locked') {
    return { text: 'Låst', variant: 'red' }
  }
  if (variant === 'must-sit') {
    return { text: 'Stå över', variant: 'warning' }
  }
  if (player.maste_spela_b_forst) {
    return { text: 'Spela B', variant: 'blue' }
  }
  const left = player.matcher_kvar ?? 2
  return {
    text: `${left} kvar`,
    variant: left <= 1 ? 'yellow' : 'green',
  }
}

export default function PlayerRow({ player, variant }) {
  const { trojnummer, namn } = player
  const isLocked = variant === 'locked'
  const underlag = buildUnderlag(player, variant)
  const badge = buildBadge(player, variant)

  return (
    <div className={`flex gap-3 py-3 ${isLocked ? 'opacity-60' : ''}`}>
      {/* Shirt number – fixed width, right-aligned */}
      <span className="w-8 shrink-0 text-right text-sm text-gray-400 pt-0.5 select-none">
        {trojnummer != null ? trojnummer : ''}
      </span>

      {/* Name + supporting row */}
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <span className={`text-sm font-medium leading-5 ${isLocked ? 'text-gray-500' : 'text-gray-900'}`}>
            {namn}
          </span>
          <Badge variant={badge.variant}>{badge.text}</Badge>
        </div>
        {underlag && (
          <p className="text-xs text-gray-400 mt-0.5 pr-1">{underlag}</p>
        )}
      </div>
    </div>
  )
}
