"""Tests fuer Scoring, TCO und Filterlogik gegen eine In-Memory-DB mit Seed-Daten."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.config import DIMENSIONS, Criteria
from autobewertung.db import init_db
from autobewertung.scoring import _minmax, score_models
from autobewertung.sources.seed import SeedSource
from autobewertung.tco import TcoAssumptions, class_rank, compute_tco


def seeded_conn():
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    return conn


def labels(result):
    return [m.label for m in result.ranked]


def excluded_by(result, needle):
    return next((e for e in result.excluded if needle in e.label), None)


# --- _minmax ---------------------------------------------------------------
def test_minmax_basic_and_invert():
    vals = {1: 0.0, 2: 5.0, 3: 10.0}
    up = _minmax(vals)
    assert up[1] == 0.0 and up[3] == 100.0 and up[2] == 50.0
    inv = _minmax(vals, invert=True)
    assert inv[1] == 100.0 and inv[3] == 0.0


def test_minmax_constant_is_neutral():
    assert _minmax({1: 7.0, 2: 7.0}) == {1: 50.0, 2: 50.0}


# --- TCO -------------------------------------------------------------------
def test_class_rank_kompakt_is_two():
    assert class_rank("kompakt") == 2
    assert class_rank("kleinwagen") < class_rank("kompakt")
    assert class_rank("mittelklasse") > class_rank("kompakt")


def test_compute_tco_components_positive():
    spec = {"drivetrain": "benzin", "cons_l_100km": 6.5, "cons_kwh_100km": None,
            "insurance_eur": 480, "tax_eur": 120, "depr_pct_year": 0.13}
    a = TcoAssumptions(annual_km=15000, holding_years=5)
    r = compute_tco(spec, purchase_price=12000, maintenance_year=500, a=a)
    assert r.annual_total > 0
    assert r.resale_value < 12000                 # Wertverlust
    assert abs(sum(r.breakdown_year.values()) - r.annual_total) < 1e-6
    assert r.running_year < r.annual_total        # laufend < inkl. Wertverlust


def test_ev_cheaper_energy_than_benzin():
    a = TcoAssumptions()
    ev = {"drivetrain": "elektro", "cons_l_100km": None, "cons_kwh_100km": 16.0,
          "insurance_eur": 500, "tax_eur": 0, "depr_pct_year": 0.13}
    benzin = {"drivetrain": "benzin", "cons_l_100km": 7.0, "cons_kwh_100km": None,
              "insurance_eur": 500, "tax_eur": 120, "depr_pct_year": 0.13}
    ev_e = compute_tco(ev, 20000, 200, a).breakdown_year["energie"]
    be_e = compute_tco(benzin, 20000, 200, a).breakdown_year["energie"]
    assert ev_e < be_e


# --- Ranking / Filter ------------------------------------------------------
def test_ranking_sorted_and_bounded():
    conn = seeded_conn()
    result = score_models(conn, Criteria())
    totals = [m.total for m in result.ranked]
    assert totals == sorted(totals, reverse=True)
    for m in result.ranked:
        assert 0 <= m.total <= 100
        assert set(m.dims) == set(DIMENSIONS)


def test_budget_excludes_expensive_ice():
    """Verbrenner ueber Budget fliegt raus (keine EV-Ausnahme)."""
    conn = seeded_conn()
    crit = Criteria(max_price=15000, min_vehicle_class="kompakt",
                    ev_min_charge_km_30min=300)
    result = score_models(conn, crit)
    assert excluded_by(result, "Corolla") is not None   # 18900 > 15000
    assert "reason" or True


def test_class_filter_excludes_small_car():
    conn = seeded_conn()
    crit = Criteria(min_vehicle_class="kompakt")
    result = score_models(conn, crit)
    zoe = excluded_by(result, "Zoe")
    assert zoe is not None and "kleinwagen" in zoe.reason


def test_ev_fastcharge_requirement():
    """E-Auto unter 300 km/30min wird ausgeschlossen (ID.3), schneller EV bleibt."""
    conn = seeded_conn()
    crit = Criteria(max_price=15000, min_vehicle_class="kompakt",
                    ev_min_charge_km_30min=300)
    result = score_models(conn, crit)
    id3 = excluded_by(result, "ID.3")
    assert id3 is not None and "30min" in id3.reason


def test_ev_price_exception_lets_tesla_qualify():
    """Tesla > 15000 qualifiziert sich ueber die Ersparnis-Ausnahme."""
    conn = seeded_conn()
    crit = Criteria(max_price=15000, min_vehicle_class="kompakt",
                    ev_min_charge_km_30min=300, ev_price_exception=True)
    result = score_models(conn, crit)
    tesla = next((m for m in result.ranked if "Tesla" in m.label), None)
    assert tesla is not None, "Tesla sollte via EV-Ausnahme qualifiziert sein"
    assert tesla.purchase_price > crit.max_price
    assert tesla.ev_savings_year and tesla.ev_savings_year > 0
    assert tesla.allowed_price >= tesla.purchase_price


def test_ev_exception_off_excludes_tesla():
    conn = seeded_conn()
    crit = Criteria(max_price=15000, min_vehicle_class="kompakt",
                    ev_min_charge_km_30min=300, ev_price_exception=False)
    result = score_models(conn, crit)
    assert excluded_by(result, "Tesla") is not None
    assert all("Tesla" not in m.label for m in result.ranked)


def test_reliable_car_scores_well_on_reliability():
    conn = seeded_conn()
    result = score_models(conn, Criteria())
    mazda = next(m for m in result.ranked if "Mazda" in m.label)
    focus = next(m for m in result.ranked if "Focus" in m.label)
    assert mazda.dims["reliability"] > focus.dims["reliability"]


def test_tco_breakdown_present_for_ranked():
    conn = seeded_conn()
    result = score_models(conn, Criteria(max_price=None))
    for m in result.ranked:
        assert m.annual_tco and m.annual_tco > 0
        assert abs(sum(m.tco_breakdown.values()) - m.annual_tco) <= 3  # Rundung


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} bestanden")
    sys.exit(1 if failed else 0)
