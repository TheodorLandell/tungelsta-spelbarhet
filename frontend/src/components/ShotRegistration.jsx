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
import { matchesPlayerQuery } from '../lib/search'
import { malTal, motstandareMalTal, shotModel } from '../lib/periods'
import { goalkeepersInSquad, goalkeeperStats } from '../lib/goalkeeper'
import MatchHeader from './MatchHeader'
import PlayerSearch from './PlayerSearch'

const PERIODS = [1, 2, 3]
const NAME_KEY = 'tranare_kortnamn'
const SYNC_INTERVAL_MS = 20000

// Vald målvakt sparas per match, så den överlever omladdning och offline.
// Själva attribueringen ligger i händelserna, inte här – det här är bara vilken
// målvakt nästa tryck ska tillskrivas (SPEC 6.8).
const GK_KEY = matchId => `malvakt_match_${matchId}`

function loadGoalkeeper(matchId) {
  try {
    const v = localStorage.getItem(GK_KEY(matchId))
    return v ? Number(v) : null
  } catch {
    return null
  }
}

function saveGoalkeeper(matchId, gkId) {
  try {
    if (gkId == null) localStorage.removeItem(GK_KEY(matchId))
    else localStorage.setItem(GK_KEY(matchId), String(gkId))
  } catch {
    // localStorage blockerad – valet gäller ändå denna session
  }
}

// Vilken målvakt som ska vara vald när vyn öppnas:
//   1. det som sparats lokalt för matchen
//   2. den målvakt som senast registrerade motståndarskott tillskrevs – så en
//      andra tränares enhet hamnar på samma målvakt som redan används
//   3. enda målvakten i truppen, om det bara finns en
function initialGoalkeeper(matchId, malvakter, events) {
  const ids = malvakter.map(p => p.player_id)
  if (!ids.length) return null

  const sparad = loadGoalkeeper(matchId)
  if (sparad != null && ids.includes(sparad)) return sparad

  const senaste = (events ?? [])
    .filter(
      e =>
        !e.deleted_at &&
        (e.side || 'egen') === 'motstandare' &&
        e.goalkeeper_id != null &&
        ids.includes(e.goalkeeper_id),
    )
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .pop()
  if (senaste) return senaste.goalkeeper_id

  return ids.length === 1 ? ids[0] : null
}

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
// Målvaktsväljare (SPEC 6.8)
//
// Sitter vid motståndarens skottblock, eftersom det är de skotten den styr.
// Med bara en målvakt i truppen är han förvald och det finns inget att byta.
// Med flera krävs två tryck för att byta – först "Byt", sedan vem. Det gör
// valet tydligt utan att det går att råka ändra mitt i en registrering.
// ---------------------------------------------------------------------------

