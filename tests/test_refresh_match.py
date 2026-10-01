"""
Tester för python -m app.refresh_match.

Kommandot nollställer matches.stats_final_ts så att nästa synk hämtar om
matchen helt: lineups, appearances, pruning och matchhändelser. Det är vägen
in för rättelser i iBIS efter slutrapporten, som den vanliga synken annars
aldrig fångar (SPEC 3.5, 6.7).
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Appearance, Base, Match, Player
from app.refresh_match import _parse_args, main, refresh_matches
from app.sync import run_sync
from tests.test_sync import (
    MOCK_SETTINGS,
    TEAM_A_ID,
    build_client,
    make_lineups_dict,
    make_match_dict,
    make_player_dict,
    make_team_dict,
)


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    MakeSession = sessionmaker(bind=eng, autoflush=True)
    with MakeSession() as session:
        yield session


def add_match(db, match_id, *, stats_final_ts, kickoff=datetime(2026, 9, 19, 13)):
    db.add(Match(
        match_id=match_id, team="A", competition_id=100, kickoff=kickoff,
        status="played", raw={}, stats_final_ts=stats_final_ts,
    ))


# ---------------------------------------------------------------------------
# Argumenttolkning
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_ett_match_id(self):
        assert _parse_args(["1703100"]) == ([1703100], False, False, None)

    def test_flera_match_id(self):
        ids, alla, torrkor, fel = _parse_args(["1703100", "1703106"])
        assert ids == [1703100, 1703106]
        assert (alla, torrkor, fel) == (False, False, None)

    def test_alla(self):
        assert _parse_args(["--alla"]) == ([], True, False, None)

    def test_torrkor(self):
        ids, alla, torrkor, fel = _parse_args(["1703100", "--torrkor"])
        assert (ids, alla, torrkor, fel) == ([1703100], False, True, None)

    def test_utan_argument_ger_fel(self):
        assert _parse_args([])[3] is not None

    def test_alla_och_match_id_samtidigt_ger_fel(self):
        assert _parse_args(["--alla", "1703100"])[3] is not None

    def test_okand_flagga_ger_fel(self):
        assert _parse_args(["--allt"])[3] is not None

    def test_ogiltigt_match_id_ger_fel(self):
        fel = _parse_args(["1703100", "tolv"])[3]
        assert fel is not None and "tolv" in fel


# ---------------------------------------------------------------------------
# refresh_matches
# ---------------------------------------------------------------------------

class TestRefreshMatches:
    def test_nollstaller_angiven_match(self, db):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        db.flush()

        resultat = refresh_matches(db, [1703100])

        assert resultat["nollstallda"] == [1703100]
        assert db.get(Match, 1703100).stats_final_ts is None

    def test_ror_inte_andra_matcher(self, db):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        add_match(db, 1703106, stats_final_ts="2026-09-26T16:00:00")
        db.flush()

        refresh_matches(db, [1703100])

        assert db.get(Match, 1703106).stats_final_ts == "2026-09-26T16:00:00"

    def test_flera_match_id(self, db):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        add_match(db, 1703106, stats_final_ts="2026-09-26T16:00:00")
        db.flush()

        resultat = refresh_matches(db, [1703100, 1703106])

        assert resultat["nollstallda"] == [1703100, 1703106]
        assert db.get(Match, 1703100).stats_final_ts is None
        assert db.get(Match, 1703106).stats_final_ts is None

    def test_alla_nollstaller_samtliga(self, db):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00",
                  kickoff=datetime(2026, 9, 19, 13))
        add_match(db, 1703106, stats_final_ts="2026-09-26T16:00:00",
                  kickoff=datetime(2026, 9, 26, 14))
        db.flush()

        resultat = refresh_matches(db, alla=True)

        assert resultat["nollstallda"] == [1703100, 1703106]
        assert db.get(Match, 1703100).stats_final_ts is None
        assert db.get(Match, 1703106).stats_final_ts is None

    def test_redan_ohamtad_sarredovisas(self, db):
        add_match(db, 1703100, stats_final_ts=None)
        db.flush()

        resultat = refresh_matches(db, [1703100])

        assert resultat["nollstallda"] == []
        assert resultat["redan_ohamtade"] == [1703100]

    def test_okant_match_id_andrar_ingenting(self, db):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        db.flush()

        resultat = refresh_matches(db, [1703100, 999999])

        assert resultat["okanda"] == [999999]
        assert resultat["nollstallda"] == []
        # Den kända matchen är orörd – hela körningen avbröts.
        assert db.get(Match, 1703100).stats_final_ts == "2026-09-19T15:30:00"

    def test_torrkor_skriver_inget(self, db):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        db.flush()

        resultat = refresh_matches(db, [1703100], torrkor=True)

        assert resultat["nollstallda"] == [1703100]
        assert db.get(Match, 1703100).stats_final_ts == "2026-09-19T15:30:00"

    def test_dubblerat_id_rapporteras_en_gang(self, db):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        db.flush()

        assert refresh_matches(db, [1703100, 1703100])["nollstallda"] == [1703100]

    def test_idempotent(self, db):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        db.flush()

        refresh_matches(db, [1703100])
        resultat = refresh_matches(db, [1703100])

        assert resultat["redan_ohamtade"] == [1703100]
        assert db.get(Match, 1703100).stats_final_ts is None

    def test_appearances_ror_inte_kommandot(self, db):
        """Kommandot rör bara stämpeln. Underlaget skrivs om av synken."""
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        db.add(Player(player_id=77, name="William Lindahl",
                      last_seen=datetime(2026, 9, 19, 13)))
        db.add(Appearance(match_id=1703100, player_id=77,
                          player_name="William Lindahl", shirt_no="22"))
        db.flush()

        refresh_matches(db, [1703100])

        assert db.get(Appearance, (1703100, 77)) is not None


# ---------------------------------------------------------------------------
# main(): exitkoder
# ---------------------------------------------------------------------------

class TestMain:
    @pytest.fixture(autouse=True)
    def _session(self, db, monkeypatch):
        """SessionLocal pekas om till testdatabasen. db.commit() stänger inte."""
        monkeypatch.setattr(
            "app.refresh_match.SessionLocal",
            MagicMock(return_value=MagicMock(
                __enter__=lambda _s: db, __exit__=lambda *_a: False
            )),
        )

    def test_nollstallning_ger_noll(self, db, capsys):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        db.flush()

        assert main(["1703100"]) == 0
        assert "1703100" in capsys.readouterr().out
        assert db.get(Match, 1703100).stats_final_ts is None

    def test_okant_id_ger_ett(self, db, capsys):
        assert main(["999999"]) == 1
        assert "Ingenting ändrades" in capsys.readouterr().out

    def test_felaktiga_argument_ger_tva(self, capsys):
        assert main([]) == 2
        assert "Användning" in capsys.readouterr().out

    def test_torrkor_namns_i_utskriften(self, db, capsys):
        add_match(db, 1703100, stats_final_ts="2026-09-19T15:30:00")
        db.flush()

        assert main(["1703100", "--torrkor"]) == 0
        assert "Torrkörning" in capsys.readouterr().out
        assert db.get(Match, 1703100).stats_final_ts == "2026-09-19T15:30:00"


# ---------------------------------------------------------------------------
# Hela kedjan: kommandot plus en synk tar bort spelaren
# ---------------------------------------------------------------------------

class TestKommandotLoserBuggen:
    def test_borttagen_spelare_forsvinner_efter_refresh_och_synk(
        self, db, monkeypatch
    ):
        """
        Konkret fall: William Lindahl togs ur truppen för match 1703100 i iBIS
        efter att matchen slutrapporterats. Synken hoppade över lineups, så
        pruningen kördes aldrig och han låg kvar. Efter refresh_match hämtas
        matchen om och han försvinner.
        """
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(
            1703100, match_datetime="2026-09-19T13:00:00",
            goals_home=2, goals_away=3,
            final_result_ts="2026-09-19T15:30:00",
        )
        full = make_lineups_dict(1703100, away_players=[
            make_player_dict(42, "Kalle", "7"),
            make_player_dict(77, "William Lindahl", "22"),
        ])
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703100: full}),
        ).ok is True
        assert db.get(Appearance, (1703100, 77)) is not None

        # iBIS plockar bort honom. En vanlig synk hoppar över matchen helt.
        utan = make_lineups_dict(1703100, away_players=[
            make_player_dict(42, "Kalle", "7"),
        ])
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                              lineups_by_id={1703100: utan})
        assert run_sync(db, client).ok is True
        client.fetch_lineups.assert_not_called()
        assert db.get(Appearance, (1703100, 77)) is not None

        # Efter kommandot hämtas matchen om, och pruningen körs.
        refresh_matches(db, [1703100])
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                              lineups_by_id={1703100: utan})
        log = run_sync(db, client)

        assert log.ok is True
        client.fetch_lineups.assert_called_once_with(1703100)
        assert db.get(Appearance, (1703100, 77)) is None
        assert db.get(Appearance, (1703100, 42)) is not None
        # Spelaren själv är orörd.
        assert db.get(Player, 77) is not None
        # Stämpeln är satt igen, så matchen fryser på nytt till nästa gång.
        assert db.get(Match, 1703100).stats_final_ts == "2026-09-19T15:30:00"

    def test_statistiken_hamtas_om_efter_rattad_slutrapport(self, db, monkeypatch):
        """Omhämtningen skriver om appearances statistik ur Events."""
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        from tests.test_sync import make_event_dict

        m = make_match_dict(
            1703100, match_datetime="2026-09-19T13:00:00",
            goals_home=2, goals_away=3,
            final_result_ts="2026-09-19T15:30:00",
        )
        lineups = make_lineups_dict(1703100, away_players=[
            make_player_dict(42, "Kalle", "7"),
        ])
        ett_mal = [make_event_dict(1, 1, 2, 42)]
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703100: lineups},
                         events_by_id={1703100: ett_mal}),
        ).ok is True
        assert db.get(Appearance, (1703100, 42)).goals == 1

        # Sekretariatet rättar: målet var inte hans.
        refresh_matches(db, [1703100])
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703100: lineups},
                         events_by_id={1703100: [make_event_dict(1, 1, 2, 99)]}),
        ).ok is True

        assert db.get(Appearance, (1703100, 42)).goals == 0
