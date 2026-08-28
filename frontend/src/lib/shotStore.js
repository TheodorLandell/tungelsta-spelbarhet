// Local-first lagring för skottregistrering (SPEC 6.3 och 6.4).
//
// Allt sparas i IndexedDB innan det visas i UI. Varje händelse får ett
// klient-UUID så att en omsynk aldrig ger dubbletter. Borttagning är en
// tombstone (deleted_at), aldrig en radering.
//
// synced_at markerar att servern har tagit emot händelsen. Bakgrundssynken
// (shotSync.js) skickar osynkade händelser, hämtar andra tränares händelser
// och slår ihop allt med reconcileRemote nedan.

const DB_NAME = 'tungelsta-spelbarhet'
const DB_VERSION = 1

const STORE_EVENTS = 'shot_events'
const STORE_SQUADS = 'match_squads'

export const KINDS = ['on_goal', 'missed', 'blocked']

export const KIND_LABEL = {
  on_goal: 'Skott på mål',
  missed: 'Skott utanför',
  blocked: 'Skott i täck',
}

// Fjärde valet i periodväljaren: en skrivskyddad översikt som summerar alla
// perioder. Standardvalet är fortfarande en riktig period, aldrig den här.
export const OVERVIEW = 'all'

let _dbPromise = null

function openDb() {
  if (_dbPromise) return _dbPromise

  _dbPromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB saknas i den här webbläsaren'))
      return
    }

    const req = indexedDB.open(DB_NAME, DB_VERSION)

    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_EVENTS)) {
        const events = db.createObjectStore(STORE_EVENTS, { keyPath: 'id' })
        events.createIndex('match_id', 'match_id', { unique: false })
      }
      if (!db.objectStoreNames.contains(STORE_SQUADS)) {
        db.createObjectStore(STORE_SQUADS, { keyPath: 'match_id' })
      }
    }

    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })

  return _dbPromise
}

function tx(db, store, mode) {
  return db.transaction(store, mode).objectStore(store)
}

function toPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function newId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  // Reserv om crypto.randomUUID saknas (äldre webbvy).
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

// ---------------------------------------------------------------------------
// Skotthändelser
// ---------------------------------------------------------------------------

export async function loadEvents(matchId) {
  const db = await openDb()
  const idx = tx(db, STORE_EVENTS, 'readonly').index('match_id')
  const rows = await toPromise(idx.getAll(matchId))
  return rows.sort((a, b) => a.created_at.localeCompare(b.created_at))
}

export async function addEvent({ matchId, playerId, kind, period, createdBy }) {
  if (!KINDS.includes(kind)) throw new Error(`Okänd kategori: ${kind}`)
  const now = new Date().toISOString()
  const event = {
    id: newId(),
    match_id: matchId,
    player_id: playerId,
    kind,
    period,
    created_at: now,
    created_by: createdBy || null,
    deleted_at: null,
    synced_at: null, // fylls i av synken i steg 14
  }
  const db = await openDb()
  const store = tx(db, STORE_EVENTS, 'readwrite')
  await toPromise(store.add(event))
  return event
}

// Borttagning = tombstone på den senaste aktiva händelsen för
// (spelare, kategori, period). Ingen rad raderas.
export async function tombstoneLatest({ matchId, playerId, kind, period }) {
  const events = await loadEvents(matchId)
  const active = events
    .filter(
      e =>
        e.player_id === playerId &&
        e.kind === kind &&
        e.period === period &&
        !e.deleted_at,
    )
    .sort((a, b) => a.created_at.localeCompare(b.created_at))

  const target = active[active.length - 1]
  if (!target) return null

  const updated = { ...target, deleted_at: new Date().toISOString(), synced_at: null }
  const db = await openDb()
  const store = tx(db, STORE_EVENTS, 'readwrite')
  await toPromise(store.put(updated))
  return updated
}

// Ångrar den allra senaste aktiva registreringen i matchen, oavsett spelare,
// kategori och period. Driver den kompakta ångra-knappen i registreringsvyn.
// Som all borttagning: en tombstone, ingen rad raderas.
export async function tombstoneMostRecent(matchId) {
  const events = await loadEvents(matchId)
  const active = events
    .filter(e => !e.deleted_at)
    .sort((a, b) => a.created_at.localeCompare(b.created_at))

  const target = active[active.length - 1]
  if (!target) return null

  const updated = { ...target, deleted_at: new Date().toISOString(), synced_at: null }
  const db = await openDb()
  const store = tx(db, STORE_EVENTS, 'readwrite')
  await toPromise(store.put(updated))
  return updated
}

// Osynkade händelser (synced_at saknas) för en match.
export async function unsyncedEvents(matchId) {
  const events = await loadEvents(matchId)
  return events.filter(e => !e.synced_at)
}

