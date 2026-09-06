// Ren logik för matchlistan (SPEC 6.1, punkt 2 i buggrapporten).
// Ingen DOM – testas med `node --test` i matches.test.js.

// Naiv lokaltid från iBIS ("2026-09-19T13:00:00") tolkas som telefonens
// lokaltid (Europe/Stockholm för tränarna).
export function parseKickoff(str) {
  return str ? new Date(str) : null
}

// En match utan satt datum. iBIS platshållarvärde ("1 januari 00:00") känns
// igen på servern, som sätter datum_saknas.
export function isDateMissing(match) {
  return Boolean(match && match.datum_saknas)
}

// Matchlistans ordning: riktiga datum kronologiskt, matcher utan satt datum
// sist. Servern sorterar redan så här – frontend håller ordningen robust även
// om något oordnat slinker igenom.
export function sortMatchesForList(matches) {
  return [...matches].sort((a, b) => {
    const ad = isDateMissing(a)
    const bd = isDateMissing(b)
    if (ad !== bd) return ad ? 1 : -1
    const ak = parseKickoff(a.kickoff)?.getTime() ?? 0
    const bk = parseKickoff(b.kickoff)?.getTime() ?? 0
    return ak - bk
  })
}

// Första kommande matchen som autoscrollen ska hoppa till. Hoppar över
// inställda matcher och matcher utan satt datum – en flyttad match följer sitt
// nya kickoff eftersom synken nu håller kickoff färsk.
export function nextMatchId(matches, now = Date.now()) {
  for (const m of matches) {
    if (m.status === 'cancelled') continue
    if (isDateMissing(m)) continue
    const k = parseKickoff(m.kickoff)
    if (k && k.getTime() >= now) return m.match_id
  }
  return null
}
