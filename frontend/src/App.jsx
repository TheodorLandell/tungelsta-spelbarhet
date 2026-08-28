import { useState, useEffect } from 'react'
import PlayerGroup from './components/PlayerGroup'
import MatchesTab from './components/Matches'
import StatsTab from './components/Stats'

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
    <div className="inline-flex rounded-xl border border-gray-300 bg-gray-100 p-1">
      {TEAMS.map(t => (
        <button
          key={t}
          onClick={() => onChange(t)}
          aria-pressed={team === t}
          className={`px-4 py-1.5 rounded-lg text-sm font-semibold transition-colors ${
            team === t
              ? 'bg-white text-gray-900 shadow-sm'
              : 'text-gray-500 hover:text-gray-700'
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
              ? 'border-blue-600 text-blue-700'
              : 'border-transparent text-gray-500 hover:text-gray-700'
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
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-bold text-gray-900 mb-1 text-center">
          Tungelsta IF
        </h1>
        <p className="text-sm text-gray-500 mb-6 text-center">Spelbarhetskoll</p>
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
              className="w-full rounded-xl border border-gray-300 px-4 py-3 text-base
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
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
            className="w-full py-3 bg-blue-600 hover:bg-blue-700 active:bg-blue-800
                       text-white rounded-xl text-sm font-semibold
                       disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
  const [team, setTeam] = useState(loadTeam)
  const [view, setView] = useState('spelbarhet')

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
      const res = await fetch(`/api/status?team=${team}`)
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
      await fetchStatus()
    } catch {
      setError('Synken misslyckades. Prova igen.')
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

  useEffect(() => { fetchStatus() }, [team])

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

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 pb-16">

      {/* Lagväljare – gäller hela appen */}
      <div className="mb-3">
        <TeamSwitcher team={team} onChange={changeTeam} />
      </div>

      {/* Navigering mellan vyerna */}
      <div className="mb-5">
        <ViewNav view={view} onChange={setView} />
      </div>

      {view === 'matcher' && (
        <MatchesTab team={team} onUnauthed={() => setAuthed(false)} />
      )}

      {view === 'statistik' && (
        <StatsTab team={team} onUnauthed={() => setAuthed(false)} />
      )}

      {view === 'spelbarhet' && (
       <>
      {/* Header */}
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 leading-tight">
            Tungelsta IF ({team})
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {data?.senaste_sync
              ? `Senaste synk: ${formatDateTime(data.senaste_sync)}`
              : 'Aldrig synkad'}
          </p>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="mt-3 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 active:bg-blue-800
                       text-white rounded-xl text-sm font-semibold
                       disabled:opacity-50 disabled:cursor-not-allowed
                       transition-colors"
          >
            {syncing ? 'Uppdaterar...' : 'Uppdatera'}
          </button>
        </div>

        <button
          onClick={() => setEditMode(m => !m)}
          className={`shrink-0 mt-1 px-4 py-2.5 rounded-xl text-sm font-semibold
                      transition-colors ${
            editMode
              ? 'bg-green-600 hover:bg-green-700 text-white'
              : 'bg-gray-100 hover:bg-gray-200 text-gray-700 border border-gray-300'
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

      {/* Player groups */}
      <div className="space-y-8">
        {(g.maste_sta_over ?? []).length > 0 && (
          <PlayerGroup
            title="Måste stå över nästa A-match"
            players={g.maste_sta_over}
            variant="must-sit"
            editMode={editMode}
            onOverride={handleOverride}
            onReset={handleReset}
          />
        )}
        <PlayerGroup
          title="Tillgängliga"
          players={g.tillgangliga ?? []}
          variant="available"
          editMode={editMode}
          onOverride={handleOverride}
          onReset={handleReset}
        />
        {(g.lasta ?? []).length > 0 && (
          <PlayerGroup
            title="Låsta i A-laget"
            players={g.lasta}
            variant="locked"
            editMode={editMode}
            onOverride={handleOverride}
            onReset={handleReset}
          />
        )}
      </div>
       </>
      )}
    </div>
  )
}
