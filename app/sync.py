"""
Synkjobb: hämtar matchdata och lineups från iBIS och sparar i databasen.

Kör manuellt: python -m app.sync
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, engine
from app.ibis_client import (
    EVENT_GOAL,
    IBISClient,
    IBISMatch,
    IBISMatchPlayer,
    IBISSquadPlayer,
    IBISTeam,
    PlayerMatchStats,
    get_team_players,
    is_date_missing,
    is_goalkeeper_player,
    is_played,
    parse_kickoff,
    parse_match_events,
    penalty_minutes_from_name,
    player_stats_from_events,
)
from app.models import (
    Appearance,
    Base,
    Match,
    MatchEvent,
    Player,
    PlayerTeam,
    SyncLog,
)

STOCKHOLM = timezone(timedelta(hours=2))


@dataclass
class SyncResult:
    """Värden lästa ur SyncLog medan sessionen fortfarande var öppen."""
    ok: bool
    matches_added: int
    warnings: list[str]
    started_at: datetime
    finished_at: datetime
    log_id: int


def _now_naive() -> datetime:
    return datetime.now(tz=STOCKHOLM).replace(tzinfo=None)


def _match_status(m: IBISMatch) -> str:
    if m.Cancelled:
        return "cancelled"
    if is_played(m):
        return "played"
    return "scheduled"


def _opponent(m: IBISMatch, team_id: int) -> str | None:
    if m.HomeTeamID == team_id:
        return m.AwayTeam
    if m.AwayTeamID == team_id:
        return m.HomeTeam
    return None


def _upsert_match(
    db: Session,
    m: IBISMatch,
    team_label: str,
    team_id: int,
    raw: dict,
    *,
    counts_for_rules: bool = True,
) -> tuple[Match, bool]:
    """Sparar eller uppdaterar en match. Returnerar (orm-objekt, är_ny)."""
    existing = db.get(Match, m.MatchID)
    status = _match_status(m)
    kickoff = parse_kickoff(m.MatchDateTime).replace(tzinfo=None)
    opponent = _opponent(m, team_id)
    date_missing = is_date_missing(m)

    if existing is None:
        match = Match(
            match_id=m.MatchID,
            team=team_label,
            competition_id=m.CompetitionID,
            kickoff=kickoff,
            status=status,
            round_name=m.RoundName,
            opponent=opponent,
            counts_for_rules=counts_for_rules,
            date_missing=date_missing,
            raw=raw,
        )
        db.add(match)
        return match, True

    # Befintlig match: håll matchdatan färsk (SPEC punkt 1). En flyttad eller
    # ändrad match ska uppdateras även när lineups hoppas över – skippvillkoren
    # längre ned i _sync_one_match gäller bara lineups, inte matchraden.
    #   kickoff (MatchDateTime), status, resultat (raw), motståndare, hall (raw)
    #   och omgång hålls färska. Gäller alla matcher, även färdigrapporterade –
    #   ett resultat kan rättas i efterhand.
    existing.kickoff = kickoff
    existing.status = status
    existing.round_name = m.RoundName
    existing.opponent = opponent
    existing.counts_for_rules = counts_for_rules
    existing.date_missing = date_missing
    existing.raw = raw
    return existing, False


def _clean_shirt(value: str | None) -> str | None:
    """Tomt tröjnummer är detsamma som inget. Aldrig '' i databasen."""
    if value is None:
        return None
    s = value.strip()
    return s or None


def _upsert_player(db: Session, p: IBISMatchPlayer, kickoff: datetime) -> None:
    """
    Sparar en spelare utifrån en matchtrupp.

    Lineupen är en *observation*: så här såg det ut i den här matchen. Numret
    skrivs därför bara om matchen är minst lika ny som den som satte det
    nuvarande numret – annars kunde en gammal match skriva över ett nyare
    nummer, eftersom matcherna inte synkas i datumordning (SPEC 3.6).
    """
    shirt = _clean_shirt(p.ShirtNo)
    is_gk = is_goalkeeper_player(p)
    has_position = bool((p.Position or "").strip())
    existing = db.get(Player, p.PlayerID)
    if existing is None:
        db.add(Player(
            player_id=p.PlayerID,
            name=p.Name,
            shirt_no=shirt,
            shirt_seen=kickoff if shirt is not None else None,
            is_goalkeeper=is_gk,
            last_seen=kickoff,
        ))
        return

    existing.name = p.Name
    # Null får aldrig skriva över ett befintligt nummer (SPEC 3.6).
    if shirt is not None and (
        existing.shirt_seen is None or kickoff >= existing.shirt_seen
    ):
        existing.shirt_no = shirt
        existing.shirt_seen = kickoff
    # Uppdatera bara målvaktsmarkeringen när lineupen faktiskt har
    # positionsdata – annars skulle en tom position nolla en tidigare
    # känd målvakt.
    if has_position:
        existing.is_goalkeeper = is_gk
    if kickoff > existing.last_seen:
        existing.last_seen = kickoff


def _upsert_squad_player(db: Session, p: IBISSquadPlayer, sync_at: datetime) -> None:
    """
    Sparar en spelare ur lagets Players[].

    Tröjnumret skrivs *inte* här. Lagtruppen är en registrering som kan ligga
    efter verkligheten, och en spelare kan dessutom stå i båda lagens trupper
    med olika nummer. Numren samlas in och avgörs i stället samlat när båda
    lagen är hämtade, se _resolve_squad_shirts.
    """
    shirt = _clean_shirt(p.ShirtNo)
    is_gk = is_goalkeeper_player(p)
    has_position = bool((p.Position or "").strip())
    existing = db.get(Player, p.PlayerID)
    if existing is None:
        db.add(Player(
            player_id=p.PlayerID,
            name=p.Name,
            shirt_no=shirt,
            shirt_seen=None,      # inte observerat i någon match ännu
            is_goalkeeper=is_gk,
            last_seen=sync_at,
        ))
    else:
        existing.name = p.Name
        if has_position:
            existing.is_goalkeeper = is_gk


def _resolve_squad_shirts(
    db: Session,
    squad_shirts: dict[int, dict[str, str]],
    sync_at: datetime,
    warnings: list[str],
) -> None:
    """
    Skriver tröjnummer från lagtrupperna, när de går att lita på (SPEC 3.6).

    Sju–åtta spelare står i båda lagens trupper, och iBIS håller numret per lag.
    Säger lagen olika har den ena listan slutat stämma – då är det säkrare att
    behålla numret spelaren faktiskt bar i sin senaste match än att låta det lag
    som råkar synkas sist vinna.

      - Ett lag listar spelaren, eller alla är överens → skriv numret
      - Lagen säger olika → skriv inte, behåll lineupens observation, varna

    Numret stämplas med synktidpunkten, så att bara en match som spelas därefter
    kan ändra det. Null och tomma nummer är redan bortsorterade.
    """
    for player_id, per_team in squad_shirts.items():
        varden = {shirt for shirt in per_team.values() if shirt is not None}
        if not varden:
            continue

        if len(varden) > 1:
            lag = ", ".join(
                f"{team}={shirt}" for team, shirt in sorted(per_team.items())
            )
            warnings.append(
                f"Spelare {player_id}: lagen anger olika tröjnummer ({lag}) – "
                "behåller numret från senaste matchtruppen"
            )
            continue

        player = db.get(Player, player_id)
        if player is None:
            continue
        player.shirt_no = varden.pop()
        player.shirt_seen = sync_at


def _prune_player_teams(
    db: Session, team_label: str, squad_ids: set[int]
) -> int:
    """
    Tar bort lagtillhörigheter som inte längre finns i iBIS (SPEC 3.6).

    Laget behåller unionen av två källor: lagets Players[] och spelare med en
    appearance i en av lagets matcher. En spelare som plockats ur truppen men
    har spelat för laget ligger kvar – han *har* tillhört laget.

    Bara raden i player_teams försvinner. Spelaren, hans appearances, skott och
    låsstatus rörs aldrig. Returnerar antalet borttagna rader.
    """
    spelat = set(db.scalars(
        select(Appearance.player_id)
        .join(Match, Match.match_id == Appearance.match_id)
        .where(Match.team == team_label)
        .distinct()
    ).all())
    behall = squad_ids | spelat

    borttagna = 0
    for row in db.scalars(
        select(PlayerTeam).where(PlayerTeam.team == team_label)
    ).all():
        if row.player_id not in behall:
            db.delete(row)
            borttagna += 1
    return borttagna


def _upsert_player_team(db: Session, player_id: int, team_label: str) -> None:
    """Registrerar att en spelare hör till ett lag. Idempotent."""
    if db.get(PlayerTeam, (player_id, team_label)) is None:
        db.add(PlayerTeam(player_id=player_id, team=team_label))


def _sync_player_teams(db: Session, team_label: str) -> None:
    """
    Fyller player_teams för ett lag som unionen av två källor:
      - spelare med en appearance i en match som tillhör laget
      - spelare i lagets Players[] (registreras separat i squad-loopen)
    """
    player_ids = db.scalars(
        select(Appearance.player_id)
        .join(Match, Match.match_id == Appearance.match_id)
        .where(Match.team == team_label)
        .distinct()
    ).all()
    for pid in player_ids:
        _upsert_player_team(db, pid, team_label)


def _has_appearances(db: Session, match_id: int) -> bool:
    return db.scalars(
        select(Appearance.player_id)
        .where(Appearance.match_id == match_id)
        .limit(1)
    ).first() is not None


def _save_appearances(
    db: Session,
    match_id: int,
    players: list[IBISMatchPlayer],
    kickoff: datetime,
    stats: dict[int, PlayerMatchStats] | None = None,
) -> None:
    """
    Sparar en appearance per spelare i truppen, med statistiken från Events.

    stats är None när iBIS inte publicerat matchens händelser än (och för
    matcher som inte spelats). Då lämnas siffrorna orörda: en befintlig rad
    behåller vad den har, en ny rad får nollor tills händelserna finns. Att
    skriva nollor över riktiga siffror vore sämre än att vänta.
    """
    for p in players:
        _upsert_player(db, p, kickoff)
        s = stats.get(p.PlayerID) if stats is not None else None
        existing = db.get(Appearance, (match_id, p.PlayerID))
        if existing is None:
            db.add(Appearance(
                match_id=match_id,
                player_id=p.PlayerID,
                player_name=p.Name,
                shirt_no=p.ShirtNo,
                goals=s.goals if s else 0,
                assists=s.assists if s else 0,
                penalty_minutes=s.penalty_minutes if s else 0,
            ))
        elif s is not None:
            # Befintlig appearance: uppdatera statistiken från iBIS.
            existing.goals = s.goals
            existing.assists = s.assists
            existing.penalty_minutes = s.penalty_minutes


def _save_match_events(
    db: Session,
    match_id: int,
    raw_match: dict,
    own_player_ids: set[int],
) -> int:
    """
    Sparar mål- och utvisningshändelser för våra egna spelare (SPEC 6.7).

    Bara händelser vi kan knyta till en spelare i truppen sparas – motståndarnas
    mål per period kommer från IntermediateResults i matchens raw, inte härifrån.

    Gamla rader för matchen rensas först, så att en rättelse i iBIS (borttagen
    eller ändrad händelse) slår igenom i stället för att ligga kvar. Returnerar
    antalet sparade händelser.
    """
    events = parse_match_events(raw_match)
    if not events:
        # iBIS har inte publicerat händelserna än. Rör inget – det som eventuellt
        # redan finns är bättre än att tömma tabellen.
        return 0

    for row in db.scalars(
        select(MatchEvent).where(MatchEvent.match_id == match_id)
    ).all():
        db.delete(row)
    db.flush()

    saved = 0
    for e in events:
        if e.PlayerID is None or e.PlayerID not in own_player_ids:
            continue
        is_goal = e.MatchEventTypeID == EVENT_GOAL
        assist = e.PlayerAssistID or None
        db.add(MatchEvent(
            match_event_id=e.MatchEventID,
            match_id=match_id,
            kind="goal" if is_goal else "penalty",
            period=e.Period,
            player_id=e.PlayerID,
            assist_player_id=assist if is_goal else None,
            penalty_minutes=(
                None if is_goal else penalty_minutes_from_name(e.PenaltyName)
            ),
            minute=e.Minute,
            second=e.Second,
        ))
        saved += 1
    return saved


def _sync_one_match(
    db: Session,
    client: IBISClient,
    match: IBISMatch,
    team_label: str,
    team_id: int,
    raw_by_match: dict[int, dict],
    counts_for_rules: bool,
    warnings: list[str],
) -> bool:
    """
    Synkar en enskild match: sparar matchraden och, när det är läge, hämtar
    lineups och sparar appearances. Returnerar True om matchraden var ny.

    Anropas inom en try/except i run_sync – kastar den ett undantag hoppas
    matchen över och synken fortsätter med nästa.
    """
    raw = raw_by_match.get(match.MatchID, match.model_dump(mode="json"))
    db_match, is_new = _upsert_match(
        db, match, team_label, team_id, raw,
        counts_for_rules=counts_for_rules,
    )

    # Inställda matcher hoppas över helt (SPEC punkt 1) – de spelas aldrig och
    # får aldrig en publicerad trupp.
    if match.Cancelled:
        return is_new

    # Avbruten utan resultat: logga varning, hoppa över appearances
    if match.Abandoned and not is_played(match):
        warnings.append(
            f"Match {match.MatchID} ({match.MatchDateTime[:10]}): "
            "avbruten utan registrerat resultat – hoppas över"
        )
        return is_new

    # Truppen publiceras i iBIS före matchstart, så lineups hämtas även för
    # matcher som ännu inte spelats. Är lineups tom sparas inget (players blir
    # []). Det är counts_for_rules som avgör om matchen når regelmotorn, se
    # app/status.py – sync.py sparar bara underlaget.
    kickoff = parse_kickoff(match.MatchDateTime).replace(tzinfo=None)

    # En färdigrapporterad match hämtas inte om (SPEC 3.5) – men bara om
    # statistiken faktiskt hämtades *efter* att slutresultatet rapporterades.
    #
    # Trupper publiceras före matchstart, så appearances skrivs redan före och
    # under matchen. En spelare som får sin andra utvisning i period 3 hann då
    # sparas med halva antalet minuter. Den gamla regeln såg bara att det fanns
    # appearances och frös de siffrorna för alltid. Genom att jämföra mot
    # stats_final_ts hämtas matchen om en gång efter slutrapporten, och igen om
    # sekretariatet rättar resultatet i efterhand.
    if (
        match.FinalResultCreatedTS
        and db_match.stats_final_ts == match.FinalResultCreatedTS
        and _has_appearances(db, match.MatchID)
    ):
        return is_new

    # Kommande matcher: trupper publiceras inte tidigare än sju dagar före
    # kickoff, så matcher längre bort än så hämtas inte – annars hämtas lineups
    # för hela säsongen vid varje synk och jobbet tar minuter. Spelade matcher
    # berörs inte av tidsgränsen.
    if not is_played(match) and kickoff - _now_naive() > timedelta(days=7):
        return is_new

    lineups = client.fetch_lineups(match.MatchID)
    players = get_team_players(
        lineups, team_id, is_home=match.HomeTeamID == team_id
    )
    own_player_ids = {p.PlayerID for p in players}

    # Matchhändelserna är numera både källan till spelarnas mål, assist och
    # utvisningsminuter (lineups har dem inte längre) och till vilken period de
    # hör (SPEC 3.3, 6.7). Events är null i lag-endpointen, så matchobjektet
    # måste hämtas separat – men bara för spelade matcher, där det finns något
    # att hämta.
    raw_match = client.fetch_match_raw(match.MatchID) if is_played(match) else None

    stats = (
        player_stats_from_events(raw_match, own_player_ids)
        if raw_match is not None
        else None
    )
    _save_appearances(db, match.MatchID, players, kickoff, stats)

    if raw_match is not None:
        _save_match_events(db, match.MatchID, raw_match, own_player_ids)

    # Stämpla vad statistiken bygger på, så nästa synk vet om den är färdig.
    db_match.stats_final_ts = match.FinalResultCreatedTS
    return is_new


def run_sync(db: Session, client: IBISClient) -> SyncResult:
    """
    Hämtar matchdata från iBIS och sparar i databasen.
    Returnerar SyncResult med värden lästa medan sessionen var öppen.
    ORM-objekt lämnar aldrig funktionen.
    """
    log = SyncLog(
        started_at=_now_naive(),
        finished_at=None,
        matches_added=0,
        warnings=[],
        ok=False,
    )
    db.add(log)
    db.flush()

    warnings: list[str] = []
    matches_added = 0
    # player_id -> {lag: tröjnummer} från lagens Players[]. Avgörs samlat när
    # båda lagen är hämtade, eftersom en spelare kan stå i båda trupperna.
    squad_shirts: dict[int, dict[str, str]] = {}

    try:
        for team_label, team_id in (("A", settings.team_a_id), ("B", settings.team_b_id)):
            db.flush()  # gör föregående lags pending-objekt synliga för get()
            raw_team = client.fetch_team_raw(settings.season_id, team_id)
            team = IBISTeam.model_validate(raw_team)

            # Bygg uppslag match_id → rådata för alla matcher (även cup/träning)
            raw_by_match: dict[int, dict] = {}
            for comp_data in raw_team.get("Competitions", []):
                for m_data in comp_data.get("Matches", []):
                    raw_by_match[m_data["MatchID"]] = m_data

            # Alla tävlingar, inte bara serien. Matcher med annan
            # CompetitionTypeID sparas för skottregistrering men markeras med
            # counts_for_rules = False och rör aldrig regelmotorn.
            for comp in team.Competitions:
                counts_for_rules = comp.CompetitionTypeID == 1
                for match in comp.Matches:
                    # Ett fel på EN match (t.ex. iBIS returnerar ett fält i fel
                    # typ i lineups-svaret) får inte avbryta hela synken. Logga
                    # som varning i sync_log, hoppa över matchen och fortsätt.
                    try:
                        is_new = _sync_one_match(
                            db, client, match, team_label, team_id,
                            raw_by_match, counts_for_rules, warnings,
                        )
                    except Exception as exc:
                        warnings.append(
                            f"Match {match.MatchID} ({match.MatchDateTime[:10]}): "
                            f"hoppades över efter fel – {exc}"
                        )
                        continue
                    if is_new:
                        matches_added += 1

            # Spara trupp-spelare från lagets Players[]-lista (även de utan matcher)
            squad_at = _now_naive()
            db.flush()  # gör match-fasens pending-objekt synliga för get()
            for squad_player in team.Players:
                _upsert_squad_player(db, squad_player, squad_at)
                _upsert_player_team(db, squad_player.PlayerID, team_label)
                shirt = _clean_shirt(squad_player.ShirtNo)
                if shirt is not None:
                    squad_shirts.setdefault(squad_player.PlayerID, {})[
                        team_label
                    ] = shirt

            # Lagtillhörighet: unionen av trupp-listan (ovan) och spelade matcher
            db.flush()
            _sync_player_teams(db, team_label)

            # …och bara den unionen. En spelare som plockats ur truppen i iBIS
            # och aldrig spelat för laget ska inte ligga kvar (SPEC 3.6).
            db.flush()
            borttagna = _prune_player_teams(
                db, team_label, {p.PlayerID for p in team.Players}
            )
            if borttagna:
                warnings.append(
                    f"Lag {team_label}: {borttagna} "
                    f"{'spelare' if borttagna == 1 else 'spelare'} borttagna ur "
                    "lagtruppen (finns kvar med sin historik)"
                )

        # Tröjnummer från lagtrupperna avgörs när båda lagen är hämtade, så att
        # lagen inte skriver över varandra (SPEC 3.6).
        db.flush()
        _resolve_squad_shirts(db, squad_shirts, _now_naive(), warnings)

        log.ok = True

    except Exception as exc:
        warnings.append(f"Synken avbröts med fel: {exc}")

    finally:
        log.matches_added = matches_added
        log.warnings = warnings
        log.finished_at = _now_naive()
        db.commit()

    # Läs ut värden medan sessionen fortfarande är öppen
    return SyncResult(
        ok=log.ok,
        matches_added=log.matches_added,
        warnings=list(log.warnings),
        started_at=log.started_at,
        finished_at=log.finished_at,
        log_id=log.id,
    )


if __name__ == "__main__":
    Base.metadata.create_all(engine)   # säkerhetsnät om alembic inte körts
    client = IBISClient()
    with SessionLocal() as db:
        log = run_sync(db, client)

    status = "klar" if log.ok else "misslyckades"
    print(f"Synk {status}. {log.matches_added} matcher tillagda.")
    for w in log.warnings:
        print(f"Varning: {w}")
    sys.exit(0 if log.ok else 1)