function GoalkeeperPicker({ malvakter, vald, onChange, disabled }) {
  const [oppen, setOppen] = useState(false)

  if (!malvakter.length) return null

  const valdSpelare = malvakter.find(p => p.player_id === vald)
  const flera = malvakter.length > 1

  return (
    <div className="mt-2 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-bold uppercase tracking-wide
                         text-white bg-black rounded px-1.5 py-0.5 shrink-0">
          MV
        </span>
        <span className="flex-1 min-w-0 text-sm text-gray-900 truncate">
          {valdSpelare ? (
            <>
              <span className="text-gray-500">I mål: </span>
              <span className="font-semibold">{valdSpelare.namn}</span>
            </>
          ) : (
            <span className="text-gray-500">Ingen målvakt vald</span>
          )}
        </span>
        {flera && (
          <button
            onClick={() => setOppen(o => !o)}
            aria-expanded={oppen}
            className="shrink-0 rounded-lg border border-gray-300 bg-white px-3 py-1
                       text-xs font-semibold text-gray-600 hover:bg-gray-100
                       select-none touch-manipulation transition-colors"
          >
            {oppen ? 'Avbryt' : 'Byt'}
          </button>
        )}
      </div>

      {oppen && (
        <div className="mt-2 space-y-1">
          {malvakter.map(p => (
            <button
              key={p.player_id}
              onClick={() => {
                onChange(p.player_id)
                setOppen(false)
              }}
              disabled={disabled}
              aria-pressed={p.player_id === vald}
              className={`w-full rounded-lg px-3 py-2.5 text-sm font-semibold text-left
                          select-none touch-manipulation border transition
                          disabled:opacity-40 ${
                            p.player_id === vald
                              ? 'bg-tuif-orange text-black border-transparent'
                              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-100'
                          }`}
            >
              {p.trojnummer != null && (
                <span className="text-gray-400 tabular-nums mr-2">
                  {p.trojnummer}
                </span>
              )}
              {p.namn}
            </button>
          ))}
        </div>
      )}

      {!valdSpelare && flera && (
        <p className="mt-1.5 text-xs text-gray-500">
          Välj vem som står innan du registrerar motståndarens skott.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Ett målvaktskort (SPEC 6.8)
//
// Målvakter visar inte på mål, utanför och i täck som utespelarna. De mäts på
// motståndarens skott: skott på mål mot, insläppta, räddningar och
// räddningsprocent.
// ---------------------------------------------------------------------------

function GoalkeeperStat({ label, value }) {
  return (
    <div className="text-center">
      <div className="text-lg font-bold tabular-nums text-gray-900 leading-none">
        {value}
      </div>
      <div className="text-[11px] text-gray-500 mt-1 leading-tight">{label}</div>
    </div>
  )
}

function GoalkeeperCard({ player, stats, iMal, overview }) {
  return (
    <div className={`px-3 py-3 ${overview ? 'bg-gray-50' : ''}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="w-7 shrink-0 text-right text-sm text-gray-400 tabular-nums select-none">
          {player.trojnummer != null ? player.trojnummer : ''}
        </span>
        <span className="flex-1 min-w-0 text-sm font-medium text-gray-900 truncate">
          {player.namn}
          <span
            className="ml-2 text-[10px] font-bold uppercase tracking-wide
                       text-white bg-black rounded px-1.5 py-0.5 align-middle"
          >
            MV
          </span>
          {iMal && (
            <span
              className="ml-1.5 text-[10px] font-bold uppercase tracking-wide
                         text-black bg-tuif-orange rounded px-1.5 py-0.5 align-middle"
            >
              I mål
            </span>
          )}
        </span>
      </div>

      {stats.registrerat ? (
        <>
          <div className="grid grid-cols-4 gap-2 rounded-xl bg-gray-50 py-2">
            <GoalkeeperStat label="På mål mot" value={stats.skottPaMalMot} />
            <GoalkeeperStat label="Insläppta" value={stats.inslappta} />
            <GoalkeeperStat label="Räddningar" value={stats.raddningar} />
            <GoalkeeperStat
              label="Räddn. %"
              value={
                stats.raddningsprocent == null ? '–' : `${stats.raddningsprocent}`
              }
            />
          </div>
          {stats.approximativ && (
            <p className="mt-1.5 text-xs text-gray-500">
              Målvakten byttes under perioden, så insläppta mål är fördelade
              efter vem som mötte flest skott.
            </p>
          )}
        </>
      ) : (
        // Inga registrerade motståndarskott: tomt, inte nollor (SPEC 6.8).
        <p className="text-xs text-gray-400">
          Inga registrerade motståndarskott i den här vyn
        </p>
      )}
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
  period,
  onPlus,
  onMinus,
  disabled,
  overview,
}) {
  // "Totalt" är alltid hela matchen, oavsett vald period (SPEC 6.2). Målen som
  // ingår är därför matchens alla mål, och de räknas in i på mål – aldrig som
  // en egen post ovanpå (SPEC 6.2).
  const total = shotModel(
    {
      on_goal: totalCounts.get(`${player.player_id}:on_goal`) || 0,
      missed: totalCounts.get(`${player.player_id}:missed`) || 0,
      blocked: totalCounts.get(`${player.player_id}:blocked`) || 0,
    },
    player.mal,
  ).totalt

  // Målrutan följer vald period: ett mål räknas bara i den period det gjordes
  // i (SPEC 6.7). Mål vars period är okänd syns bara i "Hela matchen" och
  // markeras med en prick.
  const { varde: malForPeriod, okant: okandPeriod } = malTal(player, period)
  const malKnown = typeof malForPeriod === 'number'

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
            title={
              okandPeriod
                ? 'Spelaren har mål utan periodinformation. De räknas bara i hela matchen.'
                : 'Mål hämtas från iBIS efter matchen'
            }
          >
            {malKnown ? malForPeriod : '–'}
            {okandPeriod && (
              <span className="ml-0.5 font-normal text-gray-400">·</span>
            )}
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

  const malvakter = goalkeepersInSquad(trupp)

  const [events, setEvents] = useState(null)
  const [period, setPeriod] = useState(1)
  const [goalkeeperId, setGoalkeeperId] = useState(null)
  const [sok, setSok] = useState('')
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
          // Målvakten väljs utifrån det som redan registrerats, så två
          // tränares enheter hamnar på samma (SPEC 6.8).
          setGoalkeeperId(initialGoalkeeper(matchId, malvakter, rows))
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

  // Målvaktsbyte. Påverkar bara kommande tryck – redan registrerade skott bär
  // sin målvakt i händelsen och skrivs aldrig om (SPEC 6.8).
  const changeGoalkeeper = useCallback(
    gkId => {
      setGoalkeeperId(gkId)
      saveGoalkeeper(matchId, gkId)
    },
    [matchId],
  )

  // Motståndarens skott – bara på lagnivå, ingen spelare (SPEC 6.1). Samma
  // periodtaggning, samma local-first lagring och synk som spelarnas. Skottet
  // tillskrivs den målvakt som är vald just nu.
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
          goalkeeperId,
        })
        setEvents(prev => [...(prev ?? []), ev])
        runSync()
      } catch {
        setLoadError(true)
      }
    },
    [matchId, period, name, goalkeeperId, runSync],
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
      period={period}
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
  const filteredTrupp = sok.trim()
    ? trupp.filter(p => matchesPlayerQuery(p, sok))
    : trupp

  // Saknas periodinformation för något mål säger vyn det rakt ut i periodläget
  // (SPEC 6.7). Hellre en saknad siffra än en felaktig – men tränaren ska veta
  // att siffran inte är hela sanningen.
  const malSaknarPeriod =
    !overview &&
    (trupp.some(p => malTal(p, period).okant) ||
      malTal(match, period).okant ||
      motstandareMalTal(match, period).okant)

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

        {malSaknarPeriod && (
          <p className="text-xs text-gray-500 mt-1.5">
            Några mål saknar periodinformation i iBIS och räknas bara i hela
            matchen. De är markerade med en prick.
          </p>
        )}
      </div>

      {/* Sökfält – filtrerar bara spelarlistan, motståndarblocket ligger alltid
          kvar synligt (SPEC 6.2) */}
      <PlayerSearch value={sok} onChange={setSok} />

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

          {/* Målvaktsväljaren hör till motståndarens skott – det är dem den
              styr attribueringen av (SPEC 6.8). */}
          <GoalkeeperPicker
            malvakter={malvakter}
            vald={goalkeeperId}
            onChange={changeGoalkeeper}
            disabled={overview}
          />
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
        {sok.trim() && filteredTrupp.length === 0 ? (
          <p className="px-3 py-6 text-sm text-gray-500 text-center">
            Ingen spelare matchar sökningen.
          </p>
        ) : (
          filteredTrupp.map(p =>
            // Målvakter mäts på motståndarens skott, inte på sina egna, och
            // får därför ett eget kort utan plus- och minusknappar (SPEC 6.8).
            p.malvakt ? (
              <GoalkeeperCard
                key={p.player_id}
                player={p}
                stats={goalkeeperStats(events, match, p.player_id, period)}
                iMal={p.player_id === goalkeeperId}
                overview={overview}
              />
            ) : (
              <PlayerCard
                key={p.player_id}
                player={p}
                counts={counts}
                totalCounts={totalCounts}
                period={period}
                onPlus={handlePlus}
                onMinus={handleMinus}
                disabled={!name}
                overview={overview}
              />
            ),
          )
        )}
      </div>
    </div>
  )
}
