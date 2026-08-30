import { useState, useEffect } from 'react'

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

function formatOverrideDate(isoStr) {
  if (!isoStr) return ''
  return new Date(isoStr).toLocaleDateString('sv-SE', {
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
    blue: 'bg-black text-white',
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
    if (player.override?.kind === 'lock') {
      return 'Låst manuellt'
    }
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

export default function PlayerRow({ player, variant, editMode, onOverride, onReset }) {
  const [pendingKind, setPendingKind] = useState(null)
  const [pendingValue, setPendingValue] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!editMode) {
      setPendingKind(null)
      setPendingValue(null)
      setNote('')
    }
  }, [editMode])

  const { trojnummer, namn } = player
  const isLocked = variant === 'locked'
  const ovr = player.override
  const underlag = buildUnderlag(player, variant)
  const badge = buildBadge(player, variant)

  async function confirm() {
    setBusy(true)
    try {
      await onOverride(player.player_id, pendingKind, pendingValue, note.trim())
      setPendingKind(null)
      setPendingValue(null)
      setNote('')
    } finally {
      setBusy(false)
    }
  }

  function cancel() {
    setPendingKind(null)
    setPendingValue(null)
    setNote('')
  }

  return (
    <div className={`py-3 ${isLocked && !ovr ? 'opacity-60' : ''}`}>
      <div className="flex gap-3">
        {/* Shirt number */}
        <span className="w-8 shrink-0 text-right text-sm text-gray-400 pt-0.5 select-none">
          {trojnummer != null ? trojnummer : ''}
        </span>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Name + badge */}
          <div className="flex items-start justify-between gap-2">
            <span className={`text-sm font-medium leading-5 ${isLocked && !ovr ? 'text-gray-500' : 'text-gray-900'}`}>
              {namn}
            </span>
            <Badge variant={badge.variant}>{badge.text}</Badge>
          </div>

          {/* Underlag */}
          {underlag && (
            <p className="text-xs text-gray-400 mt-0.5 pr-1">{underlag}</p>
          )}

          {/* Override info – alltid synlig när override finns */}
          {ovr && !pendingKind && (
            <div className="mt-1.5 space-y-0.5">
              <p className="text-xs text-gray-700">
                Ändrad manuellt: {ovr.note} · {formatOverrideDate(ovr.created_at)}
              </p>
              {ovr.stale && (
                <p className="text-xs text-amber-600">
                  Ny matchdata tillgänglig – ändringen kan vara inaktuell
                </p>
              )}
            </div>
          )}

          {/* Manuellt ändrad matchlista – markeras som overrides (SPEC 6.5) */}
          {player.roster_edit && !pendingKind && (
            <p className="text-xs text-gray-700 mt-1">
              Underlaget manuellt ändrat:{' '}
              {player.roster_edit.action === 'add'
                ? 'spelare tillagd i en matchtrupp'
                : 'spelare borttagen ur en matchtrupp'}
              {' – '}
              {player.roster_edit.note} ·{' '}
              {formatOverrideDate(player.roster_edit.created_at)}
              {player.roster_edit.antal > 1 &&
                ` (+${player.roster_edit.antal - 1} till)`}
              . Ändras i matchvyn.
            </p>
          )}

          {/* Redigeringskontroller (visas bara i redigeringsläge) */}
          {editMode && !pendingKind && (
            <div className="mt-2 flex flex-wrap gap-2 items-center">
              {/* Lås upp – för låsta spelare (ej när låsningen själv är en override) */}
              {isLocked && ovr?.kind !== 'lock' && (
                <button
                  onClick={() => { setPendingKind('unlock'); setPendingValue(null) }}
                  className="text-xs px-2.5 py-1 bg-tuif-orange text-black rounded-lg
                             hover:brightness-95 active:brightness-90 transition"
                >
                  Lås upp
                </button>
              )}

              {/* Matcher kvar – för olåsta spelare */}
              {!isLocked && [0, 1, 2].map(n => (
                <button
                  key={n}
                  onClick={() => { setPendingKind('set_matches_left'); setPendingValue(n) }}
                  disabled={player.matcher_kvar === n}
                  className={`text-xs px-2.5 py-1 rounded-lg border transition ${
                    player.matcher_kvar === n
                      ? 'bg-gray-100 border-gray-200 text-gray-400 cursor-default'
                      : 'bg-tuif-orange border-transparent text-black hover:brightness-95 active:brightness-90'
                  }`}
                >
                  {n} kvar
                </button>
              ))}

              {/* Lås – för olåsta spelare */}
              {!isLocked && (
                <button
                  onClick={() => { setPendingKind('lock'); setPendingValue(null) }}
                  className="text-xs px-2.5 py-1 bg-red-50 border border-red-200
                             text-red-700 rounded-lg hover:bg-red-100 transition-colors"
                >
                  Lås
                </button>
              )}

              {/* Återställ – bara när override finns */}
              {ovr && (
                <button
                  onClick={() => onReset(player.player_id)}
                  className="text-xs px-2.5 py-1 bg-red-50 border border-red-200
                             text-red-600 rounded-lg hover:bg-red-100 transition-colors ml-auto"
                >
                  Återställ
                </button>
              )}
            </div>
          )}

          {/* Bekräftelsepanel för pågående åtgärd */}
          {editMode && pendingKind && (
            <div className="mt-2 space-y-2">
              <input
                type="text"
                placeholder="Anteckning (krävs)"
                value={note}
                onChange={e => setNote(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && note.trim() && !busy) confirm() }}
                className="w-full text-xs border-2 border-gray-300 rounded-lg px-2.5 py-1.5
                           focus:outline-none focus:border-black focus:ring-2 focus:ring-black"
                autoFocus
              />
              <div className="flex gap-2">
                <button
                  onClick={confirm}
                  disabled={busy || !note.trim()}
                  className="text-xs px-3 py-1.5 bg-tuif-orange text-black
                             rounded-lg disabled:opacity-50 hover:brightness-95
                             active:brightness-90 transition"
                >
                  {busy ? 'Sparar...' : 'Bekräfta'}
                </button>
                <button
                  onClick={cancel}
                  className="text-xs px-3 py-1.5 border border-gray-300 text-gray-600
                             rounded-lg hover:bg-gray-50 transition-colors"
                >
                  Avbryt
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
