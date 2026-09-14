// Tester för målvaktsstatistik i matchvyn (SPEC 6.8).
// Rena funktioner, ingen DOM – körs med `node --test`.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  attributeConceded,
  goalkeeperStats,
  goalkeepersInSquad,
  opponentEventsByPeriod,
  registeredOnGoalAgainst,
  saveStats,
} from './goalkeeper.js'
import { OVERVIEW } from './shotStore.js'

let n = 0

function oppShot(kind, period, gkId, { deleted = false } = {}) {
  n += 1
  return {
    id: `o${n}`,
    match_id: 1,
    player_id: null,
    goalkeeper_id: gkId,
    side: 'motstandare',
    kind,
    period,
    created_at: new Date(2026, 8, 1, 19, 30, n).toISOString(),
    created_by: 'Theo',
    deleted_at: deleted ? new Date().toISOString() : null,
  }
}

function ownShot(kind, period, playerId) {
  n += 1
  return {
    id: `e${n}`,
    match_id: 1,
    player_id: playerId,
    goalkeeper_id: null,
    side: 'egen',
    kind,
    period,
    created_at: new Date(2026, 8, 1, 19, 30, n).toISOString(),
    created_by: 'Theo',
    deleted_at: null,
  }
}

// Motståndaren gör 1 mål i period 1 och 2 i period 3.
const MATCH = {
  motstandare_mal: 3,
  motstandare_mal_perioder: { 1: 1, 2: 0, 3: 2 },
  motstandare_mal_utan_period: 0,
}

// ---------------------------------------------------------------------------
// Trupp och gruppering
// ---------------------------------------------------------------------------

test('goalkeepersInSquad: plockar ut målvakterna', () => {
  const trupp = [
    { player_id: 10, namn: 'Målvakt A', malvakt: true },
    { player_id: 30, namn: 'Utespelare', malvakt: false },
    { player_id: 20, namn: 'Målvakt B', malvakt: true },
  ]
  assert.deepEqual(goalkeepersInSquad(trupp).map(p => p.player_id), [10, 20])
  assert.deepEqual(goalkeepersInSquad([]), [])
  assert.deepEqual(goalkeepersInSquad(undefined), [])
})

test('opponentEventsByPeriod: bara motståndarens aktiva skott med målvakt', () => {
  const events = [
    oppShot('on_goal', 1, 10),
    oppShot('missed', 1, 10),
    ownShot('on_goal', 1, 30),                    // eget skott
    oppShot('on_goal', 1, null),                  // saknar målvakt
    oppShot('on_goal', 2, 20, { deleted: true }), // tombstonad
    oppShot('on_goal', 3, 20),
  ]
  const per = opponentEventsByPeriod(events)
  assert.equal(per.get(1).get(10), 2)
  assert.equal(per.has(2), false)
  assert.equal(per.get(3).get(20), 1)
})

// ---------------------------------------------------------------------------
// Attribuering av insläppta mål
// ---------------------------------------------------------------------------

test('en målvakt hela matchen får alla insläppta mål', () => {
  const per = opponentEventsByPeriod([
    oppShot('on_goal', 1, 10),
    oppShot('on_goal', 3, 10),
  ])
  const { perGk, oattribuerat, approximativ } = attributeConceded(
    per, MATCH.motstandare_mal_perioder, 0,
  )
  assert.equal(perGk.get(10), 3)
  assert.equal(oattribuerat, 0)
  assert.equal(approximativ, false)
})

test('byte mellan perioder delar målen utan att bli approximativt', () => {
  const per = opponentEventsByPeriod([
    oppShot('on_goal', 1, 10),
    oppShot('on_goal', 3, 20),
  ])
  const { perGk, approximativ } = attributeConceded(
    per, MATCH.motstandare_mal_perioder, 0,
  )
  assert.equal(perGk.get(10), 1)
  assert.equal(perGk.get(20), 2)
  assert.equal(approximativ, false)
})