// Stämplar en uppsättning händelse-id som synkade. En transaktion.
export async function markSynced(ids, stamp = new Date().toISOString()) {
  if (!ids.length) return
  const db = await openDb()
  const store = tx(db, STORE_EVENTS, 'readwrite')
  for (const id of ids) {
    const row = await toPromise(store.get(id))
    if (row && !row.synced_at) {
      await toPromise(store.put({ ...row, synced_at: stamp }))
    }
  }
}

// Skriver en lista färdiga händelseobjekt (från reconcileRemote). En transaktion.
export async function putEvents(rows) {
  if (!rows.length) return
  const db = await openDb()
  const store = tx(db, STORE_EVENTS, 'readwrite')
  for (const row of rows) {
    await toPromise(store.put(row))
  }
}

// ---------------------------------------------------------------------------
// Cachad trupp – så registreringen fungerar offline efter första öppningen
// ---------------------------------------------------------------------------

export async function cacheSquad(matchId, detail) {
  const db = await openDb()
  const store = tx(db, STORE_SQUADS, 'readwrite')
  await toPromise(
    store.put({ match_id: matchId, cached_at: new Date().toISOString(), detail }),
  )
}

export async function loadCachedSquad(matchId) {
  const db = await openDb()
  const store = tx(db, STORE_SQUADS, 'readonly')
  const row = await toPromise(store.get(matchId))
  return row || null
}

// ---------------------------------------------------------------------------
// Härledda värden
// ---------------------------------------------------------------------------

// Räknar aktiva (ej tombstonade) händelser per spelare och kategori.
// Nyckel: `${player_id}:${kind}` -> antal.
//
// Med period 1, 2 eller 3 räknas bara den periodens händelser, så siffran på
// plusknappen alltid speglar vald period. Utan period, eller med OVERVIEW,
// räknas alla perioder – det är matchöversikten.
export function countActive(events, period) {
  const perPeriod = period === 1 || period === 2 || period === 3
  const counts = new Map()
  for (const e of events) {
    if (e.deleted_at) continue
    if (perPeriod && e.period !== period) continue
    const key = `${e.player_id}:${e.kind}`
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return counts
}

// Registrering är bara möjlig i en enskild period. Matchöversikten (OVERVIEW)
// är skrivskyddad.
export function canRegisterInPeriod(period) {
  return period === 1 || period === 2 || period === 3
}

export function unsyncedCount(events) {
  return events.filter(e => !e.synced_at).length
}

// ---------------------------------------------------------------------------
// Sammanslagning av serverns händelser med de lokala (SPEC 6.4)
// ---------------------------------------------------------------------------

// Ren funktion: tar de lokala händelserna och det servern svarade med, och
// returnerar de rader som behöver skrivas till IndexedDB.
//
//  - Händelse som bara finns på servern (annan tränare) skrivs in, synkad.
//  - Lokal händelse som nu finns på servern med samma tillstånd stämplas synkad.
//  - Server-tombstone appliceras på en lokalt fortfarande aktiv händelse.
//  - Lokal osynkad tombstone som servern ännu inte sett lämnas orörd – den
//    skickas i nästa runda.
export function reconcileRemote(local, remote, stamp = new Date().toISOString()) {
  const byId = new Map(local.map(e => [e.id, e]))
  const out = []

  for (const r of remote) {
    const l = byId.get(r.id)

    if (!l) {
      out.push({
        id: r.id,
        match_id: r.match_id,
        player_id: r.player_id,
        kind: r.kind,
        period: Number(r.period),
        created_at: r.created_at,
        created_by: r.created_by ?? null,
        deleted_at: r.deleted_at ?? null,
        synced_at: stamp,
      })
      continue
    }

    const remoteDeleted = Boolean(r.deleted_at)
    const localDeleted = Boolean(l.deleted_at)

    if (remoteDeleted && !localDeleted) {
      out.push({ ...l, deleted_at: r.deleted_at, synced_at: stamp })
    } else if (!l.synced_at && remoteDeleted === localDeleted) {
      out.push({ ...l, synced_at: stamp })
    }
  }

  return out
}

// ---------------------------------------------------------------------------
// Flödesrad (SPEC 6.4): de senaste registreringarna, nyast först
// ---------------------------------------------------------------------------

export function feedRows(events, namesById, limit = 12) {
  return events
    .filter(e => !e.deleted_at)
    .slice()
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, limit)
    .map(e => ({
      id: e.id,
      namn:
        (namesById && namesById.get(e.player_id)) || `Spelare ${e.player_id}`,
      kategori: KIND_LABEL[e.kind] || e.kind,
      period: e.period,
      av: e.created_by || 'okänd',
      tid: e.created_at,
    }))
}
