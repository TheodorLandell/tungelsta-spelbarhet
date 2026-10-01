"""
Tvingar fram en omhämtning av en eller flera matcher.

Kör: python -m app.refresh_match 1703100 [1703106 ...]
     python -m app.refresh_match --alla
     python -m app.refresh_match 1703100 --torrkor

En färdigrapporterad match hämtas inte om (SPEC 3.5). Villkoret är
``matches.stats_final_ts == FinalResultCreatedTS``: stämmer stämpeln vet synken
att statistiken hämtades efter slutrapporten och hoppar över lineups (SPEC 6.7).

Det gör också att rättelser i iBIS efter slutrapporten aldrig fångas. Tas en
spelare ur matchtruppen i efterhand ligger han kvar hos oss, eftersom lineups
inte hämtas om och pruningen av appearances därför aldrig körs.

Det här kommandot nollställer stämpeln. Nästa synk – nattjobbet eller
Uppdatera-knappen – behandlar matchen som ohämtad och gör om allt:

  - hämtar lineups
  - skriver appearances med statistiken ur Events
  - tar bort appearances för spelare som inte längre står i iBIS-truppen
  - skriver om match_events

Ingenting raderas här. Kommandot rör bara stämpeln, och allt annat skrivs om av
synken utifrån vad iBIS säger. roster_edits ligger kvar som det lager de är
(SPEC 6.5), så en manuellt tillagd spelare finns kvar efter omhämtningen och en
manuell borttagning fortsätter gälla.
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Match

ANVANDNING = (
    "Användning:\n"
    "  python -m app.refresh_match <match-id> [<match-id> ...]\n"
    "  python -m app.refresh_match --alla\n"
    "\n"
    "Flaggor:\n"
    "  --alla      nollställ stämpeln för samtliga matcher\n"
    "  --torrkor   visa vad som skulle göras, skriv ingenting"
)


def refresh_matches(
    db: Session,
    match_ids: list[int] | None = None,
    *,
    alla: bool = False,
    torrkor: bool = False,
) -> dict:
    """
    Nollställer ``stats_final_ts`` så att matcherna hämtas om vid nästa synk.

    ``match_ids`` de matcher som ska hämtas om. ``alla`` tar samtliga i stället.

    Okända match-id är ett fel: ingenting skrivs, och de räknas upp i svaret. En
    felstavad siffra ska inte tysta kommandot och låta en annan match hämtas om
    i smyg – det är lättare att köra om med rätt id.

    Returnerar ``{"nollstallda": [...], "redan_ohamtade": [...], "okanda": [...]}``.
    ``redan_ohamtade`` är matcher vars stämpel redan var null; de hämtas ändå om
    vid nästa synk, så de räknas som klara men särredovisas. Idempotent: att köra
    kommandot två gånger ger samma läge som en gång.
    """
    if alla:
        rader = list(db.scalars(select(Match).order_by(Match.kickoff)).all())
        okanda: list[int] = []
    else:
        begarda = list(dict.fromkeys(match_ids or []))
        rader = []
        okanda = []
        for match_id in begarda:
            match = db.get(Match, match_id)
            if match is None:
                okanda.append(match_id)
            else:
                rader.append(match)

    if okanda:
        return {"nollstallda": [], "redan_ohamtade": [], "okanda": okanda}

    nollstallda: list[int] = []
    redan: list[int] = []
    for match in rader:
        if match.stats_final_ts is None:
            redan.append(match.match_id)
            continue
        if not torrkor:
            match.stats_final_ts = None
        nollstallda.append(match.match_id)

    if not torrkor:
        db.commit()

    return {
        "nollstallda": nollstallda,
        "redan_ohamtade": redan,
        "okanda": [],
    }


def _parse_args(argv: list[str]) -> tuple[list[int], bool, bool, str | None]:
    """Returnerar (match_ids, alla, torrkor, fel)."""
    alla = False
    torrkor = False
    match_ids: list[int] = []

    for arg in argv:
        if arg == "--alla":
            alla = True
        elif arg == "--torrkor":
            torrkor = True
        elif arg.startswith("-"):
            return [], False, False, f"Okänd flagga: {arg}"
        else:
            try:
                match_ids.append(int(arg))
            except ValueError:
                return [], False, False, f"Ogiltigt match-id: {arg}"

    if alla and match_ids:
        return [], False, False, (
            "Ange antingen match-id eller --alla, inte båda."
        )
    if not alla and not match_ids:
        return [], False, False, "Ange minst ett match-id, eller --alla."

    return match_ids, alla, torrkor, None


def main(argv: list[str] | None = None) -> int:
    match_ids, alla, torrkor, fel = _parse_args(
        argv if argv is not None else sys.argv[1:]
    )
    if fel is not None:
        print(fel)
        print()
        print(ANVANDNING)
        return 2

    with SessionLocal() as db:
        resultat = refresh_matches(
            db, match_ids, alla=alla, torrkor=torrkor
        )

    if resultat["okanda"]:
        saknade = ", ".join(str(m) for m in resultat["okanda"])
        print(f"Finns inte i databasen: {saknade}")
        print("Ingenting ändrades. Kontrollera match-id och kör om.")
        return 1

    if torrkor:
        print("Torrkörning – ingenting skrevs till databasen.")
        print()

    antal = len(resultat["nollstallda"])
    if antal:
        ids = ", ".join(str(m) for m in resultat["nollstallda"])
        verb = "Nollställer" if torrkor else "Nollställde"
        print(f"{verb} stämpeln för {antal} "
              f"{'match' if antal == 1 else 'matcher'}: {ids}")

    redan = resultat["redan_ohamtade"]
    if redan:
        ids = ", ".join(str(m) for m in redan)
        print(f"Redan ohämtade sedan tidigare: {ids}")

    if not antal and not redan:
        print("Inga matcher att hämta om.")
        return 0

    print()
    pronomen = "den" if antal + len(redan) == 1 else "dem"
    print(f"Nästa synk hämtar om {pronomen} helt: lineups, appearances, "
          "pruning och matchhändelser.")
    if alla:
        print("Omhämtningen gäller hela säsongen, så nästa synk tar längre tid "
              "än vanligt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
