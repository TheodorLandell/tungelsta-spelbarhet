import { useState } from 'react'

const NAME_KEY = 'tranare_kortnamn'

const WARNING =
  'Ändringar här påverkar låsningsberäkningen. Ta bara bort spelare som iBIS ' +
  'registrerat felaktigt, inte spelare som var uppskrivna men inte spelade.'

function coachName() {
  try {
    const v = localStorage.getItem(NAME_KEY)
    return v && v.trim() ? v.trim() : null
  } catch {
    return null
  }
}

function formatStamp(isoStr) {
  if (!isoStr) return ''
  return new Date(isoStr).toLocaleDateString('sv-SE', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function PlayerLabel({ player }) {
  return (
    <span className="flex items-center gap-1.5 min-w-0">
      <span className="w-6 shrink-0 text-right text-xs text-gray-400 tabular-nums">
        {player.trojnummer != null ? player.trojnummer : ''}
      </span>
      <span className="truncate text-sm text-gray-900">{player.namn}</span>
      {player.malvakt && (
        <span className="shrink-0 text-[10px] font-bold uppercase tracking-wide
                         text-white bg-black rounded px-1 py-0.5">
          MV
        </span>
      )}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Bekräftelse med anteckning – varje ändring kräver en anteckning (SPEC 6.5)
// ---------------------------------------------------------------------------

function NoteConfirm({ label, busy, onConfirm, onCancel }) {
  const [note, setNote] = useState('')
  const trimmed = note.trim()

  return (
    <div className="mt-2 space-y-2">
      <input
        type="text"
        autoFocus
        value={note}
        onChange={e => setNote(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && trimmed && !busy) onConfirm(trimmed)
        }}
        placeholder="Anteckning (krävs)"
        className="w-full text-xs border-2 border-gray-300 rounded-lg px-2.5 py-1.5
                   focus:outline-none focus:border-black focus:ring-2 focus:ring-black"
      />
      <div className="flex gap-2">
        <button
          onClick={() => trimmed && onConfirm(trimmed)}
          disabled={busy || !trimmed}
          className="text-xs px-3 py-1.5 bg-tuif-orange text-black rounded-lg
                     disabled:opacity-50 hover:brightness-95 active:brightness-90 transition"
        >
          {busy ? 'Sparar...' : label}
        </button>
        <button
          onClick={onCancel}
          className="text-xs px-3 py-1.5 border border-gray-300 text-gray-600
                     rounded-lg hover:bg-gray-50 transition-colors"
        >
          Avbryt
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Ändra matchlista
// ---------------------------------------------------------------------------

export default function RosterEditor({ match, onChanged, onUnauthed }) {
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(null) // { playerId, action }
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const matchId = match.match_id
  const trupp = match.trupp ?? []
  const borttagna = match.borttagna ?? []
  const kandidater = match.lagtrupp ?? []

  async function call(fn) {
    setBusy(true)
    setError(null)
    try {
      const res = await fn()
      if (res.status === 401) {
        if (onUnauthed) onUnauthed()
        return false
      }
      if (!res.ok) {
        setError('Kunde inte spara ändringen. Servern svarade med ett fel.')
        return false
      }
      setPending(null)
      await onChanged()
      return true
    } catch {
      setError('Kunde inte nå servern. Kontrollera nätet och prova igen.')
      return false
    } finally {
      setBusy(false)
    }
  }

  function saveEdit(playerId, action, note) {
    return call(() =>
      fetch(`/api/matches/${matchId}/roster-edits`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          player_id: playerId,
          action,
          note,
          created_by: coachName(),
        }),
      }),
    )
  }

  function undo(playerId) {
    return call(() =>
      fetch(`/api/matches/${matchId}/roster-edits/${playerId}`, {
        method: 'DELETE',
      }),
    )
  }

  if (!open) {
    return (
      <div className="mt-6">
        <button
          onClick={() => setOpen(true)}
          className="w-full py-3 rounded-xl bg-tuif-orange
                     text-sm font-semibold text-black hover:brightness-95
                     active:brightness-90 transition"
        >
          Ändra matchlista
        </button>
      </div>
    )
  }

  const isPending = (playerId, action) =>
    pending && pending.playerId === playerId && pending.action === action

  return (
    <div className="mt-6 border border-gray-200 rounded-xl bg-white overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 bg-gray-50
                      border-b border-gray-200">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Ändra matchlista
        </h3>
        <button
          onClick={() => {
            setOpen(false)
            setPending(null)
            setError(null)
          }}
          className="text-xs font-medium text-black underline underline-offset-2
                     hover:text-gray-700"
        >
          Klar
        </button>
      </div>

      <div className="p-4 space-y-4">
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200
                      rounded-lg px-3 py-2">
          {WARNING}
        </p>

        {error && (
          <p className="text-xs text-red-700 bg-red-50 border border-red-200
                        rounded-lg px-3 py-2">
            {error}
          </p>
        )}

        {/* Matchens trupp – ta bort en spelare, eller återställ en tillagd */}
        <div>
          <h4 className="text-[11px] font-semibold uppercase tracking-wide
                         text-gray-400 mb-1.5">
            Matchens trupp
          </h4>
          <ul className="divide-y divide-gray-100 border border-gray-100 rounded-lg">
            {trupp.length === 0 && (
              <li className="px-3 py-2 text-xs text-gray-400">Ingen trupp än</li>
            )}
            {trupp.map(p => {
              const added = p.roster_edit && p.roster_edit.action === 'add'
              return (
                <li key={p.player_id} className="px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <PlayerLabel player={p} />
                    {added ? (
                      <span className="shrink-0 flex items-center gap-2">
                        <span className="text-[10px] font-bold uppercase tracking-wide
                                         text-green-700 bg-green-100 rounded px-1.5 py-0.5">
                          Tillagd
                        </span>
                        <button
                          onClick={() => undo(p.player_id)}
                          disabled={busy}
                          className="text-xs px-2.5 py-1 bg-gray-100 border border-gray-300
                                     text-gray-700 rounded-lg hover:bg-gray-200
                                     disabled:opacity-50 transition-colors"
                        >
                          Återställ
                        </button>
                      </span>
                    ) : (
                      !isPending(p.player_id, 'remove') && (
                        <button
                          onClick={() =>
                            setPending({ playerId: p.player_id, action: 'remove' })
                          }
                          className="shrink-0 text-xs px-2.5 py-1 bg-red-50 border
                                     border-red-200 text-red-700 rounded-lg
                                     hover:bg-red-100 transition-colors"
                        >
                          Ta bort
                        </button>
                      )
                    )}
                  </div>

                  {added && (
                    <p className="text-xs text-gray-400 mt-1 pl-7">
                      {p.roster_edit.note} · {formatStamp(p.roster_edit.created_at)}
                    </p>
                  )}

                  {isPending(p.player_id, 'remove') && (
                    <NoteConfirm
                      label="Ta bort"
                      busy={busy}
                      onConfirm={note => saveEdit(p.player_id, 'remove', note)}
                      onCancel={() => setPending(null)}
                    />
                  )}
                </li>
              )
            })}
          </ul>
        </div>

        {/* Borttagna spelare – återställ */}
        {borttagna.length > 0 && (
          <div>
            <h4 className="text-[11px] font-semibold uppercase tracking-wide
                           text-gray-400 mb-1.5">
              Borttagna ur truppen
            </h4>
            <ul className="divide-y divide-gray-100 border border-gray-100 rounded-lg">
              {borttagna.map(p => (
                <li key={p.player_id} className="px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <PlayerLabel player={p} />
                    <button
                      onClick={() => undo(p.player_id)}
                      disabled={busy}
                      className="shrink-0 text-xs px-2.5 py-1 bg-gray-100 border
                                 border-gray-300 text-gray-700 rounded-lg
                                 hover:bg-gray-200 disabled:opacity-50 transition-colors"
                    >
                      Återställ
                    </button>
                  </div>
                  <p className="text-xs text-gray-400 mt-1 pl-7">
                    {p.roster_edit.note} · {formatStamp(p.roster_edit.created_at)}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Lägg till spelare ur lagets trupp */}
        <div>
          <h4 className="text-[11px] font-semibold uppercase tracking-wide
                         text-gray-400 mb-1.5">
            Lägg till ur lagets trupp
          </h4>
          {kandidater.length === 0 ? (
            <p className="text-xs text-gray-400 px-1">
              Alla spelare i lagets trupp finns redan i matchen.
            </p>
          ) : (
            <ul className="divide-y divide-gray-100 border border-gray-100 rounded-lg">
              {kandidater.map(p => (
                <li key={p.player_id} className="px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <PlayerLabel player={p} />
                    {!isPending(p.player_id, 'add') && (
                      <button
                        onClick={() =>
                          setPending({ playerId: p.player_id, action: 'add' })
                        }
                        className="shrink-0 text-xs px-2.5 py-1 bg-tuif-orange
                                   text-black rounded-lg
                                   hover:brightness-95 active:brightness-90 transition"
                      >
                        Lägg till
                      </button>
                    )}
                  </div>
                  {isPending(p.player_id, 'add') && (
                    <NoteConfirm
                      label="Lägg till"
                      busy={busy}
                      onConfirm={note => saveEdit(p.player_id, 'add', note)}
                      onCancel={() => setPending(null)}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
