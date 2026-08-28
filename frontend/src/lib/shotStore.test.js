// Tester för den härledda räknelogiken i shotStore (SPEC 6.2/6.3).
// Rena funktioner, ingen IndexedDB – körs med `node --test`.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  countActive,
  canRegisterInPeriod,
  OVERVIEW,
  reconcileRemote,
  feedRows,
} from './shotStore.js'

let seq = 0
function ev(overrides = {}) {
  seq += 1
  return {
    id: `evt-${seq}`,
    match_id: 1,
    player_id: 10,
    kind: 'on_goal',
    period: 1,
    created_at: new Date(2026, 7, 28, 19, 0, seq).toISOString(),
    created_by: 'Theo',
    deleted_at: null,
    synced_at: null,
    ...overrides,
  }
}

test('periodsiffran speglar vald period, inte totalen', () => {
  // Registrera ett skott på mål i P1
  const events = [ev({ period: 1 })]

  // I P1 syns registreringen
  assert.equal(countActive(events, 1).get('10:on_goal'), 1)

  // Byt till P2 – periodsiffran är 0
  assert.equal(countActive(events, 2).get('10:on_goal') ?? 0, 0)

  // Byt till Hela matchen – siffran är 1 (summan över alla perioder)
  assert.equal(countActive(events, OVERVIEW).get('10:on_goal'), 1)
})

test('registrering är inte möjlig i matchöversikten', () => {
  assert.equal(canRegisterInPeriod(1), true)
  assert.equal(canRegisterInPeriod(2), true)
  assert.equal(canRegisterInPeriod(3), true)
  assert.equal(canRegisterInPeriod(OVERVIEW), false)
})

test('Hela matchen summerar alla perioder per spelare och kategori', () => {
  const events = [
    ev({ period: 1, kind: 'on_goal' }),
    ev({ period: 2, kind: 'on_goal' }),
    ev({ period: 3, kind: 'on_goal' }),
    ev({ period: 2, kind: 'missed' }),
  ]
  const all = countActive(events, OVERVIEW)
  assert.equal(all.get('10:on_goal'), 3)
  assert.equal(all.get('10:missed'), 1)

  // Utan periodargument räknas också alla perioder (bakåtkompatibelt)
  assert.equal(countActive(events).get('10:on_goal'), 3)
})

test('tombstonad händelse räknas inte i någon vy', () => {
  const events = [ev({ period: 1, deleted_at: new Date().toISOString() })]
  assert.equal(countActive(events, 1).get('10:on_goal') ?? 0, 0)
  assert.equal(countActive(events, OVERVIEW).get('10:on_goal') ?? 0, 0)
})

// ---------------------------------------------------------------------------
// reconcileRemote – sammanslagning med serverns händelser (SPEC 6.4)
// ---------------------------------------------------------------------------

test('reconcileRemote: händelse som bara finns på servern skrivs in som synkad', () => {
  const remote = [
    {
      id: 'remote-1',
      match_id: 1,
      player_id: 20,
      kind: 'missed',
      period: '2',
      created_at: '2026-08-28T18:00:00.000Z',
      created_by: 'Anna',
      deleted_at: null,
    },
  ]
  const out = reconcileRemote([], remote, 'STAMP')
  assert.equal(out.length, 1)
  assert.equal(out[0].id, 'remote-1')
  assert.equal(out[0].period, 2) // normaliserad till number
  assert.equal(out[0].synced_at, 'STAMP')
  assert.equal(out[0].created_by, 'Anna')
})

test('reconcileRemote: lokal osynkad händelse som nu finns på servern stämplas synkad', () => {
  const local = [ev({ id: 'a', synced_at: null })]
  const remote = [{ ...ev({ id: 'a' }), deleted_at: null }]
  const out = reconcileRemote(local, remote, 'STAMP')
  assert.equal(out.length, 1)
  assert.equal(out[0].id, 'a')
  assert.equal(out[0].synced_at, 'STAMP')
})

test('reconcileRemote: redan synkad och oförändrad ger ingen skrivning', () => {
  const local = [ev({ id: 'a', synced_at: '2026-08-28T00:00:00.000Z' })]
  const remote = [{ ...ev({ id: 'a' }), deleted_at: null }]
  assert.equal(reconcileRemote(local, remote, 'STAMP').length, 0)
})

test('reconcileRemote: server-tombstone appliceras på lokalt aktiv händelse', () => {
  const local = [ev({ id: 'a', synced_at: 'x', deleted_at: null })]
  const remote = [{ ...ev({ id: 'a' }), deleted_at: '2026-08-28T19:00:00.000Z' }]
  const out = reconcileRemote(local, remote, 'STAMP')
  assert.equal(out.length, 1)
  assert.equal(out[0].deleted_at, '2026-08-28T19:00:00.000Z')
  assert.equal(out[0].synced_at, 'STAMP')
})

test('reconcileRemote: lokal osynkad tombstone som servern inte sett lämnas orörd', () => {
  const local = [
    ev({ id: 'a', synced_at: null, deleted_at: '2026-08-28T19:30:00.000Z' }),
  ]
  const remote = [{ ...ev({ id: 'a' }), deleted_at: null }]
  assert.equal(reconcileRemote(local, remote, 'STAMP').length, 0)
})

// ---------------------------------------------------------------------------
// feedRows – flödesraden (SPEC 6.4)
// ---------------------------------------------------------------------------

test('feedRows: nyast först, med namn, kategori, period och vem som tryckte', () => {
  const names = new Map([
    [10, 'Kalle'],
    [20, 'Olle'],
  ])
  const events = [
    ev({ id: '1', player_id: 10, kind: 'on_goal', period: 1, created_by: 'Theo',
         created_at: '2026-08-28T19:00:00.000Z' }),
    ev({ id: '2', player_id: 20, kind: 'missed', period: 2, created_by: 'Anna',
         created_at: '2026-08-28T19:05:00.000Z' }),
  ]
  const rows = feedRows(events, names)
  assert.equal(rows.length, 2)
  assert.equal(rows[0].id, '2') // nyast först
  assert.equal(rows[0].namn, 'Olle')
  assert.equal(rows[0].kategori, 'Skott utanför')
  assert.equal(rows[0].period, 2)
  assert.equal(rows[0].av, 'Anna')
})

test('feedRows: tombstonade utelämnas, okänt namn får reserv, limit respekteras', () => {
  const events = [
    ev({ id: '1', player_id: 99, created_at: '2026-08-28T19:00:00.000Z' }),
    ev({ id: '2', created_at: '2026-08-28T19:01:00.000Z',
         deleted_at: '2026-08-28T19:02:00.000Z' }),
    ev({ id: '3', player_id: 99, created_by: null,
         created_at: '2026-08-28T19:03:00.000Z' }),
  ]
  const rows = feedRows(events, new Map(), 1)
  assert.equal(rows.length, 1)
  assert.equal(rows[0].id, '3')
  assert.equal(rows[0].namn, 'Spelare 99')
  assert.equal(rows[0].av, 'okänd')
})