test('byte mitt i en period ger målen till den som mötte flest skott', () => {
  const per = opponentEventsByPeriod([
    oppShot('on_goal', 3, 10),
    oppShot('on_goal', 3, 20),
    oppShot('on_goal', 3, 20),
    oppShot('missed', 3, 20),
  ])
  const { perGk, approximativ } = attributeConceded(per, { 3: 2 }, 0)
  assert.equal(perGk.get(20), 2)
  assert.equal(perGk.has(10), false)
  assert.equal(approximativ, true)
})

test('period utan registrering lämnar målen oattribuerade', () => {
  const per = opponentEventsByPeriod([oppShot('on_goal', 1, 10)])
  const { perGk, oattribuerat } = attributeConceded(
    per, MATCH.motstandare_mal_perioder, 0,
  )
  assert.equal(perGk.get(10), 1)
  assert.equal(oattribuerat, 2) // period 3 saknar registrering
})

test('mål utan period är oattribuerade', () => {
  const per = opponentEventsByPeriod([oppShot('on_goal', 1, 10)])
  const { oattribuerat } = attributeConceded(per, { 1: 1 }, 4)
  assert.equal(oattribuerat, 4)
})

test('lika många skott ger samma ägare oavsett ordning', () => {
  const a = attributeConceded(
    opponentEventsByPeriod([oppShot('on_goal', 2, 10), oppShot('on_goal', 2, 20)]),
    { 2: 1 }, 0,
  )
  const b = attributeConceded(
    opponentEventsByPeriod([oppShot('on_goal', 2, 20), oppShot('on_goal', 2, 10)]),
    { 2: 1 }, 0,
  )
  assert.equal(a.perGk.get(10), 1)
  assert.equal(b.perGk.get(10), 1)
})

// ---------------------------------------------------------------------------
// De fyra talen
// ---------------------------------------------------------------------------

test('räddningsprocenten räknas rätt', () => {
  const s = saveStats(8, 2)
  assert.equal(s.skottPaMalMot, 10)
  assert.equal(s.inslappta, 2)
  assert.equal(s.raddningar, 8)
  assert.equal(s.raddningsprocent, 80)
})

test('räddningsprocenten kraschar inte vid noll skott', () => {
  const s = saveStats(0, 0)
  assert.equal(s.skottPaMalMot, 0)
  assert.equal(s.raddningar, 0)
  assert.equal(s.raddningsprocent, null)
})

test('räddningar kan aldrig bli negativa', () => {
  for (let reg = 0; reg <= 5; reg += 1) {
    for (let mal = 0; mal <= 5; mal += 1) {
      const s = saveStats(reg, mal)
      assert.ok(s.raddningar >= 0)
      assert.equal(s.raddningar, s.skottPaMalMot - s.inslappta)
    }
  }
})

test('registeredOnGoalAgainst: räknar bara rätt målvakt och kategori', () => {
  const events = [
    oppShot('on_goal', 1, 10),
    oppShot('on_goal', 1, 10),
    oppShot('missed', 1, 10),
    oppShot('on_goal', 1, 20),
    oppShot('on_goal', 2, 10),
  ]
  assert.equal(registeredOnGoalAgainst(events, 10, 1), 2)
  assert.equal(registeredOnGoalAgainst(events, 20, 1), 1)
  assert.equal(registeredOnGoalAgainst(events, 10, OVERVIEW), 3)
})

// ---------------------------------------------------------------------------
// Hela målvaktsvyn
// ---------------------------------------------------------------------------

test('ett motståndarskott registrerat med målvakt A tilldelas målvakt A', () => {
  const events = [oppShot('on_goal', 1, 10)]
  const a = goalkeeperStats(events, MATCH, 10, 1)
  const b = goalkeeperStats(events, MATCH, 20, 1)

  assert.equal(a.registrerat, true)
  assert.equal(a.raddningar, 1)
  // Målvakt B mötte inget – tomt, inte nollor.
  assert.deepEqual(b, { registrerat: false })
})

