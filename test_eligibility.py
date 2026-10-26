from datetime import datetime, timedelta

from eligibility import (
    Appearance,
    LockReason,
    Match,
    MatchStatus,
    Team,
    available_for_b,
    blocked_for_b,
    compute_statuses,
)

START = datetime(2026, 9, 19, 13, 0)


def m(mid, team, day_offset, status=MatchStatus.PLAYED):
    return Match(mid, team, START + timedelta(days=day_offset), status)


def a(mid, pid, name="Spelare"):
    return Appearance(mid, pid, name)


def run(matches, appearances, **kw):
    st, _ = compute_statuses(matches, appearances, **kw)
    return st


# --- Kvalificeringsregeln ---------------------------------------------------

def test_b_forst_kvalificerar():
    matches = [m(1, Team.B, 0), m(2, Team.A, 7)]
    st = run(matches, [a(1, 100), a(2, 100)])
    assert not st[100].locked
    assert st[100].consecutive_a == 1


def test_a_forst_laser_spelaren():
    matches = [m(1, Team.A, 0), m(2, Team.B, 7)]
    st = run(matches, [a(1, 100), a(2, 100)])
    assert st[100].locked
    assert st[100].lock_reason is LockReason.NO_B_MATCH_FIRST
    assert st[100].lock_match_id == 1


def test_b_match_senare_raddar_inte():
    """En B-match efter lasningen andrar ingenting."""
    matches = [m(1, Team.A, 0), m(2, Team.B, 7), m(3, Team.B, 14)]
    st = run(matches, [a(1, 100), a(2, 100), a(3, 100)])
    assert st[100].locked


def test_spelare_bara_i_b_ar_inte_last():
    matches = [m(1, Team.B, 0), m(2, Team.B, 7)]
    st = run(matches, [a(1, 100), a(2, 100)])
    assert not st[100].locked
    assert st[100].matches_left == 2


# --- Kedjeregeln ------------------------------------------------------------

def test_tva_i_rad_ar_ok():
    matches = [m(1, Team.B, 0), m(2, Team.A, 7), m(3, Team.A, 14)]
    st = run(matches, [a(1, 100), a(2, 100), a(3, 100)])
    assert not st[100].locked
    assert st[100].consecutive_a == 2
    assert st[100].matches_left == 0
    assert st[100].warning == "MASTE STA OVER"


def test_tre_i_rad_laser():
    matches = [m(1, Team.B, 0), m(2, Team.A, 7), m(3, Team.A, 14), m(4, Team.A, 21)]
    st = run(matches, [a(1, 100), a(2, 100), a(3, 100), a(4, 100)])
    assert st[100].locked
    assert st[100].lock_reason is LockReason.THREE_IN_A_ROW
    assert st[100].lock_match_id == 4


def test_sta_over_nollstaller():
    matches = [
        m(1, Team.B, 0),
        m(2, Team.A, 7), m(3, Team.A, 14),
        m(4, Team.A, 21),          # star over
        m(5, Team.A, 28), m(6, Team.A, 35),
    ]
    apps = [a(1, 100), a(2, 100), a(3, 100), a(5, 100), a(6, 100)]
    st = run(matches, apps)
    assert not st[100].locked
    assert st[100].consecutive_a == 2


def test_b_match_bryter_inte_kedjan():
    matches = [
        m(1, Team.B, 0),
        m(2, Team.A, 7), m(3, Team.A, 14),
        m(4, Team.B, 18),          # B emellan - ska INTE nollstalla
        m(5, Team.A, 21),
    ]
    apps = [a(1, 100), a(2, 100), a(3, 100), a(4, 100), a(5, 100)]
    st = run(matches, apps)
    assert st[100].locked
    assert st[100].lock_reason is LockReason.THREE_IN_A_ROW


# --- Instalda och ospelade matcher -----------------------------------------

def test_installd_a_match_hoppas_over_helt():
    """Instald match ska varken oka eller nollstalla kedjan."""
    matches = [
        m(1, Team.B, 0),
        m(2, Team.A, 7), m(3, Team.A, 14),
        m(4, Team.A, 21, MatchStatus.CANCELLED),
        m(5, Team.A, 28),
    ]
    apps = [a(1, 100), a(2, 100), a(3, 100), a(5, 100)]
    st = run(matches, apps)
    assert st[100].locked, "instald match ska inte rakna som att man stod over"


