"""Tests fuer das Verschleiss-/km-Kostenmodell."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.seed import SeedSource
from autobewertung.sources.wear_import import WearImportSource
from autobewertung.wear import (_occurrences, cumulative_cost, expected_repair_per_year,
                                load_items, upcoming_items)


def seeded():
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    WearImportSource().collect(conn)   # echte modellspezifische Defekte dazu
    return conn


def tesla_id(conn):
    return conn.execute("SELECT id FROM car_model WHERE model='Model 3'").fetchone()["id"]


def test_occurrences_onetime_and_interval():
    assert _occurrences(80000, 0, 50000) == 0        # noch nicht faellig
    assert _occurrences(80000, 0, 90000) == 1        # einmalig faellig
    assert _occurrences(50000, 50000, 160000) == 3   # 50k,100k,150k


def test_cumulative_monotonic():
    conn = seeded()
    items = load_items(conn, tesla_id(conn))
    assert cumulative_cost(items, 0) == 0
    assert cumulative_cost(items, 250000) > cumulative_cost(items, 100000)


def test_tesla_querlenker_from_real_import():
    """Echter Tesla-Querlenker (Buchsen ~540 EUR ab ~40k) kommt aus wear_real.csv."""
    conn = seeded()
    up = upcoming_items(conn, tesla_id(conn), start_km=30000, span_km=80000)
    q = [u for u in up if "Querlenker" in u["component"]]
    assert q, "Querlenker sollte im Fenster faellig sein"
    assert any(abs(u["cost_eur"] - 540) < 1 for u in q)


def test_id3_variants_both_get_real_wear():
    """Eine 'ID.3'-CSV-Zeile trifft Pro UND Pro S."""
    conn = seeded()
    for model in ("ID.3 Pro", "ID.3 Pro S"):
        mid = conn.execute("SELECT id FROM car_model WHERE model=?", (model,)).fetchone()["id"]
        n = conn.execute("SELECT COUNT(*) c FROM wear_item WHERE model_id=? AND source='real'",
                         (mid,)).fetchone()["c"]
        assert n >= 2, f"{model} sollte echte Verschleiss-Posten haben"


def test_expected_repair_positive_and_amortized():
    conn = seeded()
    per_year = expected_repair_per_year(conn, tesla_id(conn), 60000, 15000, 5)
    assert per_year > 0


def test_bigger_window_costs_more_or_equal():
    """Groesseres km-Fenster (gleicher Start) kann nie weniger kosten."""
    conn = seeded()
    items = load_items(conn, tesla_id(conn))
    small = cumulative_cost(items, 100000) - cumulative_cost(items, 60000)
    big = cumulative_cost(items, 160000) - cumulative_cost(items, 60000)
    assert big >= small >= 0


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} bestanden")
    sys.exit(1 if failed else 0)