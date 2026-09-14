// Tester för perioduppdelning och skottmodell i matchvyn (SPEC 6.2, 6.7).
// Rena funktioner, ingen DOM – körs med `node --test`.

import test from 'node:test'
import assert from 'node:assert/strict'

import { periodTal, malTal, motstandareMalTal, shotModel, OVERVIEW } from './periods.js'

// ---------------------------------------------------------------------------
// Mål räknas bara i den period de gjordes i
// ---------------------------------------------------------------------------

test('mål i period 2 räknas i period 2 och i hela matchen, inte i 1 eller 3', () => {
  const rad = { total: 1, perioder: { 1: 0, 2: 1, 3: 0 }, utanPeriod: 0 }

  assert.equal(periodTal(rad, 1).varde, 0)
  assert.equal(periodTal(rad, 2).varde, 1)
  assert.equal(periodTal(rad, 3).varde, 0)
  assert.equal(periodTal(rad, OVERVIEW).varde, 1)
})

test('periodTal: nycklar som strängar fungerar (JSON)', () => {
  const rad = { total: 3, perioder: { '1': 2, '2': 0, '3': 1 }, utanPeriod: 0 }
  assert.equal(periodTal(rad, 1).varde, 2)
  assert.equal(periodTal(rad, 3).varde, 1)
  assert.equal(periodTal(rad, OVERVIEW).varde, 3)
})

test('periodTal: okänd total ger null, inte noll', () => {
  // Tom målruta före matchen, inte en nolla (SPEC 6.2).
  assert.equal(periodTal({ total: null, perioder: null, utanPeriod: 0 }, 1).varde, null)
  assert.equal(periodTal({ total: undefined }, OVERVIEW).varde, null)
})

test('periodTal: utan periodinformation räknas målen bara i hela matchen', () => {
  const rad = { total: 2, perioder: null, utanPeriod: 2 }

  for (const p of [1, 2, 3]) {
    assert.equal(periodTal(rad, p).varde, 0)
    assert.equal(periodTal(rad, p).okant, true, `period ${p} ska markeras`)
  }
  assert.equal(periodTal(rad, OVERVIEW).varde, 2)
  // I hela matchen är målen med i siffran, så ingen markering behövs.
  assert.equal(periodTal(rad, OVERVIEW).okant, false)
})

test('periodTal: delvis periodinformation markeras i periodvyn', () => {
  // 3 mål, ett känt i period 1, två utan period.
  const rad = { total: 3, perioder: { 1: 1, 2: 0, 3: 0 }, utanPeriod: 2 }

  assert.deepEqual(periodTal(rad, 1), { varde: 1, okant: true })
  assert.deepEqual(periodTal(rad, 2), { varde: 0, okant: true })
  assert.deepEqual(periodTal(rad, OVERVIEW), { varde: 3, okant: false })
})

test('periodTal: allt känt ger ingen markering', () => {
  const rad = { total: 2, perioder: { 1: 1, 2: 1, 3: 0 }, utanPeriod: 0 }
  assert.deepEqual(periodTal(rad, 1), { varde: 1, okant: false })
  assert.deepEqual(periodTal(rad, 3), { varde: 0, okant: false })
})

test('periodTal: rad helt utan periodfält räknas som okänd, inte som noll', () => {
  // Så ser en matchvy ut som cachats offline av en äldre version. Vi vet
  // ingenting om perioderna – då är en markerad nolla ärligare än en tyst.
  const gammal = { total: 2 }
  assert.deepEqual(periodTal(gammal, 1), { varde: 0, okant: true })
  assert.deepEqual(periodTal(gammal, OVERVIEW), { varde: 2, okant: false })

  // Utan mål finns inget att markera.
  assert.deepEqual(periodTal({ total: 0 }, 1), { varde: 0, okant: false })
})

test('utvisningsminuter följer samma regel: två a 2 min ger 4', () => {
  // Joacim Rastas Costell: en utvisning i period 1 och en i period 3.
  const rad = { total: 4, perioder: { 1: 2, 2: 0, 3: 2 }, utanPeriod: 0 }

  assert.equal(periodTal(rad, 1).varde, 2)
  assert.equal(periodTal(rad, 2).varde, 0)
  assert.equal(periodTal(rad, 3).varde, 2)
  assert.equal(periodTal(rad, OVERVIEW).varde, 4)
})

test('malTal och motstandareMalTal läser rätt fält ur en rad', () => {
  const match = {
    mal: 2,
    mal_perioder: { 1: 1, 2: 1, 3: 0 },
    mal_utan_period: 0,
    motstandare_mal: 1,
    motstandare_mal_perioder: { 1: 0, 2: 0, 3: 1 },
    motstandare_mal_utan_period: 0,
  }

  assert.deepEqual(malTal(match, 1), { varde: 1, okant: false })
  assert.deepEqual(motstandareMalTal(match, 1), { varde: 0, okant: false })
  assert.deepEqual(motstandareMalTal(match, 3), { varde: 1, okant: false })
  assert.deepEqual(malTal(match, OVERVIEW), { varde: 2, okant: false })
})

// ---------------------------------------------------------------------------
// Mål är skott på mål
// ---------------------------------------------------------------------------

test('mål ingår i skott på mål och dubbelräknas inte i totalen', () => {
  const m = shotModel({ on_goal: 4, missed: 1, blocked: 1 }, 2)

  assert.equal(m.paMal, 6)      // 4 registrerade + 2 mål
  assert.equal(m.utanfor, 1)
  assert.equal(m.iTack, 1)
  assert.equal(m.totalt, 8)     // inte 10
  assert.equal(m.totalt, m.paMal + m.utanfor + m.iTack)
  assert.equal(m.mal, 2)
})

test('shotModel: okända mål räknas inte in i totalen', () => {
  // Innan iBIS rapporterat är målen null – totalen är då bara skotten.
  const m = shotModel({ on_goal: 3, missed: 2, blocked: 1 }, null)
  assert.equal(m.mal, null)
  assert.equal(m.paMal, 3)
  assert.equal(m.totalt, 6)
})

test('shotModel: noll mål och noll skott ger noll överallt', () => {
  const m = shotModel({ on_goal: 0, missed: 0, blocked: 0 }, 0)
  assert.equal(m.paMal, 0)
  assert.equal(m.totalt, 0)
  assert.equal(m.mal, 0)
})

test('shotModel: mål utan registrerade skott räknas ändå som på mål', () => {
  const m = shotModel({ on_goal: 0, missed: 0, blocked: 0 }, 3)
  assert.equal(m.paMal, 3)
  assert.equal(m.totalt, 3)
})

test('shotModel: saknade skott tolkas som noll', () => {
  const m = shotModel(undefined, 1)
  assert.equal(m.paMal, 1)
  assert.equal(m.totalt, 1)
})
