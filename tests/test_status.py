"""
Tester för eligibility-tjänsten (app/status.py).
Kontrollerar att DB-läsning och mappning till eligibility.py är korrekt.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Appearance as OrmAppearance
from app.models import Base, Match as OrmMatch, Player as OrmPlayer
from app.status import get_statuses


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    MakeSession = sessionmaker(bind=eng, autoflush=True)
    with MakeSession() as session:
        yield session


def add_match(db, match_id, team, kickoff, status="played", counts_for_rules=True):
    db.add(OrmMatch(
        match_id=match_id, team=team, competition_id=100,
        kickoff=kickoff, status=status, counts_for_rules=counts_for_rules, raw={},
    ))


def add_appearance(db, match_id, player_id, name="Testspelare"):
    db.add(OrmAppearance(match_id=match_id, player_id=player_id, player_name=name))


class TestGetStatuses:
    def test_tom_databas_ger_inga_spelare(self, db):
        statuses, warnings = get_statuses(db)
        assert statuses == {}
        assert warnings == []

    def test_b_match_utan_a_match_ger_tillgaenglig_spelare(self, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_appearance(db, 1, 42, "Kalle Karlsson")
        db.flush()

        statuses, warnings = get_statuses(db)

        assert 42 in statuses
        s = statuses[42]
        assert not s.locked
        assert s.has_b_appearance
        assert s.matches_left == 2

    def test_a_match_utan_b_match_forst_laser_spelaren(self, db):
        add_match(db, 1, "A", datetime(2020, 1, 1))
        add_appearance(db, 1, 42, "Erik Eriksson")
        db.flush()

        statuses, _ = get_statuses(db)

        assert statuses[42].locked
        assert statuses[42].lock_reason.value == "spelade A-match innan nagon B-match"

    def test_tre_a_matcher_i_rad_laser(self, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_match(db, 3, "A", datetime(2020, 1, 20))
        add_match(db, 4, "A", datetime(2020, 1, 30))
        for match_id in (1, 2, 3, 4):
            add_appearance(db, match_id, 42, "Lars Larsson")
        db.flush()

        statuses, _ = get_statuses(db)

        assert statuses[42].locked
        assert statuses[42].lock_reason.value == "tre A-matcher i rad"

    def test_tva_a_matcher_i_rad_ar_ok(self, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_match(db, 3, "A", datetime(2020, 1, 20))
        for match_id in (1, 2, 3):
            add_appearance(db, match_id, 42, "Spelare")
        db.flush()

        statuses, _ = get_statuses(db)

        s = statuses[42]
        assert not s.locked
        assert s.matches_left == 0   # måste stå över

    def test_instaelld_match_ignoreras(self, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10), status="cancelled")
        add_match(db, 3, "A", datetime(2020, 1, 20))
        add_match(db, 4, "A", datetime(2020, 1, 30))
        add_match(db, 5, "A", datetime(2020, 2, 10))
        for mid in (1, 2, 3, 4, 5):
            add_appearance(db, mid, 42, "Spelare")
        db.flush()

        statuses, _ = get_statuses(db)

        # Match 2 är inställd → ignoreras. Matchar 3, 4, 5 är tre i rad → låst.
        assert statuses[42].locked

    def test_schemalagd_match_raknas_inte(self, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_match(db, 3, "A", datetime(2099, 1, 1), status="scheduled")
        for mid in (1, 2, 3):
            add_appearance(db, mid, 42, "Spelare")
        db.flush()

        statuses, _ = get_statuses(db)

        s = statuses[42]
        assert not s.locked
        assert s.matches_left == 1  # bara en spelad A-match → en kvar

    def test_flera_spelare_oberoende(self, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_appearance(db, 1, 10, "Spelare A")
        add_appearance(db, 1, 20, "Spelare B")
        add_appearance(db, 2, 10, "Spelare A")  # spelare A spelar A utan B → låst
        db.flush()

        statuses, _ = get_statuses(db)

        # Spelare 10 spelade B, sedan A → OK (inte låst, 1 A-match → matches_left=1)
        assert not statuses[10].locked
        # Spelare 20 spelade bara B → tillgänglig
        assert not statuses[20].locked
        assert statuses[20].matches_left == 2

    def test_status_mappning_team_enum(self, db):
        """Strängen 'A'/'B' i DB mappas korrekt till Team-enum."""
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_appearance(db, 1, 42, "Spelare")
        db.flush()

        # Inga exceptions = mappningen fungerade
        statuses, _ = get_statuses(db)
        assert 42 in statuses


class TestTraningsmatcherRaknasInte:
    """En match med counts_for_rules == False får aldrig påverka reglerna."""

    def test_kvalificeringsregeln_en_traningsmatch_i_a_laser_inte(self, db):
        # Spelaren har bara en A-träningsmatch, aldrig någon B-match.
        # Räknade den skulle kvalificeringsregeln låsa spelaren direkt.
        add_match(db, 1, "A", datetime(2026, 9, 1, 20, 20), counts_for_rules=False)
        add_appearance(db, 1, 42, "Träningsspelare")
        db.flush()

        statuses, _ = get_statuses(db)

        # Träningsmatchen skickas inte in alls → spelaren finns inte i utfallet
        assert 42 not in statuses

    def test_kvalificeringsregeln_traningsmatch_paverkar_inte_riktig_kedja(self, db):
        # B (räknas), sedan A-träningsmatch (räknas inte), sedan A (räknas).
        # Räknade träningsmatchen skulle spelaren ha 2 A i rad; nu bara 1.
        add_match(db, 1, "B", datetime(2026, 8, 1), counts_for_rules=True)
        add_match(db, 2, "A", datetime(2026, 8, 15), counts_for_rules=False)
        add_match(db, 3, "A", datetime(2026, 9, 1), counts_for_rules=True)
        for mid in (1, 2, 3):
            add_appearance(db, mid, 42, "Spelare")
        db.flush()

        statuses, _ = get_statuses(db)

        s = statuses[42]
        assert not s.locked
        assert s.matches_left == 1          # bara en räknad A-match
        assert s.a_match_ids == [3]

    def test_kedjeregeln_traningsmatch_okar_inte_kedjan(self, db):
        # B, A, A (räknas) = 2 i rad → måste stå över, men inte låst.
        # En A-träningsmatch mitt i får inte göra det till 3 i rad.
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 8, 10))
        add_match(db, 3, "A", datetime(2026, 8, 20), counts_for_rules=False)  # träning
        add_match(db, 4, "A", datetime(2026, 8, 30))
        for mid in (1, 2, 3, 4):
            add_appearance(db, mid, 42, "Spelare")
        db.flush()

        statuses, _ = get_statuses(db)

        s = statuses[42]
        assert not s.locked
        assert s.matches_left == 0
        assert sorted(s.a_match_ids) == [2, 4]

    def test_kedjeregeln_traningsmatch_nollstaller_inte_kedjan(self, db):
        # B, A, A (räknas). En A-träningsmatch som spelaren INTE står i får
        # inte heller nollställa kedjan – den ska ignoreras helt.
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 8, 10))
        add_match(db, 3, "A", datetime(2026, 8, 20), counts_for_rules=False)
        add_match(db, 4, "A", datetime(2026, 8, 30))
        add_appearance(db, 1, 42, "Spelare")
        add_appearance(db, 2, 42, "Spelare")
        add_appearance(db, 4, 42, "Spelare")  # står inte i träningsmatch 3
        db.flush()

        statuses, _ = get_statuses(db)

        s = statuses[42]
        assert not s.locked
        assert s.matches_left == 0          # 2 räknade A i rad, kedjan obruten
        assert s.consecutive_a == 2

    def test_appearances_i_traningsmatch_ignoreras_helt(self, db):
        # Enbart en träningsmatch i databasen → tomt utfall, inga spelare.
        add_match(db, 99, "A", datetime(2026, 9, 1), counts_for_rules=False)
        add_appearance(db, 99, 1, "En")
        add_appearance(db, 99, 2, "Två")
        db.flush()

        statuses, warnings = get_statuses(db)

        assert statuses == {}
        assert warnings == []
