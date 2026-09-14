// Perioduppdelning och skottmodell i matchvyn (SPEC 6.2 och 6.7).
//
// Två regler som hänger ihop:
//
//  1. Ett mål räknas bara i den period det gjordes i. Backend skickar
//     `mal_perioder` från iBIS matchhändelser och `mal_utan_period` för de mål
//     där perioden inte går att avgöra. De sistnämnda räknas bara i
//     "hela matchen" – hellre en saknad siffra än en felaktig.
//
//  2. Ett mål är ett skott på mål. Skott på mål = registrerade skott på mål
//     plus mål. Totalen är på mål + utanför + i täck, så ett mål räknas aldrig
//     två gånger.

// Explicit filändelse: den här modulen importeras av `node --test`, som till
// skillnad från Vite inte gissar sig till .js.
import { OVERVIEW } from './shotStore.js'

export const PERIODS = [1, 2, 3]

export function isPeriod(period) {
  return PERIODS.includes(period)
}

// Målen (eller utvisningsminuterna) som hör till vald period, plus om något
// saknar periodinformation.
//
// `period` är 1, 2 eller 3 för en enskild period, och OVERVIEW för hela
// matchen. Returnerar `{ varde, okant }`:
//
//   varde  antalet i vald omfattning, eller null när totalen är okänd – så att
//          UI kan visa en tom ruta i stället för en nolla (SPEC 6.2). Noll mål
//          och "inte rapporterat än" är olika saker.
//   okant  true när något i totalen inte går att placera i en period och vi
//          står i en enskild period. Då är siffran inte hela sanningen och UI
//          markerar det.
//
// Saknas periodfälten helt är raden hämtad ur en äldre lokal cache. Då vet vi
// ingenting om perioderna, och allt räknas som okänt – hellre en markerad
// siffra än en tyst nolla.
export function periodTal({ total, perioder, utanPeriod }, period) {
  if (typeof total !== 'number') return { varde: null, okant: false }
  if (!isPeriod(period)) return { varde: total, okant: false }

  const saknarFalt = perioder === undefined && utanPeriod === undefined
  if (saknarFalt) return { varde: 0, okant: total > 0 }

  // Nycklarna kommer som strängar över JSON.
  const varde = perioder
    ? perioder[period] ?? perioder[String(period)] ?? 0
    : 0
  return { varde, okant: (utanPeriod ?? 0) > 0 }
}

// Målen för vald period på en spelarrad eller på matchen – båda bär fälten
// mal / mal_perioder / mal_utan_period.
export function malTal(row, period) {
  return periodTal(
    {
      total: row?.mal,
      perioder: row?.mal_perioder,
      utanPeriod: row?.mal_utan_period,
    },
    period,
  )
}

// Motståndarens mål för vald period.
export function motstandareMalTal(match, period) {
  return periodTal(
    {
      total: match?.motstandare_mal,
      perioder: match?.motstandare_mal_perioder,
      utanPeriod: match?.motstandare_mal_utan_period,
    },
    period,
  )
}

// Skottmodellen för ett lag eller en spelare (SPEC 6.2).
//
// `shots` är de registrerade skotten { on_goal, missed, blocked } och `mal`
// målen från iBIS för samma omfattning. Målen läggs till på mål och räknas
// aldrig separat i totalen.
export function shotModel(shots, mal) {
  const malKant = typeof mal === 'number'
  const paMal = (shots?.on_goal ?? 0) + (malKant ? mal : 0)
  const utanfor = shots?.missed ?? 0
  const iTack = shots?.blocked ?? 0
  return {
    mal: malKant ? mal : null,
    paMal,
    utanfor,
    iTack,
    totalt: paMal + utanfor + iTack,
  }
}

export { OVERVIEW }
