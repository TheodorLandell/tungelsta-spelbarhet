from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Match(Base):
    __tablename__ = "matches"

    match_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team: Mapped[str] = mapped_column(String(1))           # 'A' | 'B'
    competition_id: Mapped[int] = mapped_column(Integer)
    kickoff: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16))        # 'played' | 'scheduled' | 'cancelled'
    round_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opponent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # CompetitionTypeID == 1 (serie) → True. Cup, träningsmatch och allt annat
    # → False. Endast matcher med True skickas in i regelmotorn.
    counts_for_rules: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1"
    )
    # True när iBIS ännu inte satt datum/tid (MatchTimeMissing, eller
    # platshållaren 1 januari 00:00). Går aldrig in i regelmotorn – oavsett
    # kickoff – och visas sist i matchlistan med "Datum ej satt".
    date_missing: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    # FinalResultCreatedTS som gällde när appearances senast hämtades. Trupper
    # publiceras före matchstart, så statistiken skrivs även mitt under matchen
    # – och då är den halvfärdig. Synken får därför bara hoppa över en match
    # vars statistik hämtades *efter* att slutresultatet rapporterades, alltså
    # när det här värdet är satt och lika med matchens nuvarande
    # FinalResultCreatedTS. Null betyder "hämta om" (SPEC 3.5).
    stats_final_ts: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON)

    appearances: Mapped[list["Appearance"]] = relationship(back_populates="match")


class Appearance(Base):
    __tablename__ = "appearances"

    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.match_id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.player_id"), primary_key=True)
    player_name: Mapped[str] = mapped_column(String(128))
    shirt_no: Mapped[str | None] = mapped_column(String(8), nullable=True)
    goals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    assists: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    penalty_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    match: Mapped["Match"] = relationship(back_populates="appearances")
    player: Mapped["Player"] = relationship(back_populates="appearances")


class Player(Base):
    __tablename__ = "players"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    shirt_no: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_goalkeeper: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    last_seen: Mapped[datetime] = mapped_column(DateTime)

    appearances: Mapped[list["Appearance"]] = relationship(back_populates="player")
    overrides: Mapped[list["Override"]] = relationship(back_populates="player")
    teams: Mapped[list["PlayerTeam"]] = relationship(back_populates="player")


class PlayerTeam(Base):
    """
    Lagtillhörighet per spelare. Många-till-många: sju spelare står i båda
    lagens trupper. Fylls av synken som unionen av två källor:
      - spelaren finns i lagets Players[] från teams-endpointen
      - spelaren har en appearance i en match som tillhör laget
    Används bara som filter i gränssnittet, aldrig av regelmotorn.
    """

    __tablename__ = "player_teams"

    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.player_id"), primary_key=True
    )
    team: Mapped[str] = mapped_column(String(1), primary_key=True)  # 'A' | 'B'

    player: Mapped["Player"] = relationship(back_populates="teams")


class Override(Base):
    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.player_id"))
    kind: Mapped[str] = mapped_column(String(32))          # 'lock' | 'unlock' | 'set_matches_left'
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null för lock/unlock
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(Text)
    data_snapshot: Mapped[datetime] = mapped_column(DateTime)

    player: Mapped["Player"] = relationship(back_populates="overrides")


class RosterEdit(Base):
    """
    Manuell ändring av en matchs trupp (SPEC 6.5). Ligger som ett lager ovanpå
    iBIS-datan, som aldrig skrivs över.

      - 'add'    lägger till en spelare i underlaget för matchen
      - 'remove' tar bort en spelare som iBIS registrerat felaktigt

    Ändringen påverkar både regelmotorn och skottregistreringens spelarlista,
    eftersom syftet är att rätta fel i underlaget. Varje ändring kräver en
    anteckning och går att ångra (raden raderas). Som overrides rör den aldrig
    rådatan. Högst en aktiv rad per (match, spelare); en ny ersätter en tidigare.
    """

    __tablename__ = "roster_edits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("matches.match_id"), index=True
    )
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.player_id"))
    action: Mapped[str] = mapped_column(String(8))          # 'add' | 'remove'
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(Text)


class ShotEvent(Base):
    """
    Manuellt registrerade skott (SPEC 6.2–6.4). Primärnyckeln är ett UUID som
    skapas på klienten, så att samma händelse kan skickas flera gånger utan att
    bli en dubblett. Borttagning är en tombstone (deleted_at), aldrig en radering.
    created_by är tränarens kortnamn från klienten.

    side skiljer det egna laget från motståndaren. Motståndarens skott
    registreras bara på lagnivå (SPEC 6.1), så player_id är null för
    side == 'motstandare' och satt för side == 'egen'.

    goalkeeper_id är spegelvänt: satt för motståndarens skott och null för våra
    egna. Det är den målvakt som var vald när trycket gjordes, och är därmed
    den enda källan till vem som stod i mål vid ett givet skott (SPEC 6.8).
    """

    __tablename__ = "shot_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("matches.match_id"), index=True
    )
    player_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("players.player_id"), nullable=True
    )
    goalkeeper_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("players.player_id"), nullable=True
    )
    side: Mapped[str] = mapped_column(          # 'egen' | 'motstandare'
        String(16), default="egen", server_default="egen"
    )
    kind: Mapped[str] = mapped_column(String(16))          # 'on_goal' | 'missed' | 'blocked'
    period: Mapped[int] = mapped_column(Integer)           # 1 | 2 | 3
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MatchEvent(Base):
    """
    Mål och utvisningar från iBIS Events[] (SPEC 6.7).

    Enda källan till *vilken period* ett mål gjordes i. Events är null i
    lag-endpointen och fylls först i /matches/{id} när matchen spelats, så
    tabellen är tom för matcher där iBIS ännu inte publicerat händelserna.

    Primärnyckeln är iBIS eget MatchEventID, så en omsynk skriver över samma
    rad i stället för att skapa dubbletter. Bara händelser för våra egna
    spelare sparas – motståndarnas mål per period kommer från
    IntermediateResults i stället, som redan ligger i matchens raw.

    penalty_minutes är null när längden inte gick att läsa ur PenaltyName. En
    sådan utvisning räknas bara i "hela matchen" (se app/periods.py).
    """

    __tablename__ = "match_events"

    match_event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("matches.match_id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))          # 'goal' | 'penalty'
    period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.player_id"))
    assist_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalty_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    second: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SyncLog(Base):
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    matches_added: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    ok: Mapped[bool] = mapped_column(Boolean)
