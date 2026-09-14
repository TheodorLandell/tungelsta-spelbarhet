"""
Tester för trupp- och tröjnummersynk (SPEC 3.6).

Tröjnummer kommer från två källor som skriver samma fält:

  - lagets ``Players[]``   en registrering, kan ligga efter verkligheten
  - matchernas ``lineups`` en observation av vad spelaren faktiskt bar

Utan regler tar de ut varandra. Den som synkas sist vinner, vilket i praktiken
betyder att en spelare i båda lagens trupper får lag B:s nummer – även när lag A
och senaste matchtruppen är överens om ett annat.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Appearance, Base, Match, Player, PlayerTeam, ShotEvent
from app.sync import run_sync

from tests.test_sync import (
    MOCK_SETTINGS,
    OTHER_ID,
    TEAM_A_ID,
    TEAM_B_ID,
    build_client,
    make_lineups_dict,
    make_match_dict,
    make_player_dict,
    make_squad_player_dict,
    make_team_dict,
)


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    MakeSession = sessionmaker(bind=eng, autoflush=True)
    with MakeSession() as session:
        yield session


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)


def played(match_id, dt="2026-09-01T19:00:00", **kw):
    return make_match_dict(
        match_id, match_datetime=dt, goals_home=1, goals_away=2,
        final_result_ts="2026-09-01T21:00:00", **kw,
    )


def shirt_of(db, player_id):
    p = db.get(Player, player_id)
    return p.shirt_no if p else None


def teams_of(db, player_id):
    return {
        r.team for r in db.scalars(
            select_player_teams(player_id)
        ).all()
    }


def select_player_teams(player_id):
    from sqlalchemy import select
    return select(PlayerTeam).where(PlayerTeam.player_id == player_id)


# ---------------------------------------------------------------------------
# Tröjnummer
# ---------------------------------------------------------------------------

class TestTrojnummer:
    def test_andrat_nummer_slar_igenom(self, db):
        db.add(Player(
            player_id=50, name="Kalle", shirt_no="70",
            is_goalkeeper=False, last_seen=datetime(2026, 8, 1),
        ))
        db.flush()

        client = build_client(
            team_a_dict=make_team_dict(
                TEAM_A_ID, [],
                players=[make_squad_player_dict(50, "Kalle", 11)],
            ),
        )
        assert run_sync(db, client).ok is True

        assert shirt_of(db, 50) == "11"

    def test_null_skriver_inte_over_befintligt_nummer(self, db):
        db.add(Player(
            player_id=50, name="Kalle", shirt_no="7",
            is_goalkeeper=False, last_seen=datetime(2026, 8, 1),
        ))
        db.flush()

        client = build_client(
            team_a_dict=make_team_dict(
                TEAM_A_ID, [],
                players=[make_squad_player_dict(50, "Kalle", None)],
            ),
        )
        assert run_sync(db, client).ok is True

        assert shirt_of(db, 50) == "7"

    def test_tom_strang_skriver_inte_over_befintligt_nummer(self, db):
        db.add(Player(
            player_id=50, name="Kalle", shirt_no="7",
            is_goalkeeper=False, last_seen=datetime(2026, 8, 1),
        ))
        db.flush()

        client = build_client(
            team_a_dict=make_team_dict(
                TEAM_A_ID, [],
                players=[make_squad_player_dict(50, "Kalle", "")],
            ),
        )
        assert run_sync(db, client).ok is True

        assert shirt_of(db, 50) == "7"

    def test_lineup_skriver_aldrig_tom_strang(self, db):
        m = played(3001)
        lineups = make_lineups_dict(3001, away_players=[
            make_player_dict(50, "Kalle", shirt_no=""),
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={3001: lineups},
        )
        assert run_sync(db, client).ok is True

        assert shirt_of(db, 50) is None

    def test_nyare_match_vinner_over_aldre(self, db):
        # Matcherna kommer i iBIS ordning, inte datumordning: den äldre sist.
        ny = played(3002, dt="2026-09-20T19:00:00")
        gammal = played(3003, dt="2026-08-10T19:00:00")
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [ny, gammal]),
            lineups_by_id={
                3002: make_lineups_dict(3002, away_players=[
                    make_player_dict(50, "Kalle", shirt_no="11")]),
                3003: make_lineups_dict(3003, away_players=[
                    make_player_dict(50, "Kalle", shirt_no="70")]),
            },
        )
        assert run_sync(db, client).ok is True

        # Numret från den senaste matchen han faktiskt spelade.
        assert shirt_of(db, 50) == "11"

    def test_lagen_overens_later_truppen_bestamma(self, db):
        client = build_client(
            team_a_dict=make_team_dict(
                TEAM_A_ID, [], players=[make_squad_player_dict(50, "Kalle", 9)]),
            team_b_dict=make_team_dict(
                TEAM_B_ID, [], players=[make_squad_player_dict(50, "Kalle", 9)]),
        )
        assert run_sync(db, client).ok is True

        assert shirt_of(db, 50) == "9"

    def test_lagen_oense_behaller_numret_fran_senaste_matchtruppen(self, db):
        """
        Felix Wikström i verkligheten: lag A säger 11, lag B säger 70, och i
        senaste spelade matchen bar han 11. Utan den här regeln vinner lag B
        bara för att det synkas sist.
        """
        m = played(3004, dt="2026-09-14T20:00:00")
        client = build_client(
            team_a_dict=make_team_dict(
                TEAM_A_ID, [m], players=[make_squad_player_dict(50, "Felix", 11)]),
            team_b_dict=make_team_dict(
                TEAM_B_ID, [], players=[make_squad_player_dict(50, "Felix", 70)]),
            lineups_by_id={
                3004: make_lineups_dict(3004, away_players=[
                    make_player_dict(50, "Felix", shirt_no="11")]),
            },
        )
        resultat = run_sync(db, client)
        assert resultat.ok is True

        assert shirt_of(db, 50) == "11"
        # Konflikten ska synas i synkloggen, inte tigas ihjäl.
        assert any("olika tröjnummer" in w for w in resultat.warnings)

    def test_trupplista_satter_nummer_pa_spelare_utan_matcher(self, db):
        client = build_client(
            team_a_dict=make_team_dict(
                TEAM_A_ID, [], players=[make_squad_player_dict(60, "Nykomling", 23)]),
        )
        assert run_sync(db, client).ok is True

        assert shirt_of(db, 60) == "23"

    def test_lineup_och_lagtrupp_tar_inte_ut_varandra(self, db):
        """
        Samma nummer i båda källorna ska förbli samma, oavsett i vilken ordning
        de skrivs – och en senare synk får inte flippa värdet.
        """
        m = played(3005, dt="2026-09-14T20:00:00")
        client = build_client(
            team_a_dict=make_team_dict(
                TEAM_A_ID, [m], players=[make_squad_player_dict(50, "Kalle", 12)]),
            lineups_by_id={
                3005: make_lineups_dict(3005, away_players=[
                    make_player_dict(50, "Kalle", shirt_no="12")]),
            },
        )
        assert run_sync(db, client).ok is True
        assert shirt_of(db, 50) == "12"

        # Kör igen – värdet ska ligga stilla.
        assert run_sync(db, client).ok is True
        assert shirt_of(db, 50) == "12"


# ---------------------------------------------------------------------------
# Lagtillhörighet
# ---------------------------------------------------------------------------

class TestPlayerTeams:
    def test_borttagen_ur_truppen_forsvinner_ur_player_teams(self, db):
        db.add(Player(
            player_id=70, name="Avregistrerad", shirt_no="97",
            is_goalkeeper=False, last_seen=datetime(2026, 8, 1),
        ))
        db.add(PlayerTeam(player_id=70, team="A"))
        db.flush()

        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [], players=[]),
        )
        resultat = run_sync(db, client)
        assert resultat.ok is True

        assert teams_of(db, 70) == set()
        # Spelaren själv finns kvar med sin historik.
        kvar = db.get(Player, 70)
        assert kvar is not None
        assert kvar.name == "Avregistrerad"
        assert kvar.shirt_no == "97"
        assert any("borttagna ur lagtruppen" in w for w in resultat.warnings)

    def test_spelare_med_appearance_ligger_kvar(self, db):
        # Han har spelat för laget – då *har* han tillhört det, även om han
        # plockats ur truppen i iBIS.
        m = played(3006)
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m], players=[]),
            lineups_by_id={
                3006: make_lineups_dict(3006, away_players=[
                    make_player_dict(80, "Spelade", shirt_no="5")]),
            },
        )
        assert run_sync(db, client).ok is True

        assert teams_of(db, 80) == {"A"}

    def test_appearances_skott_och_historik_rors_aldrig(self, db):
        db.add(Match(
            match_id=3007, team="A", competition_id=100,
            kickoff=datetime(2026, 8, 1, 19), status="played", raw={},
        ))
        db.add(Player(
            player_id=90, name="Avregistrerad", shirt_no="14",
            is_goalkeeper=False, last_seen=datetime(2026, 8, 1),
        ))
        db.add(PlayerTeam(player_id=90, team="B"))
        db.add(Appearance(
            match_id=3007, player_id=90, player_name="Avregistrerad",
            shirt_no="14", goals=2, assists=1, penalty_minutes=2,
        ))
        db.add(ShotEvent(
            id="uuid-1", match_id=3007, player_id=90, side="egen",
            kind="on_goal", period=1,
            created_at=datetime(2026, 8, 1, 19, 30), created_by="Theo",
        ))
        db.flush()

        client = build_client(team_b_dict=make_team_dict(TEAM_B_ID, [], players=[]))
        assert run_sync(db, client).ok is True

        # Lag B pruneras bort – han står inte i B:s trupp och har inte spelat
        # för B. Lag A kommer i stället till via hans appearance i en A-match,
        # vilket är just poängen: spelat = tillhört.
        assert teams_of(db, 90) == {"A"}
        app = db.get(Appearance, (3007, 90))
        assert app is not None and app.goals == 2 and app.penalty_minutes == 2
        assert db.get(ShotEvent, "uuid-1") is not None

    def test_lagen_pruneras_var_for_sig(self, db):
        db.add(Player(
            player_id=95, name="Dubbel", shirt_no="8",
            is_goalkeeper=False, last_seen=datetime(2026, 8, 1),
        ))
        db.add(PlayerTeam(player_id=95, team="A"))
        db.add(PlayerTeam(player_id=95, team="B"))
        db.flush()

        # Kvar i lag B:s trupp, borta ur lag A:s.
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [], players=[]),
            team_b_dict=make_team_dict(
                TEAM_B_ID, [], players=[make_squad_player_dict(95, "Dubbel", 8)]),
        )
        assert run_sync(db, client).ok is True

        assert teams_of(db, 95) == {"B"}

    def test_spelare_i_truppen_ligger_kvar(self, db):
        client = build_client(
            team_a_dict=make_team_dict(
                TEAM_A_ID, [], players=[make_squad_player_dict(96, "Kvar", 3)]),
        )
        assert run_sync(db, client).ok is True
        assert teams_of(db, 96) == {"A"}

        # Andra körningen ska inte plocka bort honom.
        assert run_sync(db, client).ok is True
        assert teams_of(db, 96) == {"A"}
