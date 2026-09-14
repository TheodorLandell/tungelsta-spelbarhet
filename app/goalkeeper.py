"""
Målvaktsstatistik (SPEC 6.8).

En målvakt mäts inte som en utespelare. I stället för på mål, utanför och i
täck visas fyra tal:

  - Skott på mål mot   registrerade motståndarskott på mål **plus** insläppta
  - Insläppta mål      motståndarens mål, tilldelade via perioden
  - Räddningar         skott på mål mot minus insläppta
  - Räddningsprocent   räddningar delat med skott på mål mot

Att målen ingår i "skott på mål mot" följer samma modell som för utespelarna
(SPEC 6.2): ett mål *är* ett skott på mål. Eftersom tränarna aldrig registrerar
mål manuellt skulle de annars saknas helt, och räddningar – som är differensen –
kunde bli negativa. Med målen inräknade är räddningar exakt de registrerade
skotten på mål, vilket också är vad en räddning faktiskt är.

**Två källor, två precisioner.** Skotten vet själva vilken målvakt de hör till:
``shot_events.goalkeeper_id`` sätts från målvaktsvalet när trycket görs, så den
attribueringen är exakt. Insläppta mål kommer från iBIS och har bara period, inte
målvakt. De tilldelas därför den målvakt som mötte flest skott i perioden. Byts
målvakt mitt i en period blir det en approximation – den är acceptabel, men
markeras i gränssnittet.
"""

PERIODS = (1, 2, 3)


def attribute_conceded(
    events_by_period: dict[int, dict[int, int]],
    conceded_by_period: dict[int, int],
    conceded_unknown: int = 0,
) -> tuple[dict[int, int], int, bool]:
    """
    Fördelar insläppta mål på målvakter, period för period.

    ``events_by_period``  {period: {goalkeeper_id: antal motståndarskott}}
    ``conceded_by_period``{period: antal insläppta mål}
    ``conceded_unknown``  insläppta mål vars period iBIS inte angav

    Returnerar ``(per_malvakt, oattribuerat, approximativ)``:

      per_malvakt   {goalkeeper_id: insläppta mål}
      oattribuerat  mål som ingen målvakt kan tillskrivas – perioden saknar
                    registrerade motståndarskott, eller målet saknar period
      approximativ  True när någon period med insläppta mål hade fler än en
                    målvakt, så att fördelningen bygger på vem som mötte flest
                    skott i stället för på faktiska byten

    Summan av per_malvakt och oattribuerat är alltid totalen som skickades in,
    så ingen siffra kan tappas bort eller räknas två gånger.
    """
    per_gk: dict[int, int] = {}
    oattribuerat = conceded_unknown
    approximativ = False

    for period in PERIODS:
        mal = conceded_by_period.get(period, 0)
        if mal <= 0:
            continue

        i_perioden = {
            gk: antal
            for gk, antal in (events_by_period.get(period) or {}).items()
            if gk is not None and antal > 0
        }
        if not i_perioden:
            # Ingen registrering i perioden – vi vet inte vem som stod.
            oattribuerat += mal
            continue

        if len(i_perioden) > 1:
            approximativ = True

        # Flest mötta skott vinner perioden. Lika många → lägsta id, så att
        # resultatet är samma oavsett i vilken ordning raderna kom.
        agare = min(i_perioden, key=lambda gk: (-i_perioden[gk], gk))
        per_gk[agare] = per_gk.get(agare, 0) + mal

    return per_gk, oattribuerat, approximativ


def save_stats(shots_on_goal_registered: int, conceded: int) -> dict:
    """
    De fyra talen för en målvakt.

    ``shots_on_goal_registered`` är de registrerade motståndarskotten på mål –
    alltså räddningarna, eftersom ett skott som gick in aldrig registreras
    manuellt. Skott på mål mot är summan av dem och de insläppta målen.
    """
    skott_pa_mal_mot = shots_on_goal_registered + conceded
    raddningar = shots_on_goal_registered
    return {
        "skott_pa_mal_mot": skott_pa_mal_mot,
        "inslappta": conceded,
        "raddningar": raddningar,
        # Ingen procent utan skott att räkna på – aldrig division med noll.
        "raddningsprocent": (
            round(raddningar * 100 / skott_pa_mal_mot)
            if skott_pa_mal_mot > 0
            else None
        ),
    }