test('byte av målvakt mitt i matchen lägger efterföljande skott på den nya', () => {
  // A står period 1, B tar över i period 3.
  const events = [
    oppShot('on_goal', 1, 10),
    oppShot('on_goal', 1, 10),
    oppShot('on_goal', 3, 20),
    oppShot('on_goal', 3, 20),
    oppShot('on_goal', 3, 20),
  ]

  const a = goalkeeperStats(events, MATCH, 10, OVERVIEW)
  const b = goalkeeperStats(events, MATCH, 20, OVERVIEW)

  // A: 2 räddningar + 1 insläppt i period 1
  assert.equal(a.raddningar, 2)
  assert.equal(a.inslappta, 1)
  assert.equal(a.skottPaMalMot, 3)
  assert.equal(a.raddningsprocent, 67)

  // B: 3 räddningar + 2 insläppta i period 3
  assert.equal(b.raddningar, 3)
  assert.equal(b.inslappta, 2)
  assert.equal(b.skottPaMalMot, 5)
  assert.equal(b.raddningsprocent, 60)
})

test('siffrorna följer vald period', () => {
  const events = [
    oppShot('on_goal', 1, 10),
    oppShot('on_goal', 1, 10),
    oppShot('on_goal', 3, 20),
  ]

  // Period 1: bara A har siffror.
  assert.equal(goalkeeperStats(events, MATCH, 10, 1).raddningar, 2)
  assert.equal(goalkeeperStats(events, MATCH, 10, 1).inslappta, 1)
  assert.deepEqual(goalkeeperStats(events, MATCH, 20, 1), { registrerat: false })

  // Period 3: bara B.
  assert.deepEqual(goalkeeperStats(events, MATCH, 10, 3), { registrerat: false })
  assert.equal(goalkeeperStats(events, MATCH, 20, 3).inslappta, 2)
})

test('en målvakt utan registrerade motståndarskott visar tomt', () => {
  assert.deepEqual(goalkeeperStats([], MATCH, 10, 1), { registrerat: false })
  assert.deepEqual(goalkeeperStats([], MATCH, 10, OVERVIEW), { registrerat: false })
  // Egna skott räknas inte som motståndarskott.
  assert.deepEqual(
    goalkeeperStats([ownShot('on_goal', 1, 30)], MATCH, 10, 1),
    { registrerat: false },
  )
  // Utan vald målvakt finns inget att visa.
  assert.deepEqual(goalkeeperStats([oppShot('on_goal', 1, 10)], MATCH, null, 1),
    { registrerat: false })
})

test('skott utan målvakt räknas inte in', () => {
  // Registrerade innan målvaktsvalet fanns.
  const events = [oppShot('on_goal', 1, null), oppShot('on_goal', 1, null)]
  assert.deepEqual(goalkeeperStats(events, MATCH, 10, 1), { registrerat: false })
})

test('bara utanför och i täck ger noll på mål men är registrerat', () => {
  const events = [oppShot('missed', 2, 10), oppShot('blocked', 2, 10)]
  const s = goalkeeperStats(events, MATCH, 10, 2)

  assert.equal(s.registrerat, true)
  assert.equal(s.skottPaMalMot, 0)
  assert.equal(s.raddningar, 0)
  assert.equal(s.raddningsprocent, null)
})

test('byte mitt i en period markeras som approximativt', () => {
  const events = [
    oppShot('on_goal', 3, 10),
    oppShot('on_goal', 3, 20),
    oppShot('on_goal', 3, 20),
  ]
  const b = goalkeeperStats(events, MATCH, 20, 3)
  assert.equal(b.inslappta, 2)
  assert.equal(b.approximativ, true)

  // A ägde inte perioden och får inga insläppta.
  const a = goalkeeperStats(events, MATCH, 10, 3)
  assert.equal(a.inslappta, 0)
})

test('utan periodsiffror för motståndaren blir inga mål attribuerade', () => {
  const match = {
    motstandare_mal: 3,
    motstandare_mal_perioder: null,
    motstandare_mal_utan_period: 3,
  }
  const s = goalkeeperStats([oppShot('on_goal', 1, 10)], match, 10, OVERVIEW)

  assert.equal(s.registrerat, true)
  assert.equal(s.raddningar, 1)
  assert.equal(s.inslappta, 0) // målen går inte att placera
  assert.equal(s.skottPaMalMot, 1)
})
