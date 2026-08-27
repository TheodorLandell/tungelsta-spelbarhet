import { useState, useEffect } from 'react'
import PlayerGroup from './components/PlayerGroup'

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

function Counter({ label, count, colorClass }) {
  return (
    <div className={`rounded-xl p-3 text-center ${colorClass}`}>
      <div className="text-3xl font-bold tabular-nums">{count}</div>
      <div className="text-xs mt-1 leading-tight">{label}</div>
    </div>
  )
}

export default function App() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState(null)

  async function fetchStatus() {
    try {
      const res = await fetch('/api/status')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
      setError(null)
    } catch {
      setError('Kunde inte hämta data. Prova igen.')
    } finally {
      setLoading(false)
    }
  }

  async function handleSync() {
    setSyncing(true)
    setError(null)
    try {
      const res = await fetch('/api/sync', { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await fetchStatus()
    } catch {
      setError('Synken misslyckades. Prova igen.')
    } finally {
      setSyncing(false)
    }
  }

  useEffect(() => { fetchStatus() }, [])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500">
        Laddar...
      </div>
    )
  }

  const r = data?.rakningar ?? {}
  const g = data?.grupper ?? {}
  const warnings = data?.varningar ?? []

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 pb-16">

      {/* Header */}
      <div className="mb-5">
        <h1 className="text-2xl font-bold text-gray-900 leading-tight">
          Tungelsta IF (B)
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
          />
        )}
        <PlayerGroup
          title="Tillgängliga"
          players={g.tillgangliga ?? []}
          variant="available"
        />
        {(g.lasta ?? []).length > 0 && (
          <PlayerGroup
            title="Låsta i A-laget"
            players={g.lasta}
            variant="locked"
          />
        )}
      </div>
    </div>
  )
}
