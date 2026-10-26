// Upplåsning av låsta spelare (SPEC 1). En låst spelare låses upp när han
// stått över tre A-matcher i rad, räknat från hans senaste A-match.
// Raden visar hur långt han kommit, så att tränarna ser när han blir fri.

export const UNLOCK_AFTER = 3

export function unlockProgressText(overstadda) {
  const n = overstadda ?? 0
  if (n === UNLOCK_AFTER - 1) return 'Fri om han står över nästa A-match'
  return `Stått över ${n} av ${UNLOCK_AFTER} A-matcher`
}

// Låst manuellt vinner över regeln, så då visas ingen räkning alls.
export function showUnlockProgress(player) {
  return player.override?.kind !== 'lock'
}
