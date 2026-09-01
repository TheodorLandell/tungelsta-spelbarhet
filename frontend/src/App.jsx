import { useState, useEffect, useCallback } from 'react'
import PlayerGroup from './components/PlayerGroup'
import PlayerSearch from './components/PlayerSearch'
import MatchesTab from './components/Matches'
import StatsTab from './components/Stats'
import { matchesPlayerQuery } from './lib/search'

function formatDateTime(isoStr) {
  if (!isoStr) return null
  const d = new Date(isoStr)
  return d.toLocaleString('sv-SE', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const TEAMS = ['A', 'B']

function loadTeam() {
  try {
    const saved = localStorage.getItem('lag')
    if (TEAMS.includes(saved)) return saved
  } catch {
    // localStorage kan vara blockerad – fall tillbaka på standard
  }
  return 'B'
}

function TeamSwitcher({ team, onChange }) {
  return (
    <div className="inline-flex rounded-xl bg-gray-100 p-0.5">
      {TEAMS.map(t => (
        <button
          key={t}
          onClick={() => onChange(t)}
          aria-pressed={team === t}
          className={`px-4 py-1.5 rounded-lg text-sm font-semibold transition-colors ${
            team === t
              ? 'bg-tuif-orange text-black'
              : 'text-gray-500 hover:bg-gray-200'
          }`}
        >
          Lag {t}
        </button>
      ))}
    </div>
  )
}

const VIEWS = [
  { id: 'spelbarhet', label: 'Spelbarhet' },
  { id: 'matcher', label: 'Matcher' },
  { id: 'statistik', label: 'Statistik' },
]

function ViewNav({ view, onChange }) {
  return (
    <div className="flex gap-1 border-b border-gray-200">
      {VIEWS.map(v => (
        <button
          key={v.id}
          onClick={() => onChange(v.id)}
          aria-current={view === v.id ? 'page' : undefined}
          className={`px-4 py-2 text-sm font-semibold -mb-px border-b-2 transition-colors ${
            view === v.id
              ? 'border-tuif-orange text-black'
              : 'border-transparent text-gray-500 hover:text-gray-800'
          }`}
        >
          {v.label}
        </button>
      ))}
    </div>
  )
}

function Counter({ label, count, colorClass }) {
  return (
    <div className={`rounded-xl p-3 text-center ${colorClass}`}>
      <div className="text-3xl font-bold tabular-nums">{count}</div>
      <div className="text-xs mt-1 leading-tight">{label}</div>
    </div>
  )
}

// Diskret statusruta i hörnet (SPEC 6.6). Ingen uppkoppling → "Offline",
// serverfel eller felkod → "Error", allt fungerar → ingen ruta alls.
function LiveStatusBadge({ status }) {
  if (status === 'ok') return null
  const offline = status === 'offline'
  return (
    <div
      className={`fixed bottom-2 left-2 z-50 flex items-center gap-1.5 rounded-lg
                  border px-2 py-1 text-[11px] font-medium shadow-sm ${
                    offline
                      ? 'bg-gray-100 border-gray-300 text-gray-600'
                      : 'bg-red-50 border-red-200 text-red-700'
                  }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          offline ? 'bg-gray-400' : 'bg-red-500'
        }`}
      />
      {offline ? 'Offline' : 'Error'}
    </div>
  )
}

function LoginForm({ onLogin }) {
  const [pwd, setPwd] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    const msg = await onLogin(pwd)
    if (msg) setErr(msg)
    setBusy(false)
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-white px-4">
      <div className="w-full max-w-sm">
        <div className="bg-tuif-orange rounded-xl px-4 py-5 mb-6 text-center">
          <h1 className="font-header text-5xl uppercase tracking-wide text-black leading-none">
            Tungelsta IF
          </h1>
          <p className="text-sm text-black/70 mt-1">Spelbarhetskoll</p>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label
              className="block text-sm font-medium text-gray-700 mb-1"
              htmlFor="pwd"
            >
              Lösenord
            </label>
            <input
              id="pwd"
              type="password"
              value={pwd}
              onChange={e => setPwd(e.target.value)}
              autoFocus
              autoComplete="current-password"
              className="w-full rounded-xl border-2 border-gray-300 px-4 py-3 text-base
                         focus:outline-none focus:border-black focus:ring-2 focus:ring-black"
            />
          </div>
          {err && (
            <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-xl
                            text-red-700 text-sm">
              {err}
            </div>
          )}
          <button
            type="submit"
            disabled={busy || !pwd}
            className="w-full py-3 bg-tuif-orange text-black rounded-xl
                       text-sm font-semibold hover:brightness-95 active:brightness-90
                       disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            {busy ? 'Loggar in...' : 'Logga in'}
          </button>
        </form>
      </div>
    </div>
  )
}

export default function App() {
  // null = kontrollerar session, false = ej inloggad, true = inloggad
  const [authed, setAuthed] = useState(null)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState(null)
  const [editMode, setEditMode] = useState(false)
  const [sok, setSok] = useState('')
  const [team, setTeam] = useState(loadTeam)
  const [view, setView] = useState('spelbarhet')
  // true när man är inne i en enskild match – då göms meny och lagväljare,
  // tillbaka-länken i matchvyn räcker. Sätts av MatchesTab.
  const [insideMatch, setInsideMatch] = useState(false)
  // Live-uppdatering av matchresultatet (SPEC 6.6). Pollas överallt i appen,
  // fire-and-forget – rör aldrig skottregistreringen. liveStatus styr
  // statusrutan i hörnet: 'ok' | 'offline' | 'error'.
  const [live, setLive] = useState(null)
  const [liveStatus, setLiveStatus] = useState('ok')

  // Stabil referens – live-pollningen re-renderar App var 60:e sekund, och en
  // ny arrow-funktion här skulle annars trigga omladdning i match- och
  // statistikvyn vid varje puls.
  const handleUnauthed = useCallback(() => setAuthed(false), [])

  function changeTeam(t) {
    setTeam(t)
    try {
      localStorage.setItem('lag', t)
    } catch {
      // localStorage kan vara blockerad – valet gäller ändå denna session
    }
  }

  async function fetchStatus() {
    setLoading(true)
    try {
      // Spelbarhetsvyn är en samlad lista över alla spelare, oberoende av lag.
      const res = await fetch('/api/status')
      if (res.status === 401) {
        setAuthed(false)
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
      setAuthed(true)
      setError(null)
    } catch {
      setError('Kunde inte hämta data. Prova igen.')
    } finally {
      setLoading(false)
    }
  }

  async function handleLogin(password) {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    })
    if (res.ok) {
      setAuthed(true)
      fetchStatus()
      return null
    }
    if (res.status === 401) return 'Fel lösenord. Försök igen.'
    return 'Något gick fel. Försök igen.'
  }

  // Synken körs i bakgrunden på servern. Polla statusendpointen tills den är
  // klar. Ingen kort tidsgräns – knappen ska inte se ut att ha misslyckats
  // bara för att synken tar tid. Efter tio minuter slutar vi ändå vänta och
  // läser in listan med det som hunnit bli klart.
  async function waitForSyncDone() {
    for (let i = 0; i < 300; i++) {
      await new Promise(r => setTimeout(r, 2000))
      let res
      try {
        res = await fetch('/api/sync/status')
      } catch {
        // Nätverksglapp mitt i synken – fortsätt polla.
        continue
      }
      if (res.status === 401) {
        setAuthed(false)
        return
      }
      if (!res.ok) continue
      const s = await res.json()
      if (!s.pagar) return
    }
  }

  async function handleSync() {
    setSyncing(true)
    setError(null)
    try {
      const res = await fetch('/api/sync', { method: 'POST' })
      if (res.status === 401) {
        setAuthed(false)
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      // Endpointen svarar direkt att synken startat (eller att en redan
      // pågår). Vänta tills den blivit klar och uppdatera sedan listan.
      await waitForSyncDone()
      await fetchStatus()
    } catch {
      setError('Kunde inte uppdatera just nu. Prova igen.')
    } finally {
      setSyncing(false)
    }
  }

  async function handleOverride(playerId, kind, value, note) {
    try {
      const res = await fetch('/api/overrides', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player_id: playerId, kind, value, note }),
      })
      if (res.status === 401) { setAuthed(false); return }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await fetchStatus()
    } catch {
      setError('Kunde inte spara ändringen. Prova igen.')
    }
  }

  async function handleReset(playerId) {
    try {
      const res = await fetch(`/api/overrides/${playerId}`, { method: 'DELETE' })
      if (res.status === 401) { setAuthed(false); return }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await fetchStatus()
    } catch {
      setError('Kunde inte återställa. Prova igen.')
    }
  }

  useEffect(() => { fetchStatus() }, [])

  // Bakgrundspollning av live-resultatet var 60:e sekund. Fristående från
  // skottsynken: den läser aldrig IndexedDB och blockerar aldrig ett tryck.
  // Uppdaterar bara resultatraden och lagens skottotaler i matchvyn –
  // spelbarhets- och statistikvyn rörs inte.
  useEffect(() => {
    if (authed !== true) return undefined
    let alive = true

    async function poll() {
      if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        if (alive) setLiveStatus('offline')
        return
      }
      try {
        const res = await fetch('/api/live')
        if (!alive) return
        if (res.status === 401) {
          setAuthed(false)
          return
        }
        if (!res.ok) {
          setLiveStatus('error')
          return
        }
        const data = await res.json()
        if (!alive) return
        setLive(data)
        setLiveStatus('ok')
      } catch {
        if (alive) setLiveStatus('offline')
      }
    }

    poll()
    const iv = setInterval(poll, 60000)
    const onOnline = () => poll()
    window.addEventListener('online', onOnline)
    return () => {
      alive = false
      clearInterval(iv)
      window.removeEventListener('online', onOnline)
    }
  }, [authed])

  if (authed === null || (authed === true && loading && !data)) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500">
        Laddar...
      </div>
    )
  }

  if (authed === false) {
    return <LoginForm onLogin={handleLogin} />
  }

  const r = data?.rakningar ?? {}
  const g = data?.grupper ?? {}
  const warnings = data?.varningar ?? []
  const antalSpelare =
    (r.maste_sta_over ?? 0) + (r.tillgangliga ?? 0) + (r.lasta ?? 0)

  // Sökfältet filtrerar grupperingen men rör aldrig räknarna högst upp (SPEC 5).
  const sokQuery = sok.trim()
  const isSearching = sokQuery.length > 0
  const filterPlayers = players =>
    isSearching ? players.filter(p => matchesPlayerQuery(p, sokQuery)) : players
  const mastStaOverList = filterPlayers(g.maste_sta_over ?? [])
  const tillgangligaList = filterPlayers(g.tillgangliga ?? [])
  const lastaList = filterPlayers(g.lasta ?? [])
  const totalTraffar =
    mastStaOverList.length + tillgangligaList.length + lastaList.length

  // Lagväljaren hör hemma i matchlistan och statistiken. Menyn göms när man är
  // inne i en match – tillbaka-länken räcker där.
  const visaLagvaljare =
    (view === 'matcher' && !insideMatch) || view === 'statistik'

  return (
    <div className="min-h-screen bg-white">
      <LiveStatusBadge status={liveStatus} />

      {/* Klubbheader – tjockt orange band, klubbnamnet centrerat i svart, i en
          kondenserad fetstilt sans (bara här, inte i brödtext). */}
      <header className="bg-tuif-orange">
        <div className="max-w-2xl mx-auto px-4 py-5 text-center">
          <span className="font-header text-5xl sm:text-6xl uppercase tracking-wide text-black leading-none">
            Tungelsta IF
          </span>
        </div>
      </header>

      <div className="max-w-2xl mx-auto px-4 py-6 pb-16">

      {visaLagvaljare && (
        <div className="mb-3">
          <TeamSwitcher team={team} onChange={changeTeam} />
        </div>
      )}

      {/* Navigering mellan vyerna – göms inne i en match */}
      {!insideMatch && (
        <div className="mb-5">
          <ViewNav view={view} onChange={setView} />
        </div>
      )}

      {view === 'matcher' && (
        <MatchesTab
          team={team}
          live={live}
          onUnauthed={handleUnauthed}
          onInsideMatchChange={setInsideMatch}
        />
      )}

      {view === 'statistik' && (
        <StatsTab team={team} onUnauthed={handleUnauthed} />
      )}

      {view === 'spelbarhet' && (
       <>
      {/* Header */}
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 leading-tight">
            Spelbarhet
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {data?.senaste_sync
              ? `Senaste synk: ${formatDateTime(data.senaste_sync)}`
              : 'Aldrig synkad'}
          </p>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="mt-3 px-5 py-2.5 bg-tuif-orange text-black
                       hover:brightness-95 active:brightness-90
                       rounded-xl text-sm font-semibold
                       disabled:opacity-50 disabled:cursor-not-allowed
                       transition"
          >
            {syncing ? 'Uppdaterar...' : 'Uppdatera'}
          </button>
        </div>

        <button
          onClick={() => setEditMode(m => !m)}
          aria-pressed={editMode}
          className={`shrink-0 mt-1 px-4 py-2.5 rounded-xl text-sm font-semibold
                      transition ${
            editMode
              ? 'bg-black text-white'
              : 'bg-tuif-orange text-black hover:brightness-95 active:brightness-90'
          }`}
        >
          {editMode ? 'Klar' : 'Redigera'}
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mb-4 px-4 py-3 bg-red-50 border border-red-200 rounded-xl
                        text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* Counters */}
      <div className="grid grid-cols-3 gap-3 mb-6">
        <Counter
          label="Måste stå över"
          count={r.maste_sta_over ?? 0}
          colorClass="bg-amber-50 text-amber-900"
        />
        <Counter
          label="Tillgängliga"
          count={r.tillgangliga ?? 0}
          colorClass="bg-green-50 text-green-900"
        />
        <Counter
          label="Låsta"
          count={r.lasta ?? 0}
          colorClass="bg-red-50 text-red-900"
        />
      </div>

      {/* Warnings */}
      {warnings.length > 0 && (
        <div className="mb-6">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-amber-700 mb-2">
            Varningar
          </h2>
          <ul className="space-y-1.5">
            {warnings.map((w, i) => (
              <li
                key={i}
                className="text-sm text-amber-900 bg-amber-50 border border-amber-200
                           rounded-lg px-3 py-2"
              >
                {w}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Tom lista innan säsongen har börjat – förklara varför, se inte trasig ut.
          Bara när servern faktiskt svarat (data satt) och det inte är ett fel. */}
      {data && !error && antalSpelare === 0 && (
        <div className="border border-gray-200 bg-white rounded-xl px-4 py-8 text-center">
          <p className="text-sm text-gray-500">
            {data.senaste_sync
              ? 'Ingen spelare har stått i truppen i en spelad seriematch än. Listan fylls när säsongen har börjat.'
              : 'Ingen synk har gjorts än. Tryck Uppdatera för att hämta lag och matcher från iBIS.'}
          </p>
        </div>
      )}

      {/* Sökfält – mellan räknarna och spelarlistan (SPEC 5) */}
      {antalSpelare > 0 && (
        <PlayerSearch value={sok} onChange={setSok} />
      )}

      {/* Player groups */}
      {antalSpelare > 0 && (
        isSearching && totalTraffar === 0 ? (
          <p className="text-sm text-gray-500 text-center py-8">
            Ingen spelare matchar sökningen.
          </p>
        ) : (
          <div className="space-y-8">
            {mastStaOverList.length > 0 && (
              <PlayerGroup
                title="Måste stå över nästa A-match"
                players={mastStaOverList}
                variant="must-sit"
                editMode={editMode}
                onOverride={handleOverride}
                onReset={handleReset}
              />
            )}
            {(!isSearching || tillgangligaList.length > 0) && (
              <PlayerGroup
                title="Tillgängliga"
                players={tillgangligaList}
                variant="available"
                editMode={editMode}
                onOverride={handleOverride}
                onReset={handleReset}
              />
            )}
            {lastaList.length > 0 && (
              <PlayerGroup
                title="Låsta i A-laget"
                players={lastaList}
                variant="locked"
                editMode={editMode}
                onOverride={handleOverride}
                onReset={handleReset}
              />
            )}
          </div>
        )
      )}
       </>
      )}
      </div>
    </div>
  )
}
