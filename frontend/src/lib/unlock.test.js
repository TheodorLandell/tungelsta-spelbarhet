// Tester för upplåsningsraden hos låsta spelare (SPEC 1).
// Rena funktioner, ingen DOM – körs med `node --test`.

import test from 'node:test'
import assert from 'node:assert/strict'

import { unlockProgressText, showUnlockProgress } from './unlock.js'

test('räkningen visas som antal av tre', () => {
  assert.equal(unlockProgressText(0), 'Stått över 0 av 3 A-matcher')
  assert.equal(unlockProgressText(1), 'Stått över 1 av 3 A-matcher')
})

test('vid två av tre sägs vad som krävs för att bli fri', () => {
  assert.equal(unlockProgressText(2), 'Fri om han står över nästa A-match')
})

test('saknat värde räknas som noll', () => {
  assert.equal(unlockProgressText(undefined), 'Stått över 0 av 3 A-matcher')
  assert.equal(unlockProgressText(null), 'Stått över 0 av 3 A-matcher')
})

test('manuell låsning visar ingen räkning', () => {
  assert.equal(showUnlockProgress({ override: { kind: 'lock' } }), false)
  assert.equal(showUnlockProgress({ override: null }), true)
  assert.equal(showUnlockProgress({ override: { kind: 'set_matches_left' } }), true)
})
