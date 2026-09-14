"""
Tester för backfill av goalkeeper_id på gamla motståndarskott (SPEC 6.8).

Fyller bara i när det går att göra säkert: exakt en målvakt i matchens trupp.
Flera målvakter, eller ingen, lämnas null – en gissning vore värre än en
saknad siffra.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backfill_goalkeeper import backfill
from app.models import Appearance, Base, Match, Player, RosterEdit, ShotEvent


@pytest.fixture
def db():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    MakeSession = sessionmaker(bind=eng, autoflush=True)
    with MakeSession() as session:
        yield session


def add_match(db, match_id, team="B"):
    db.add(Match(
        match_id=match_id, team=team, competition_id=100,
        kickoff=datetime(2026, 9, 14, 20), status="played",
        opponent="IFK Haninge (C)", raw={},
    ))


def add_player(db, player_id, namn, *, malvakt=False):
    db.add(Player(
        player_id=player_id, name=namn, shirt_no="9",
        is_goalkeeper=malvakt, last_seen=datetime(2026, 9, 14),
    ))


def add_appearance(db, match_id, player_id, namn):
    db.add(Appearance(
        match_id=match_id, player_id=player_id, player_name=namn,
        shirt_no="9", goals=0, assists=0, penalty_minutes=0,
    ))


_n = [0]


def add_shot(db, match_id, side, *, gk=None, period=1, player_id=None):
    _n[0] += 1
    sid = f"s{_n[0]}"
    db.add(ShotEvent(
        id=sid, match_id=match_id, player_id=player_id, goalkeeper_id=gk,
        side=side, kind="on_goal", period=period,
        created_at=datetime(2026, 9, 14, 20, 30), created_by="Theo",
    ))
    return sid


def shot(db, sid):
    return db.get(ShotEvent, sid)


# ---------------------------------------------------------------------------
# Match 1723835: Alex Dodzeen var enda målvakten
# ---------------------------------------------------------------------------

class TestMatch1723835:
    def _seed(self, db):
        """Truppen från den verkliga matchen: en målvakt, 14 utespelare."""
        add_match(db, 1723835, "B")
        add_player(db, 506638, "Alex Dodzeen", malvakt=True)
        add_appearance(db, 1723835, 506638, "Alex Dodzeen")
        for pid, namn in [
            (205819, "Martin Midelf"), (490139, "Felix Wikström"),
            (480798, "William Lindahl"), (132951, "Johnny Andersson"),
            (118461, "Joacim Rastas Costell"),
        ]:
            add_player(db, pid, namn)
            add_appearance(db, 1723835, pid, namn)

        # Motståndarskott registrerade innan målvaktsvalet fanns
        ids = [add_shot(db, 1723835, "motstandare", period=p)
               for p in (1, 1, 2, 3, 3)]
        db.flush()
        return ids

    def test_alla_motstandarskott_hamnar_pa_alex_dodzeen(self, db):
        ids = self._seed(db)

        resultat = backfill(db)

        assert resultat["tilldelade"] == 5
        assert resultat["lamnade"] == 0
        for sid in ids:
            assert shot(db, sid).goalkeeper_id == 506638

    def test_beslutet_loggas_med_namn_och_antal(self, db):
        self._seed(db)
        resultat = backfill(db)

        assert len(resultat["matcher"]) == 1
        match_id, antal, beslut = resultat["matcher"][0]
        assert match_id == 1723835
        assert antal == 5
        assert "Alex Dodzeen" in beslut
        assert "506638" in beslut

    def test_torrkorning_skriver_inget(self, db):
        ids = self._seed(db)

        resultat = backfill(db, dry_run=True)

        assert resultat["tilldelade"] == 5
        for sid in ids:
            assert shot(db, sid).goalkeeper_id is None


# ---------------------------------------------------------------------------
# Säkerhetsreglerna
# ---------------------------------------------------------------------------

class TestSakerhet:
    def test_flera_malvakter_lamnas_null(self, db):
        add_match(db, 1)
        add_player(db, 10, "Målvakt A", malvakt=True)
        add_player(db, 20, "Målvakt B", malvakt=True)
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 1, 20, "Målvakt B")
        sid = add_shot(db, 1, "motstandare")
        db.flush()

        resultat = backfill(db)

        assert resultat["tilldelade"] == 0
        assert resultat["lamnade"] == 1
        assert shot(db, sid).goalkeeper_id is None
        assert "flera målvakter" in resultat["matcher"][0][2]

    def test_ingen_malvakt_lamnas_null(self, db):
        add_match(db, 1)
        add_player(db, 30, "Utespelare")
        add_appearance(db, 1, 30, "Utespelare")
        sid = add_shot(db, 1, "motstandare")
        db.flush()

        resultat = backfill(db)

        assert resultat["lamnade"] == 1
        assert shot(db, sid).goalkeeper_id is None
        assert "ingen målvakt" in resultat["matcher"][0][2]

    def test_egna_skott_ross_aldrig(self, db):
        add_match(db, 1)
        add_player(db, 10, "Målvakt A", malvakt=True)
        add_player(db, 30, "Utespelare")
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 1, 30, "Utespelare")
        eget = add_shot(db, 1, "egen", player_id=30)
        db.flush()

        resultat = backfill(db)

        assert resultat["tilldelade"] == 0
        assert shot(db, eget).goalkeeper_id is None
        assert shot(db, eget).player_id == 30

    def test_redan_satt_malvakt_rors_inte(self, db):
        add_match(db, 1)
        add_player(db, 10, "Målvakt A", malvakt=True)
        add_player(db, 20, "Målvakt B", malvakt=True)
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 1, 20, "Målvakt B")
        sid = add_shot(db, 1, "motstandare", gk=20)
        db.flush()

        resultat = backfill(db)

        # Matchen har flera målvakter, men skottet har redan en – det plockas
        # aldrig upp av backfillen.
        assert resultat["tilldelade"] == 0
        assert resultat["lamnade"] == 0
        assert shot(db, sid).goalkeeper_id == 20

    def test_idempotent(self, db):
        add_match(db, 1)
        add_player(db, 10, "Målvakt A", malvakt=True)
        add_appearance(db, 1, 10, "Målvakt A")
        sid = add_shot(db, 1, "motstandare")
        db.flush()

        assert backfill(db)["tilldelade"] == 1
        # Andra körningen har ingenting kvar att göra.
        andra = backfill(db)
        assert andra["tilldelade"] == 0
        assert andra["matcher"] == []
        assert shot(db, sid).goalkeeper_id == 10

    def test_utan_skott_att_fylla_i_hander_inget(self, db):
        add_match(db, 1)
        db.flush()
        resultat = backfill(db)
        assert resultat == {"tilldelade": 0, "lamnade": 0, "matcher": []}

    def test_roster_edit_paverkar_vilka_malvakter_som_raknas(self, db):
        # iBIS hade med två målvakter, men tränaren har tagit bort den ena.
        # Då är truppen entydig igen.
        add_match(db, 1)
        add_player(db, 10, "Målvakt A", malvakt=True)
        add_player(db, 20, "Målvakt B", malvakt=True)
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 1, 20, "Målvakt B")
        db.add(RosterEdit(
            match_id=1, player_id=20, action="remove", note="stod inte i truppen",
            created_at=datetime(2026, 9, 14, 21), created_by="Theo",
        ))
        sid = add_shot(db, 1, "motstandare")
        db.flush()

        resultat = backfill(db)

        assert resultat["tilldelade"] == 1
        assert shot(db, sid).goalkeeper_id == 10

    def test_varje_match_bedoms_for_sig(self, db):
        add_match(db, 1)
        add_match(db, 2)
        add_player(db, 10, "Målvakt A", malvakt=True)
        add_player(db, 20, "Målvakt B", malvakt=True)
        # Match 1: bara en målvakt. Match 2: båda.
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 2, 10, "Målvakt A")
        add_appearance(db, 2, 20, "Målvakt B")
        s1 = add_shot(db, 1, "motstandare")
        s2 = add_shot(db, 2, "motstandare")
        db.flush()

        resultat = backfill(db)

        assert resultat["tilldelade"] == 1
        assert resultat["lamnade"] == 1
        assert shot(db, s1).goalkeeper_id == 10
        assert shot(db, s2).goalkeeper_id is None
