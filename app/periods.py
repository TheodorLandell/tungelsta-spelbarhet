"""
Perioduppdelning av mål och utvisningsminuter (SPEC 6.7).

Mål och utvisningar kommer från två håll som båda behövs:

  - ``lineups`` ger matchens **totaler** per spelare. De är alltid ifyllda för
    en spelad match och är den auktoritativa siffran.
  - ``Events`` i matchobjektet ger **perioden** för varje enskild händelse, men
    är null tills matchen spelats och kan saknas för äldre matcher.

Regeln när de två inte går ihop är hämtad ur SPEC 6.7: hellre en saknad siffra
än en felaktig. Uppdelningen är därför aldrig tillåten att motsäga totalen –
det som inte kan placeras i en period räknas bara i "hela matchen".

Perioder utanför 1–3 (förlängning) har ingen plats i periodväljaren och
behandlas som okänd period, av samma skäl.
"""

PERIODS = (1, 2, 3)


def empty_periods() -> dict[int, int]:
    return {p: 0 for p in PERIODS}


def split_by_period(total: int, per_period: dict[int, int]) -> tuple[dict[int, int], int]:
    """
    Fördelar en känd total över perioderna utifrån händelserna.

    Returnerar ``(perioder, utan_period)`` där summan av de två alltid är
    exakt ``total``. Tre fall:

      - Händelserna täcker totalen        → perioder som de är, utan_period 0
      - Händelserna täcker en del av den  → resten hamnar i utan_period
      - Händelserna säger *mer* än totalen → uppdelningen är inte att lita på,
        allt hamnar i utan_period

    Det sista fallet betyder att totalen och händelserna kommer från två olika
    tidpunkter, eller att en utvisningstext tolkats fel. Då är det säkrare att
    visa siffran bara i "hela matchen" än att lägga den i fel period.
    """
    if total <= 0:
        return empty_periods(), 0

    known = {p: per_period.get(p, 0) for p in PERIODS}
    summa = sum(known.values())

    if summa > total:
        return empty_periods(), total
    return known, total - summa


def goals_for_scope(perioder: dict[int, int] | None, total: int | None, period) -> int | None:
    """
    Målen (eller minuterna) som hör till vald omfattning.

    ``period`` är 1, 2 eller 3 för en enskild period, och något annat – till
    exempel "all" – för hela matchen. Utan känd total returneras None, så att
    UI kan visa en tom ruta i stället för en nolla (SPEC 6.2).
    """
    if total is None:
        return None
    if period not in PERIODS:
        return total
    if not perioder:
        return 0
    return perioder.get(period, perioder.get(str(period), 0))


def team_periods_from_raw(raw: dict, *, hemma: bool) -> dict[int, int] | None:
    """
    Lagets mål per period ur ``IntermediateResults``.

    Till skillnad från Events är den ifylld redan i lag-endpointen, så
    matchhuvudets lagsiffror kostar inget extra anrop mot iBIS. Returnerar
    None när den saknas – då har laget inga kända periodsiffror.
    """
    rows = raw.get("IntermediateResults")
    if not rows:
        return None

    key = "GoalsHomeTeam" if hemma else "GoalsAwayTeam"
    out = empty_periods()
    found = False
    for r in rows:
        if not isinstance(r, dict):
            continue
        p = r.get("Period")
        v = r.get(key)
        if p in PERIODS and isinstance(v, int):
            out[p] = v
            found = True
    return out if found else None