def test_kommande_match_raknas_inte_som_standard():
    matches = [
        m(1, Team.B, 0),
        m(2, Team.A, 7), m(3, Team.A, 14),
        m(4, Team.A, 21, MatchStatus.SCHEDULED),
    ]
    apps = [a(1, 100), a(2, 100), a(3, 100), a(4, 100)]
    st = run(matches, apps)
    assert not st[100].locked
    assert st[100].consecutive_a == 2


def test_simulering_med_kommande_match():
    """include_scheduled visar vad som HANDER om man skriver upp honom."""
    matches = [
        m(1, Team.B, 0),
        m(2, Team.A, 7), m(3, Team.A, 14),
        m(4, Team.A, 21, MatchStatus.SCHEDULED),
    ]
    apps = [a(1, 100), a(2, 100), a(3, 100), a(4, 100)]
    st = run(matches, apps, include_scheduled=True)
    assert st[100].locked


# --- Datumsortering ---------------------------------------------------------

def test_sorteras_pa_datum_inte_inmatningsordning():
    """Uppskjuten match som spelas senare ska hamna pa sin faktiska plats."""
    matches = [m(3, Team.A, 30), m(1, Team.B, 0), m(2, Team.A, 10)]
    apps = [a(1, 100), a(2, 100), a(3, 100)]
    st = run(matches, apps)
    assert not st[100].locked
    assert st[100].consecutive_a == 2


def test_uppskjuten_match_flyttar_kedjan():
    """A-match omg 2 skjuts upp till efter omg 4 - kedjan foljer datum."""
    matches = [
        m(1, Team.B, 0),
        m(2, Team.A, 7),
        m(3, Team.A, 14),
        m(4, Team.A, 60),   # uppskjuten, spelas sist
    ]
    # Spelaren star over match 3 men ar med i 2 och 4
    apps = [a(1, 100), a(2, 100), a(4, 100)]
    st = run(matches, apps)
    assert not st[100].locked
    assert st[100].consecutive_a == 1


# --- Flera spelare och listorna --------------------------------------------

def test_flera_spelare_oberoende():
    matches = [m(1, Team.B, 0), m(2, Team.A, 7), m(3, Team.A, 14), m(4, Team.A, 21)]
    apps = [
        a(1, 100, "Kvalificerad"), a(2, 100, "Kvalificerad"),
        a(3, 100, "Kvalificerad"), a(4, 100, "Kvalificerad"),
        a(2, 200, "Direkt till A"),
        a(1, 300, "Bara B"),
    ]
    st = run(matches, apps)
    assert st[100].lock_reason is LockReason.THREE_IN_A_ROW
    assert st[200].lock_reason is LockReason.NO_B_MATCH_FIRST
    assert not st[300].locked

    blocked = blocked_for_b(st)
    assert {s.player_id for s in blocked} == {100, 200}

    avail = available_for_b(st)
    assert [s.player_id for s in avail] == [300]


def test_sortering_mest_utsatt_overst():
    matches = [m(1, Team.B, 0), m(2, Team.A, 7), m(3, Team.A, 14)]
    apps = [
        a(1, 100, "Tva i rad"), a(2, 100, "Tva i rad"), a(3, 100, "Tva i rad"),
        a(1, 200, "En i rad"), a(3, 200, "En i rad"),
        a(1, 300, "Noll"),
    ]
    st = run(matches, apps)
    order = [s.player_id for s in available_for_b(st)]
    assert order == [100, 200, 300]


def test_last_spelare_slutar_raknas():
    matches = [m(1, Team.A, 0), m(2, Team.A, 7), m(3, Team.A, 14)]
    apps = [a(1, 100), a(2, 100), a(3, 100)]
    st = run(matches, apps)
    assert st[100].lock_reason is LockReason.NO_B_MATCH_FIRST
    assert st[100].lock_match_id == 1  # last redan pa forsta, inte senare
    assert st[100].matches_left is None


def test_varning_vid_samtidig_kickoff():
    same = datetime(2026, 10, 3, 15, 0)
    matches = [Match(1, Team.B, same), Match(2, Team.A, same)]
    st, warnings = compute_statuses(matches, [a(1, 100), a(2, 100)])
    assert warnings, "ska varna nar A och B har identisk starttid"
    assert st[100].locked  # konservativt: A rakans forst


def test_spelare_utan_matcher_finns_inte():
    matches = [m(1, Team.A, 0), m(2, Team.B, 7)]
    st = run(matches, [a(1, 100)])
    assert 999 not in st


