// Matchhuvud (SPEC 6.2): ingen box, ingen ram. En resultatrad i klassisk stil –
// hemmalag, mål, mål, bortalag – med lagnamn och siffror betydligt större än
// brödtexten. Direkt under respektive lagnamn en liten rad med lagets skott:
// "6 totalt · 1 på mål · 1 utanför · 4 i täck". Inga andelar, ingen stjärna.
//
// Före matchen är resultatet blankt. När iBIS rapporterat visas siffrorna, även
// 0-0. Lagstatistiken följer vald period precis som spelarnas.

const ZERO = { on_goal: 0, missed: 0, blocked: 0 }

function shotsTotal(mal, s) {
  const known = typeof mal === 'number'
  return (known ? mal : 0) + s.on_goal + s.missed + s.blocked
}

function ShotLine({ mal, shots }) {
  return (
    <p className="mt-1 text-[11px] leading-tight text-gray-500 tabular-nums">
      {shotsTotal(mal, shots)} totalt · {shots.on_goal} på mål ·{' '}
      {shots.missed} utanför · {shots.blocked} i täck
    </p>
  )
}

export default function MatchHeader({ match, ownShots = ZERO, oppShots = ZERO }) {
  const installd = match.status === 'cancelled'

  // Namn på hemma- och bortalag från iBIS. Faller tillbaka på motståndarnamnet
  // plus hemma/borta om raw saknar dem (äldre data).
  const homeName =
    match.hemmalag || (match.hemma === false ? match.motstandare : 'Hemmalaget')
  const awayName =
    match.bortalag || (match.hemma === true ? match.motstandare : 'Bortalaget')

  // Våra mål hör till den sida vi spelar på; motståndarens till den andra.
  const ownIsHome = match.hemma !== false
  const homeGoals = ownIsHome ? match.mal : match.motstandare_mal
  const awayGoals = ownIsHome ? match.motstandare_mal : match.mal
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
        {!installd && <ShotLine mal={homeGoals} shots={homeShots} />}
      </div>

      <div className="shrink-0 pt-0.5 text-center">
        {installd ? (
          <span className="text-sm font-semibold text-amber-700 bg-amber-100
                           rounded-lg px-3 py-1">
            Inställd
          </span>
        ) : harResultat ? (
          <div className="text-3xl font-extrabold tabular-nums text-gray-900
                          whitespace-nowrap">
            {homeGoals}
            <span className="mx-2 text-gray-300">–</span>
            {awayGoals}
          </div>
        ) : (
          // Före matchen: blankt, inga platshållarrutor.
          <div className="h-9" aria-hidden="true" />
        )}
      </div>

      <div className="flex-1 min-w-0 text-left">
        <div className="text-lg font-bold leading-tight text-gray-900 break-words">
          {awayName}
        </div>
        {!installd && <ShotLine mal={awayGoals} shots={awayShots} />}
      </div>
    </div>
  )
}
