"""
Tester för steg 15 – ändra matchlista (SPEC 6.5).

roster_edits ligger som ett lager ovanpå iBIS. iBIS-datan skrivs aldrig över.
En tillagd spelare räknas i kedjan, en borttagen gör det inte, och en ångrad
ändring återställer läget. En edit på en match med counts_for_rules = False får
aldrig påverka reglerna.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import app, get_db, _clear_status_cache
from app.auth import require_session
from app.models import Appearance, Base, Match, Player, PlayerTeam, RosterEdit
from app.status import get_statuses


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_status_cache()
    yield
    _clear_status_cache()


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


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_session] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_match(db, match_id, team, kickoff, status="played", counts_for_rules=True):
    db.add(Match(
        match_id=match_id, team=team, competition_id=100, kickoff=kickoff,
        status=status, counts_for_rules=counts_for_rules, raw={},
    ))


def add_player(db, player_id, name="Spelare", shirt_no="9", is_goalkeeper=False):
    db.add(Player(
        player_id=player_id, name=name, shirt_no=shirt_no,
        is_goalkeeper=is_goalkeeper, last_seen=datetime(2026, 1, 1),
    ))


def add_appearance(db, match_id, player_id, name="Spelare"):
    db.add(Appearance(match_id=match_id, player_id=player_id, player_name=name))


def add_roster_edit(db, match_id, player_id, action, note="iBIS-fel"):
    db.add(RosterEdit(
        match_id=match_id, player_id=player_id, action=action, note=note,
        created_at=datetime(2026, 8, 28, 12, 0), created_by="Theo",
    ))


# ---------------------------------------------------------------------------
# Regelmotorn – tillagd räknas, borttagen räknas inte
# ---------------------------------------------------------------------------

class TestRosterEditPaverkarKedjan:
    def test_tillagd_spelare_raknas_i_kedjan(self, db):
        # B (kvalificerad), sedan två A-matcher spelaren står i via iBIS.
        # Utan ändring: 2 i rad → måste stå över, men inte låst.
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 8, 10))
        add_match(db, 3, "A", datetime(2026, 8, 20))
        add_player(db, 42, "Kalle")
        for mid in (1, 2, 3):
            add_appearance(db, mid, 42, "Kalle")
        db.flush()

        before, _ = get_statuses(db)
        assert not before[42].locked
        assert before[42].matches_left == 0

        # iBIS glömde skriva upp honom i match 4 – lägg till manuellt
        add_match(db, 4, "A", datetime(2026, 8, 30))
        add_roster_edit(db, 4, 42, "add")
        db.flush()

        after, _ = get_statuses(db)
        assert after[42].locked
        assert after[42].lock_reason.value == "tre A-matcher i rad"

    def test_borttagen_spelare_raknas_inte(self, db):
        # B, sedan tre A-matcher, spelaren i alla → låst (tre i rad).
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 8, 10))
        add_match(db, 3, "A", datetime(2026, 8, 20))
        add_match(db, 4, "A", datetime(2026, 8, 30))
        add_player(db, 42, "Kalle")
        for mid in (1, 2, 3, 4):
            add_appearance(db, mid, 42, "Kalle")
        db.flush()

        before, _ = get_statuses(db)
        assert before[42].locked

        # iBIS skrev upp honom i match 3 felaktigt – ta bort
        add_roster_edit(db, 3, 42, "remove")
        db.flush()

        after, _ = get_statuses(db)
        assert not after[42].locked
        # match 3 nollställer inte längre – A2 (+1), gap i A3, A4 (+1)
        assert sorted(after[42].a_match_ids) == [2, 4]
        assert after[42].consecutive_a == 1

    def test_angrad_andring_aterstaller_laget(self, db):
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 8, 10))
        add_match(db, 3, "A", datetime(2026, 8, 20))
        add_match(db, 4, "A", datetime(2026, 8, 30))
        add_player(db, 42, "Kalle")
        for mid in (1, 2, 3):
            add_appearance(db, mid, 42, "Kalle")
        add_roster_edit(db, 4, 42, "add")
        db.flush()

        locked, _ = get_statuses(db)
        assert locked[42].locked

        # Ångra: radera roster_edit-raden
        for e in db.query(RosterEdit).all():
            db.delete(e)
        db.flush()

        # Tillbaka till iBIS-läget: spelaren står inte i match 4 → står över den
        # → kedjan nollställd, inte låst. Identiskt med motorn utan edits.
        restored, _ = get_statuses(db)
        raw, _ = get_statuses(db, apply_edits=False)
        assert not restored[42].locked
        assert restored[42].consecutive_a == 0
        assert restored[42].locked == raw[42].locked
        assert restored[42].consecutive_a == raw[42].consecutive_a

    def test_edit_pa_icke_raknande_match_paverkar_inte_reglerna(self, db):
        # Spelaren har bara en A-träningsmatch (counts_for_rules = False) och en
        # roster_edit på den. Räknades den skulle kvalificeringsregeln låsa
        # spelaren direkt. Nu ska han inte ens finnas i utfallet.
        add_match(db, 1, "A", datetime(2026, 9, 1), counts_for_rules=False)
        add_player(db, 42, "Träningsspelare")
        add_roster_edit(db, 1, 42, "add")
        db.flush()

        statuses, _ = get_statuses(db)
        assert 42 not in statuses

    def test_remove_pa_icke_raknande_match_bryter_inte_riktig_kedja(self, db):
        # B, A, A (räknas) = 2 i rad. En A-träningsmatch mitt i som spelaren
        # står i via iBIS – en remove där får inte påverka något, eftersom
        # matchen ändå inte räknas.
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 8, 10))
        add_match(db, 3, "A", datetime(2026, 8, 20), counts_for_rules=False)
        add_match(db, 4, "A", datetime(2026, 8, 30))
        add_player(db, 42, "Kalle")
        for mid in (1, 2, 3, 4):
            add_appearance(db, mid, 42, "Kalle")
        add_roster_edit(db, 3, 42, "remove")
        db.flush()

        statuses, _ = get_statuses(db)
        s = statuses[42]
        assert not s.locked
        assert s.matches_left == 0
        assert sorted(s.a_match_ids) == [2, 4]


# ---------------------------------------------------------------------------
# Del 1 – markering av spelare vars låsstatus påverkats
# ---------------------------------------------------------------------------

class TestDel1Markering:
    def test_paverkad_spelare_markeras_i_status(self, client, db):
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 8, 10))
        add_match(db, 3, "A", datetime(2026, 8, 20))
        add_match(db, 4, "A", datetime(2026, 8, 30))
        add_player(db, 42, "Kalle")
        for mid in (1, 2, 3):
            add_appearance(db, mid, 42, "Kalle")
        add_roster_edit(db, 4, 42, "add", note="Glömd i iBIS")
        db.flush()

        data = client.get("/api/status").json()
        row = next(p for p in data["grupper"]["lasta"] if p["player_id"] == 42)
        assert row["roster_edit"] is not None
        assert row["roster_edit"]["note"] == "Glömd i iBIS"
        assert row["roster_edit"]["action"] == "add"

    def test_edit_utan_effekt_pa_lasstatus_markeras_inte(self, client, db):
        # Spelaren är redan tillgänglig; en add i en match han redan står i
        # ändrar ingenting.
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 8, 10))
        add_player(db, 42, "Kalle")
        add_appearance(db, 1, 42, "Kalle")
        add_appearance(db, 2, 42, "Kalle")
        add_roster_edit(db, 2, 42, "add", note="ingen effekt")
        db.flush()

        data = client.get("/api/status").json()
        alla = (
            data["grupper"]["tillgangliga"]
            + data["grupper"]["maste_sta_over"]
            + data["grupper"]["lasta"]
        )
        row = next(p for p in alla if p["player_id"] == 42)
        assert row["roster_edit"] is None

    def test_edit_pa_icke_raknande_match_markeras_inte(self, client, db):
        add_match(db, 1, "B", datetime(2026, 8, 1))
        add_match(db, 2, "A", datetime(2026, 9, 1), counts_for_rules=False)
        add_player(db, 42, "Kalle")
        add_appearance(db, 1, 42, "Kalle")
        add_roster_edit(db, 2, 42, "add", note="träningsmatch")
        db.flush()

        data = client.get("/api/status").json()
        row = next(
            p for p in data["grupper"]["tillgangliga"] if p["player_id"] == 42
        )
        assert row["roster_edit"] is None
        assert not any(
            p["player_id"] == 42 for p in data["grupper"]["lasta"]
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class TestRosterEditEndpoints:
    def _match(self, db):
        add_match(db, 1, "B", datetime(2026, 9, 1), status="scheduled")
        add_player(db, 10, "Utespelare", "7")
        add_player(db, 11, "Bänkad", "12")
        add_appearance(db, 1, 10, "Utespelare")
        db.add(PlayerTeam(player_id=10, team="B"))
        db.add(PlayerTeam(player_id=11, team="B"))
        db.flush()

    def test_add_lagger_till_spelare_i_truppen(self, client, db):
        self._match(db)
        res = client.post(
            "/api/matches/1/roster-edits",
            json={"player_id": 11, "action": "add", "note": "Stod uppskriven"},
        )
        assert res.status_code == 200

        data = client.get("/api/matches/1").json()
        ids = [p["player_id"] for p in data["trupp"]]
        assert 11 in ids
        added = next(p for p in data["trupp"] if p["player_id"] == 11)
        assert added["roster_edit"]["action"] == "add"
        assert added["roster_edit"]["note"] == "Stod uppskriven"
        # inte längre en kandidat att lägga till
        assert not any(p["player_id"] == 11 for p in data["lagtrupp"])

    def test_remove_tar_bort_spelare_ur_truppen(self, client, db):
        self._match(db)
        res = client.post(
            "/api/matches/1/roster-edits",
            json={"player_id": 10, "action": "remove", "note": "Fel i iBIS"},
        )
        assert res.status_code == 200

        data = client.get("/api/matches/1").json()
        assert not any(p["player_id"] == 10 for p in data["trupp"])
        assert [p["player_id"] for p in data["borttagna"]] == [10]
        assert data["borttagna"][0]["roster_edit"]["note"] == "Fel i iBIS"

    def test_angra_aterstaller(self, client, db):
        self._match(db)
        client.post(
            "/api/matches/1/roster-edits",
            json={"player_id": 10, "action": "remove", "note": "Fel"},
        )
        res = client.delete("/api/matches/1/roster-edits/10")
        assert res.status_code == 200

        data = client.get("/api/matches/1").json()
        assert any(p["player_id"] == 10 for p in data["trupp"])
        assert data["borttagna"] == []

    def test_ny_edit_ersatter_tidigare(self, client, db):
        self._match(db)
        client.post(
            "/api/matches/1/roster-edits",
            json={"player_id": 11, "action": "add", "note": "först"},
        )
        client.post(
            "/api/matches/1/roster-edits",
            json={"player_id": 11, "action": "add", "note": "sedan"},
        )
        rows = db.query(RosterEdit).filter(RosterEdit.player_id == 11).all()
        assert len(rows) == 1
        assert rows[0].note == "sedan"

    def test_okand_match_ger_404(self, client, db):
        self._match(db)
        res = client.post(
            "/api/matches/999/roster-edits",
            json={"player_id": 10, "action": "remove", "note": "x"},
        )
        assert res.status_code == 404

    def test_ogiltig_action_ger_400(self, client, db):
        self._match(db)
        res = client.post(
            "/api/matches/1/roster-edits",
            json={"player_id": 10, "action": "swap", "note": "x"},
        )
        assert res.status_code == 400

    def test_tom_anteckning_ger_400(self, client, db):
        self._match(db)
        res = client.post(
            "/api/matches/1/roster-edits",
            json={"player_id": 10, "action": "remove", "note": "   "},
        )
        assert res.status_code == 400

    def test_okand_spelare_ger_404(self, client, db):
        self._match(db)
        res = client.post(
            "/api/matches/1/roster-edits",
            json={"player_id": 8888, "action": "add", "note": "x"},
        )
        assert res.status_code == 404

    def test_angra_obefintlig_edit_ger_200(self, client, db):
        self._match(db)
        assert client.delete("/api/matches/1/roster-edits/10").status_code == 200


# ---------------------------------------------------------------------------
# Skottregistreringens spelarlista följer med (SPEC 6.5)
# ---------------------------------------------------------------------------

class TestSkottlistanFoljerMed:
    def test_tillagd_och_borttagen_syns_i_matchtruppen(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1), status="played",
                  counts_for_rules=False)
        add_player(db, 10, "Kvar", "7")
        add_player(db, 20, "Bort", "8")
        add_player(db, 30, "Till", "9")
        add_appearance(db, 1, 10, "Kvar")
        add_appearance(db, 1, 20, "Bort")
        db.add(PlayerTeam(player_id=30, team="A"))
        db.flush()

        client.post("/api/matches/1/roster-edits",
                    json={"player_id": 20, "action": "remove", "note": "fel"})
        client.post("/api/matches/1/roster-edits",
                    json={"player_id": 30, "action": "add", "note": "spelade"})

        trupp_ids = {p["player_id"] for p in client.get("/api/matches/1").json()["trupp"]}
        assert trupp_ids == {10, 30}
