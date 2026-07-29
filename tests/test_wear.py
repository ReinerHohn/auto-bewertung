"""Tests fuer das Verschleiss-/km-Kostenmodell."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.seed import SeedSource
from autobewertung.wear import (_occurrences, cumulative_cost, expected_repair_per_year,
                                load_items, upcoming_items)


def seeded():
    conn = init_db(":memory:")
    SeedSource().collect(conn)
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


def test_tesla_querlenker_in_window():
    """Tesla-Querlenker (750 EUR bei 80k) faellt im Fenster 60k-140k an."""
    conn = seeded()
    up = upcoming_items(conn, tesla_id(conn), start_km=60000, span_km=80000)
    q = [u for u in up if "Querlenker" in u["component"]]
    assert q and abs(q[0]["cost_eur"] - 750) < 1


def test_expected_repair_positive_and_amortized():
    conn = seeded()
    per_year = expected_repair_per_year(conn, tesla_id(conn), 60000, 15000, 5)
    assert per_year > 0


def test_low_mileage_window_cheaper_than_high():
    """Frueheres km-Fenster (weniger Verschleiss) guenstiger als spaeteres."""
    conn = seeded()
    mid = tesla_id(conn)
    early = expected_repair_per_year(conn, mid, 20000, 15000, 5)
    late = expected_repair_per_year(conn, mid, 120000, 15000, 5)
    assert late >= early


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