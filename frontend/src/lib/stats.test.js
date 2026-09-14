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
  malProcentText,
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

// 2 mål + 4 registrerade skott på mål = 6 på mål. Totalen är 6 + 1 + 1 = 8.
const SKOTT = {
  registrerat: true,
  totalt: 8,
  mal: 2,
  malprocent: 33,
  pa_mal: { antal: 6, andel: 75 },
  utanfor: { antal: 1, andel: 13 },
  i_tack: { antal: 1, andel: 12 },
}

test('skottSegment: tre segment i visningsordning, mål är inget eget', () => {
  const seg = skottSegment(SKOTT)
  assert.deepEqual(seg.map(s => s.key), ['pa_mal', 'utanfor', 'i_tack'])
  assert.deepEqual(seg.map(s => s.antal), [6, 1, 1])
})

test('skottSegment: de tre andelarna summerar till 100 procent', () => {
  const seg = skottSegment(SKOTT)
  assert.equal(seg.length, 3)
  assert.equal(seg.reduce((a, s) => a + s.andel, 0), 100)
})

test('skottSegment: målen ingår i på mål och dubbelräknas inte i totalen', () => {
  const seg = skottSegment(SKOTT)
  // Summan av segmenten är hela totalen – målen ligger redan i på mål.
  assert.equal(seg.reduce((a, s) => a + s.antal, 0), SKOTT.totalt)
})

test('malProcentText: mål och målprocent under baren', () => {
  assert.equal(malProcentText(SKOTT), '2 mål av 6 på mål (33 %)')
})

test('malProcentText: utan skott på mål visas ingen procent', () => {
  const skott = {
    registrerat: true, totalt: 1, mal: 0, malprocent: null,
    pa_mal: { antal: 0, andel: 0 },
    utanfor: { antal: 1, andel: 100 },
    i_tack: { antal: 0, andel: 0 },
  }
  assert.equal(malProcentText(skott), '0 mål av 0 på mål')
})

test('malProcentText: utan registrering finns inget att visa', () => {
  assert.equal(malProcentText({ registrerat: false }), null)
  assert.equal(malProcentText(null), null)
})

// ---------------------------------------------------------------------------
// Verklig match: IFK Haninge (C) - Tungelsta IF (B) 6-8, match_id 1723835
//
// Svaren nedan är exakt vad /api/stats returnerar för spelarna. Målen ligger
// redan inne i pa_mal – de är ingen egen post.
// ---------------------------------------------------------------------------

const MATCH_1723835 = [
  { namn: 'William Lindahl', mal: 3, regPaMal: 2, skott: {
    registrerat: true, totalt: 7, mal: 3, malprocent: 60,
    pa_mal: { antal: 5, andel: 72 },
    utanfor: { antal: 1, andel: 14 },
    i_tack: { antal: 1, andel: 14 } } },
  { namn: 'Johnny Andersson', mal: 2, regPaMal: 1, skott: {
    registrerat: true, totalt: 10, mal: 2, malprocent: 67,
    pa_mal: { antal: 3, andel: 30 },
    utanfor: { antal: 3, andel: 30 },
    i_tack: { antal: 4, andel: 40 } } },
  { namn: 'Adam Burgren', mal: 1, regPaMal: 4, skott: {
    registrerat: true, totalt: 7, mal: 1, malprocent: 20,
    pa_mal: { antal: 5, andel: 71 },
    utanfor: { antal: 2, andel: 29 },
    i_tack: { antal: 0, andel: 0 } } },
  { namn: 'Felix Wikström', mal: 1, regPaMal: 0, skott: {
    registrerat: true, totalt: 4, mal: 1, malprocent: 100,
    pa_mal: { antal: 1, andel: 25 },
    utanfor: { antal: 1, andel: 25 },
    i_tack: { antal: 2, andel: 50 } } },
  { namn: 'Tim Johannesson', mal: 1, regPaMal: 3, skott: {
    registrerat: true, totalt: 5, mal: 1, malprocent: 25,
    pa_mal: { antal: 4, andel: 80 },
    utanfor: { antal: 0, andel: 0 },
    i_tack: { antal: 1, andel: 20 } } },
]

test('verklig match: varje spelares på mål inkluderar hans mål', () => {
  for (const { namn, mal, regPaMal, skott } of MATCH_1723835) {
    const paMal = skottSegment(skott).find(s => s.key === 'pa_mal')
    assert.equal(paMal.antal, regPaMal + mal, namn)
    assert.ok(paMal.antal >= mal, `${namn}: på mål ska minst vara antalet mål`)
  }
})

test('verklig match: fördelningen har exakt tre rader, ingen målrad', () => {
  for (const { namn, skott } of MATCH_1723835) {
    const seg = skottSegment(skott)
    assert.equal(seg.length, 3, namn)
    assert.deepEqual(seg.map(s => s.key), ['pa_mal', 'utanfor', 'i_tack'], namn)
    assert.ok(!seg.some(s => s.key === 'mal'), `${namn}: mål ska inte vara en rad`)
  }
})

test('verklig match: de tre andelarna summerar till 100 procent', () => {
  for (const { namn, skott } of MATCH_1723835) {
    const summa = skottSegment(skott).reduce((a, s) => a + s.andel, 0)
    assert.equal(summa, 100, namn)
  }
})

test('verklig match: segmenten summerar till totalen, målen dubbelräknas inte', () => {
  for (const { namn, skott } of MATCH_1723835) {
    const summa = skottSegment(skott).reduce((a, s) => a + s.antal, 0)
    assert.equal(summa, skott.totalt, namn)
  }
})

test('verklig match: målen visas under baren i stället', () => {
  assert.equal(malProcentText(MATCH_1723835[1].skott), '2 mål av 3 på mål (67 %)')
  assert.equal(malProcentText(MATCH_1723835[0].skott), '3 mål av 5 på mål (60 %)')
})
