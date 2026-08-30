import { useState, useEffect, useCallback, useRef } from 'react'
import {
  KINDS,
  KIND_LABEL,
  OVERVIEW,
  loadEvents,
  addEvent,
  tombstoneLatest,
  countActive,
  countSide,
  canRegisterInPeriod,
  unsyncedCount,
} from '../lib/shotStore'
import { syncMatch, SyncError } from '../lib/shotSync'
import MatchHeader from './MatchHeader'

const PERIODS = [1, 2, 3]
const NAME_KEY = 'tranare_kortnamn'
const SYNC_INTERVAL_MS = 20000

function isOnline() {
  return typeof navigator === 'undefined' ? true : navigator.onLine !== false
}

function loadName() {
  try {
    const v = localStorage.getItem(NAME_KEY)
    return v && v.trim() ? v.trim() : null
  } catch {
    return null
  }
}

// ---------------------------------------------------------------------------
// Kortnamn – sparas lokalt en gång och följer med registreringarna (SPEC 8)
// ---------------------------------------------------------------------------

function NamePrompt({ current, onSave }) {
  const [value, setValue] = useState(current ?? '')
  const trimmed = value.trim()

  return (
    <div className="border border-amber-200 bg-amber-50 rounded-xl p-4 mb-4">
      <label
        htmlFor="kortnamn"
        className="block text-sm font-medium text-amber-900 mb-1"
      >
        Ditt kortnamn
      </label>
      <p className="text-xs text-amber-800 mb-2">
        Sparas på den här enheten och följer med dina registreringar.
      </p>
      <div className="flex gap-2">
        <input
          id="kortnamn"
          type="text"
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder="T.ex. Theo"
          className="flex-1 rounded-lg border border-amber-300 px-3 py-2 text-base
                     focus:outline-none focus:ring-2 focus:ring-amber-500"
        />
        <button
          onClick={() => trimmed && onSave(trimmed)}
          disabled={!trimmed}
          className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-lg
                     text-sm font-semibold disabled:opacity-50 transition-colors"
        >
          Spara
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// En kategori-kontroll: stor plusknapp med antal, smalare minusknapp under
// ---------------------------------------------------------------------------

function CategoryControl({ kind, count, onPlus, onMinus, disabled, readOnly }) {
  if (readOnly) {
    // Matchöversikt – bara siffran, inga knappar. Går inte att registrera i.
    return (
      <div className="flex flex-col items-center rounded-xl border border-gray-300
                      bg-white px-2 py-3">
        <span className="text-[11px] font-medium text-gray-500 text-center mb-1 leading-tight">
          {KIND_LABEL[kind]}
        </span>
        <span className="text-2xl font-bold tabular-nums text-gray-800">{count}</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col">
      <span className="text-[11px] font-medium text-gray-500 text-center mb-1 leading-tight">
        {KIND_LABEL[kind]}
      </span>
      <button
        onClick={onPlus}
        disabled={disabled}
        aria-label={`Lägg till ${KIND_LABEL[kind].toLowerCase()}`}
        className="h-16 rounded-xl bg-tuif-orange text-black
                   hover:brightness-95 active:brightness-90
                   font-bold tabular-nums text-2xl
                   flex items-center justify-center gap-2
                   disabled:opacity-40 disabled:cursor-not-allowed transition
                   select-none touch-manipulation"
      >
        <span className="text-lg font-normal opacity-80">+</span>
        {count}
      </button>
      <button
        onClick={onMinus}
        disabled={disabled || count === 0}
        aria-label={`Ta bort ${KIND_LABEL[kind].toLowerCase()}`}
        className="mt-1 h-7 rounded-lg bg-gray-100 hover:bg-gray-200 active:bg-gray-300
                   text-gray-600 text-sm font-semibold
                   disabled:opacity-40 disabled:cursor-not-allowed transition-colors
                   select-none touch-manipulation"
      >
        −
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// En spelarrad
// ---------------------------------------------------------------------------

function PlayerCard({
  player,
  counts,
  totalCounts,
  malFromIbis,
  onPlus,
  onMinus,
  disabled,
  overview,
}) {
  // "Totalt" är alltid hela matchen, oavsett vald period.
  const tOnGoal = totalCounts.get(`${player.player_id}:on_goal`) || 0
  const tMissed = totalCounts.get(`${player.player_id}:missed`) || 0
  const tBlocked = totalCounts.get(`${player.player_id}:blocked`) || 0

  const malKnown = typeof malFromIbis === 'number'
  const total = (malKnown ? malFromIbis : 0) + tOnGoal + tMissed + tBlocked

  return (
    <div className={`px-3 py-3 ${overview ? 'bg-gray-50' : ''}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="w-7 shrink-0 text-right text-sm text-gray-400 tabular-nums select-none">
          {player.trojnummer != null ? player.trojnummer : ''}
        </span>
        <span className="flex-1 min-w-0 text-sm font-medium text-gray-900 truncate">
          {player.namn}
          {player.malvakt && (
            <span
              className="ml-2 text-[10px] font-bold uppercase tracking-wide
                         text-white bg-black rounded px-1.5 py-0.5 align-middle"
            >
              MV
            </span>
          )}
        </span>

        {/* Målruta – fylls från iBIS, aldrig manuellt */}
        <span className="shrink-0 flex items-center gap-1.5 text-xs">
          <span className="text-gray-400">Mål</span>
          <span
            className={`inline-flex min-w-[1.75rem] justify-center rounded-md border px-1.5 py-0.5
                        font-bold tabular-nums ${
                          malKnown
                            ? 'border-gray-200 bg-gray-50 text-gray-700'
                            : 'border-dashed border-gray-300 bg-gray-50 text-gray-300'
                        }`}
            title="Mål hämtas från iBIS efter matchen"
          >
            {malKnown ? malFromIbis : '–'}
          </span>
        </span>

        {/* Totala skott – räknas fram, knappas aldrig in */}
        <span className="shrink-0 flex items-center gap-1.5 text-xs">
          <span className="text-gray-400">Totalt</span>
          <span className="font-bold tabular-nums text-gray-900">{total}</span>
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {KINDS.map(kind => (
          <CategoryControl
            key={kind}
            kind={kind}
            count={counts.get(`${player.player_id}:${kind}`) || 0}
            onPlus={() => onPlus(player.player_id, kind)}
            onMinus={() => onMinus(player.player_id, kind)}
            disabled={disabled}
            readOnly={overview}
          />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Statusrad – speglar vad synken faktiskt gör (SPEC 6.3 och 11)
// ---------------------------------------------------------------------------

function SyncStatusRow({ syncState, osynkade, online }) {
  let dot = 'bg-amber-500'
  let wrap = 'text-amber-800 bg-amber-50 border-amber-200'
  let text

  const vantar =
    osynkade > 0
      ? ` ${osynkade} ${osynkade === 1 ? 'registrering' : 'registreringar'} väntar.`
      : ''

  if (!online || syncState === 'offline') {
    dot = 'bg-gray-400'
    wrap = 'text-gray-600 bg-gray-50 border-gray-200'
    text =
      'Ingen anslutning. Registreringar sparas lokalt och synkas när nätet är tillbaka.' +
      vantar
  } else if (syncState === 'syncing') {
    dot = 'bg-black animate-pulse'
    wrap = 'text-gray-800 bg-gray-100 border-gray-300'
    text = 'Synkar...'
  } else if (syncState === 'error') {
    dot = 'bg-amber-500'
    wrap = 'text-amber-800 bg-amber-50 border-amber-200'
    text = 'Kunde inte synka mot servern just nu. Försöker igen.' + vantar
  } else if (osynkade === 0) {
    dot = 'bg-green-500'
    wrap = 'text-green-800 bg-green-50 border-green-200'
    text = 'Allt synkat.'
  } else {
    text = `${osynkade} ${
      osynkade === 1 ? 'registrering' : 'registreringar'
    } sparade lokalt, ännu inte synkade.`
  }

  return (
    <div
      className={`flex items-center gap-2 text-xs border rounded-lg px-3 py-2 ${wrap}`}
    >
      <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
      <span>{text}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Registreringsvyn
// ---------------------------------------------------------------------------

export default function ShotRegistration({ match, offlineNotice, onUnauthed }) {
  const matchId = match.match_id
  const trupp = match.trupp ?? []

  const [events, setEvents] = useState(null)
  const [period, setPeriod] = useState(1)
  const [name, setName] = useState(loadName)
  const [loadError, setLoadError] = useState(false)
  const [syncState, setSyncState] = useState('idle') // idle|syncing|synced|offline|error
  const [online, setOnline] = useState(isOnline)
  const syncingRef = useRef(false)
  const eventsReadyRef = useRef(false)
  const runSyncRef = useRef(() => {})

  // Ladda tidigare registreringar för matchen ur IndexedDB
  useEffect(() => {
    let alive = true
    setEvents(null)
    setLoadError(false)
    eventsReadyRef.current = false
    loadEvents(matchId)
      .then(rows => {
        if (alive) {
          setEvents(rows)
          eventsReadyRef.current = true
          runSyncRef.current() // första synken direkt när händelserna finns
        }
      })
      .catch(() => {
        if (alive) setLoadError(true)
      })
    return () => {
      alive = false
    }
  }, [matchId])

  // Läser om händelserna ur IndexedDB efter en synk – plockar upp andra
  // tränares registreringar och synced_at-stämplar.
  const reload = useCallback(async () => {
    try {
      const rows = await loadEvents(matchId)
      setEvents(rows)
    } catch {
      // Läsfel hanteras av den vanliga laddningen; synken rör inte UI:t.
    }
  }, [matchId])

  // Ett synkvarv. Blockerar aldrig ett tryck – anropas fire-and-forget.
  const runSync = useCallback(async () => {
    if (syncingRef.current || !eventsReadyRef.current) return
    if (!isOnline()) {
      setSyncState('offline')
      return
    }
    syncingRef.current = true
    setSyncState('syncing')
    try {
      await syncMatch(matchId)
      await reload()
      setSyncState('synced')
    } catch (err) {
      if (err instanceof SyncError && err.kind === 'unauthed') {
        if (onUnauthed) onUnauthed()
        setSyncState('error')
      } else if (err instanceof SyncError && err.kind === 'network') {
        setSyncState(isOnline() ? 'error' : 'offline')
      } else {
        setSyncState('error')
      }
    } finally {
      syncingRef.current = false
    }
  }, [matchId, reload, onUnauthed])

  runSyncRef.current = runSync

  // Bakgrundssynk: vid öppning, på intervall och när nätet kommer tillbaka.
  useEffect(() => {
    if (loadError) return undefined
    runSync()
    const iv = setInterval(runSync, SYNC_INTERVAL_MS)
    const onOnline = () => {
      setOnline(true)
      runSync()
    }
    const onOffline = () => {
      setOnline(false)
      setSyncState('offline')
    }
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    return () => {
      clearInterval(iv)
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
    }
  }, [runSync, loadError])

  const changePeriod = useCallback(p => setPeriod(p), [])

  const saveName = useCallback(n => {
    try {
      localStorage.setItem(NAME_KEY, n)
    } catch {
      // localStorage blockerad – namnet gäller ändå denna session
    }
    setName(n)
  }, [])

  const handlePlus = useCallback(
    async (playerId, kind) => {
      if (!canRegisterInPeriod(period)) return
      // Optimistiskt: visa direkt, spara i IndexedDB i samma svep
      try {
        const ev = await addEvent({
          matchId,
          playerId,
          kind,
          period,
          createdBy: name,
        })
        setEvents(prev => [...(prev ?? []), ev])
        runSync()
      } catch {
        setLoadError(true)
      }
    },
    [matchId, period, name, runSync],
  )

  const handleMinus = useCallback(
    async (playerId, kind) => {
      if (!canRegisterInPeriod(period)) return
      try {
        const updated = await tombstoneLatest({ matchId, playerId, kind, period })
        if (updated) {
          setEvents(prev =>
            (prev ?? []).map(e => (e.id === updated.id ? updated : e)),
          )
          runSync()
        }
      } catch {
        setLoadError(true)
      }
    },
    [matchId, period, runSync],
  )

  // Motståndarens skott – bara på lagnivå, ingen spelare (SPEC 6.1). Samma
  // periodtaggning, samma local-first lagring och synk som spelarnas.
  const handleOpponentPlus = useCallback(
    async kind => {
      if (!canRegisterInPeriod(period)) return
      try {
        const ev = await addEvent({
          matchId,
          playerId: null,
          kind,
          period,
          createdBy: name,
          side: 'motstandare',
        })
        setEvents(prev => [...(prev ?? []), ev])
        runSync()
      } catch {
        setLoadError(true)
      }
    },
    [matchId, period, name, runSync],
  )

  const handleOpponentMinus = useCallback(
    async kind => {
      if (!canRegisterInPeriod(period)) return
      try {
        const updated = await tombstoneLatest({
          matchId,
          playerId: null,
          kind,
          period,
          side: 'motstandare',
        })
        if (updated) {
          setEvents(prev =>
            (prev ?? []).map(e => (e.id === updated.id ? updated : e)),
          )
          runSync()
        }
      } catch {
        setLoadError(true)
      }
    },
    [matchId, period, runSync],
  )


  const overview = !canRegisterInPeriod(period)

  // Matchhuvud (SPEC 6.2): resultatrad plus lagstatistik som följer vald period,
  // inklusive "Hela matchen". Visas i alla lägen, även innan registreringarna
  // laddats.
  const header = (
    <MatchHeader
      match={match}
      ownShots={countSide(events ?? [], 'egen', period)}
      oppShots={countSide(events ?? [], 'motstandare', period)}
    />
  )

  if (loadError) {
    return (
      <div>
        {header}
        <div className="border border-red-200 bg-red-50 rounded-xl px-4 py-6
                        text-sm text-red-700 text-center">
          Kunde inte läsa den lokala lagringen på den här enheten. Registrering är
          inte möjlig just nu.
        </div>
      </div>
    )
  }

  if (events === null) {
    return (
      <div>
        {header}
        <p className="text-sm text-gray-500 px-4 py-6 text-center">
          Laddar registreringar...
        </p>
      </div>
    )
  }

  const counts = countActive(events, period) // vald period, eller alla i översikten
  const totalCounts = countActive(events) // alltid hela matchen, för "Totalt"
  const oppCounts = countSide(events, 'motstandare', period) // motståndaren, vald period
  const osynkade = unsyncedCount(events)
  const malById = new Map(trupp.map(p => [p.player_id, p.mal]))

  return (
    <div>
      {header}

      <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
        Skottregistrering
      </h3>

      {/* Statusrad: speglar vad synken faktiskt gör */}
      <div className="mb-2 space-y-1.5">
        <SyncStatusRow syncState={syncState} osynkade={osynkade} online={online} />
        {offlineNotice && (
          <div className="flex items-center gap-2 text-xs text-gray-600
                          bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
            <span className="w-2 h-2 rounded-full bg-gray-400 shrink-0" />
            <span>{offlineNotice}</span>
          </div>
        )}
      </div>

      {/* Kortnamn krävs för att registrera */}
      {!name && <NamePrompt current={name} onSave={saveName} />}
      {name && (
        <p className="text-xs text-gray-400 mb-2">
          Registrerar som {name} ·{' '}
          <button
            onClick={() => setName(null)}
            className="underline hover:text-gray-600"
          >
            byt
          </button>
        </p>
      )}

      {/* Periodväljare – taggar registreringen */}
      <div className="mb-2">
        <div className="flex gap-2">
          {PERIODS.map(p => (
            <button
              key={p}
              onClick={() => changePeriod(p)}
              aria-pressed={period === p}
              className={`flex-1 h-12 rounded-xl text-base font-bold transition
                          select-none touch-manipulation border ${
                            period === p
                              ? 'bg-tuif-orange text-black border-transparent'
                              : 'bg-white text-gray-500 border-gray-200 hover:bg-gray-50'
                          }`}
            >
              P{p}
            </button>
          ))}
          {/* Fjärde valet: skrivskyddad översikt över hela matchen */}
          <button
            onClick={() => changePeriod(OVERVIEW)}
            aria-pressed={overview}
            className={`flex-1 h-12 rounded-xl text-xs font-bold transition
                        select-none touch-manipulation border border-dashed ${
                          overview
                            ? 'bg-tuif-orange text-black border-transparent'
                            : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50'
                        }`}
          >
            Hela matchen
          </button>
        </div>

        {overview ? (
          <p className="text-xs text-gray-700 mt-1.5">
            Översikt över hela matchen, skrivskyddad. Välj P1, P2 eller P3 för att
            registrera.
          </p>
        ) : (
          <p className="text-xs text-gray-500 mt-1.5">
            Tryck sparas som{' '}
            <span className="font-semibold text-gray-700">period {period}</span>.
          </p>
        )}
      </div>

      {/* Motståndarens skott – bara på lagnivå (SPEC 6.1). Formgivet som ett
          spelarkort med lagnamnet där spelarnamnet står, och placerat ovanför
          spelarlistan. Samma tre kategorier, knappar, periodtaggning och synk. */}
      <div className={`border rounded-xl bg-white overflow-hidden mt-2 ${
                        overview ? 'border-gray-400' : 'border-gray-200'
                      }`}>
        <div className="px-3 py-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="flex-1 min-w-0 text-sm font-medium text-gray-900 truncate">
              {match.motstandare ?? 'Motståndaren'}
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {KINDS.map(kind => (
              <CategoryControl
                key={kind}
                kind={kind}
                count={oppCounts[kind] || 0}
                onPlus={() => handleOpponentPlus(kind)}
                onMinus={() => handleOpponentMinus(kind)}
                disabled={!name}
                readOnly={overview}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Spelarlista */}
      <div className={`border rounded-xl bg-white divide-y divide-gray-100
                      overflow-hidden mt-4 ${
                        overview ? 'border-gray-400' : 'border-gray-200'
                      }`}>
        {overview && (
          <div className="px-3 py-2 bg-gray-100 text-[11px] font-semibold uppercase
                          tracking-wide text-gray-600">
            Översikt · hela matchen · skrivskyddat
          </div>
        )}
        {trupp.map(p => (
          <PlayerCard
            key={p.player_id}
            player={p}
            counts={counts}
            totalCounts={totalCounts}
            malFromIbis={malById.get(p.player_id)}
            onPlus={handlePlus}
            onMinus={handleMinus}
            disabled={!name}
            overview={overview}
          />
        ))}
      </div>
    </div>
  )
}
