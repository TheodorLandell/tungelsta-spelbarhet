// Ren logik för statistiksidan (SPEC 7). Rendering ligger i components/Stats.jsx;
// det som går att testa utan DOM ligger här och körs med `node --test`.

// Omfattningsvalen visas som knappar, så N kan sättas utan att skriva i ett fält
// (SPEC 7). Varje val mappar till backendens scope/n.
export const OMFATTNINGAR = [
  { id: 'senaste', label: 'Senaste matchen', scope: 'senaste' },
  { id: 'n3', label: 'Senaste 3', scope: 'senaste_n', n: 3 },
  { id: 'n5', label: 'Senaste 5', scope: 'senaste_n', n: 5 },
  { id: 'n10', label: 'Senaste 10', scope: 'senaste_n', n: 10 },
  { id: 'sasong', label: 'Hela säsongen', scope: 'sasong' },
]

export const DEFAULT_OMFATTNING = 'sasong'

export function omfattningById(id) {
  return OMFATTNINGAR.find(o => o.id === id) ?? OMFATTNINGAR[OMFATTNINGAR.length - 1]
}

// Query-sträng till GET /api/stats för ett lag och en omfattning.
export function statsQuery(team, omfattning) {
  const params = new URLSearchParams({ team, scope: omfattning.scope })
  if (omfattning.scope === 'senaste_n') params.set('n', String(omfattning.n))
  return params.toString()
}

// Kort mening om vad omfattningen faktiskt gav, till raden under rubriken.
export function beskrivOmfattning(omfattning, antalMatcher) {
  const m = `${antalMatcher} ${antalMatcher === 1 ? 'match' : 'matcher'}`
  if (omfattning.scope === 'sasong') return `Hela säsongen · ${m}`
  if (omfattning.scope === 'senaste') return `Senaste matchen · ${m}`
  return `Senaste ${omfattning.n} · ${m}`
}

// Segmenten till den staplade skottbaren, i visningsordning. Tar spelarens
// skott-objekt från svaret och returnerar [] om registrering saknas.
export const SKOTT_SEGMENT = [
  { key: 'mal', label: 'Mål' },
  { key: 'pa_mal', label: 'På mål' },
  { key: 'utanfor', label: 'Utanför' },
  { key: 'i_tack', label: 'I täck' },
]

export function skottSegment(skott) {
  if (!skott || !skott.registrerat) return []
  return SKOTT_SEGMENT.map(s => ({
    ...s,
    antal: skott[s.key]?.antal ?? 0,
    andel: skott[s.key]?.andel ?? null,
  }))
}
