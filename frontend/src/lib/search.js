// Delad sokfiltrering for spelbarhetslistan och registreringsvyn (SPEC 5, 6.2).
// Delstrang, skiftlagesokanslig, hanterar a/a/o genom att normalisera bort
// diakritiska tecken sa att t.ex. "ostberg" hittar "Ostberg".

// Combining diacritical marks block (U+0300-U+036F), reached via NFD
// normalization of letters like a, a, o.
const COMBINING_START = 0x0300
const COMBINING_END = 0x036f

function stripDiacritics(s) {
  let out = ''
  for (const ch of s) {
    const code = ch.codePointAt(0)
    if (code >= COMBINING_START && code <= COMBINING_END) continue
    out += ch
  }
  return out
}

export function normalizeText(s) {
  return stripDiacritics((s ?? '').normalize('NFD')).toLowerCase()
}

export function matchesPlayerQuery(player, query) {
  const q = normalizeText(query).trim()
  if (!q) return true
  const name = normalizeText(player?.namn)
  const shirt = player?.trojnummer != null ? String(player.trojnummer) : ''
  return name.includes(q) || shirt.includes(q)
}
