import { useState, useEffect, useRef, useCallback } from 'react'
import ShotRegistration from './ShotRegistration'
import RosterEditor from './RosterEditor'
import MatchHeader from './MatchHeader'
import { cacheSquad, loadCachedSquad } from '../lib/shotStore'

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

// Matcher som inte räknas i låsningsreglerna (cup, träningsmatch) markeras
// tydligt i listan och i matchvyn.
function matchTypeLabel(match) {
  if (match.raknas !== false) return null
  return match.matchtyp === 'cup' ? 'Cup' : 'Träningsmatch'
}

function NonCountingBadge({ label, className = '' }) {
  return (
    <span
      className={`shrink-0 text-[10px] font-bold uppercase tracking-wide
                  text-purple-700 bg-purple-100 rounded px-1.5 py-0.5 ${className}`}
    >
      {label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Matchlista
// ---------------------------------------------------------------------------

function MatchListRow({ match, isNext, onSelect, rowRef, liveRow }) {
  const installd = match.status === 'cancelled'
  const ha = homeAwayLabel(match.hemma)
  const typLabel = matchTypeLabel(match)

  // Live-resultat (SPEC 6.6) väger tyngst medan matchen pågår.
  const liveResultat = liveRow?.resultat ?? null
  const resultat = liveResultat ?? match.resultat
  const spelad = match.status === 'played' || Boolean(liveResultat)

  return (
    <button
      ref={rowRef}
      onClick={() => onSelect(match.match_id)}
      className={`w-full text-left px-4 py-3 flex gap-3 items-start
                  hover:bg-gray-50 active:bg-gray-100 transition-colors ${
                    isNext ? 'bg-orange-50' : ''
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
                             text-white bg-black rounded px-1.5 py-0.5">
              Nästa
            </span>
          )}
          {typLabel && <NonCountingBadge label={typLabel} />}
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
        ) : spelad && resultat ? (
          <span className="text-sm font-bold tabular-nums text-gray-900">
            {resultat.hemma}–{resultat.borta}
            {liveResultat && (
              <span className="ml-1 align-middle text-[10px] font-bold uppercase
                               tracking-wide text-tuif-orange">
                pågår
              </span>
            )}
          </span>
        ) : (
          <span className="text-xs text-gray-400">Ej spelad</span>
        )}
      </div>
    </button>
  )
}

function MatchList({ matches, onSelect, liveById }) {
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
          liveRow={liveById?.get(m.match_id) ?? null}
        />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Matchvy med skottregistrering
// ---------------------------------------------------------------------------

function MatchDetail({ match, offlineNotice, onUnauthed, onRosterChanged }) {
  const typLabel = matchTypeLabel(match)

  return (
    <div>
      {/* Cup och träningsmatch: markera att den inte räknas i reglerna. Datum,
          hall och "ej spelad än" hör hemma i matchlistan, inte här (SPEC 6.2). */}
      {typLabel && (
        <div className="mb-3 flex items-center gap-2">
          <NonCountingBadge label={typLabel} />
          <span className="text-xs text-purple-700">
            Räknas inte i låsningsreglerna. Skott går att registrera som vanligt.
          </span>
        </div>
      )}

      {!match.trupp_publicerad ? (
        <>
          <MatchHeader match={match} />
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Skottregistrering
          </h3>
          <div className="border border-gray-200 bg-gray-50 rounded-xl px-4 py-6
                          text-sm text-gray-500 text-center">
            Truppen är inte publicerad än. Öppna matchen igen när den finns i iBIS
            så cachas den för offline.
          </div>
        </>
      ) : (
        <ShotRegistration
          match={match}
          offlineNotice={offlineNotice}
          onUnauthed={onUnauthed}
        />
      )}

      {/* Ändra matchlista (SPEC 6.5) – kräver servern, göms för cachad vy */}
      {!offlineNotice && onRosterChanged && (
        <RosterEditor
          match={match}
          onChanged={onRosterChanged}
          onUnauthed={onUnauthed}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Behållare: växlar mellan lista och vald match, sköter hämtning
// ---------------------------------------------------------------------------

function formatStamp(isoStr) {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  return d.toLocaleString('sv-SE', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Lägger live-resultatet (SPEC 6.6) ovanpå den hämtade matchvyn: resultatrad,
// lagmål och spelarnas mål/assist/utvisningsminuter. Rör inte trupp_publicerad,
// vald period eller något som skottregistreringen äger.
function mergeLive(detail, liveRow) {
  if (!detail || !liveRow) return detail
  const bySpelare = new Map((liveRow.spelare ?? []).map(s => [s.player_id, s]))
  return {
    ...detail,
    status: liveRow.status ?? detail.status,
    resultat: liveRow.resultat ?? detail.resultat,
    mal: liveRow.mal ?? detail.mal,
    motstandare_mal: liveRow.motstandare_mal ?? detail.motstandare_mal,
    trupp: (detail.trupp ?? []).map(p => {
      const s = bySpelare.get(p.player_id)
      return s
        ? {
            ...p,
            mal: s.mal,
            assist: s.assist,
            utvisningsminuter: s.utvisningsminuter,
          }
        : p
    }),
  }
}

export default function MatchesTab({ team, live, onUnauthed, onInsideMatchChange }) {
  const [matches, setMatches] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [offlineNotice, setOfflineNotice] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Låt skalet veta om vi är inne i en match, så meny och lagväljare kan gömmas.
  useEffect(() => {
    onInsideMatchChange?.(selectedId != null)
  }, [selectedId, onInsideMatchChange])

  useEffect(() => () => onInsideMatchChange?.(false), [onInsideMatchChange])

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

  // Faller tillbaka på den cachade truppen om servern inte går att nå eller
  // svarar med ett fel. Skiljer på de två i felmeddelandet, men bara när det
  // inte finns någon cache att visa.
  const useCachedOr = useCallback(async (id, serverMsg) => {
    try {
      const cached = await loadCachedSquad(id)
      if (cached?.detail) {
        setDetail(cached.detail)
        setOfflineNotice(
          `Kunde inte uppdatera truppen från servern. Visar sparad version från ${formatStamp(
            cached.cached_at,
          )}.`,
        )
        return true
      }
    } catch {
      // IndexedDB otillgänglig – falla igenom till felmeddelandet
    }
    setError(serverMsg)
    return false
  }, [])

  const loadDetail = useCallback(
    async id => {
      setLoading(true)
      setError(null)
      setOfflineNotice(null)
      try {
        const res = await fetch(`/api/matches/${id}`)
        if (res.status === 401) {
          onUnauthed()
          return
        }
        if (!res.ok) {
          await useCachedOr(
            id,
            'Kunde inte hämta matchen. Servern svarade med ett fel.',
          )
          return
        }
        const data = await res.json()
        setDetail(data)
        try {
          await cacheSquad(id, data)
        } catch {
          // Cachning misslyckades – registrering funkar ändå så länge nätet finns
        }
      } catch {
        await useCachedOr(
          id,
          'Kunde inte nå servern. Kontrollera nätet och prova igen.',
        )
      } finally {
        setLoading(false)
      }
    },
    [onUnauthed, useCachedOr],
  )

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
            className="mb-4 text-sm font-medium text-black underline underline-offset-2
                       hover:text-gray-700"
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

  const liveById = new Map((live?.matcher ?? []).map(m => [m.match_id, m]))

  if (selectedId != null) {
    return (
      <div>
        <button
          onClick={() => setSelectedId(null)}
          className="mb-4 text-sm font-medium text-black underline underline-offset-2
                     hover:text-gray-700"
        >
          ‹ Matchlista
        </button>
        {detail && (
          <MatchDetail
            match={mergeLive(detail, liveById.get(selectedId))}
            offlineNotice={offlineNotice}
            onUnauthed={onUnauthed}
            onRosterChanged={() => loadDetail(selectedId)}
          />
        )}
      </div>
    )
  }

  return (
    <MatchList
      matches={matches ?? []}
      onSelect={setSelectedId}
      liveById={liveById}
    />
  )
}
