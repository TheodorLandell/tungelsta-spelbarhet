// Matchhuvud (SPEC 6.2): ingen box, ingen ram. En resultatrad i klassisk stil –
// hemmalag, mål, mål, bortalag – med lagnamn och siffror betydligt större än
// brödtexten. Direkt under respektive lagnamn en liten rad med lagets skott:
// "6 totalt · 2 på mål · 1 utanför · 3 i täck". Inga andelar, ingen stjärna.
//
// Mål är skott på mål (SPEC 6.2): "på mål" är de registrerade skotten på mål
// plus målen, och totalen är på mål + utanför + i täck. Ett mål räknas därför
// aldrig två gånger.
//
// Lagstatistiken följer vald period precis som spelarnas, och målen räknas bara
// i den period de gjordes i (SPEC 6.7). Mål vars period är okänd räknas bara i
// "hela matchen" och markeras med en prick i periodvyn.
//
// Själva resultatet i mitten är alltid hela matchens, oavsett vald period – det
// är matchens ställning, inte en periodsiffra. Före matchen är det blankt. När
// iBIS rapporterat visas siffrorna, även 0-0.
//
// Trekolumnslayout: mittkolumnen har en fast bredd (MID_COL_WIDTH) som aldrig
// ändras – blankt, 0-0 eller 10-9 tar exakt samma plats – så att hemma- och
// bortalagets kolumner aldrig hoppar när resultatet kommer in.

import { malTal, motstandareMalTal, shotModel } from '../lib/periods'

const MID_COL_WIDTH = 'w-24' // 6rem, rymmer tvåsiffriga resultat utan att växa

const ZERO = { on_goal: 0, missed: 0, blocked: 0 }

function ShotLine({ mal, shots, okandPeriod }) {
  const m = shotModel(shots, mal)
  return (
    <p className="mt-1 text-[11px] leading-tight text-gray-500 tabular-nums">
      {m.totalt} totalt · {m.paMal} på mål · {m.utanfor} utanför ·{' '}
      {m.iTack} i täck
      {okandPeriod && (
        <span
          className="ml-1 text-gray-400"
          title="Mål utan periodinformation räknas bara i hela matchen"
        >
          ·
        </span>
      )}
    </p>
  )
}

export default function MatchHeader({
  match,
  ownShots = ZERO,
  oppShots = ZERO,
  period,
}) {
  const installd = match.status === 'cancelled'

  // Namn på hemma- och bortalag från iBIS. Faller tillbaka på motståndarnamnet
  // plus hemma/borta om raw saknar dem (äldre data).
  const homeName =
    match.hemmalag || (match.hemma === false ? match.motstandare : 'Hemmalaget')
  const awayName =
    match.bortalag || (match.hemma === true ? match.motstandare : 'Bortalaget')

  // Våra mål hör till den sida vi spelar på; motståndarens till den andra.
  const ownIsHome = match.hemma !== false

  // Resultatet i mitten: alltid hela matchen.
  const homeGoals = ownIsHome ? match.mal : match.motstandare_mal
  const awayGoals = ownIsHome ? match.motstandare_mal : match.mal

  // Skottraderna: målen för vald period.
  const own = malTal(match, period)
  const opp = motstandareMalTal(match, period)

  const homePeriodGoals = ownIsHome ? own.varde : opp.varde
  const awayPeriodGoals = ownIsHome ? opp.varde : own.varde
  const homeOkand = ownIsHome ? own.okant : opp.okant
  const awayOkand = ownIsHome ? opp.okant : own.okant
  const homeShots = ownIsHome ? ownShots : oppShots
  const awayShots = ownIsHome ? oppShots : ownShots

  const harResultat =
    typeof homeGoals === 'number' && typeof awayGoals === 'number'

  return (
    <div className="mb-5 flex items-start gap-3">
      <div className="flex-1 min-w-0 text-right">
        <div className="text-lg font-bold leading-tight text-gray-900 break-words">
          {homeName}
        </div>
        {!installd && (
          <ShotLine
            mal={homePeriodGoals}
            shots={homeShots}
            okandPeriod={homeOkand}
          />
        )}
      </div>

      <div className={`shrink-0 ${MID_COL_WIDTH} pt-0.5 text-center`}>
        {installd ? (
          <span className="inline-block max-w-full text-sm font-semibold
                           text-amber-700 bg-amber-100 rounded-lg px-3 py-1">
            Inställd
          </span>
        ) : harResultat ? (
          <div className="text-3xl font-extrabold tabular-nums text-gray-900">
            {homeGoals}
            <span className="mx-1.5 font-normal text-gray-300">–</span>
            {awayGoals}
          </div>
        ) : (
          // Före matchen: blankt, men mittkolumnen behåller sin fasta bredd.
          <div className="h-9" aria-hidden="true" />
        )}
      </div>

      <div className="flex-1 min-w-0 text-left">
        <div className="text-lg font-bold leading-tight text-gray-900 break-words">
          {awayName}
        </div>
        {!installd && (
          <ShotLine
            mal={awayPeriodGoals}
            shots={awayShots}
            okandPeriod={awayOkand}
          />
        )}
      </div>
    </div>
  )
}