# --- Upplasning -------------------------------------------------------------

def _last_i_match_2():
    """Spelaren ar last i match 2 (A utan B forst) och star sedan over."""
    return [m(1, Team.B, 0), m(2, Team.A, 7)], [a(2, 100)]


def test_last_star_over_tva_ar_fortfarande_last():
    matches, apps = _last_i_match_2()
    matches += [m(3, Team.A, 14), m(4, Team.A, 21)]
    st = run(matches, apps)
    assert st[100].locked
    assert st[100].skipped_a == 2


def test_last_star_over_tre_lases_upp():
    matches, apps = _last_i_match_2()
    matches += [m(3, Team.A, 14), m(4, Team.A, 21), m(5, Team.A, 28)]
    st = run(matches, apps)
    assert not st[100].locked
    assert st[100].lock_reason is None
    assert not st[100].has_b_appearance  # maste spela B forst
    assert st[100].skipped_a == 0


def test_upplast_spelar_a_direkt_lases_igen():
    matches, apps = _last_i_match_2()
    matches += [m(3, Team.A, 14), m(4, Team.A, 21), m(5, Team.A, 28), m(6, Team.A, 35)]
    apps += [a(6, 100)]
    st = run(matches, apps)
    assert st[100].locked
    assert st[100].lock_reason is LockReason.NO_B_MATCH_FIRST
    assert st[100].lock_match_id == 6


def test_upplast_spelar_b_forst_kvalificerad_med_tva_kvar():
    matches, apps = _last_i_match_2()
    matches += [m(3, Team.A, 14), m(4, Team.A, 21), m(5, Team.A, 28), m(6, Team.B, 32)]
    apps += [a(6, 100)]
    st = run(matches, apps)
    assert not st[100].locked
    assert st[100].has_b_appearance
    assert st[100].matches_left == 2


def test_rakningen_utgar_fran_senaste_a_matchen():
    """Last i match 2, spelar A i match 3 - raknas fran 3, inte 2."""
    matches, apps = _last_i_match_2()
    matches += [m(3, Team.A, 14), m(4, Team.A, 21), m(5, Team.A, 28)]
    apps += [a(3, 100)]
    st = run(matches, apps)
    assert st[100].locked, "tre A-matcher sedan lasningen men bara tva overstadda"
    assert st[100].skipped_a == 2

    matches += [m(6, Team.A, 35)]
    st = run(matches, apps)
    assert not st[100].locked


def test_last_och_upplast_tva_ganger():
    matches = [
        m(1, Team.B, 0),
        m(2, Team.A, 7),                                  # last (ingen B forst)
        m(3, Team.A, 14), m(4, Team.A, 21), m(5, Team.A, 28),  # star over -> upp
        m(6, Team.B, 32),                                 # kvalificerar
        m(7, Team.A, 35), m(8, Team.A, 42), m(9, Team.A, 49),  # tre i rad -> last
        m(10, Team.A, 56), m(11, Team.A, 63), m(12, Team.A, 70),  # star over -> upp
    ]
    apps = [a(2, 100), a(6, 100), a(7, 100), a(8, 100), a(9, 100)]

    st = run(matches[:9], apps)
    assert st[100].locked
    assert st[100].lock_reason is LockReason.THREE_IN_A_ROW

    st = run(matches, apps)
    assert not st[100].locked
    assert not st[100].has_b_appearance


def test_installd_match_raknas_inte_som_overstadd():
    matches, apps = _last_i_match_2()
    matches += [
        m(3, Team.A, 14), m(4, Team.A, 21, MatchStatus.CANCELLED), m(5, Team.A, 28),
    ]
    st = run(matches, apps)
    assert st[100].locked
    assert st[100].skipped_a == 2


def test_b_matcher_paverkar_inte_upplasningen():
    matches, apps = _last_i_match_2()
    matches += [
        m(3, Team.A, 14), m(4, Team.B, 18), m(5, Team.A, 21),
        m(6, Team.B, 25), m(7, Team.B, 26),
    ]
    apps += [a(4, 100), a(6, 100)]
    st = run(matches, apps)
    assert st[100].locked, "B-matcher ska varken rakna som overstadda eller nollstalla"
    assert st[100].skipped_a == 2

    matches += [m(8, Team.A, 28)]
    st = run(matches, apps)
    assert not st[100].locked
    assert not st[100].has_b_appearance, "B-match under lasningen kvalificerar inte"
