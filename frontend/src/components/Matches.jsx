import { useState, useEffect, useRef, useCallback } from 'react'

function parseKickoff(str) {
  // Naiv lokaltid från iBIS, "2026-09-19T13:00:00" – tolkas som Europe/Stockholm
  // på tränarens telefon.
  return str ? new Date(str) : null
}

function formatDay(str) {
  const d = parseKickoff(str)
  if (!d) return ''
  return d.toLocaleDateString('sv-SE', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

function formatTime(str) {
  const d = parseKickoff(str)
  if (!d) return ''
  return d.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' })
}

function homeAwayLabel(hemma) {
  if (hemma === true) return 'Hemma'
  if (hemma === false) return 'Borta'
  return null
}

// ---------------------------------------------------------------------------
// Matchlista
// ---------------------------------------------------------------------------

function MatchListRow({ match, isNext, onSelect, rowRef }) {
  const installd = match.status === 'cancelled'
  const spelad = match.status === 'played'
  const ha = homeAwayLabel(match.hemma)

  return (
    <button
      ref={rowRef}
      onClick={() => onSelect(match.match_id)}
      className={`w-full text-left px-4 py-3 flex gap-3 items-start
                  hover:bg-gray-50 active:bg-gray-100 transition-colors ${
                    isNext ? 'bg-blue-50/60' : ''
                  }`}
    >
      {/* Datum + tid */}
      <div className="w-16 shrink-0">
        <div className="text-sm font-semibold text-gray-900 leading-tight">
          {formatDay(match.kickoff)}
        </div>
        <div className="text-xs text-gray-500 tabular-nums">
          {installd ? '—' : formatTime(match.kickoff)}
        </div>
      </div>

      {/* Motståndare + plats */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span
            className={`text-sm font-medium truncate ${
              installd ? 'text-gray-400 line-through' : 'text-gray-900'
            }`}
          >
            {match.motstandare ?? 'Okänd motståndare'}
          </span>
          {isNext && !installd && (
            <span className="shrink-0 text-[10px] font-bold uppercase tracking-wide
                             text-blue-700 bg-blue-100 rounded px-1.5 py-0.5">
              Nästa
            </span>
          )}
        </div>
        <div className="text-xs text-gray-500 mt-0.5 truncate">
          {[ha, match.hall].filter(Boolean).join(' · ') || 'Plats saknas'}
        </div>
      </div>

      {/* Resultat / status */}
      <div className="shrink-0 text-right">
        {installd ? (
          <span className="text-xs font-semibold text-amber-700 bg-amber-100
                           rounded px-2 py-0.5">
            Inställd
          </span>
        ) : spelad && match.resultat ? (
          <span className="text-sm font-bold tabular-nums text-gray-900">
            {match.resultat.hemma}–{match.resultat.borta}
          </span>
        ) : (
          <span className="text-xs text-gray-400">Ej spelad</span>
        )}
      </div>
    </button>
  )
}

function MatchList({ matches, onSelect }) {
  const nextRef = useRef(null)

  const now = Date.now()
  const nextId = matches.reduce((acc, m) => {
    if (acc !== null) return acc
    if (m.status === 'cancelled') return acc
    const k = parseKickoff(m.kickoff)
    return k && k.getTime() >= now ? m.match_id : acc
  }, null)

  useEffect(() => {
    if (nextRef.current) {
      nextRef.current.scrollIntoView({ block: 'center' })
    }
  }, [matches])

  if (matches.length === 0) {
    return (
      <p className="text-sm text-gray-500 px-4 py-6 text-center">
        Inga matcher för det här laget än. Kör en synk.
      </p>
    )
  }

  return (
    <div className="border border-gray-200 rounded-xl bg-white divide-y divide-gray-100
                    overflow-hidden">
      {matches.map(m => (
        <MatchListRow
          key={m.match_id}
          match={m}
          isNext={m.match_id === nextId}
          rowRef={m.match_id === nextId ? nextRef : null}
          onSelect={onSelect}
        />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Matchvy (skrivskyddad)
// ---------------------------------------------------------------------------

function SquadRow({ player, spelad }) {
  return (
    <div className="px-4 py-2.5 flex gap-3 items-center">
      <span className="w-8 shrink-0 text-right text-sm text-gray-400 tabular-nums select-none">
        {player.trojnummer != null ? player.trojnummer : ''}
      </span>
      <span className="flex-1 min-w-0 text-sm text-gray-900 truncate">
        {player.namn}
        {player.malvakt && (
          <span className="ml-2 text-[10px] font-bold uppercase tracking-wide
                           text-indigo-700 bg-indigo-100 rounded px-1.5 py-0.5 align-middle">
            MV
          </span>
        )}
      </span>
      {spelad && (
        <span className="shrink-0 flex gap-3 text-sm tabular-nums text-gray-700">
          <span className="w-6 text-right" title="Mål">{player.mal ?? 0}</span>
          <span className="w-6 text-right" title="Assist">{player.assist ?? 0}</span>
          <span className="w-6 text-right" title="Utvisningsminuter">
            {player.utvisningsminuter ?? 0}
          </span>
        </span>
      )}
    </div>
  )
}

function MatchDetail({ match }) {
  const ha = homeAwayLabel(match.hemma)
  const spelad = match.spelad

  return (
    <div>
      {/* Matchhuvud */}
      <div className="border border-gray-200 rounded-xl bg-white p-4 mb-5">
        <h2 className="text-lg font-bold text-gray-900 leading-tight">
          {match.motstandare ?? 'Okänd motståndare'}
        </h2>
        <p className="text-sm text-gray-500 mt-1">
          {[
            formatDay(match.kickoff),
            match.status !== 'cancelled' && formatTime(match.kickoff),
            ha,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
        {match.hall && (
          <p className="text-sm text-gray-500 mt-0.5">{match.hall}</p>
        )}

        <div className="mt-3">
          {match.status === 'cancelled' ? (
            <span className="text-sm font-semibold text-amber-700 bg-amber-100
                             rounded-lg px-3 py-1">
              Inställd match
            </span>
          ) : spelad && match.resultat ? (
            <span className="text-2xl font-bold tabular-nums text-gray-900">
              {match.resultat.hemma}–{match.resultat.borta}
            </span>
          ) : (
            <span className="text-sm text-gray-400">Ej spelad än</span>
          )}
        </div>
      </div>

      {/* Trupp */}
      <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
        Trupp
      </h3>

      {!match.trupp_publicerad ? (
        <div className="border border-gray-200 bg-gray-50 rounded-xl px-4 py-6
                        text-sm text-gray-500 text-center">
          Truppen är inte publicerad än.
        </div>
      ) : (
        <div className="border border-gray-200 rounded-xl bg-white divide-y divide-gray-100
                        overflow-hidden">
          {spelad && (
            <div className="px-4 py-2 flex gap-3 items-center bg-gray-50
                            text-[10px] font-semibold uppercase tracking-wide text-gray-400">
              <span className="w-8 shrink-0" />
              <span className="flex-1">Spelare</span>
              <span className="shrink-0 flex gap-3">
                <span className="w-6 text-right">Mål</span>
                <span className="w-6 text-right">Ass</span>
                <span className="w-6 text-right">Utv</span>
              </span>
            </div>
          )}
          {match.trupp.map(p => (
            <SquadRow key={p.player_id} player={p} spelad={spelad} />
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Behållare: växlar mellan lista och vald match, sköter hämtning
// ---------------------------------------------------------------------------

export default function MatchesTab({ team, onUnauthed }) {
  const [matches, setMatches] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const loadList = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/matches?team=${team}`)
      if (res.status === 401) {
        onUnauthed()
        return
      }
      if (!res.ok) {
        setError('Kunde inte hämta matcherna. Servern svarade med ett fel.')
        return
      }
      const data = await res.json()
      setMatches(data.matcher)
    } catch {
      setError('Kunde inte nå servern. Kontrollera nätet och prova igen.')
    } finally {
      setLoading(false)
    }
  }, [team])

  const loadDetail = useCallback(async id => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/matches/${id}`)
      if (res.status === 401) {
        onUnauthed()
        return
      }
      if (!res.ok) {
        setError('Kunde inte hämta matchen. Servern svarade med ett fel.')
        return
      }
      setDetail(await res.json())
    } catch {
      setError('Kunde inte nå servern. Kontrollera nätet och prova igen.')
    } finally {
      setLoading(false)
    }
  }, [])

  // Byte av lag: tillbaka till listan och hämta om
  useEffect(() => {
    setSelectedId(null)
    setDetail(null)
    loadList()
  }, [team, loadList])

  useEffect(() => {
    if (selectedId != null) {
      setDetail(null)
      loadDetail(selectedId)
    }
  }, [selectedId, loadDetail])

  if (error) {
    return (
      <div>
        {selectedId != null && (
          <button
            onClick={() => setSelectedId(null)}
            className="mb-4 text-sm text-blue-700 hover:text-blue-800"
          >
            ‹ Matchlista
          </button>
        )}
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-xl
                        text-red-700 text-sm">
          {error}
        </div>
        <button
          onClick={() => (selectedId != null ? loadDetail(selectedId) : loadList())}
          className="mt-3 px-4 py-2 bg-gray-100 hover:bg-gray-200 border border-gray-300
                     rounded-xl text-sm font-semibold text-gray-700 transition-colors"
        >
          Prova igen
        </button>
      </div>
    )
  }

  if (loading && (selectedId != null ? !detail : !matches)) {
    return <p className="text-sm text-gray-500 px-4 py-6 text-center">Laddar...</p>
  }

  if (selectedId != null) {
    return (
      <div>
        <button
          onClick={() => setSelectedId(null)}
          className="mb-4 text-sm text-blue-700 hover:text-blue-800 font-medium"
        >
          ‹ Matchlista
        </button>
        {detail && <MatchDetail match={detail} />}
      </div>
    )
  }

  return <MatchList matches={matches ?? []} onSelect={setSelectedId} />
}
