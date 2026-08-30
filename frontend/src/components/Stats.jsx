import { useState, useEffect, useCallback } from 'react'
import {
  OMFATTNINGAR,
  DEFAULT_OMFATTNING,
  omfattningById,
  statsQuery,
  beskrivOmfattning,
  skottSegment,
} from '../lib/stats'

const OMF_KEY = 'stats_omfattning'

const SEGMENT_FARG = {
  mal: 'bg-emerald-500',
  pa_mal: 'bg-black',
  utanfor: 'bg-amber-500',
  i_tack: 'bg-gray-400',
}

function loadOmfattning() {
  try {
    const saved = localStorage.getItem(OMF_KEY)
    if (OMFATTNINGAR.some(o => o.id === saved)) return saved
  } catch {
    // localStorage kan vara blockerad – fall tillbaka på standard
  }
  return DEFAULT_OMFATTNING
}

// ---------------------------------------------------------------------------
// Omfattningsväljare – knappar, ingen sifferinmatning (SPEC 7)
// ---------------------------------------------------------------------------

function OmfattningVal({ valt, onChange }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {OMFATTNINGAR.map(o => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          aria-pressed={valt === o.id}
          className={`px-3 py-1.5 rounded-lg text-sm font-semibold transition
                      select-none touch-manipulation border ${
                        valt === o.id
                          ? 'bg-tuif-orange text-black border-transparent'
                          : 'bg-white text-gray-500 border-gray-200 hover:bg-gray-50'
                      }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Skottfördelning – staplad bar plus lista med antal och andel
// ---------------------------------------------------------------------------

function Skottfordelning({ skott }) {
  if (!skott || !skott.registrerat) {
    // Saknad data visas som en markering, aldrig som noll (SPEC 7).
    return (
      <p className="mt-2 text-xs text-gray-400">
        Skott: ingen registrering i den här omfattningen
      </p>
    )
  }

  const segment = skottSegment(skott)

  return (
    <div className="mt-2">
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Skott totalt
        </span>
        <span className="text-sm font-bold tabular-nums text-gray-900">
          {skott.totalt}
        </span>
      </div>

      {skott.totalt > 0 ? (
        <div className="flex h-2.5 rounded-full overflow-hidden bg-gray-100">
          {segment.map(s =>
            s.antal > 0 ? (
              <div
                key={s.key}
                className={SEGMENT_FARG[s.key]}
                style={{ width: `${(s.antal / skott.totalt) * 100}%` }}
                title={`${s.label}: ${s.antal}`}
              />
            ) : null,
          )}
        </div>
      ) : (
        <div className="h-2.5 rounded-full bg-gray-100" />
      )}

      <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
        {segment.map(s => (
          <li key={s.key} className="flex items-center gap-1.5 text-xs">
            <span className={`w-2 h-2 rounded-full shrink-0 ${SEGMENT_FARG[s.key]}`} />
            <span className="text-gray-500">{s.label}</span>
            <span className="ml-auto tabular-nums text-gray-900">
              {s.antal}
              <span className="text-gray-400">
                {' '}
                {s.andel == null ? '(–)' : `(${s.andel} %)`}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// En spelare
// ---------------------------------------------------------------------------

function Nyckeltal({ label, value }) {
  return (
    <div className="text-center">
      <div className="text-lg font-bold tabular-nums text-gray-900 leading-none">
        {value}
      </div>
      <div className="text-[11px] text-gray-500 mt-1 leading-tight">{label}</div>
    </div>
  )
}

function SpelarKort({ spelare }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="w-7 shrink-0 text-right text-sm text-gray-400 tabular-nums select-none">
          {spelare.trojnummer != null ? spelare.trojnummer : ''}
        </span>
        <span className="flex-1 min-w-0 text-sm font-medium text-gray-900 truncate">
          {spelare.namn}
          {spelare.malvakt && (
            <span
              className="ml-2 text-[10px] font-bold uppercase tracking-wide
                         text-white bg-black rounded px-1.5 py-0.5 align-middle"
            >
              MV
            </span>
          )}
        </span>
        <span className="shrink-0 text-xs text-gray-500">
          {spelare.matcher} {spelare.matcher === 1 ? 'match' : 'matcher'}
        </span>
      </div>

      <div className="grid grid-cols-4 gap-2 rounded-xl bg-gray-50 py-2">
        <Nyckeltal label="Mål" value={spelare.mal} />
        <Nyckeltal label="Assist" value={spelare.assist} />
        <Nyckeltal label="Poäng" value={spelare.poang} />
        <Nyckeltal label="Utv. min" value={spelare.utvisningsminuter} />
      </div>

      <Skottfordelning skott={spelare.skott} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Statistikfliken
// ---------------------------------------------------------------------------

export default function StatsTab({ team, onUnauthed }) {
  const [omfId, setOmfId] = useState(loadOmfattning)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const omfattning = omfattningById(omfId)

  const changeOmf = useCallback(id => {
    setOmfId(id)
    try {
      localStorage.setItem(OMF_KEY, id)
    } catch {
      // localStorage blockerad – valet gäller ändå denna session
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/stats?${statsQuery(team, omfattning)}`)
      if (res.status === 401) {
        onUnauthed()
        return
      }
      if (!res.ok) {
        setError('Kunde inte hämta statistiken. Servern svarade med ett fel.')
        return
      }
      setData(await res.json())
    } catch {
      setError('Kunde inte nå servern. Kontrollera nätet och prova igen.')
    } finally {
      setLoading(false)
    }
  }, [team, omfattning, onUnauthed])

  useEffect(() => {
    load()
  }, [load])

  const spelare = data?.spelare ?? []

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 leading-tight mb-1">
        Statistik ({team})
      </h1>
      <p className="text-sm text-gray-500 mb-3">
        {data
          ? beskrivOmfattning(omfattning, data.omfattning.antal_matcher)
          : 'Mål, assist, utvisningar och skott per spelare'}
      </p>

      <div className="mb-4">
        <OmfattningVal valt={omfId} onChange={changeOmf} />
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-xl
                        text-red-700 text-sm">
          {error}
          <button
            onClick={load}
            className="mt-2 block px-3 py-1.5 bg-white border border-red-200 rounded-lg
                       text-sm font-semibold text-red-700 hover:bg-red-50 transition-colors"
          >
            Prova igen
          </button>
        </div>
      )}

      {!error && loading && !data && (
        <p className="text-sm text-gray-500 px-4 py-6 text-center">Laddar...</p>
      )}

      {!error && data && spelare.length === 0 && (
        <p className="text-sm text-gray-500 px-4 py-6 text-center">
          Inga spelade matcher i den här omfattningen än.
        </p>
      )}

      {!error && spelare.length > 0 && (
        <div className={`border border-gray-200 rounded-xl bg-white divide-y
                        divide-gray-100 overflow-hidden ${loading ? 'opacity-60' : ''}`}>
          {spelare.map(s => (
            <SpelarKort key={s.player_id} spelare={s} />
          ))}
        </div>
      )}

      <p className="mt-3 text-xs text-gray-400">
        Skott finns bara för matcher där någon registrerat under matchen. En
        spelare utan registrering visas utan skottsiffror, inte med noll.
      </p>
    </div>
  )
}
