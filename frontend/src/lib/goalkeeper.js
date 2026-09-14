// Målvaktsstatistik i matchvyn (SPEC 6.8).
//
// Speglar app/goalkeeper.py. Matchvyn räknar lokalt ur IndexedDB eftersom
// registreringen måste fungera utan nät, medan statistiksidan räknar samma sak
// på servern. Reglerna måste därför vara identiska på båda ställena.
//
// En målvakt mäts inte som en utespelare:
//
//   Skott på mål mot   registrerade motståndarskott på mål plus insläppta
//   Insläppta mål      motståndarens mål, tilldelade via perioden
//   Räddningar         skott på mål mot minus insläppta
//   Räddningsprocent   räddningar delat med skott på mål mot
//
// Att målen ingår i "skott på mål mot" följer samma modell som för utespelarna:
// ett mål är ett skott på mål. Tränarna registrerar aldrig mål manuellt, så
// utan dem skulle räddningar kunna bli negativa.

import { OVERVIEW } from './shotStore.js'

export const PERIODS = [1, 2, 3]

function isPeriod(period) {
  return PERIODS.includes(period)
}

export function goalkeepersInSquad(trupp) {
  return (trupp ?? []).filter(p => p.malvakt)
}

// Motståndarens aktiva skott, grupperade som period -> målvakt -> antal.
// Skott utan målvakt (registrerade innan valet fanns) hoppas över.
export function opponentEventsByPeriod(events) {
  const out = new Map()
  for (const e of events ?? []) {
    if (e.deleted_at) continue
    if ((e.side || 'egen') !== 'motstandare') continue
    if (e.goalkeeper_id == null) continue
    if (!out.has(e.period)) out.set(e.period, new Map())
    const per = out.get(e.period)
    per.set(e.goalkeeper_id, (per.get(e.goalkeeper_id) || 0) + 1)
  }
  return out
}

// Registrerade skott på mål mot en viss målvakt. Med en period räknas bara
// den; med OVERVIEW hela matchen.
export function registeredOnGoalAgainst(events, gkId, period) {
  const perPeriod = isPeriod(period)
  let n = 0
  for (const e of events ?? []) {
    if (e.deleted_at) continue
    if ((e.side || 'egen') !== 'motstandare') continue
    if (e.kind !== 'on_goal') continue
    if (e.goalkeeper_id !== gkId) continue
    if (perPeriod && e.period !== period) continue
    n += 1
  }
  return n
}

function concededInPeriod(concededByPeriod, p) {
  if (!concededByPeriod) return 0
  return concededByPeriod[p] ?? concededByPeriod[String(p)] ?? 0
}

// Fördelar insläppta mål på målvakter, period för period.
//
// Målen från iBIS har bara period, inte målvakt, så perioden tilldelas den
// målvakt som mötte flest skott i den. Byts målvakt mitt i en period blir det
// en approximation – acceptabelt, men den markeras i UI.
export function attributeConceded(
  eventsByPeriod,
  concededByPeriod,
  concededUnknown = 0,
) {
  const perGk = new Map()
  const perPeriod = new Map()
  let oattribuerat = concededUnknown ?? 0
  let approximativ = false

  for (const p of PERIODS) {
    const mal = concededInPeriod(concededByPeriod, p)
    if (mal <= 0) continue

    const iPerioden = eventsByPeriod.get(p)
    if (!iPerioden || iPerioden.size === 0) {
      // Ingen registrering i perioden – vi vet inte vem som stod.
      oattribuerat += mal
      continue
    }

    const delad = iPerioden.size > 1
    if (delad) approximativ = true

    // Flest mötta skott vinner perioden. Lika många → lägsta id, så att
    // resultatet blir samma oavsett i vilken ordning händelserna kom.
    const agare = [...iPerioden.entries()].sort(
      (a, b) => b[1] - a[1] || a[0] - b[0],
    )[0][0]

    perGk.set(agare, (perGk.get(agare) || 0) + mal)
    perPeriod.set(p, { agare, mal, approximativ: delad })
  }

  return { perGk, perPeriod, oattribuerat, approximativ }
}

// De fyra talen. `registrerade` är motståndarens registrerade skott på mål,
// alltså räddningarna – ett skott som gick in registreras aldrig manuellt.
export function saveStats(registrerade, inslappta) {
  const skottPaMalMot = registrerade + inslappta
  return {
    skottPaMalMot,
    inslappta,
    raddningar: registrerade,
    // Ingen procent utan skott att räkna på – aldrig division med noll.
    raddningsprocent:
      skottPaMalMot > 0 ? Math.round((registrerade * 100) / skottPaMalMot) : null,
  }
}

// Allt en målvakt ska visa i matchvyn för vald period.
//
// `registrerat` är false när målvakten inte mött några registrerade
// motståndarskott i omfattningen. Då visas tomt, aldrig noll (SPEC 6.8).
export function goalkeeperStats(events, match, gkId, period) {
  if (gkId == null) return { registrerat: false }

  const eventsByPeriod = opponentEventsByPeriod(events)
  const { perGk, perPeriod, approximativ } = attributeConceded(
    eventsByPeriod,
    match?.motstandare_mal_perioder,
    match?.motstandare_mal_utan_period,
  )

  const enPeriod = isPeriod(period)

  const motteSkott = enPeriod
    ? (eventsByPeriod.get(period)?.get(gkId) ?? 0) > 0
    : [...eventsByPeriod.values()].some(per => (per.get(gkId) ?? 0) > 0)

  if (!motteSkott) return { registrerat: false }

  const registrerade = registeredOnGoalAgainst(events, gkId, period)

  let inslappta
  let approx
  if (enPeriod) {
    const rad = perPeriod.get(period)
    inslappta = rad && rad.agare === gkId ? rad.mal : 0
    approx = Boolean(rad && rad.agare === gkId && rad.approximativ)
  } else {
    inslappta = perGk.get(gkId) ?? 0
    approx = approximativ
  }

  return {
    registrerat: true,
    ...saveStats(registrerade, inslappta),
    approximativ: approx,
  }
}
