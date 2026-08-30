// Bakgrundssynk av skotthändelser mot servern (SPEC 6.3 och 6.4).
//
// Registreringen fungerar helt utan detta – synken rör aldrig ett tryck.
// Den skickar osynkade händelser, hämtar andra tränares händelser och slår
// ihop allt lokalt via reconcileRemote. Nätfel ger en kort retry; i övrigt
// försöker anroparen igen med jämna mellanrum.

import {
  loadEvents,
  unsyncedEvents,
  markSynced,
  putEvents,
  reconcileRemote,
} from './shotStore'

function path(matchId) {
  return `/api/matches/${matchId}/shot-events`
}

// kind: 'network' (servern svarade inte) | 'http' (servern svarade med fel)
//       | 'unauthed' (utloggad)
export class SyncError extends Error {
  constructor(kind, status) {
    super(`sync ${kind}${status ? ` (${status})` : ''}`)
    this.kind = kind
    this.status = status ?? null
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function toWire(e) {
  return {
    id: e.id,
    player_id: e.player_id ?? null,
    side: e.side ?? 'egen',
    kind: e.kind,
    period: e.period,
    created_at: e.created_at,
    created_by: e.created_by ?? null,
    deleted_at: e.deleted_at ?? null,
  }
}

async function request(url, options) {
  let res
  try {
    res = await fetch(url, options)
  } catch {
    throw new SyncError('network')
  }
  if (res.status === 401) throw new SyncError('unauthed', 401)
  if (!res.ok) throw new SyncError('http', res.status)
  return res
}

async function pushBatch(matchId, events) {
  await request(path(matchId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ handelser: events.map(toWire) }),
  })
}

async function fetchRemote(matchId) {
  const res = await request(path(matchId))
  const data = await res.json()
  return data.handelser ?? []
}

// Ett synkvarv för en match. Kastar SyncError vid fel, annars
// { pushed, merged }. Kan köras om hur ofta som helst – den är idempotent.
export async function syncMatch(matchId, { retries = 2, baseDelay = 500 } = {}) {
  const pending = await unsyncedEvents(matchId)
  let pushed = 0

  if (pending.length) {
    let attempt = 0
    for (;;) {
      try {
        await pushBatch(matchId, pending)
        await markSynced(pending.map(e => e.id))
        pushed = pending.length
        break
      } catch (err) {
        if (err instanceof SyncError && err.kind === 'network' && attempt < retries) {
          attempt += 1
          await sleep(baseDelay * 2 ** (attempt - 1))
          continue
        }
        throw err
      }
    }
  }

  const remote = await fetchRemote(matchId)
  const local = await loadEvents(matchId)
  const toPersist = reconcileRemote(local, remote)
  await putEvents(toPersist)

  return { pushed, merged: toPersist.length }
}
