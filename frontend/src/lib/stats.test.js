// Tester för den rena logiken på statistiksidan (SPEC 7).
// Rena funktioner, ingen DOM – körs med `node --test`.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  OMFATTNINGAR,
  omfattningById,
  statsQuery,
  beskrivOmfattning,
  skottSegment,
} from './stats.js'

test('statsQuery: sasong tar inte med n', () => {
  const q = statsQuery('A', omfattningById('sasong'))
  const p = new URLSearchParams(q)
  assert.equal(p.get('team'), 'A')
  assert.equal(p.get('scope'), 'sasong')
  assert.equal(p.get('n'), null)
})

test('statsQuery: senaste matchen', () => {
  const p = new URLSearchParams(statsQuery('B', omfattningById('senaste')))
  assert.equal(p.get('team'), 'B')
  assert.equal(p.get('scope'), 'senaste')
  assert.equal(p.get('n'), null)
})

test('statsQuery: senaste N tar med n', () => {
  const p = new URLSearchParams(statsQuery('A', omfattningById('n10')))
  assert.equal(p.get('scope'), 'senaste_n')
  assert.equal(p.get('n'), '10')
})

test('omfattningById: okänt id faller tillbaka på hela säsongen', () => {
  assert.equal(omfattningById('finns-inte').id, 'sasong')
})

test('alla omfattningar har unik etikett och giltigt scope', () => {
  const scopes = new Set(['senaste', 'senaste_n', 'sasong'])
  const labels = new Set()
  for (const o of OMFATTNINGAR) {
    assert.ok(scopes.has(o.scope), `ogiltigt scope: ${o.scope}`)
    if (o.scope === 'senaste_n') assert.equal(typeof o.n, 'number')
    labels.add(o.label)
  }
  assert.equal(labels.size, OMFATTNINGAR.length)
})

test('beskrivOmfattning: singular och plural', () => {
  assert.equal(beskrivOmfattning(omfattningById('senaste'), 1), 'Senaste matchen · 1 match')
  assert.equal(beskrivOmfattning(omfattningById('sasong'), 12), 'Hela säsongen · 12 matcher')
  assert.equal(beskrivOmfattning(omfattningById('n5'), 3), 'Senaste 5 · 3 matcher')
})

test('skottSegment: utan registrering ger tom lista', () => {
  assert.deepEqual(skottSegment({ registrerat: false }), [])
  assert.deepEqual(skottSegment(null), [])
})

test('skottSegment: fyra segment i visningsordning', () => {
  const skott = {
    registrerat: true,
    totalt: 8,
    mal: { antal: 2, andel: 25 },
    pa_mal: { antal: 4, andel: 50 },
    utanfor: { antal: 1, andel: 13 },
    i_tack: { antal: 1, andel: 12 },
  }
  const seg = skottSegment(skott)
  assert.deepEqual(seg.map(s => s.key), ['mal', 'pa_mal', 'utanfor', 'i_tack'])
  assert.deepEqual(seg.map(s => s.antal), [2, 4, 1, 1])
  assert.equal(seg.reduce((a, s) => a + s.andel, 0), 100)
})
