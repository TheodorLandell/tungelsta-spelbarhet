// Tester för matchlistans rena logik. Körs med `node --test`.

import test from 'node:test'
import assert from 'node:assert/strict'

import { sortMatchesForList, nextMatchId, isDateMissing } from './matches.js'

const m = (over = {}) => ({
  match_id: 1,
  kickoff: '2026-09-19T13:15:00',
  status: 'scheduled',
  datum_saknas: false,
  ...over,
})

test('isDateMissing: läser datum_saknas', () => {
  assert.equal(isDateMissing(m({ datum_saknas: true })), true)
  assert.equal(isDateMissing(m()), false)
  assert.equal(isDateMissing(null), false)
})

test('sortMatchesForList: datumordning, matcher utan datum sist', () => {
  const matches = [
    m({ match_id: 3, kickoff: '2026-10-05T15:00:00' }),
    m({ match_id: 99, datum_saknas: true, kickoff: '2026-01-01T00:00:00' }),
    m({ match_id: 1, kickoff: '2026-09-01T13:00:00' }),
    m({ match_id: 2, kickoff: '2026-09-20T14:00:00' }),
  ]
  assert.deepEqual(
    sortMatchesForList(matches).map(x => x.match_id),
    [1, 2, 3, 99],
  )
})

test('sortMatchesForList: en flyttad match hamnar på sin nya plats', () => {
  // B-match (id 2) flyttad till före A-matchen (id 1)
  const matches = [
    m({ match_id: 1, kickoff: '2026-09-14T19:00:00' }),
    m({ match_id: 2, kickoff: '2026-09-10T12:00:00' }),
  ]
  assert.deepEqual(
    sortMatchesForList(matches).map(x => x.match_id),
    [2, 1],
  )
})

test('sortMatchesForList: muterar inte indata', () => {
  const matches = [
    m({ match_id: 2, kickoff: '2026-09-20T14:00:00' }),
    m({ match_id: 1, kickoff: '2026-09-01T13:00:00' }),
  ]
  sortMatchesForList(matches)
  assert.equal(matches[0].match_id, 2)
})

test('nextMatchId: första matchen med kickoff i framtiden', () => {
  const now = new Date('2026-09-15T00:00:00').getTime()
  const matches = sortMatchesForList([
    m({ match_id: 1, kickoff: '2026-09-01T13:00:00' }),
    m({ match_id: 2, kickoff: '2026-09-20T14:00:00' }),
    m({ match_id: 3, kickoff: '2026-10-05T15:00:00' }),
  ])
  assert.equal(nextMatchId(matches, now), 2)
})

test('nextMatchId: hoppar över inställda matcher', () => {
  const now = new Date('2026-09-15T00:00:00').getTime()
  const matches = [
    m({ match_id: 2, kickoff: '2026-09-20T14:00:00', status: 'cancelled' }),
    m({ match_id: 3, kickoff: '2026-10-05T15:00:00' }),
  ]
  assert.equal(nextMatchId(matches, now), 3)
})

test('nextMatchId: en match utan satt datum blir aldrig "nästa"', () => {
  const now = new Date('2026-09-15T00:00:00').getTime()
  // Jan 1-platshållaren ligger i det förflutna men datum_saknas ska ändå
  // hindra att den räknas – och den vore aldrig kommande.
  const matches = [
    m({ match_id: 99, datum_saknas: true, kickoff: '2026-01-01T00:00:00' }),
    m({ match_id: 3, kickoff: '2026-10-05T15:00:00' }),
  ]
  assert.equal(nextMatchId(matches, now), 3)
})

test('nextMatchId: inga kommande matcher ger null', () => {
  const now = new Date('2026-12-01T00:00:00').getTime()
  const matches = [m({ match_id: 1, kickoff: '2026-09-01T13:00:00' })]
  assert.equal(nextMatchId(matches, now), null)
})
